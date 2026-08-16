from pathlib import Path
import json
import socket
import sqlite3
import subprocess
import sys
import textwrap
from datetime import datetime, timedelta, timezone


ROOT = Path(__file__).resolve().parents[1]
RUNTIME_CONTROL_FILES = (
    "xauusd_control_center.ps1",
    "xauusd_watchdog_launcher.vbs",
    "xauusd_watchdog_guard.ps1",
    "xauusd_watchdog_guard_launcher.vbs",
)


def test_quote_bridge_uses_standalone_local_configuration() -> None:
    launcher = (
        ROOT
        / "ctrader"
        / "XauusdForwardQuoteBridge"
        / "run_live_quote_bridge.ps1"
    ).read_text(encoding="utf-8")

    assert "CTRADER_CLI_PATH" in launcher
    assert "CTRADER_SECRET_ROOT" in launcher
    assert ".local\\config" in launcher
    assert "$repositoryRoot" not in launcher
    assert "src\\ctrader\\windows_cli_path.txt" not in launcher


def test_control_center_treats_weekly_close_as_healthy() -> None:
    control_center = (
        ROOT / "scripts" / "xauusd_control_center.ps1"
    ).read_text(encoding="utf-8")

    assert "Test-ExpectedWeeklyMarketClosure" in control_center
    assert "Get-BrokerMarketSession" in control_center
    assert 'return "MARKET CLOSED"' in control_center
    assert '"MARKET CLOSED", "API OK"' in control_center
    assert '"SYNC ERROR", "SYNC STALE"' in control_center
    assert '"COLLECTOR STALE", "ANNOTATOR STALE"' in control_center
    assert '"SESSION STALE"' in control_center


def test_control_center_loads_collector_keys_without_exposing_them() -> None:
    control_center = (
        ROOT / "scripts" / "xauusd_control_center.ps1"
    ).read_text(encoding="utf-8")

    assert 'function Get-CollectorSecret' in control_center
    assert '.local\\secrets\\collector-keys.json' in control_center
    assert 'Get-CollectorSecret -Name "BLS_API_KEY"' in control_center
    assert 'Get-CollectorSecret -Name "BEA_API_KEY"' in control_center
    assert 'Get-CollectorSecret -Name "FRED_API_KEY"' in control_center
    assert 'Get-CollectorSecret -Name "EIA_API_KEY"' in control_center
    assert 'ConvertFrom-Json' in control_center


def test_control_center_updates_only_the_isolated_main_runtime() -> None:
    path = ROOT / "scripts" / "xauusd_control_center.ps1"
    control_center = path.read_text(encoding="utf-8")

    assert (
        '$reloadableServiceKeys = @('
        '"collector", "annotator", "api", "sync", "assistant")'
    ) in control_center
    assert 'Match = "run_assistant_worker.py"' in control_center
    assert 'CODE_REVISION_RELOAD_APPLIED' in control_center
    assert 'Write-RuntimeCodeState -Revision $Revision' in control_center
    assert 'Test-CodeReloadHealth -ReloadStarted $reloadStarted' in control_center
    assert 'Start-WatchdogReplacement' in control_center
    assert 'currentRevision -ne $appliedRevision' in control_center
    assert "Get-DeployedMainRevision" in control_center
    assert "$runtimeUpdateCheckInterval = [TimeSpan]::FromMinutes(5)" in control_center
    assert "$codeReloadTimeout = [TimeSpan]::FromMinutes(5)" in control_center
    assert "Add($codeReloadTimeout)" in control_center
    assert "Get-VerifiedOriginMain" in control_center
    assert "Test-RevisionDescendsFrom" in control_center
    assert "Test-MainCandidate" in control_center
    assert "accepted_main_revision" in control_center
    assert "Install-ProductionRuntime" in control_center
    assert 'RuntimeRoot must be separate from the development checkout' in control_center
    assert 'worktree add --detach --quiet' in control_center
    assert '-WindowStyle Hidden -PassThru' in control_center
    assert '"quote"' not in control_center.split("$reloadableServiceKeys =", 1)[1].splitlines()[0]

    reported = subprocess.run(
        [
            "powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
            "-File", str(path), "-Action", "CodeRevision",
        ],
        cwd=ROOT, capture_output=True, text=True, check=True,
    ).stdout.strip()
    expected = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT,
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    assert reported == expected


def test_local_assistant_worker_keeps_only_the_primary_model_resident() -> None:
    worker = (ROOT / "scripts" / "run_assistant_worker.py").read_text(
        encoding="utf-8",
    )

    assert '"keep_alive": -1' in worker
    assert "QWEN_ASSISTANT_MODEL" in worker
    assert '"resident_primary": QWEN_ASSISTANT_MODEL' in worker
    assert "MINISTRAL_ASSISTANT_MODEL" in worker
    assert "configured_api_credentials" not in worker


def _run_control_center_contract(tmp_path, body: str) -> str:
    runtime = tmp_path / "runtime"
    repository = tmp_path / "repository"
    runtime.mkdir(exist_ok=True)
    repository.mkdir(exist_ok=True)
    script = ROOT / "scripts" / "xauusd_control_center.ps1"
    command = (
        f"$null = . '{script}' -Action CodeRevision -RuntimeRoot '{runtime}' "
        f"-RepositoryRoot '{repository}'; {body}"
    )
    return subprocess.run(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command],
        capture_output=True, text=True, check=True,
    ).stdout.strip()


def _write_runtime_observation(tmp_path, **overrides) -> None:
    started_at = datetime.now(timezone.utc).isoformat()
    state = {
        "update_status": "OBSERVING",
        "observing_revision": "b" * 40,
        "previous_revision": "a" * 40,
        "observation_started_at": started_at,
        "observation_ready_at": started_at,
        "observation_last_decision_time": "2026-08-13T03:00:00+00:00",
        "observation_success_cycles": 0,
        "observation_consecutive_failures": 0,
    }
    state.update(overrides)
    path = tmp_path / "runtime" / ".local" / "forward" / "runtime-update-state.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state), encoding="utf-8")


