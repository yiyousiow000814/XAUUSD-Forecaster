"""Behavior assertions for the three historically surviving mutation families."""
from datetime import datetime, timezone
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess

import pytest

from scripts import run_dashboard_sync as sync
from xauusd_forecaster.forward_ledger import ForwardLedger

ROOT = Path(__file__).resolve().parents[1]


def test_sync_publishes_heartbeat_before_optional_work(monkeypatch):
    events = []
    target = {'name': 'isolated-test'}
    monkeypatch.setattr(sync, 'configured_targets', lambda _: [target])

    def heartbeat(_):
        events.append('heartbeat-accepted')
        return [target], sync.SyncResourceResults([], [])

    def optional(targets):
        assert events == ['heartbeat-accepted'], 'HEARTBEAT_ORDER_VIOLATED'
        assert targets == [target]
        events.append('optional-admitted')
        return sync.SyncResourceResults([], [])

    monkeypatch.setattr(sync, 'sync_heartbeat_once', heartbeat)
    monkeypatch.setattr(sync, 'sync_resource_lane', optional)
    sync.sync_once({})
    assert events == ['heartbeat-accepted', 'optional-admitted']


def test_forward_ledger_rejects_real_update_and_delete(tmp_path):
    ledger = ForwardLedger(tmp_path / 'isolated.sqlite3', now=datetime(2026, 9, 1, tzinfo=timezone.utc))
    try:
        original = ledger.connection.execute("SELECT value FROM runtime_metadata WHERE key='FORWARD_EPOCH'").fetchone()[0]
        for statement in (
            "UPDATE runtime_metadata SET value='changed' WHERE key='FORWARD_EPOCH'",
            "DELETE FROM runtime_metadata WHERE key='FORWARD_EPOCH'",
        ):
            rejected = False
            try:
                ledger.connection.execute(statement)
            except sqlite3.IntegrityError as error:
                rejected = 'append-only' in str(error)
            finally:
                ledger.connection.rollback()
            assert rejected, 'FORWARD_APPEND_ONLY_VIOLATED'
            assert ledger.connection.execute("SELECT value FROM runtime_metadata WHERE key='FORWARD_EPOCH'").fetchone()[0] == original
    finally:
        ledger.close()


@pytest.mark.parametrize('shell_name', ['powershell.exe', 'pwsh'])
def test_preview_guard_rejects_at_exact_boundary_before_later_preconditions(tmp_path, shell_name):
    shell = shutil.which(shell_name)
    if not shell:
        pytest.skip(f'{shell_name} unavailable; Windows mutation job requires both shells')
    source = ROOT / 'scripts/control_center_transaction_engine.ps1'
    script = tmp_path / 'guard.ps1'
    # Only parse and execute the exact function. No facade load, environment
    # secrets, runtime config, services, provider calls or writes are possible.
    script.write_text(r'''
param([string]$Source)
$ErrorActionPreference='Stop'
[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false)
$tokens=$null;$errors=$null
$ast=[Management.Automation.Language.Parser]::ParseFile($Source,[ref]$tokens,[ref]$errors)
if (@($errors).Count) { throw 'GUARD_SOURCE_PARSE_FAILED' }
$function=@($ast.FindAll({param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq 'Start-ReleasePromotion'},$true))
if ($function.Count -ne 1) { throw 'GUARD_SOURCE_AMBIGUOUS' }
Invoke-Expression $function[0].Extent.Text
$productionCandidateArtifactKind='PRODUCTION_CANDIDATE'
$script:kind='PREVIEW';$script:later=0;$script:unlocked=0
function Enter-ReleaseTransactionLock { return $true }
function Exit-ReleaseTransactionLock { $script:unlocked++ }
function Get-ReleaseControlState { return [pscustomobject]@{transaction=$null;candidate=[pscustomobject]@{artifact_kind=$script:kind}} }
function Assert-ActiveControlBundle { $script:later++;throw 'LATER_PRECONDITION_REACHED' }
try { Start-ReleasePromotion; throw 'UNEXPECTED_RETURN' } catch { $preview=$_.Exception.Message }
if ($preview -ne 'Preview and unknown artifacts cannot be promoted.' -or $script:later -ne 0 -or $script:unlocked -ne 1) { throw 'PREVIEW_GUARD_BOUNDARY_VIOLATED' }
$script:kind='PRODUCTION_CANDIDATE'
try { Start-ReleasePromotion; throw 'UNEXPECTED_RETURN' } catch { $production=$_.Exception.Message }
if ($production -ne 'LATER_PRECONDITION_REACHED' -or $script:later -ne 1 -or $script:unlocked -ne 2) { throw 'GUARD_CONTROL_CASE_INVALID' }
'EXACT_GUARD_BOUNDARY_PASSED'
''', encoding='utf-8')
    environment = {key: value for key, value in os.environ.items()
                   if key.upper() in {'PATH', 'SYSTEMROOT', 'WINDIR', 'TEMP', 'TMP', 'COMSPEC'}}
    result = subprocess.run([shell, '-NoProfile', '-NonInteractive', '-File', str(script), '-Source', str(source)],
                            env=environment, capture_output=True, encoding='utf-8', timeout=20,
                            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == 'EXACT_GUARD_BOUNDARY_PASSED'
