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


def _run_preview_guard_contract(tmp_path, shell_name, source):
    shell = shutil.which(shell_name)
    if not shell:
        pytest.skip(f'{shell_name} unavailable; Windows mutation job requires both shells')
    script = tmp_path / 'guard.ps1'
    phase_file = tmp_path / 'guard-phases.txt'
    # Load the real definition-only owner as the production facade does, without
    # loading that facade or executing any service/provider/configuration path.
    script.write_text(r'''
param([string]$Source,[string]$PhasePath)
$ErrorActionPreference='Stop'
[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false)
function Write-ContractPhase { param([string]$Phase)
    [IO.File]::AppendAllText($PhasePath,
        [DateTimeOffset]::UtcNow.ToString('o')+' '+$Phase+' pid='+$PID+[Environment]::NewLine,
        [Text.UTF8Encoding]::new($false))
}
Write-ContractPhase 'entry'
$identity=[Diagnostics.Process]::GetCurrentProcess()
Write-ContractPhase ('identity start_ticks='+$identity.StartTime.ToUniversalTime().Ticks)
try {
$tokens=$null;$errors=$null
Write-ContractPhase 'parse-before'
$ast=[Management.Automation.Language.Parser]::ParseFile($Source,[ref]$tokens,[ref]$errors)
Write-ContractPhase 'parse-after'
if (@($errors).Count) { throw 'GUARD_SOURCE_PARSE_FAILED' }
Write-ContractPhase 'find-before'
$function=@($ast.FindAll({param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq 'Start-ReleasePromotion'},$true))
Write-ContractPhase 'find-after'
if ($function.Count -ne 1) { throw 'GUARD_SOURCE_AMBIGUOUS' }
$topLevel=@($ast.EndBlock.Statements)
if ($ast.ParamBlock -or $ast.BeginBlock -or $ast.ProcessBlock -or
    $ast.DynamicParamBlock -or $ast.CleanBlock -or $ast.ScriptRequirements -or
    $ast.EndBlock.Traps.Count -or $ast.UsingStatements.Count -or
    $ast.Attributes.Count -or @($topLevel | Where-Object {
        $_ -isnot [Management.Automation.Language.FunctionDefinitionAst] -or
        $_.IsWorkflow -or $_.IsFilter
    }).Count) { throw 'GUARD_OWNER_DECLARATIONS_ONLY' }
Write-ContractPhase 'owner-load-before'
. $Source
Write-ContractPhase 'owner-load-after'
if (${function:Start-ReleasePromotion}.File -cne $Source) { throw 'GUARD_OWNER_SOURCE_MISMATCH' }
$productionCandidateArtifactKind='PRODUCTION_CANDIDATE'
$script:kind='PREVIEW';$script:later=0;$script:unlocked=0
function Enter-ReleaseTransactionLock { return $true }
function Exit-ReleaseTransactionLock { $script:unlocked++ }
function Get-ReleaseControlState { return [pscustomobject]@{transaction=$null;candidate=[pscustomobject]@{artifact_kind=$script:kind}} }
function Assert-ActiveControlBundle { $script:later++;throw 'LATER_PRECONDITION_REACHED' }
Write-ContractPhase 'preview-before'
try { Start-ReleasePromotion; throw 'UNEXPECTED_RETURN' } catch { $preview=$_.Exception.Message }
Write-ContractPhase 'preview-after'
if ($preview -ne 'Preview and unknown artifacts cannot be promoted.' -or $script:later -ne 0 -or $script:unlocked -ne 1) { throw 'PREVIEW_GUARD_BOUNDARY_VIOLATED' }
$script:kind='PRODUCTION_CANDIDATE'
Write-ContractPhase 'production-before'
try { Start-ReleasePromotion; throw 'UNEXPECTED_RETURN' } catch { $production=$_.Exception.Message }
Write-ContractPhase 'production-after'
if ($production -ne 'LATER_PRECONDITION_REACHED' -or $script:later -ne 1 -or $script:unlocked -ne 2) { throw 'GUARD_CONTROL_CASE_INVALID' }
'EXACT_GUARD_BOUNDARY_PASSED'
} finally { Write-ContractPhase 'exit' }
''', encoding='utf-8')
    environment = {key: value for key, value in os.environ.items()
                   if key.upper() in {'PATH', 'SYSTEMROOT', 'WINDIR', 'TEMP', 'TMP', 'COMSPEC'}}
    process = subprocess.Popen(
        [shell, '-NoProfile', '-NonInteractive', '-File', str(script), '-Source', str(source),
         '-PhasePath', str(phase_file)], env=environment,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, encoding='utf-8',
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    stdout = stderr = ''
    observed_exit = None
    cleanup = 'NOT_REQUIRED'
    try:
        try:
            stdout, stderr = process.communicate(timeout=20)
        except subprocess.TimeoutExpired as failure:
            observed_exit = process.poll()  # Observe before timeout cleanup, not after kill.
            if observed_exit is None:
                process.kill()  # Retain subprocess.run's exact owned-root cleanup.
            try:
                stdout, stderr = process.communicate(timeout=3)
                cleanup = 'OWNED_ROOT_EXITED'
            except subprocess.TimeoutExpired as drain_failure:
                # The stdout reader location alone cannot prove a child is alive.
                # Retain partial streams and keep the result explicitly unresolved.
                stdout = repr(drain_failure.stdout or failure.stdout or 'STREAM_CONTENT_UNAVAILABLE')
                stderr = repr(drain_failure.stderr or failure.stderr or 'STREAM_CONTENT_UNAVAILABLE')
                cleanup = 'STREAM_DRAIN_UNRESOLVED'
            raise AssertionError('GUARD_CHILD_DEADLINE') from failure
        assert process.returncode == 0, stdout + stderr
        assert stdout.strip() == 'EXACT_GUARD_BOUNDARY_PASSED'
    except BaseException:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=3)
        raise
    finally:
        phases = 'PHASE_NOT_OBSERVED'
        if phase_file.exists():
            with phase_file.open(encoding='utf-8') as stream:
                phases = stream.read(8192)
        # Fixed phase writes use the existing installation-test pattern. Pytest
        # retains this bounded block with any failure in the normal log/JUnit.
        print(f'GUARD_DIAGNOSTICS pid={process.pid} observed_exit={observed_exit} '
              f'final_exit={process.poll()} cleanup={cleanup} phases={phases!r} '
              f'stdout={stdout[:8192]!r} stderr={stderr[:8192]!r}')