def _write_control_bundle(root: Path, label: str, *, scripts_dir: bool = False) -> None:
    target = root / "scripts" if scripts_dir else root
    target.mkdir(parents=True, exist_ok=True)
    for name in RUNTIME_CONTROL_FILES:
        (target / name).write_text(f"{label}|{name}\n", encoding="utf-8")


def _bundle_result_expression(root: str) -> str:
    names = ",".join(f"'{name}'" for name in RUNTIME_CONTROL_FILES)
    return (
        f"$bundle = @({names}) | ForEach-Object {{ "
        f"(Get-Content -LiteralPath (Join-Path {root} $_) -Raw).Trim() }}; "
        "Write-Output ($bundle -join ',')"
    )


def test_failed_preflight_never_switches_the_runtime_checkout(tmp_path) -> None:
    result = _run_control_center_contract(
        tmp_path,
        "$script:preflights = 0; $script:checkouts = 0; "
        "function Get-CodeRevision { return ('a' * 40) }; "
        "function Test-MainCandidate { return $true }; "
        "function Invoke-ProductionShapePreflight { $script:preflights += 1; return $false }; "
        "function git { $script:checkouts += 1; $global:LASTEXITCODE = 0 }; "
        "$accepted = Update-RuntimeCheckout -Revision ('b' * 40); "
        'Write-Output "$accepted,$script:preflights,$script:checkouts"',
    )

    assert result == "False,1,0"


def test_preflight_selects_an_available_loopback_port(tmp_path) -> None:
    with socket.socket() as occupied:
        occupied.bind(("127.0.0.1", 0))
        occupied.listen()
        occupied_port = occupied.getsockname()[1]
        result = int(_run_control_center_contract(
            tmp_path,
            "Write-Output (Get-AvailableLoopbackPort)",
        ))

    assert 0 < result <= 65_535
    assert result != occupied_port


def test_candidate_preflight_migrates_an_isolated_consistent_copy(tmp_path) -> None:
    source = tmp_path / "legacy.sqlite3"
    target = tmp_path / "candidate" / "forward.sqlite3"
    connection = sqlite3.connect(source)
    connection.execute("CREATE TABLE retained_evidence (value TEXT NOT NULL)")
    connection.execute("INSERT INTO retained_evidence VALUES ('immutable')")
    connection.commit()
    connection.close()
    (source.parent / "dashboard-sync-status.json").write_text(
        json.dumps({"status": "OK", "last_success": "2026-08-13T19:00:00+00:00"}),
        encoding="utf-8",
    )
    incremental_states = {
        "dashboard-news-sync-state-cloudflare.json": {"cursor": "news-cursor"},
        "dashboard-learning-sync-state-cloudflare.json": {"hash": "learning"},
        "dashboard-learning-history-sync-state-cloudflare.json": {"cursor": 42},
        "dashboard-market-history-sync-state-cloudflare.json": {"cursor": "market"},
    }
    for name, payload in incremental_states.items():
        (source.parent / name).write_text(json.dumps(payload), encoding="utf-8")
    quotes = source.parent / "quotes"
    quotes.mkdir()
    (quotes / "market-session.json").write_text(
        json.dumps({"is_open": True}), encoding="utf-8",
    )

    result = _run_control_center_contract(
        tmp_path,
        f"New-CandidatePreflightDatabase -Python '{sys.executable}' "
        f"-StageRoot '{ROOT}' -SourceDatabase '{source}' "
        f"-TargetDatabase '{target}'; Copy-CandidatePreflightState "
        f"-SourceDatabase '{source}' -TargetDatabase '{target}'; "
        "Write-Output 'prepared'",
    )

    assert result == "prepared"
    source_connection = sqlite3.connect(source)
    target_connection = sqlite3.connect(target)
    assert source_connection.execute(
        "SELECT name FROM sqlite_master WHERE name='news_event_identity_resolutions_v1'"
    ).fetchone() is None
    assert target_connection.execute(
        "SELECT value FROM retained_evidence"
    ).fetchone() == ("immutable",)
    assert target_connection.execute(
        "SELECT name FROM sqlite_master WHERE name='news_event_identity_resolutions_v1'"
    ).fetchone() == ("news_event_identity_resolutions_v1",)
    assert json.loads(
        (target.parent / "dashboard-sync-status.json").read_text(encoding="utf-8")
    )["status"] == "OK"
    for name, payload in incremental_states.items():
        assert json.loads(
            (target.parent / name).read_text(encoding="utf-8")
        ) == payload
    assert json.loads(
        (target.parent / "quotes" / "market-session.json").read_text(encoding="utf-8")
    )["is_open"] is True
    source_connection.close()
    target_connection.close()


def test_preflight_failure_always_stops_the_staged_api_process(tmp_path) -> None:
    database = tmp_path / "runtime" / ".local" / "forward" / "forward-evidence.sqlite3"
    database.parent.mkdir(parents=True)
    database.write_bytes(b"")
    result = _run_control_center_contract(
        tmp_path,
        "$script:stops = 0; function git { $global:LASTEXITCODE = 0 }; "
        "function Get-Command { return [pscustomobject]@{ Source = 'missing-python.exe' } }; "
        "function New-CandidatePreflightDatabase {}; "
        "function Start-Process { $process = [pscustomobject]@{ HasExited = $false; Id = 424242 }; "
        "$process | Add-Member ScriptMethod WaitForExit { param($milliseconds) return $true }; "
        "return $process }; function Invoke-WebRequest { return [pscustomobject]@{ StatusCode = 200 } }; "
        "function Stop-Process { $script:stops += 1 }; "
        "$accepted = Invoke-ProductionShapePreflight -Revision ('b' * 40); "
        "$state = Get-RuntimeUpdateState; "
        'Write-Output "$accepted,$script:stops,$($state.update_status)"',
    )

    assert result == "False,1,PREFLIGHT_FAILED"


def test_switch_preparation_reports_when_previous_bundle_cannot_be_restored(
    tmp_path,
) -> None:
    previous = "a" * 40
    candidate = "b" * 40
    result = _run_control_center_contract(
        tmp_path,
        "$script:checkouts = @(); "
        f"function Get-CodeRevision {{ return '{previous}' }}; "
        "function Test-MainCandidate { return $true }; "
        "function Invoke-ProductionShapePreflight { return $true }; "
        "function git { if ($args -contains 'checkout') { "
        "$script:checkouts += [string]$args[-1] }; $global:LASTEXITCODE = 0 }; "
        "function Copy-Item { throw 'copy failed' }; "
        f"$accepted = Update-RuntimeCheckout -Revision '{candidate}'; "
        "$state = Get-RuntimeUpdateState; "
        'Write-Output "$accepted,$($script:checkouts -join \'|\'),$($state.update_status)"',
    )

    assert result == f"False,{candidate}|{previous},ROLLBACK_FAILED"


def test_candidate_switch_installs_one_complete_runtime_control_bundle(tmp_path) -> None:
    _write_control_bundle(tmp_path / "runtime", "previous", scripts_dir=True)
    _write_control_bundle(
        tmp_path / "repository" / ".local" / "runtime-control", "previous"
    )
    candidate = "b" * 40
    result = _run_control_center_contract(
        tmp_path,
        "function Get-CodeRevision { return ('a' * 40) }; "
        "function Test-MainCandidate { return $true }; "
        "function Invoke-ProductionShapePreflight { return $true }; "
        "function git { if ($args -contains 'checkout') { foreach ($name in "
        "$runtimeControlFileNames) { Set-Content -LiteralPath "
        "(Join-Path $moduleRoot ('scripts\\' + $name)) "
        f"-Value ('{candidate}|' + $name) }} }}; $global:LASTEXITCODE = 0 }}; "
        f"$accepted = Update-RuntimeCheckout -Revision '{candidate}'; "
        "$state = Get-RuntimeUpdateState; "
        + _bundle_result_expression(
            "(Join-Path $repositoryRoot '.local\\runtime-control')"
        )
        + "; Write-Output \"$accepted,$($state.update_status)\"",
    ).splitlines()

    assert result == [
        ",".join(f"{candidate}|{name}" for name in RUNTIME_CONTROL_FILES),
        "True,STAGED",
    ]


def test_switch_copy_failure_restores_the_complete_previous_control_bundle(
    tmp_path,
) -> None:
    _write_control_bundle(tmp_path / "runtime", "previous", scripts_dir=True)
    _write_control_bundle(
        tmp_path / "repository" / ".local" / "runtime-control", "previous"
    )
    previous = "a" * 40
    candidate = "b" * 40
    result = _run_control_center_contract(
        tmp_path,
        "$script:failedCandidateCopy = $false; "
        f"function Get-CodeRevision {{ return '{previous}' }}; "
        "function Test-MainCandidate { return $true }; "
        "function Invoke-ProductionShapePreflight { return $true }; "
        "function git { if ($args -contains 'checkout') { $revision = [string]$args[-1]; "
        "foreach ($name in $runtimeControlFileNames) { Set-Content -LiteralPath "
        "(Join-Path $moduleRoot ('scripts\\' + $name)) "
        "-Value ($revision + '|' + $name) } }; $global:LASTEXITCODE = 0 }; "
        "function Copy-Item { param([string]$LiteralPath,[string]$Destination,[switch]$Force); "
        "$value = (Get-Content -LiteralPath $LiteralPath -Raw); "
        f"if (-not $script:failedCandidateCopy -and $value -like '{candidate}*' -and "
        "$LiteralPath -like '*xauusd_watchdog_guard.ps1') { "
        "$script:failedCandidateCopy = $true; throw 'candidate copy failed' }; "
        "Microsoft.PowerShell.Management\\Copy-Item -LiteralPath $LiteralPath "
        "-Destination $Destination -Force:$Force }; "
        f"$accepted = Update-RuntimeCheckout -Revision '{candidate}'; "
        "$state = Get-RuntimeUpdateState; "
        + _bundle_result_expression(
            "(Join-Path $repositoryRoot '.local\\runtime-control')"
        )
        + "; Write-Output \"$accepted,$($state.update_status)\"",
    ).splitlines()

    assert result == [
        ",".join(f"{previous}|{name}" for name in RUNTIME_CONTROL_FILES),
        "False,SWITCH_FAILED",
    ]


def test_half_installed_candidate_bundle_is_reverted_before_switch_rollback(
    tmp_path,
) -> None:
    _write_control_bundle(tmp_path / "runtime", "previous", scripts_dir=True)
    _write_control_bundle(
        tmp_path / "repository" / ".local" / "runtime-control", "previous"
    )
    previous = "a" * 40
    candidate = "b" * 40
    result = _run_control_center_contract(
        tmp_path,
        "$script:failedCandidateMove = $false; "
        f"function Get-CodeRevision {{ return '{previous}' }}; "
        "function Test-MainCandidate { return $true }; "
        "function Invoke-ProductionShapePreflight { return $true }; "
        "function git { if ($args -contains 'checkout') { $revision = [string]$args[-1]; "
        "foreach ($name in $runtimeControlFileNames) { Set-Content -LiteralPath "
        "(Join-Path $moduleRoot ('scripts\\' + $name)) "
        "-Value ($revision + '|' + $name) } }; $global:LASTEXITCODE = 0 }; "
        "function Move-Item { param([string]$LiteralPath,[string]$Destination,[switch]$Force); "
        f"$value = (Get-Content -LiteralPath $LiteralPath -Raw); if (-not "
        f"$script:failedCandidateMove -and $value -like '{candidate}*' -and "
        "$LiteralPath -like '*xauusd_watchdog_guard.ps1') { "
        "$script:failedCandidateMove = $true; throw 'candidate move failed' }; "
        "Microsoft.PowerShell.Management\\Move-Item -LiteralPath $LiteralPath "
        "-Destination $Destination -Force:$Force }; "
        f"$accepted = Update-RuntimeCheckout -Revision '{candidate}'; "
        "$state = Get-RuntimeUpdateState; "
        + _bundle_result_expression(
            "(Join-Path $repositoryRoot '.local\\runtime-control')"
        )
        + "; Write-Output \"$accepted,$($state.update_status)\"",
    ).splitlines()

    assert result == [
        ",".join(f"{previous}|{name}" for name in RUNTIME_CONTROL_FILES),
        "False,SWITCH_FAILED",
    ]