@pytest.mark.parametrize('shell_name', ['powershell.exe', 'pwsh'])
def test_preview_guard_rejects_at_exact_boundary_before_later_preconditions(tmp_path, shell_name):
    _run_preview_guard_contract(
        tmp_path, shell_name, ROOT / 'scripts/control_center_transaction_engine.ps1')


@pytest.mark.parametrize('shell_name', ['powershell.exe', 'pwsh'])
@pytest.mark.parametrize('side_effect', ['statement', 'required-module', 'trap'])
def test_preview_guard_owner_rejects_top_level_side_effects_before_loading(tmp_path, shell_name, side_effect):
    source = tmp_path / 'unsafe-owner.ps1'
    marker = tmp_path / 'must-not-exist.txt'
    original = (ROOT / 'scripts/control_center_transaction_engine.ps1').read_text(encoding='utf-8-sig')
    # The only possible injected side effect is confined to this test's own root.
    escaped_marker = str(marker).replace("'", "''")
    write = f"[IO.File]::WriteAllText('{escaped_marker}', 'executed')"
    if side_effect == 'required-module':
        module = tmp_path / 'owned-module.psm1'
        module.write_text(write + '\n', encoding='utf-8')
        escaped_module = str(module).replace("'", "''")
        changed = f"#Requires -Modules '{escaped_module}'\n" + original
    elif side_effect == 'trap':
        changed = original + '\ntrap { ' + write + '; continue }\n'
    else:
        changed = original + '\n' + write + '\n'
    source.write_text(changed, encoding='utf-8')
    with pytest.raises(AssertionError, match='GUARD_OWNER_DECLARATIONS_ONLY'):
        _run_preview_guard_contract(tmp_path, shell_name, source)
    assert not marker.exists(), 'GUARD_OWNER_TOP_LEVEL_EXECUTED'