def test_observation_rollback_restores_the_complete_previous_control_bundle(
    tmp_path,
) -> None:
    previous = "a" * 40
    candidate = "b" * 40
    _write_control_bundle(tmp_path / "runtime", previous, scripts_dir=True)
    _write_control_bundle(
        tmp_path / "repository" / ".local" / "runtime-control", candidate
    )
    result = _run_control_center_contract(
        tmp_path,
        "function git { $global:LASTEXITCODE = 0 }; "
        "function Restart-CodeReloadableServices {}; "
        "function Write-RuntimeCodeState {}; function Write-RuntimeUpdateFailure {}; "
        "function Write-WatchdogEvent {}; "
        f"$restored = Invoke-RuntimeRollback -FailedRevision '{candidate}' "
        f"-PreviousRevision '{previous}' -Reason 'contract test'; "
        + _bundle_result_expression(
            "(Join-Path $repositoryRoot '.local\\runtime-control')"
        )
        + "; Write-Output $restored",
    ).splitlines()

    assert result == [
        ",".join(f"{previous}|{name}" for name in RUNTIME_CONTROL_FILES),
        "True",
    ]


def test_candidate_observation_is_durable_before_revision_is_marked_applied(
    tmp_path,
) -> None:
    result = _run_control_center_contract(
        tmp_path,
        "$script:order = @(); "
        "function Restart-CodeReloadableServices { $script:order += 'reload'; "
        "return [DateTimeOffset]::Parse('2026-08-12T08:00:00+00:00') }; "
        "function Start-RuntimeObservation { $script:order += 'observe' }; "
        "function Write-RuntimeCodeState { $script:order += 'applied' }; "
        "function Write-WatchdogEvent {}; "
        "Invoke-RuntimeCandidateActivation -Revision ('b' * 40) "
        "-PreviousRevision ('a' * 40); "
        'Write-Output ($script:order -join ",")',
    )

    assert result == "reload,observe,applied"


def test_observation_reuses_the_reload_health_boundary(tmp_path) -> None:
    result = _run_control_center_contract(
        tmp_path,
        "$script:captured = $null; "
        "function Get-LatestRuntimeDecisionTime { return $null }; "
        "function Write-WatchdogEvent {}; "
        "function Write-RuntimeUpdateState { param([hashtable]$Values); "
        "$script:captured = $Values }; "
        "$boundary = [DateTimeOffset]::Parse('2026-08-12T08:00:00+00:00'); "
        "Start-RuntimeObservation -Revision ('b' * 40) -PreviousRevision ('a' * 40) "
        "-HealthBoundary $boundary; "
        "Write-Output $script:captured.observation_health_boundary_at",
    )

    assert result == "2026-08-12T08:00:00.0000000+00:00"


def test_runtime_checkout_hands_off_before_old_supervisor_checks_health(
    tmp_path,
) -> None:
    result = _run_control_center_contract(
        tmp_path,
        "$script:order = @(); "
        "function Update-RuntimeCheckout { $script:order += 'checkout'; return $true }; "
        "function Write-WatchdogEvent { $script:order += 'event' }; "
        "function Start-WatchdogReplacement { $script:order += 'replacement' }; "
        "$handedOff = Invoke-RuntimeCheckoutHandoff -Revision ('b' * 40); "
        "Write-Output \"$handedOff,$($script:order -join ',')\"",
    )

    assert result == "True,checkout,event,replacement"


def test_two_new_decision_cycles_activate_even_when_observed_together(tmp_path) -> None:
    _write_runtime_observation(tmp_path)
    result = _run_control_center_contract(
        tmp_path,
        "function Test-CodeReloadHealth { return $true }; "
        "function Test-CurrentProductionShape { return $null }; "
        "function Get-RuntimeDecisionTimes { return @("
        "'2026-08-13T03:10:00+00:00','2026-08-13T03:05:00+00:00') }; "
        "$observed = Test-RuntimeObservation; "
        "$state = Get-RuntimeUpdateState; "
        'Write-Output "$observed,$($state.update_status),$($state.observation_success_cycles)"',
    )

    assert result == "True,ACTIVE,2"


def test_observation_counts_only_strictly_new_five_minute_cycles(tmp_path) -> None:
    _write_runtime_observation(tmp_path)
    result = _run_control_center_contract(
        tmp_path,
        "function Test-CodeReloadHealth { return $true }; "
        "function Test-CurrentProductionShape { return $null }; "
        "$script:times = @('invalid','2026-08-13T02:55:00+00:00',"
        "'2026-08-13T03:01:00+00:00','2026-08-13T03:05:00+00:00'); "
        "$script:index = 0; function Get-RuntimeDecisionTimes { "
        "$value = $script:times[$script:index]; $script:index += 1; return $value }; "
        "$null = Test-RuntimeObservation; $null = Test-RuntimeObservation; "
        "$null = Test-RuntimeObservation; $null = Test-RuntimeObservation; "
        "$state = Get-RuntimeUpdateState; "
        'Write-Output "$($state.update_status),$($state.observation_success_cycles),$($state.observation_last_decision_time)"',
    )

    assert result == "OBSERVING,1,2026-08-13T03:05:00+00:00"


def test_three_consecutive_observation_failures_trigger_one_rollback(tmp_path) -> None:
    _write_runtime_observation(tmp_path)
    result = _run_control_center_contract(
        tmp_path,
        "$script:rollbacks = 0; function Test-CodeReloadHealth { return $false }; "
        "function Invoke-RuntimeRollback { $script:rollbacks += 1; "
        "Write-RuntimeUpdateState @{ update_status = 'ROLLED_BACK' }; return $true }; "
        "$first = Test-RuntimeObservation; $second = Test-RuntimeObservation; "
        "$third = Test-RuntimeObservation; $state = Get-RuntimeUpdateState; "
        'Write-Output "$first,$second,$third,$script:rollbacks,$($state.update_status)"',
    )

    assert result == "True,True,False,1,ROLLED_BACK"


def test_snapshot_refresh_defers_observation_without_consuming_failure_budget(
    tmp_path,
) -> None:
    _write_runtime_observation(tmp_path)
    result = _run_control_center_contract(
        tmp_path,
        "$script:rollbacks = 0; function Test-CodeReloadHealth { return $true }; "
        "function Test-CurrentProductionShape { "
        "return 'DEFERRED:STATUS_SNAPSHOT_REFRESH_IN_PROGRESS' }; "
        "function Invoke-RuntimeRollback { $script:rollbacks += 1; return $true }; "
        "$observed = Test-RuntimeObservation; $state = Get-RuntimeUpdateState; "
        'Write-Output "$observed,$($state.update_status),'
        '$($state.observation_consecutive_failures),'
        '$($state.observation_deferred_code),$script:rollbacks"',
    )

    assert result == (
        "True,OBSERVING,0,STATUS_SNAPSHOT_REFRESH_IN_PROGRESS,0"
    )


def test_observation_window_waits_for_the_worker_family_to_finish_starting(
    tmp_path,
) -> None:
    _write_runtime_observation(tmp_path, observation_ready_at=None)
    result = _run_control_center_contract(
        tmp_path,
        "$script:rollbacks = 0; function Test-CodeReloadHealth { "
        "param($ReloadStarted, $AllowedWorkerStates); "
        "return $null -eq $AllowedWorkerStates -or $AllowedWorkerStates.Count -gt 1 }; "
        "function Invoke-RuntimeRollback { $script:rollbacks += 1; return $true }; "
        "$observed = Test-RuntimeObservation; $state = Get-RuntimeUpdateState; "
        'Write-Output "$observed,$($state.update_status),$($null -eq $state.observation_ready_at),$script:rollbacks"',
    )

    assert result == "True,OBSERVING,True,0"


def test_market_closure_pauses_observation_timeout_until_reopen(tmp_path) -> None:
    _write_runtime_observation(
        tmp_path,
        observation_started_at="2020-01-01T00:00:00+00:00",
        observation_ready_at="2020-01-01T00:00:00+00:00",
    )
    result = _run_control_center_contract(
        tmp_path,
        "$script:rollbacks = 0; function Test-CodeReloadHealth { return $true }; "
        "function Test-CurrentProductionShape { return $null }; "
        "function Get-RuntimeDecisionTimes { return @('2026-08-13T03:00:00+00:00') }; "
        "function Invoke-RuntimeRollback { $script:rollbacks += 1; return $true }; "
        "function Invoke-RestMethod { return [pscustomobject]@{ system = "
        "[pscustomobject]@{ market_session = 'CLOSED' } } }; "
        "$closed = Test-RuntimeObservation; $paused = Get-RuntimeUpdateState; "
        "function Invoke-RestMethod { return [pscustomobject]@{ system = "
        "[pscustomobject]@{ market_session = 'OPEN' } } }; "
        "$reopened = Test-RuntimeObservation; "
        "$wasPaused = [DateTimeOffset]::Parse([string]$paused.observation_ready_at) "
        "-gt [DateTimeOffset]::Parse('2020-01-02T00:00:00+00:00'); "
        'Write-Output "$closed,$reopened,$wasPaused,$script:rollbacks"',
    )

    assert result == "True,True,True,0"


def test_observation_timeout_matches_the_thirty_minute_decision_window(
    tmp_path,
) -> None:
    old = "2020-01-01T00:00:00+00:00"
    quotes = tmp_path / "runtime" / ".local" / "forward" / "quotes"
    quotes.mkdir(parents=True)
    results = []
    for minutes_to_close in (10, 45):
        _write_runtime_observation(
            tmp_path, observation_started_at=old, observation_ready_at=old,
        )
        now = datetime.now(timezone.utc)
        (quotes / "market-session.json").write_text(json.dumps({
            "observed_at": now.isoformat(),
            "is_open": True,
            "next_close_time": (
                now + timedelta(minutes=minutes_to_close)
            ).isoformat(),
        }), encoding="utf-8")
        results.append(_run_control_center_contract(
            tmp_path,
            "$script:rollbacks = 0; function Test-CodeReloadHealth { return $true }; "
            "function Test-CurrentProductionShape { return $null }; "
            "function Get-RuntimeDecisionTimes { return @() }; "
            "function Invoke-RuntimeRollback { $script:rollbacks += 1; return $true }; "
            "$observed = Test-RuntimeObservation; $state = Get-RuntimeUpdateState; "
            "$paused = [DateTimeOffset]::Parse([string]$state.observation_ready_at) "
            "-gt [DateTimeOffset]::Parse('2020-01-02T00:00:00+00:00'); "
            'Write-Output "$observed,$paused,$script:rollbacks"',
        ))

    assert results == ["True,True,0", "False,False,1"]


def test_watchdog_autostart_uses_one_windowless_registration_path(tmp_path) -> None:
    control_center = (
        ROOT / "scripts" / "xauusd_control_center.ps1"
    ).read_text(encoding="utf-8")
    launcher = ROOT / "scripts" / "xauusd_watchdog_launcher.vbs"
    launcher_text = launcher.read_text(encoding="utf-8")
    guard_launcher = ROOT / "scripts" / "xauusd_watchdog_guard_launcher.vbs"
    guard_launcher_text = guard_launcher.read_text(encoding="utf-8")

    assert "function Register-AutoStartTask" in control_center
    assert control_center.count("Register-ScheduledTask -TaskName $taskName") == 1
    assert control_center.count("Register-ScheduledTask -TaskName $guardTaskName") == 1
    assert 'New-TimeSpan -Minutes 2' in control_center
    assert "Ensure-WatchdogGuardTask" in control_center
    assert '"System32\\wscript.exe"' in control_center
    assert "shell.Run(command, 0, True)" in launcher_text
    assert "shell.Run(command, 0, True)" in guard_launcher_text
    assert "-WindowStyle Hidden" in guard_launcher_text
    assert "New-ScheduledTaskAction -Execute $wscript" in control_center
    assert "Loop While exitCode = 75" in launcher_text
    assert "-WindowStyle Hidden -ExecutionPolicy Bypass -File" not in control_center

    marker = tmp_path / "watchdog-marker.txt"
    probe = tmp_path / "watchdog-probe.ps1"
    probe.write_text(
        textwrap.dedent(
            f'''\
            param(
                [string]$Action,
                [string]$RuntimeRoot,
                [string]$RepositoryRoot
            )
            "$Action|$RuntimeRoot|$RepositoryRoot" | Set-Content -LiteralPath '{marker}'
            '''
        ),
        encoding="utf-8",
    )
    subprocess.run(
        [
            "cscript.exe", "//NoLogo", str(launcher), str(probe),
            str(tmp_path / "runtime"), str(tmp_path / "repository"),
        ],
        check=True,
    )

    assert marker.read_text(encoding="utf-8-sig").strip() == (
        f"Watchdog|{tmp_path / 'runtime'}|{tmp_path / 'repository'}"
    )


def test_broker_closed_heartbeat_is_healthy_without_fresh_ticks(tmp_path) -> None:
    repo = tmp_path / "repo"
    quotes = repo / ".local" / "forward" / "quotes"
    quotes.mkdir(parents=True)
    now = datetime.now(timezone.utc)
    (quotes / "market-session.json").write_text(json.dumps({
        "observed_at": now.isoformat(),
        "is_open": False,
        "time_till_open_seconds": 3600,
    }), encoding="utf-8")
    script = ROOT / "scripts" / "xauusd_control_center.ps1"
    command = (
        f"$null = . '{script}' -Action CodeRevision -RuntimeRoot '{repo}' "
        f"-RepositoryRoot '{repo}'; "
        "$service = [pscustomobject]@{ Key = 'quote' }; "
        "$processes = @([pscustomobject]@{ ProcessId = 1 }); "
        "Get-ServiceState -Service $service -Processes $processes"
    )
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command],
        capture_output=True, text=True, check=True,
    ).stdout.strip()

    assert result == "MARKET CLOSED"


def test_fresh_quotes_without_broker_session_trigger_bridge_recovery(tmp_path) -> None:
    repo = tmp_path / "repo"
    quotes = repo / ".local" / "forward" / "quotes"
    quotes.mkdir(parents=True)
    (quotes / "xauusd-quotes.jsonl").write_text("{}\n", encoding="utf-8")
    script = ROOT / "scripts" / "xauusd_control_center.ps1"
    command = (
        f"$null = . '{script}' -Action CodeRevision -RuntimeRoot '{repo}' "
        f"-RepositoryRoot '{repo}'; "
        "$service = [pscustomobject]@{ Key = 'quote' }; "
        "$processes = @([pscustomobject]@{ ProcessId = 1 }); "
        "Get-ServiceState -Service $service -Processes $processes"
    )
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command],
        capture_output=True, text=True, check=True,
    ).stdout.strip()

    assert result == "SESSION STALE"


def test_watchdog_guard_restarts_only_after_heartbeat_is_stale(tmp_path) -> None:
    heartbeat = tmp_path / "control-watchdog-heartbeat.json"
    heartbeat.write_text(json.dumps({
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "process_id": 0,
    }), encoding="utf-8")
    guard = ROOT / "scripts" / "xauusd_watchdog_guard.ps1"
    command = (
        f"$null = . '{guard}' -TaskName 'test-watchdog' "
        f"-HeartbeatPath '{heartbeat}' -MaxAgeSeconds 120; "
        "$script:starts = 0; "
        "function Stop-ScheduledTask {}; "
        "function Start-ScheduledTask { $script:starts += 1 }; "
        "$fresh = Invoke-WatchdogGuard; "
        f"@{{ observed_at = '2020-01-01T00:00:00+00:00'; process_id = 0 }} "
        f"| ConvertTo-Json | Set-Content -LiteralPath '{heartbeat}'; "
        "$stale = Invoke-WatchdogGuard; "
        "Write-Output \"$fresh,$stale,$script:starts\""
    )
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command],
        capture_output=True, text=True, check=True,
    ).stdout.strip()

    assert result == "False,True,1"


def test_main_checkpoint_accepts_squash_merge_without_accepting_stale_main(
    tmp_path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    for command in (
        ["git", "init", "-q"],
        ["git", "config", "user.email", "test@example.com"],
        ["git", "config", "user.name", "Test"],
    ):
        subprocess.run(command, cwd=repo, check=True)
    (repo / "value.txt").write_text("base", encoding="utf-8")
    subprocess.run(["git", "add", "value.txt"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "base"], cwd=repo, check=True)
    base = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True,
        text=True, check=True,
    ).stdout.strip()
    (repo / "value.txt").write_text("feature", encoding="utf-8")
    subprocess.run(["git", "commit", "-qam", "feature"], cwd=repo, check=True)
    feature = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True,
        text=True, check=True,
    ).stdout.strip()
    tree = subprocess.run(
        ["git", "rev-parse", "HEAD^{tree}"], cwd=repo, capture_output=True,
        text=True, check=True,
    ).stdout.strip()
    squash = subprocess.run(
        ["git", "commit-tree", tree, "-p", base, "-m", "squash"], cwd=repo,
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    state = repo / ".local" / "forward" / "runtime-update-state.json"
    state.parent.mkdir(parents=True)
    state.write_text(
        json.dumps({"accepted_main_revision": base}), encoding="utf-8",
    )
    script = ROOT / "scripts" / "xauusd_control_center.ps1"
    command = (
        f"$null = . '{script}' -Action CodeRevision -RuntimeRoot '{repo}' "
        f"-RepositoryRoot '{repo}'; "
        f"$advance = Test-MainCandidate -CurrentRevision '{feature}' "
        f"-CandidateRevision '{squash}'; "
        f"$stale = Test-MainCandidate -CurrentRevision '{feature}' "
        f"-CandidateRevision '{base}'; Write-Output \"$advance,$stale\""
    )
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command],
        capture_output=True, text=True, check=True,
    ).stdout.strip()

    assert result == "True,False"


def test_runtime_update_requires_matching_deployed_and_verified_main(tmp_path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    current = "a" * 40
    candidate = "b" * 40
    script = ROOT / "scripts" / "xauusd_control_center.ps1"
    command = (
        f"$null = . '{script}' -Action CodeRevision -RuntimeRoot '{repo}' "
        f"-RepositoryRoot '{repo}'; "
        f"function Get-DeployedMainRevision {{ return '{candidate}' }}; "
        f"function Get-VerifiedOriginMain {{ return '{candidate}' }}; "
        "function Test-MainCandidate { return $true }; "
        f"Get-DesiredMainRevision -CurrentRevision '{current}'"
    )
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command],
        capture_output=True, text=True, check=True,
    ).stdout.strip()

    assert result == candidate


def test_failed_candidate_is_only_blocked_by_the_same_preflight_contract(
    tmp_path,
) -> None:
    candidate = "b" * 40
    current = "a" * 40
    same_contract = _run_control_center_contract(
        tmp_path,
        "function Test-RevisionDescendsFrom { return $true }; "
        f"Write-RuntimeUpdateState @{{ accepted_main_revision = '{current}'; "
        f"failed_revision = '{candidate}'; failed_preflight_contract = "
        "$runtimePreflightContractVersion }; "
        f"Write-Output (Test-MainCandidate '{current}' '{candidate}')",
    )
    upgraded_contract = _run_control_center_contract(
        tmp_path,
        "function Test-RevisionDescendsFrom { return $true }; "
        f"Write-RuntimeUpdateState @{{ accepted_main_revision = '{current}'; "
        f"failed_revision = '{candidate}'; failed_preflight_contract = "
        "'legacy-direct-database-v1' }; "
        f"Write-Output (Test-MainCandidate '{current}' '{candidate}')",
    )

    assert same_contract == "False"
    assert upgraded_contract == "True"


def test_runtime_update_rejects_deployment_git_mismatch(tmp_path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    current = "a" * 40
    deployed = "b" * 40
    verified = "c" * 40
    script = ROOT / "scripts" / "xauusd_control_center.ps1"
    command = (
        f"$null = . '{script}' -Action CodeRevision -RuntimeRoot '{repo}' "
        f"-RepositoryRoot '{repo}'; "
        f"function Get-DeployedMainRevision {{ return '{deployed}' }}; "
        f"function Get-VerifiedOriginMain {{ return '{verified}' }}; "
        "function Test-MainCandidate { return $true }; "
        f"$result = Get-DesiredMainRevision -CurrentRevision '{current}'; "
        "if ($null -eq $result) { Write-Output 'REJECTED' }"
    )
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command],
        capture_output=True, text=True, check=True,
    ).stdout.strip()

    assert result == "REJECTED"


def test_code_reload_health_requires_fresh_successful_sync(tmp_path) -> None:
    repo = tmp_path / "repo"
    status = repo / ".local" / "forward" / "dashboard-sync-status.json"
    status.parent.mkdir(parents=True)
    status.write_text(json.dumps({
        "last_attempt": "2026-08-12T08:00:01+00:00",
        "status": "OK",
    }), encoding="utf-8")
    for name, service in (
        ("collector-status.json", "collector"),
        ("news-annotator-status.json", "annotator"),
        ("assistant-worker-status.json", "assistant"),
    ):
        (status.parent / name).write_text(json.dumps({
            "service": service,
            "state": "RUNNING",
            "last_success": "2026-08-12T08:00:01+00:00",
        }), encoding="utf-8")
    script = ROOT / "scripts" / "xauusd_control_center.ps1"
    command = (
        f"$null = . '{script}' -Action CodeRevision -RuntimeRoot '{repo}' "
        f"-RepositoryRoot '{repo}'; "
        "function Get-ForecasterProcesses { return [pscustomobject]@{ ProcessId = 1 } }; "
        "function Invoke-WebRequest { return [pscustomobject]@{ StatusCode = 200 } }; "
        "$started = [DateTimeOffset]::Parse('2026-08-12T08:00:00+00:00'); "
        "$healthy = Test-CodeReloadHealth -ReloadStarted $started; "
        f"@{{ last_attempt = '2026-08-12T08:00:01+00:00'; status = 'ERROR' }} "
        f"| ConvertTo-Json | Set-Content -LiteralPath '{status}'; "
        "$failed = Test-CodeReloadHealth -ReloadStarted $started; "
        "Write-Output \"$healthy,$failed\""
    )
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command],
        capture_output=True, text=True, check=True,
    ).stdout.strip()

    assert result == "True,False"


def test_code_reload_accepts_fresh_service_startup_but_rejects_failed_state(
    tmp_path,
) -> None:
    repo = tmp_path / "repo"
    status = repo / ".local" / "forward" / "dashboard-sync-status.json"
    status.parent.mkdir(parents=True)
    status.write_text(json.dumps({
        "last_attempt": "2026-08-12T08:00:01+00:00",
        "status": "OK",
    }), encoding="utf-8")
    (status.parent / "collector-status.json").write_text(json.dumps({
        "service": "collector", "state": "STARTING",
        "last_success": "2026-08-12T08:00:01+00:00",
    }), encoding="utf-8")
    annotator = status.parent / "news-annotator-status.json"
    annotator.write_text(json.dumps({
        "service": "annotator", "state": "STARTING",
        "last_success": "2026-08-12T08:00:01+00:00",
    }), encoding="utf-8")
    (status.parent / "assistant-worker-status.json").write_text(json.dumps({
        "service": "assistant", "state": "STARTING",
        "last_success": "2026-08-12T08:00:01+00:00",
    }), encoding="utf-8")
    script = ROOT / "scripts" / "xauusd_control_center.ps1"
    command = (
        f"$null = . '{script}' -Action CodeRevision -RuntimeRoot '{repo}' "
        f"-RepositoryRoot '{repo}'; "
        "function Get-ForecasterProcesses { return [pscustomobject]@{ ProcessId = 1 } }; "
        "function Invoke-WebRequest { return [pscustomobject]@{ StatusCode = 200 } }; "
        "$started = [DateTimeOffset]::Parse('2026-08-12T08:00:00+00:00'); "
        "$servicesStarting = Test-CodeReloadHealth -ReloadStarted $started; "
        f"@{{ service = 'annotator'; state = 'ERROR'; "
        f"last_success = '2026-08-12T08:00:01+00:00' }} "
        f"| ConvertTo-Json | Set-Content -LiteralPath '{annotator}'; "
        "$failed = Test-CodeReloadHealth -ReloadStarted $started; "
        "Write-Output \"$servicesStarting,$failed\""
    )
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command],
        capture_output=True, text=True, check=True,
    ).stdout.strip()

    assert result == "True,False"


def test_service_state_rejects_stale_worker_heartbeat(tmp_path) -> None:
    repo = tmp_path / "repo"
    status = repo / ".local" / "forward" / "collector-status.json"
    status.parent.mkdir(parents=True)
    now = datetime.now(timezone.utc)
    status.write_text(json.dumps({
        "service": "collector",
        "state": "RUNNING",
        "last_success": now.isoformat(),
    }), encoding="utf-8")
    old_start = (now - timedelta(minutes=10)).isoformat()
    stale = (now - timedelta(minutes=6)).isoformat()
    script = ROOT / "scripts" / "xauusd_control_center.ps1"
    command = (
        f"$null = . '{script}' -Action CodeRevision -RuntimeRoot '{repo}' "
        f"-RepositoryRoot '{repo}'; "
        f"function Get-ServiceProcessStartedAt {{ return [DateTimeOffset]::Parse('{old_start}') }}; "
        "$service = [pscustomobject]@{ Key = 'collector' }; "
        "$processes = @([pscustomobject]@{ ProcessId = 1 }); "
        "$fresh = Get-ServiceState -Service $service -Processes $processes; "
        f"@{{ service = 'collector'; state = 'RUNNING'; last_success = '{stale}' }} "
        f"| ConvertTo-Json | Set-Content -LiteralPath '{status}'; "
        "$old = Get-ServiceState -Service $service -Processes $processes; "
        "Write-Output \"$fresh,$old\""
    )
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command],
        capture_output=True, text=True, check=True,
    ).stdout.strip()

    assert result == "RUNNING,COLLECTOR STALE"


def test_worker_family_keeps_current_startup_alive_but_bounds_stalled_startup(
    tmp_path,
) -> None:
    repo = tmp_path / "repo"
    status_root = repo / ".local" / "forward"
    status_root.mkdir(parents=True)
    now = datetime.now(timezone.utc)
    current_start = (now - timedelta(minutes=8)).isoformat()
    stalled_start = (now - timedelta(minutes=16)).isoformat()
    script = ROOT / "scripts" / "xauusd_control_center.ps1"
    results = []

    for service, filename in (
        ("collector", "collector-status.json"),
        ("annotator", "news-annotator-status.json"),
    ):
        status = status_root / filename
        status.write_text(json.dumps({
            "service": service,
            "state": "STARTING",
            "last_success": (now - timedelta(minutes=8)).isoformat(),
        }), encoding="utf-8")
        command = (
            f"$null = . '{script}' -Action CodeRevision -RuntimeRoot '{repo}' "
            f"-RepositoryRoot '{repo}'; "
            f"function Get-ServiceProcessStartedAt {{ return [DateTimeOffset]::Parse('{current_start}') }}; "
            f"$service = [pscustomobject]@{{ Key = '{service}' }}; "
            "$processes = @([pscustomobject]@{ ProcessId = 1 }); "
            "$current = Get-ServiceState -Service $service -Processes $processes; "
            f"function Get-ServiceProcessStartedAt {{ return [DateTimeOffset]::Parse('{stalled_start}') }}; "
            "$stalled = Get-ServiceState -Service $service -Processes $processes; "
            "Write-Output \"$current,$stalled\""
        )
        results.append(subprocess.run(
            ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command],
            capture_output=True, text=True, check=True,
        ).stdout.strip())

    assert results == [
        "STARTING,COLLECTOR STALE",
        "STARTING,ANNOTATOR STALE",
    ]
