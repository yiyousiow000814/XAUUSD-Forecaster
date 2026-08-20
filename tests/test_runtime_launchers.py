from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import socket
import sqlite3
import subprocess
import sys
import textwrap
import threading
import time
from datetime import datetime, timedelta, timezone

import pytest


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


def test_control_center_stages_releases_without_main_driven_activation() -> None:
    path = ROOT / "scripts" / "xauusd_control_center.ps1"
    control_center = path.read_text(encoding="utf-8")

    assert (
        '$reloadableServiceKeys = @('
        '"collector", "annotator", "api", "sync")'
    ) in control_center
    assert 'Match = "run_assistant_worker.py"' not in control_center
    assert 'CODE_REVISION_RELOAD_APPLIED' in control_center
    assert 'Write-RuntimeCodeState -Revision $Revision' in control_center
    assert 'Test-CodeReloadHealth -ReloadStarted $reloadStarted' in control_center
    assert 'Start-WatchdogReplacement' in control_center
    assert 'Invoke-RuntimeCandidateActivation' in control_center
    assert "$codeReloadTimeout = [TimeSpan]::FromMinutes(5)" in control_center
    assert "Add($codeReloadTimeout)" in control_center
    preflight = control_center.split(
        "function Invoke-ProductionShapePreflight", 1,
    )[1].split("function Update-RuntimeCheckout", 1)[0]
    assert '"--allow-pending-generation-decision"' in preflight
    assert "Get-DesiredMainRevision" not in control_center
    assert "Invoke-RuntimeCheckoutHandoff" not in control_center
    assert "Start-CandidateDiscovery" in control_center
    assert "Start-ReleasePromotion" in control_center
    assert "Invoke-ReverseStable" in control_center
    assert "Install-ProductionRuntime" in control_center
    assert 'RuntimeRoot must be separate from the development checkout' in control_center
    assert 'worktree add --detach --quiet' in control_center
    assert '-WindowStyle Hidden -PassThru' in control_center
    assert 'isolated-critical-status-diagnostics-v4' in control_center
    assert '$statusUrl = "http://127.0.0.1:$preflightPort/api/critical-status"' in preflight
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


def test_local_assistant_worker_is_not_installed_or_supervised() -> None:
    control_center = (
        ROOT / "scripts" / "xauusd_control_center.ps1"
    ).read_text(encoding="utf-8")

    assert not (ROOT / "scripts" / "run_assistant_worker.py").exists()
    assert not (ROOT / "xauusd_forecaster" / "assistant_local_runtime.py").exists()
    assert 'Key = "assistant"' not in control_center
    assert "assistant" not in control_center.lower()


def _run_control_center_contract(tmp_path, body: str) -> str:
    runtime = tmp_path / "runtime"
    repository = tmp_path / "repository"
    runtime.mkdir(exist_ok=True)
    repository.mkdir(exist_ok=True)
    manifest = repository / "web" / "worker-validation-manifest.json"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(
        (ROOT / "web" / "worker-validation-manifest.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    script = ROOT / "scripts" / "xauusd_control_center.ps1"
    command = (
        f"$null = . '{script}' -Action CodeRevision -RuntimeRoot '{runtime}' "
        f"-RepositoryRoot '{repository}'; {body}"
    )
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command],
        capture_output=True, text=True, check=False,
    )
    if result.returncode:
        raise AssertionError(
            "PowerShell control contract failed\n"
            f"stdout:\n{result.stdout}\n"
            f"stderr:\n{result.stderr}"
        )
    return result.stdout.strip()


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


def _authorized_candidate(previous: str, candidate: str) -> str:
    stable_worker = "11111111-1111-4111-8111-111111111111"
    candidate_worker = "22222222-2222-4222-8222-222222222222"
    key = f"{candidate_worker}:{candidate}"
    return (
        f"$stable = New-ReleaseIdentity -GitSha '{previous}' "
        f"-WorkerVersionId '{stable_worker}' -WindowsRevision '{previous}' "
        "-ValidationState 'PASSED' -ArtifactKind 'PRODUCTION_CANDIDATE'; "
        "$stable.compatibility_state = 'PASSED'; "
        f"$candidateRelease = New-ReleaseIdentity -GitSha '{candidate}' "
        f"-WorkerVersionId '{candidate_worker}' -WindowsRevision '{candidate}' "
        "-ValidationState 'PASSED' -ArtifactKind 'PRODUCTION_CANDIDATE'; "
        "$candidateRelease.compatibility_state = 'PASSED'; "
        f"$candidateRelease.validation = [pscustomobject]@{{ key = '{key}' }}; "
        "$releaseState = New-ReleaseControlState -Stable $stable "
        "-Candidate $candidateRelease; Write-ReleaseControlState $releaseState; "
    )


def test_failed_preflight_never_switches_the_runtime_checkout(tmp_path) -> None:
    previous = "a" * 40
    candidate = "b" * 40
    result = _run_control_center_contract(
        tmp_path,
        _authorized_candidate(previous, candidate)
        + "$script:preflights = 0; $script:checkouts = 0; "
        f"function Get-CodeRevision {{ return '{previous}' }}; "
        "function Invoke-ProductionShapePreflight { $script:preflights += 1; return $false }; "
        "function git { $script:checkouts += 1; $global:LASTEXITCODE = 0 }; "
        f"$accepted = Update-RuntimeCheckout -Revision '{candidate}'; "
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
    result = json.loads(_run_control_center_contract(
        tmp_path,
        "$script:stops = 0; function git { $global:LASTEXITCODE = 0 }; "
        "function Get-Command { return [pscustomobject]@{ Source = 'missing-python.exe' } }; "
        "function Copy-CandidatePreflightDatabase {}; "
        "function Migrate-CandidatePreflightDatabase {}; "
        "function Copy-CandidatePreflightState {}; "
        "function Start-Process { $process = [pscustomobject]@{ HasExited = $false; Id = 424242 }; "
        "$process | Add-Member ScriptMethod Refresh { return $null }; "
        "$process | Add-Member ScriptMethod WaitForExit { param($milliseconds) "
        "$this.HasExited = $true; return $true }; "
        "return $process }; function Wait-CandidateCriticalStatus { return [pscustomobject]@{ "
        "ready = $false; error_code = 'CRITICAL_STATUS_HTTP_ERROR'; last_probe = "
        "[pscustomobject]@{ http_status = 500; response_body = 'failed'; "
        "transport_error = $null; elapsed_ms = 2 } } }; "
        "function Stop-Process { $script:stops += 1 }; "
        "$accepted = Invoke-ProductionShapePreflight -Revision ('b' * 40); "
        "$state = Get-RuntimeUpdateState; "
        "[pscustomobject]@{ accepted = $accepted; stops = $script:stops; "
        "status = $state.update_status; code = $state.failure_code; "
        "phase = $state.failure_phase; diagnostics = $state.preflight_diagnostics } "
        "| ConvertTo-Json -Compress -Depth 8",
    ))

    assert result["accepted"] is False
    assert result["stops"] == 1
    assert result["status"] == "PREFLIGHT_FAILED"
    assert result["code"] == "CRITICAL_STATUS_HTTP_ERROR"
    assert result["phase"] == "WAIT_CRITICAL_STATUS"
    assert result["diagnostics"]["last_http_status"] == 500
    assert result["diagnostics"]["last_http_body"] == "failed"


@pytest.mark.parametrize(
    ("copy_body", "migration_body", "expected_phase", "expected_code", "detail"),
    [
        ("throw 'copy exploded'", "", "COPY_DATABASE", "COPY_DATABASE_FAILED", "copy exploded"),
        ("", "throw 'migration exploded'", "MIGRATE_DATABASE", "MIGRATE_DATABASE_FAILED", "migration exploded"),
    ],
)
def test_preflight_failure_identifies_database_phase(
    tmp_path, copy_body, migration_body, expected_phase, expected_code, detail
) -> None:
    database = tmp_path / "runtime" / ".local" / "forward" / "forward-evidence.sqlite3"
    database.parent.mkdir(parents=True)
    database.write_bytes(b"")
    result = json.loads(_run_control_center_contract(
        tmp_path,
        "function git { $global:LASTEXITCODE = 0 }; "
        "function Get-Command { return [pscustomobject]@{ Source = 'missing-python.exe' } }; "
        f"function Copy-CandidatePreflightDatabase {{ {copy_body} }}; "
        f"function Migrate-CandidatePreflightDatabase {{ {migration_body} }}; "
        "function Copy-CandidatePreflightState {}; "
        "$accepted = Invoke-ProductionShapePreflight -Revision ('b' * 40); "
        "$state = Get-RuntimeUpdateState; "
        "[pscustomobject]@{ accepted = $accepted; phase = $state.failure_phase; "
        "code = $state.failure_code; detail = $state.preflight_diagnostics.failure_detail } "
        "| ConvertTo-Json -Compress -Depth 8",
    ))

    assert result["accepted"] is False
    assert result["phase"] == expected_phase
    assert result["code"] == expected_code
    assert detail in result["detail"]


def test_candidate_api_exit_persists_bounded_secret_safe_diagnostics(tmp_path) -> None:
    database = tmp_path / "runtime" / ".local" / "forward" / "forward-evidence.sqlite3"
    database.parent.mkdir(parents=True)
    database.write_bytes(b"")
    result = json.loads(_run_control_center_contract(
        tmp_path,
        "function git { $global:LASTEXITCODE = 0 }; "
        "function Get-Command { return [pscustomobject]@{ Source = 'missing-python.exe' } }; "
        "function Copy-CandidatePreflightDatabase {}; "
        "function Migrate-CandidatePreflightDatabase {}; "
        "function Copy-CandidatePreflightState {}; "
        "function Start-Process { param($FilePath,$ArgumentList,$WorkingDirectory,"
        "$WindowStyle,[switch]$PassThru,$RedirectStandardOutput,$RedirectStandardError); "
        "Set-Content -LiteralPath $RedirectStandardOutput -Value ('x' * 5000); "
        "Set-Content -LiteralPath $RedirectStandardError "
        "-Value 'api_key=super-secret Bearer abc.def.ghi'; "
        "$process = [pscustomobject]@{ HasExited = $true; ExitCode = 23; Id = 42 }; "
        "$process | Add-Member ScriptMethod Refresh { return $null }; "
        "$process | Add-Member ScriptMethod WaitForExit { param($milliseconds) return $true }; "
        "return $process }; "
        "$accepted = Invoke-ProductionShapePreflight -Revision ('b' * 40); "
        "$state = Get-RuntimeUpdateState; "
        "[pscustomobject]@{ accepted = $accepted; code = $state.failure_code; "
        "phase = $state.failure_phase; exited = "
        "$state.preflight_diagnostics.candidate_process_exited; exit_code = "
        "$state.preflight_diagnostics.candidate_exit_code; stdout = "
        "$state.preflight_diagnostics.stdout_tail; stderr = "
        "$state.preflight_diagnostics.stderr_tail } | ConvertTo-Json -Compress",
    ))

    assert result["accepted"] is False
    assert result["code"] == "CANDIDATE_API_EXITED"
    assert result["phase"] == "WAIT_CRITICAL_STATUS"
    assert result["exited"] is True
    assert result["exit_code"] == 23
    assert len(result["stdout"]) <= 2060
    assert "super-secret" not in result["stderr"]
    assert "abc.def.ghi" not in result["stderr"]
    assert "[REDACTED]" in result["stderr"]


class _CandidateProbeHandler(BaseHTTPRequestHandler):
    mode = "success"

    def do_GET(self) -> None:  # noqa: N802
        if self.mode == "timeout":
            time.sleep(2)
        status = 500 if self.mode == "error" else 200
        body = (
            b'{"error":"api_key=super-secret"}'
            if self.mode == "error" else b'{"status":"OK"}'
        )
        try:
            self.send_response(status)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def log_message(self, *_args) -> None:
        pass


def _run_candidate_probe(tmp_path, mode: str) -> dict:
    tmp_path.mkdir()
    handler = type("CandidateProbeHandler", (_CandidateProbeHandler,), {"mode": mode})
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        result = _run_control_center_contract(
            tmp_path,
            f"Invoke-CandidateStatusProbe -Url "
            f"'http://127.0.0.1:{server.server_port}/api/critical-status' "
            "-TimeoutSeconds 1 | ConvertTo-Json -Compress",
        )
        return json.loads(result)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_candidate_critical_status_probe_distinguishes_success_http_and_timeout(
    tmp_path,
) -> None:
    success = _run_candidate_probe(tmp_path / "success", "success")
    error = _run_candidate_probe(tmp_path / "error", "error")
    timeout = _run_candidate_probe(tmp_path / "timeout", "timeout")

    assert success["ready"] is True
    assert success["http_status"] == 200
    assert success["error_code"] is None
    assert error["ready"] is False
    assert error["http_status"] == 500
    assert error["error_code"] == "CRITICAL_STATUS_HTTP_ERROR"
    assert "super-secret" not in json.dumps(error)
    assert "[REDACTED]" in json.dumps(error)
    assert timeout["ready"] is False
    assert timeout["http_status"] is None
    assert timeout["error_code"] == "CRITICAL_STATUS_TIMEOUT"


def test_candidate_readiness_accepts_a_successful_critical_probe(tmp_path) -> None:
    result = _run_control_center_contract(
        tmp_path,
        "$process = [pscustomobject]@{ HasExited = $false }; "
        "$process | Add-Member ScriptMethod Refresh { return $null }; "
        "function Start-Sleep {}; function Invoke-CandidateStatusProbe { "
        "return [pscustomobject]@{ ready = $true; error_code = $null; "
        "http_status = 200; response_body = $null; transport_error = $null; "
        "elapsed_ms = 1 } }; $result = Wait-CandidateCriticalStatus "
        "-Process $process -Url 'http://127.0.0.1:1/api/critical-status' "
        "-Deadline ([DateTimeOffset]::UtcNow.AddSeconds(1)); "
        'Write-Output "$($result.ready),$($result.last_probe.http_status)"',
    )

    assert result == "True,200"


def test_business_switch_never_invokes_control_bundle_copy(
    tmp_path,
) -> None:
    previous = "a" * 40
    candidate = "b" * 40
    result = _run_control_center_contract(
        tmp_path,
        _authorized_candidate(previous, candidate)
        + "$script:checkouts = @(); "
        f"function Get-CodeRevision {{ return '{previous}' }}; "
        "function Invoke-ProductionShapePreflight { return $true }; "
        "function git { if ($args -contains 'checkout') { "
        "$script:checkouts += [string]$args[-1] }; $global:LASTEXITCODE = 0 }; "
        "function Copy-Item { throw 'copy failed' }; "
        f"$accepted = Update-RuntimeCheckout -Revision '{candidate}'; "
        "$state = Get-RuntimeUpdateState; "
        'Write-Output "$accepted,$($script:checkouts -join \'|\'),$($state.update_status)"',
    )

    assert result == f"True,{candidate},STAGED"


def test_candidate_switch_preserves_reviewed_runtime_control_bundle(tmp_path) -> None:
    _write_control_bundle(tmp_path / "runtime", "previous", scripts_dir=True)
    _write_control_bundle(
        tmp_path / "repository" / ".local" / "runtime-control", "previous"
    )
    candidate = "b" * 40
    previous = "a" * 40
    result = _run_control_center_contract(
        tmp_path,
        _authorized_candidate(previous, candidate)
        + f"function Get-CodeRevision {{ return '{previous}' }}; "
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
        ",".join(f"previous|{name}" for name in RUNTIME_CONTROL_FILES),
        "True,STAGED",
    ]


def test_runtime_control_bundle_records_exact_source_revision_and_hashes(tmp_path) -> None:
    revision = "d" * 40
    _write_control_bundle(tmp_path / "runtime", "reviewed", scripts_dir=True)
    result = _run_control_center_contract(
        tmp_path,
        f"Sync-StableRuntimeControlFiles -SourceRoot $moduleRoot "
        f"-ControlRoot (Join-Path $repositoryRoot '.local\\runtime-control') "
        f"-SourceRevision '{revision}'; "
        "$manifest=Get-Content -LiteralPath (Join-Path $repositoryRoot "
        "'.local\\runtime-control\\runtime-control-bundle.json') -Raw | ConvertFrom-Json; "
        '$hashCount=@($manifest.files.PSObject.Properties).Count; '
        'Write-Output "$($manifest.source_revision),$($manifest.exact_revision),$hashCount"',
    )

    assert result == f"{revision},True,{len(RUNTIME_CONTROL_FILES)}"


def test_business_switch_ignores_control_copy_failure_hook(
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
        _authorized_candidate(previous, candidate)
        + "$script:failedCandidateCopy = $false; "
        f"function Get-CodeRevision {{ return '{previous}' }}; "
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
        ",".join(f"previous|{name}" for name in RUNTIME_CONTROL_FILES),
        "True,STAGED",
    ]


def test_business_switch_never_moves_control_bundle_files(
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
        _authorized_candidate(previous, candidate)
        + "$script:failedCandidateMove = $false; "
        f"function Get-CodeRevision {{ return '{previous}' }}; "
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
        ",".join(f"previous|{name}" for name in RUNTIME_CONTROL_FILES),
        "True,STAGED",
    ]


def test_observation_rollback_preserves_independent_control_bundle(
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
        ",".join(f"{candidate}|{name}" for name in RUNTIME_CONTROL_FILES),
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


def test_candidate_discovery_cannot_change_stable(tmp_path) -> None:
    previous = "a" * 40
    candidate = "b" * 40
    result = _run_control_center_contract(
        tmp_path,
        _authorized_candidate(previous, candidate)
        + "$before = (Get-ReleaseControlState).stable.validation_key; "
        "$new = New-ReleaseIdentity -GitSha ('c' * 40) "
        "-WorkerVersionId '33333333-3333-4333-8333-333333333333' "
        "-WindowsRevision ('c' * 40); $state = Get-ReleaseControlState; "
        "$state.candidate = $new; Write-ReleaseControlState $state; "
        "$after = (Get-ReleaseControlState).stable.validation_key; "
        'Write-Output "$before,$after"',
    )

    stable_key = f"11111111-1111-4111-8111-111111111111:{previous}"
    assert result == f"{stable_key},{stable_key}"


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


def test_failed_candidate_cannot_promote(tmp_path) -> None:
    previous = "a" * 40
    candidate = "b" * 40
    result = _run_control_center_contract(
        tmp_path,
        _authorized_candidate(previous, candidate)
        + "$state = Get-ReleaseControlState; "
        "$state.candidate.validation_state = 'FAILED'; "
        "Write-ReleaseControlState $state; "
        "function Enter-ReleaseTransactionLock { return $true }; "
        "function Exit-ReleaseTransactionLock {}; "
        "try { Start-ReleasePromotion | Out-Null; 'PROMOTED' } "
        "catch { 'REJECTED' }",
    )

    assert result == "REJECTED"


def test_old_candidate_evidence_cannot_authorize_new_candidate(tmp_path) -> None:
    previous = "a" * 40
    candidate = "b" * 40
    result = _run_control_center_contract(
        tmp_path,
        _authorized_candidate(previous, candidate)
        + "$state = Get-ReleaseControlState; "
        "$state.candidate.worker_version_id = '33333333-3333-4333-8333-333333333333'; "
        "Write-ReleaseControlState $state; "
        "function Enter-ReleaseTransactionLock { return $true }; "
        "function Exit-ReleaseTransactionLock {}; "
        "try { Start-ReleasePromotion | Out-Null; 'PROMOTED' } "
        "catch { 'REJECTED' }",
    )

    assert result == "REJECTED"


def test_completed_promotion_records_previous_stable(tmp_path) -> None:
    previous = "a" * 40
    candidate = "b" * 40
    result = _run_control_center_contract(
        tmp_path,
        _authorized_candidate(previous, candidate)
        + "$state = Get-ReleaseControlState; "
        "$state.transaction = [pscustomobject]@{ type='PROMOTE'; phase='OBSERVING'; "
        "target=$state.candidate; previous=$state.stable }; "
        "Write-ReleaseControlState $state; "
        "function Test-CloudflareRollbackTarget { return $true }; "
        "Complete-ReleasePromotion; "
        "$final = Get-ReleaseControlState; "
        'Write-Output "$($final.stable.git_sha),$($final.previous_stable.git_sha),$($null -eq $final.transaction)"',
    )

    assert result == f"{candidate},{previous},True"


def test_candidate_arriving_during_promotion_is_queued(tmp_path) -> None:
    previous = "a" * 40
    candidate = "b" * 40
    queued = "c" * 40
    result = _run_control_center_contract(
        tmp_path,
        _authorized_candidate(previous, candidate)
        + "$state = Get-ReleaseControlState; "
        "$state.transaction = [pscustomobject]@{ type='PROMOTE'; phase='CUTOVER' }; "
        "$state.candidate_discovery.initialized_at='2026-08-20T11:00:00Z'; "
        "$state.candidate_discovery.watermark_created_at='2026-08-20T11:00:00Z'; "
        "$state.candidate_discovery.watermark_version_id='11111111-1111-4111-8111-111111111111'; "
        "Write-ReleaseControlState $state; "
        f"$new = New-ReleaseIdentity -GitSha '{queued}' "
        "-WorkerVersionId '33333333-3333-4333-8333-333333333333' "
        f"-WindowsRevision '{queued}'; "
        "function Get-CloudflareVersions { return @([pscustomobject]@{ "
        "id=$new.worker_version_id; metadata=[pscustomobject]@{ created_on='2026-08-20T12:00:00Z' }; "
        f"annotations=[pscustomobject]@{{ 'workers/message'='release:{queued} branch:main artifact_kind:PRODUCTION_CANDIDATE' }} }}) }}; "
        "$null = Find-NewCandidateRelease; $final = Get-ReleaseControlState; "
        'Write-Output "$($final.candidate.git_sha),$($final.queued_candidate.git_sha)"',
    )

    assert result == f"{candidate},{queued}"


def test_preview_version_is_consumed_by_watermark_but_never_becomes_candidate(
    tmp_path,
) -> None:
    previous = "a" * 40
    preview = "c" * 40
    result = _run_control_center_contract(
        tmp_path,
        _authorized_candidate(previous, "b" * 40)
        + "$state=Get-ReleaseControlState; "
        "$state.candidate=$null; "
        "$state.candidate_discovery.initialized_at='2026-08-20T11:00:00Z'; "
        "$state.candidate_discovery.watermark_created_at='2026-08-20T11:00:00Z'; "
        "$state.candidate_discovery.watermark_version_id='old'; "
        "Write-ReleaseControlState $state; "
        "function Get-CloudflareVersions { @([pscustomobject]@{ id='preview-version'; "
        "metadata=[pscustomobject]@{created_on='2026-08-20T12:00:00Z'}; "
        f"annotations=[pscustomobject]@{{'workers/message'='release:{preview} branch:feature artifact_kind:PREVIEW'}} }}) }}; "
        "$found=Find-NewCandidateRelease; $final=Get-ReleaseControlState; "
        'Write-Output "$($null -eq $found),$($null -eq $final.candidate),$($final.candidate_discovery.watermark_version_id)"',
    )

    assert result == "True,True,preview-version"


def test_failed_candidate_is_not_rediscovered_after_restart(tmp_path) -> None:
    previous = "a" * 40
    candidate = "b" * 40
    result = _run_control_center_contract(
        tmp_path,
        _authorized_candidate(previous, "c" * 40)
        + "$state=Get-ReleaseControlState; $state.candidate=$null; "
        "$state.candidate_discovery.initialized_at='2026-08-20T11:00:00Z'; "
        "$state.candidate_discovery.watermark_created_at='2026-08-20T11:00:00Z'; "
        "$state.candidate_discovery.watermark_version_id='old'; "
        "Write-ReleaseControlState $state; "
        "function Get-CloudflareVersions { @([pscustomobject]@{id='candidate-version'; "
        "metadata=[pscustomobject]@{created_on='2026-08-20T12:00:00Z'}; "
        f"annotations=[pscustomobject]@{{'workers/message'='release:{candidate} branch:main artifact_kind:PRODUCTION_CANDIDATE'}} }}) }}; "
        "$first=Find-NewCandidateRelease; $state=Get-ReleaseControlState; "
        "$state.candidate.validation_state='FAILED'; Write-ReleaseControlState $state; "
        "$second=Find-NewCandidateRelease; $final=Get-ReleaseControlState; "
        'Write-Output "$($first.git_sha),$($null -eq $second),$($final.candidate.validation_state)"',
    )

    assert result == f"{candidate},True,FAILED"


def test_preview_artifact_cannot_promote_even_with_passed_evidence(tmp_path) -> None:
    result = _run_control_center_contract(
        tmp_path,
        _authorized_candidate("a" * 40, "b" * 40)
        + "$state=Get-ReleaseControlState; $state.candidate.artifact_kind='PREVIEW'; "
        "Write-ReleaseControlState $state; "
        "function Enter-ReleaseTransactionLock { return $true }; "
        "function Exit-ReleaseTransactionLock {}; "
        "try { Start-ReleasePromotion | Out-Null; 'PROMOTED' } catch { 'REJECTED' }",
    )

    assert result == "REJECTED"


def test_preview_evidence_cannot_authorize_production_candidate(tmp_path) -> None:
    result = _run_control_center_contract(
        tmp_path,
        "$sha=('b'*40); $preview=New-ReleaseIdentity -GitSha $sha "
        "-WorkerVersionId 'same-worker' -WindowsRevision $sha "
        "-ArtifactKind 'PREVIEW' -ValidationState 'PASSED'; "
        "$candidate=New-ReleaseIdentity -GitSha $sha -WorkerVersionId 'same-worker' "
        "-WindowsRevision $sha -ArtifactKind 'PRODUCTION_CANDIDATE'; "
        "Write-Output (Test-ReleaseIdentity $preview $candidate)",
    )

    assert result == "False"


def test_static_assets_are_excluded_from_expected_worker_invocations(tmp_path) -> None:
    candidate = "b" * 40
    result = _run_control_center_contract(
        tmp_path,
        f"$candidate=New-ReleaseIdentity -GitSha '{candidate}' -WorkerVersionId 'worker' "
        f"-WindowsRevision '{candidate}' -ArtifactKind 'PRODUCTION_CANDIDATE'; "
        "$plan=Get-CandidateRouteValidationPlan -ChangedFiles @('web/app/page.tsx'); "
        "function Invoke-WebRequest { return [pscustomobject]@{StatusCode=200;Content='AURUM SIGNAL ROOM 系统健康状态 新闻与决策'} }; "
        "function Start-Sleep {}; function Get-CandidateInvocationCount { return 0 }; "
        "$e=Invoke-CandidateWorkerValidation -Candidate $candidate -RoutePlan $plan; "
        'Write-Output "$($plan.static_assets.Count),$($e.expected_worker_invocations),$($e.cpu_evidence)"',
    )

    assert result == "4,0,NOT_REQUIRED"


def test_manifest_selects_baseline_and_affected_route_sample_families(tmp_path) -> None:
    result = _run_control_center_contract(
        tmp_path,
        "$heavy=Get-CandidateRouteValidationPlan "
        "-ChangedFiles @('web/app/api/market-history/route.ts'); "
        "$shared=Get-CandidateRouteValidationPlan "
        "-ChangedFiles @('web/worker/api-router.ts'); "
        "$admin=Get-CandidateRouteValidationPlan "
        "-ChangedFiles @('web/app/admin/api/session/route.ts'); "
        "$docs=Get-CandidateRouteValidationPlan -ChangedFiles @('docs/README.md'); "
        "$heavyRoutes=@($heavy.worker_reads)+@($heavy.worker_writes); "
        "$sharedRoutes=@($shared.worker_reads)+@($shared.worker_writes); "
        "$adminRoutes=@($admin.worker_reads)+@($admin.worker_writes); "
        "$heavySamples=($heavyRoutes|Measure-Object acceptance_samples -Sum).Sum; "
        "$sharedSamples=($sharedRoutes|Measure-Object acceptance_samples -Sum).Sum; "
        'Write-Output "$($heavyRoutes.Count),$heavySamples,$($sharedRoutes.Count),'
        '$sharedSamples,$($adminRoutes.Count),$($docs.worker_cpu_required)"',
    )

    assert result == "7,70,27,270,0,False"


def test_one_failed_route_family_fails_candidate_cpu_evidence(tmp_path) -> None:
    candidate = "b" * 40
    result = _run_control_center_contract(
        tmp_path,
        f"$candidate=New-ReleaseIdentity -GitSha '{candidate}' -WorkerVersionId 'worker' "
        f"-WindowsRevision '{candidate}' -ArtifactKind 'PRODUCTION_CANDIDATE'; "
        "$routes=@([pscustomobject]@{path='/api/status';method='GET';family='status-read';acceptance_samples=10},"
        "[pscustomobject]@{path='/api/audit';method='GET';family='audit-read';acceptance_samples=10}); "
        "function Get-CandidatePlatformEvidence { param($Candidate,$From,$To,$ExpectedInvocations,$RoutePath,$RouteMethod,$RouteFamily); "
        "$failed=($RouteFamily -eq 'audit-read'); $gate=if($failed){'FAILED'}else{'PASSED'}; return [pscustomobject]@{route_family=$RouteFamily;"
        "invocations=$ExpectedInvocations;max_cpu_ms=9;p95_cpu_ms=4;p99_cpu_ms=7;max_wall_ms=10;"
        "exceeded_cpu=0;exceeded_memory=0;responses_1102=0;responses_5xx=0;"
        "gate_state=$gate;passed=(-not $failed)} }; "
        "$e=Get-CandidateCpuEvidence -Candidate $candidate -From ([DateTimeOffset]::UtcNow.AddMinutes(-1)) "
        "-To ([DateTimeOffset]::UtcNow) -Routes $routes; "
        'Write-Output "$($e.gate_state),$($e.routes.Count),$($e.routes[1].route_family)"',
    )

    assert result == "FAILED,2,audit-read"


def test_version_at_or_before_watermark_cannot_replace_candidate(tmp_path) -> None:
    previous = "a" * 40
    current = "b" * 40
    historical = "c" * 40
    result = _run_control_center_contract(
        tmp_path,
        _authorized_candidate(previous, current)
        + "$state=Get-ReleaseControlState; "
        "$state.candidate_discovery.initialized_at='2026-08-20T12:00:00Z'; "
        "$state.candidate_discovery.watermark_created_at='2026-08-20T12:00:00Z'; "
        "$state.candidate_discovery.watermark_version_id='newer'; Write-ReleaseControlState $state; "
        "function Get-CloudflareVersions { @([pscustomobject]@{id='older'; "
        "metadata=[pscustomobject]@{created_on='2026-08-20T11:00:00Z'}; "
        f"annotations=[pscustomobject]@{{'workers/message'='release:{historical} branch:main artifact_kind:PRODUCTION_CANDIDATE'}} }}) }}; "
        "$found=Find-NewCandidateRelease; $final=Get-ReleaseControlState; "
        'Write-Output "$($null -eq $found),$($final.candidate.git_sha)"',
    )

    assert result == f"True,{current}"


def test_v1_state_migrates_only_the_reviewed_legacy_candidate_provenance(
    tmp_path,
) -> None:
    accepted_worker = "dd823aa4-20f0-47e1-9255-1b785a4c17b0"
    accepted_sha = "14c055a35040fa963700c988f770c9bb52fa669e"
    result = _run_control_center_contract(
        tmp_path,
        "$state=[pscustomobject]@{schema_version='stable-candidate-release-v1'; "
        "stable=[pscustomobject]@{git_sha=('a'*40);worker_version_id='stable';windows_revision=('a'*40)}; "
        f"candidate=[pscustomobject]@{{git_sha='{accepted_sha}';worker_version_id='{accepted_worker}';windows_revision='{accepted_sha}'}}; "
        "previous_stable=$null;queued_candidate=$null}; Write-ReleaseControlState $state; "
        "$migrated=Get-ReleaseControlState; "
        'Write-Output "$($migrated.schema_version),$($migrated.stable.artifact_kind),'
        '$($migrated.stable.worker_git_sha),$($migrated.candidate.artifact_kind),'
        '$($migrated.candidate.validation_state)"',
    )

    assert result == (
        "stable-candidate-release-v3,LEGACY_BOOTSTRAP_STABLE,NOT_RECORDED,"
        "LEGACY_REFERENCE,REBASE_REQUIRED"
    )


def test_failed_runtime_rollback_does_not_rewrite_previous_stable(tmp_path) -> None:
    previous = "a" * 40
    candidate = "b" * 40
    older = "c" * 40
    result = _run_control_center_contract(
        tmp_path,
        _authorized_candidate(previous, candidate)
        + f"$state=Get-ReleaseControlState; $state.previous_stable=New-ReleaseIdentity "
        f"-GitSha '{older}' -WorkerVersionId 'older-worker' -WindowsRevision '{older}' "
        "-ArtifactKind 'PRODUCTION_CANDIDATE'; "
        "$state.transaction=[pscustomobject]@{type='PROMOTE';previous=$state.stable;target=$state.candidate}; "
        "Write-ReleaseControlState $state; function git {$global:LASTEXITCODE=0}; "
        "function Sync-StableRuntimeControlFiles {}; function Restart-CodeReloadableServices {}; "
        "function Write-RuntimeCodeState {}; function Write-RuntimeUpdateFailure {}; "
        "function Write-WatchdogEvent {}; function Invoke-CloudflareDeployment {}; "
        f"$null=Invoke-RuntimeRollback -FailedRevision '{candidate}' -PreviousRevision '{previous}' -Reason 'test'; "
        "$final=Get-ReleaseControlState; Write-Output $final.previous_stable.git_sha",
    )

    assert result == older


@pytest.mark.parametrize(
    ("p95", "p99", "maximum", "expected"),
    [(4, 7, 9, "PASSED"), (7, 9, 10, "REVIEW_REQUIRED"), (4, 18, 18, "FAILED")],
)
def test_worker_cpu_gate_requires_free_tier_headroom(
    tmp_path, p95: int, p99: int, maximum: int, expected: str,
) -> None:
    result = _run_control_center_contract(
        tmp_path,
        "$e=[pscustomobject]@{invocations=8; exceeded_cpu=0; responses_1102=0; "
        f"responses_5xx=0; p95_cpu_ms={p95}; p99_cpu_ms={p99}; max_cpu_ms={maximum}}}; "
        "Write-Output (Get-WorkerCpuGateState -Evidence $e -ExpectedInvocations 8)",
    )

    assert result == expected


@pytest.mark.parametrize(
    ("exceeded_cpu", "responses_5xx"), [(1, 0), (0, 1)],
)
def test_worker_cpu_gate_fails_platform_errors(
    tmp_path, exceeded_cpu: int, responses_5xx: int,
) -> None:
    result = _run_control_center_contract(
        tmp_path,
        f"$e=[pscustomobject]@{{invocations=8; exceeded_cpu={exceeded_cpu}; "
        f"responses_1102={exceeded_cpu}; responses_5xx={responses_5xx}; "
        "p95_cpu_ms=4; p99_cpu_ms=7; max_cpu_ms=9}; "
        "Write-Output (Get-WorkerCpuGateState -Evidence $e -ExpectedInvocations 8)",
    )

    assert result == "FAILED"


def test_worker_windows_mismatch_cannot_switch_runtime(tmp_path) -> None:
    previous = "a" * 40
    candidate = "b" * 40
    result = _run_control_center_contract(
        tmp_path,
        _authorized_candidate(previous, candidate)
        + "$state = Get-ReleaseControlState; "
        "$state.candidate.windows_revision = ('c' * 40); "
        "Write-ReleaseControlState $state; "
        f"Write-Output (Update-RuntimeCheckout -Revision '{candidate}')",
    )

    assert result == "False"


def test_platform_evidence_is_bound_to_exact_worker_version(tmp_path) -> None:
    candidate = "b" * 40
    worker = "22222222-2222-4222-8222-222222222222"
    result = _run_control_center_contract(
        tmp_path,
        f"$candidate = New-ReleaseIdentity -GitSha '{candidate}' "
        f"-WorkerVersionId '{worker}' -WindowsRevision '{candidate}'; "
        "$script:observedVersions = @(); "
        "function Invoke-WorkersObservabilityQuery { param($Filters,$Calculations,$From,$To); "
        "$script:observedVersions += @($Filters | Where-Object key -eq '$workers.scriptVersion.id')[0].value; "
        "$alias = [string]$Calculations[0].alias; "
        "if ($alias -eq 'invocations') { return [pscustomobject]@{ calculations=@("
        "[pscustomobject]@{alias='invocations';aggregates=@([pscustomobject]@{value=6})},"
        "[pscustomobject]@{alias='max_cpu_ms';aggregates=@([pscustomobject]@{value=8})},"
        "[pscustomobject]@{alias='p95_cpu_ms';aggregates=@([pscustomobject]@{value=5})},"
        "[pscustomobject]@{alias='p99_cpu_ms';aggregates=@([pscustomobject]@{value=7})},"
        "[pscustomobject]@{alias='max_wall_ms';aggregates=@([pscustomobject]@{value=25})}) } }; "
        "return [pscustomobject]@{ calculations=@([pscustomobject]@{alias=$alias;aggregates=@([pscustomobject]@{value=0})}) } }; "
        "$now=[DateTimeOffset]::UtcNow; $evidence=Get-CandidatePlatformEvidence "
        "-Candidate $candidate -From $now.AddMinutes(-1) -To $now -ExpectedInvocations 6; "
        'Write-Output "$($evidence.passed),$($evidence.invocations),$(@($script:observedVersions | Select-Object -Unique) -join \';\')"',
    )

    assert result == f"True,6,{worker}"


def test_bootstrap_preserves_accepted_268_candidate_and_evidence(tmp_path) -> None:
    stable = "a" * 40
    result = _run_control_center_contract(
        tmp_path,
        "function Get-CloudflareDeployment { return [pscustomobject]@{versions=@("
        "[pscustomobject]@{version_id='76d314fc-e484-4f50-8ace-3689e0896709';percentage=100},"
        "[pscustomobject]@{version_id='dd823aa4-20f0-47e1-9255-1b785a4c17b0';percentage=0})} };"
        "function Get-CloudflareVersions { return @() };"
        f"function Get-RuntimeCodeState {{ return [pscustomobject]@{{applied_revision='{stable}'}} }};"
        "$state=Initialize-ReleaseControl;"
        'Write-Output "$($state.stable.worker_version_id),$($state.candidate.worker_version_id),$($state.candidate.git_sha),$($state.candidate.validation.cpu_evidence.exceeded_cpu)"',
    )

    assert result == (
        "76d314fc-e484-4f50-8ace-3689e0896709,"
        "dd823aa4-20f0-47e1-9255-1b785a4c17b0,"
        "14c055a35040fa963700c988f770c9bb52fa669e,0"
    )


def test_abandoned_release_lock_is_recovered_without_touching_state(tmp_path) -> None:
    runtime = tmp_path / "runtime"
    lock = runtime / ".local" / "forward" / "release-control.lock"
    lock.mkdir(parents=True)
    (lock / "owner.json").write_text(json.dumps({
        "owner_pid": 2147483647,
        "acquired_at": datetime.now(timezone.utc).isoformat(),
    }), encoding="utf-8")
    result = _run_control_center_contract(
        tmp_path,
        "$entered=Enter-ReleaseTransactionLock; $history=Get-Content -LiteralPath $releaseHistoryPath -Raw; "
        'Write-Output "$entered,$($history.Contains(\'ABANDONED_LOCK_RECOVERED\'))"; '
        "Exit-ReleaseTransactionLock",
    )

    assert result == "True,True"


def test_live_release_lock_is_never_stolen(tmp_path) -> None:
    result = _run_control_center_contract(
        tmp_path,
        "New-Item -ItemType Directory -Path $releaseLockPath -Force | Out-Null; "
        "[pscustomobject]@{owner_pid=$PID;acquired_at=[DateTimeOffset]::UtcNow.ToString('o')} "
        "| ConvertTo-Json | Set-Content -LiteralPath (Join-Path $releaseLockPath 'owner.json'); "
        "Write-Output (Enter-ReleaseTransactionLock)",
    )

    assert result == "False"


def test_passed_candidate_promotes_only_after_observation_commit(tmp_path) -> None:
    previous = "a" * 40
    candidate = "b" * 40
    result = _run_control_center_contract(
        tmp_path,
        _authorized_candidate(previous, candidate)
        + "function Enter-ReleaseTransactionLock { return $true }; "
        "function Exit-ReleaseTransactionLock {}; "
        "function Assert-ActiveControlBundle { return [pscustomobject]@{exact_revision=$true} }; "
        "function Test-ProductionCandidateProvenance { return $true }; "
        "function Test-CloudflareRollbackTarget { return $true }; "
        "function Test-CloudflareReleasePlacement { return $true }; "
        f"function Get-RuntimeCodeState {{ return [pscustomobject]@{{applied_revision='{previous}'}} }}; "
        "function Test-SingleProductionOwner { return $true }; "
        "function Update-RuntimeCheckout { return $true }; "
        "$script:cutover=@(); "
        "function Restart-CodeReloadableServices { $script:cutover += 'windows-with-sync-paused'; return [DateTimeOffset]::UtcNow }; "
        "function Complete-DeferredServiceReload { $script:cutover += 'sync-resumed' }; "
        "function Start-RuntimeObservation {}; "
        "function Write-RuntimeCodeState {}; "
        "function Write-WatchdogEvent {}; "
        "function Invoke-CloudflareDeployment { $script:cutover += 'worker' }; "
        "$started=Start-ReleasePromotion; $during=Get-ReleaseControlState; "
        "Complete-ReleasePromotion; $after=Get-ReleaseControlState; "
        'Write-Output "$started,$($during.stable.git_sha),$($during.transaction.phase),$($after.stable.git_sha),$($script:cutover -join \';\')"',
    )

    assert result == (
        f"True,{previous},OBSERVING,{candidate},"
        "windows-with-sync-paused;worker;sync-resumed"
    )


def test_crashed_cutover_is_reconciled_to_recovery_required(tmp_path) -> None:
    previous = "a" * 40
    candidate = "b" * 40
    result = _run_control_center_contract(
        tmp_path,
        _authorized_candidate(previous, candidate)
        + "$state=Get-ReleaseControlState; "
        "$state.transaction=[pscustomobject]@{type='PROMOTE';phase='CUTOVER';target=$state.candidate;previous=$state.stable}; "
        "Write-ReleaseControlState $state; "
        "function Get-CloudflareDeployment { return [pscustomobject]@{versions=@([pscustomobject]@{version_id='22222222-2222-4222-8222-222222222222';percentage=100})} }; "
        f"function Get-RuntimeCodeState {{ return [pscustomobject]@{{applied_revision='{candidate}'}} }}; "
        "$final=Reconcile-ReleaseControlState; "
        'Write-Output "$($final.deployment_status),$($final.drift.code),$($final.drift.phase)"',
    )

    assert result == "RECOVERY_REQUIRED,INCOMPLETE_RELEASE_TRANSACTION,CUTOVER"


def test_crash_after_observation_pass_commits_exact_stable(tmp_path) -> None:
    previous = "a" * 40
    candidate = "b" * 40
    result = _run_control_center_contract(
        tmp_path,
        _authorized_candidate(previous, candidate)
        + "$state=Get-ReleaseControlState;"
        "$state.transaction=[pscustomobject]@{type='PROMOTE';phase='OBSERVING';target=$state.candidate;previous=$state.stable};"
        "Write-ReleaseControlState $state;"
        f"Write-RuntimeUpdateState @{{update_status='ACTIVE';activated_revision='{candidate}'}};"
        "function Test-CloudflareRollbackTarget { return $true };"
        "function Get-CloudflareDeployment { return [pscustomobject]@{versions=@([pscustomobject]@{version_id='22222222-2222-4222-8222-222222222222';percentage=100})} };"
        f"function Get-RuntimeCodeState {{ return [pscustomobject]@{{applied_revision='{candidate}'}} }};"
        "$final=Reconcile-ReleaseControlState;"
        'Write-Output "$($final.deployment_status),$($final.stable.git_sha),$($null -eq $final.transaction)"',
    )

    assert result == f"READY,{candidate},True"


def test_crashed_reverse_enters_observation_before_commit(tmp_path) -> None:
    previous = "a" * 40
    current = "b" * 40
    result = _run_control_center_contract(
        tmp_path,
        _authorized_candidate(previous, current)
        + "$state=Get-ReleaseControlState;$target=$state.stable;$current=$state.candidate;"
        "$state.stable=$current;$state.previous_stable=$target;"
        "$state.transaction=[pscustomobject]@{type='REVERSE';phase='REVERSING';target=$target;previous=$current};"
        "Write-ReleaseControlState $state;"
        "function Get-CloudflareDeployment { return [pscustomobject]@{versions=@([pscustomobject]@{version_id='11111111-1111-4111-8111-111111111111';percentage=100})} };"
        f"function Get-RuntimeCodeState {{ return [pscustomobject]@{{applied_revision='{previous}'}} }};"
        "$final=Reconcile-ReleaseControlState;"
        'Write-Output "$($final.deployment_status),$($final.stable.git_sha),$($final.previous_stable.git_sha)"',
    )

    assert result == f"REVERSE_OBSERVING,{current},{previous}"


def test_reverse_restores_both_identities_without_d1_mutation(tmp_path) -> None:
    previous = "a" * 40
    current = "b" * 40
    result = _run_control_center_contract(
        tmp_path,
        _authorized_candidate(previous, current)
        + "$state=Get-ReleaseControlState;$state.previous_stable=$state.stable;"
        "$state.stable=$state.candidate;$state.candidate=$null;Write-ReleaseControlState $state;"
        "function Enter-ReleaseTransactionLock { return $true };function Exit-ReleaseTransactionLock {};"
        "function Assert-ActiveControlBundle { return [pscustomobject]@{exact_revision=$true} };"
        "function Test-CloudflareRollbackTarget { return $true };"
        "function Test-SingleProductionOwner { return $true };"
        "function Stop-ForecasterService {};"
        "function Start-RuntimeObservation {};"
        "$script:worker='';$script:windows='';"
        "function Invoke-CloudflareDeployment { param($StableVersionId);$script:worker=$StableVersionId };"
        "function Invoke-ReleaseWindowsRestore { param($Revision);$script:windows=$Revision };"
        "$ok=Invoke-ReverseStable;$final=Get-ReleaseControlState;"
        'Write-Output "$ok,$($final.stable.git_sha),$script:worker,$script:windows"',
    )
    source = (ROOT / "scripts" / "xauusd_control_center.ps1").read_text(encoding="utf-8")
    reverse_body = source.split("function Invoke-ReverseStable", 1)[1].split(
        "function Reconcile-ReleaseControlState", 1,
    )[0]

    assert result == f"True,{current},11111111-1111-4111-8111-111111111111,{previous}"
    assert "D1" not in reverse_body
    assert "database" not in reverse_body.lower()


def test_release_drift_is_detected_without_changing_stable(tmp_path) -> None:
    previous = "a" * 40
    candidate = "b" * 40
    result = _run_control_center_contract(
        tmp_path,
        _authorized_candidate(previous, candidate)
        + "function Get-CloudflareDeployment { return [pscustomobject]@{versions=@([pscustomobject]@{version_id='99999999-9999-4999-8999-999999999999';percentage=100})} }; "
        f"function Get-RuntimeCodeState {{ return [pscustomobject]@{{applied_revision='{previous}'}} }}; "
        "$final=Reconcile-ReleaseControlState; "
        'Write-Output "$($final.deployment_status),$($final.stable.git_sha)"',
    )

    assert result == f"DEPLOYMENT_DRIFT,{previous}"


def test_release_requires_exactly_one_owner_for_every_side_effect_service(
    tmp_path,
) -> None:
    result = _run_control_center_contract(
        tmp_path,
        "function Get-ForecasterProcesses { param($Service); "
        "if ($Service.Key -eq 'sync') { return @([pscustomobject]@{ProcessId=1},[pscustomobject]@{ProcessId=2}) }; "
        "return [pscustomobject]@{ProcessId=1} }; "
        "$duplicate=Test-SingleProductionOwner; "
        "function Get-ForecasterProcesses { param($Service); return [pscustomobject]@{ProcessId=1} }; "
        "$single=Test-SingleProductionOwner; Write-Output \"$duplicate,$single\"",
    )

    assert result == "False,True"


def test_storage_migration_requires_coordinated_compatibility_review(tmp_path) -> None:
    result = _run_control_center_contract(
        tmp_path,
        "Write-Output (Test-AutomaticStorageCompatibility -ChangedFiles "
        "@('web/worker/index.ts','web/drizzle/0022_new.sql'))",
    )

    assert result == "False"


def test_platform_binding_change_requires_coordinated_review(tmp_path) -> None:
    result = _run_control_center_contract(
        tmp_path,
        "Write-Output (Test-AutomaticStorageCompatibility -ChangedFiles "
        "@('web/wrangler.jsonc'))",
    )
    assert result == "False"


def test_required_github_gate_set_is_exact_and_missing_gate_stays_pending(tmp_path) -> None:
    result = _run_control_center_contract(
        tmp_path,
        "$runs=@($requiredGitHubChecks | ForEach-Object { [pscustomobject]@{"
        "name=$_;status='completed';conclusion='success'} });"
        "$script:payload=[pscustomobject]@{check_runs=$runs}|ConvertTo-Json -Depth 5;"
        "function gh { $global:LASTEXITCODE=0; return $script:payload };"
        "$all=Test-RequiredGitHubChecks -Revision ('a'*40);"
        "$script:payload=[pscustomobject]@{check_runs=@($runs | Where-Object name -ne 'Web build and tests')}|ConvertTo-Json -Depth 5;"
        "$missing=Test-RequiredGitHubChecks -Revision ('a'*40);"
        "$runs[0].conclusion='failure';$script:payload=[pscustomobject]@{check_runs=$runs}|ConvertTo-Json -Depth 5;"
        "$failed=Test-RequiredGitHubChecks -Revision ('a'*40);"
        'Write-Output "$all,$missing,$failed"',
    )
    assert result == "PASSED,PENDING,FAILED"


def test_production_candidate_requires_main_reachability(tmp_path) -> None:
    result = _run_control_center_contract(
        tmp_path,
        "$sha=('b'*40);$candidate=New-ReleaseIdentity -GitSha $sha "
        "-WorkerVersionId 'worker' -WindowsRevision $sha -Branch 'feature' "
        "-ArtifactKind 'PRODUCTION_CANDIDATE';"
        "$script:ancestor=$true;function git {"
        "if($args -contains 'merge-base' -and -not $script:ancestor){$global:LASTEXITCODE=1}"
        "else{$global:LASTEXITCODE=0}};"
        "$feature=Test-ProductionCandidateProvenance $candidate;"
        "$candidate.artifact_kind='PREVIEW';$preview=Test-ProductionCandidateProvenance $candidate;"
        "$candidate.artifact_kind='UNKNOWN';$unknown=Test-ProductionCandidateProvenance $candidate;"
        "$candidate.artifact_kind='PRODUCTION_CANDIDATE';"
        "$candidate.branch='main';$main=Test-ProductionCandidateProvenance $candidate;"
        "$script:ancestor=$false;$unreachable=Test-ProductionCandidateProvenance $candidate;"
        'Write-Output "$feature,$preview,$unknown,$main,$unreachable"',
    )
    assert result == "False,False,False,True,False"


def test_legacy_reference_evidence_is_readable_but_never_promotable(tmp_path) -> None:
    result = _run_control_center_contract(
        tmp_path,
        _authorized_candidate("a" * 40, "b" * 40)
        + "$state=Get-ReleaseControlState;"
        "$state.candidate.artifact_kind='LEGACY_REFERENCE';"
        "$state.candidate.validation_state='REBASE_REQUIRED';"
        "$state.candidate.validation=[pscustomobject]@{reason='REBASE_ON_RELEASE_CONTROL_MAIN_REQUIRED';"
        "cpu_evidence=[pscustomobject]@{samples=104;p95_cpu_ms=4;max_cpu_ms=5}};"
        "Write-ReleaseControlState $state;"
        "function Enter-ReleaseTransactionLock{return $true};function Exit-ReleaseTransactionLock{};"
        "$presentation=Get-ControlCenterReleasePresentation (Get-ReleaseControlState);"
        "try{Start-ReleasePromotion|Out-Null;$promoted=$true}catch{$promoted=$false};"
        "$final=Get-ReleaseControlState;"
        'Write-Output "$($presentation.can_promote),$promoted,'
        '$($final.candidate.validation.cpu_evidence.samples),'
        '$($final.candidate.validation.reason)"',
    )
    assert result == (
        "False,False,104,REBASE_ON_RELEASE_CONTROL_MAIN_REQUIRED"
    )


def test_normal_release_control_never_applies_or_provisions_storage() -> None:
    control = (ROOT / "scripts" / "xauusd_control_center.ps1").read_text(encoding="utf-8")
    runbook = (ROOT / "docs" / "runbooks" / "CLOUDFLARE_DEPLOYMENT.md").read_text(
        encoding="utf-8"
    )
    assert "d1 migrations apply" not in control
    assert "--experimental-provision" not in control
    normal_commands = runbook.split("## Bootstrap", 1)[0]
    assert "d1 migrations apply" not in normal_commands


def test_review_required_is_terminal_for_exact_candidate(tmp_path) -> None:
    result = _run_control_center_contract(
        tmp_path,
        _authorized_candidate("a" * 40, "b" * 40)
        + "$state=Get-ReleaseControlState;$state.candidate.validation_state='REVIEW_REQUIRED';"
        "Write-ReleaseControlState $state;"
        "function Enter-ReleaseTransactionLock{return $true};function Exit-ReleaseTransactionLock{};"
        "function Reconcile-ReleaseControlState{};function Find-NewCandidateRelease{return $null};"
        "function Invoke-AutomaticCandidateValidation{throw 'must not retry'};"
        "$ok=Invoke-CandidateDiscovery;Write-Output $ok",
    )
    assert result == "True"


def test_payload_producer_and_fixture_builder_select_worker_families(tmp_path) -> None:
    result = _run_control_center_contract(
        tmp_path,
        "$producer=Get-CandidateRouteValidationPlan -ChangedFiles @('scripts/run_dashboard_sync.py');"
        "$fixture=Get-CandidateRouteValidationPlan -ChangedFiles @('scripts/build_release_validation_fixtures.py');"
        "$package=Get-CandidateRouteValidationPlan -ChangedFiles @('web/package-lock.json');"
        "$build=Get-CandidateRouteValidationPlan -ChangedFiles @('web/build/sites-vite-plugin.ts');"
        "$docs=Get-CandidateRouteValidationPlan -ChangedFiles @('docs/README.md');"
        '$p=@($producer.worker_writes|Where-Object family -eq "news-content-write").Count;'
        '$f=@($fixture.worker_reads).Count+@($fixture.worker_writes).Count;'
        '$b=@($package.worker_reads|Where-Object {$_.baseline}).Count+'
        '@($package.worker_writes|Where-Object {$_.baseline}).Count;'
        '$bb=@($build.worker_reads|Where-Object {$_.baseline}).Count+'
        '@($build.worker_writes|Where-Object {$_.baseline}).Count;'
        'Write-Output "$p,$f,$b,$bb,$($docs.worker_cpu_required)"',
    )
    producer_scenarios, all_routes, baseline, build_baseline, docs = result.split(",")
    assert int(producer_scenarios) == 2
    assert int(all_routes) >= 20
    assert int(baseline) >= 5
    assert int(build_baseline) == int(baseline)
    assert docs == "False"


def test_release_validator_sends_exact_fixture_bytes(tmp_path) -> None:
    fixture = tmp_path / "repository" / "fixtures" / "utf8.json"
    fixture.parent.mkdir(parents=True, exist_ok=True)
    fixture.write_bytes('{"text":"精确字节"}'.encode("utf-8"))
    expected = fixture.read_bytes().hex()
    result = _run_control_center_contract(
        tmp_path,
        "$route=[pscustomobject]@{method='POST';path='/api/test';family='test';"
        "strategy='PRODUCTION_SHAPED_DRY_RUN';fixture='utf8.json'};"
        "$script:sent=$null;function Invoke-WebRequest{param($UseBasicParsing,$Method,$Uri,$Headers,$TimeoutSec,$ContentType,$Body);"
        "$script:sent=$Body;$content=[pscustomobject]@{status='DRY_RUN_OK';mutated=$false;route_family='test'}|ConvertTo-Json -Compress;"
        "return [pscustomobject]@{StatusCode=200;Content=$content}};"
        "$null=Invoke-CandidateRouteSample -Route $route -VersionHeaders @{} "
        "-ValidationRun 'run' -FixtureRoot (Join-Path $repositoryRoot 'fixtures') -IngestToken 'token';"
        "$hex=($script:sent|ForEach-Object {$_.ToString('x2')}) -join '';Write-Output $hex",
    )
    assert result == expected


def test_reverse_observation_commits_only_after_active_runtime_evidence(tmp_path) -> None:
    previous = "a" * 40
    current = "b" * 40
    result = _run_control_center_contract(
        tmp_path,
        _authorized_candidate(previous, current)
        + "$state=Get-ReleaseControlState;$target=$state.stable;$now=$state.candidate;"
        "$state.stable=$now;$state.previous_stable=$target;"
        "$state.transaction=[pscustomobject]@{type='REVERSE';phase='REVERSE_OBSERVING';target=$target;previous=$now};"
        "Write-ReleaseControlState $state;"
        f"Write-RuntimeUpdateState @{{update_status='ACTIVE';activated_revision='{previous}';observation_mode='REVERSE'}};"
        "function Test-CloudflareRollbackTarget{return $true};"
        "function Get-CloudflareDeployment{return [pscustomobject]@{versions=@([pscustomobject]@{version_id='11111111-1111-4111-8111-111111111111';percentage=100})}};"
        f"function Get-RuntimeCodeState{{return [pscustomobject]@{{applied_revision='{previous}'}}}};"
        "$final=Reconcile-ReleaseControlState;"
        'Write-Output "$($final.deployment_status),$($final.stable.git_sha),$($null -eq $final.transaction)"',
    )
    assert result == f"READY,{previous},True"


def test_business_transitions_do_not_call_control_bundle_installer() -> None:
    source = (ROOT / "scripts" / "xauusd_control_center.ps1").read_text(encoding="utf-8")
    for start, end in (
        ("function Update-RuntimeCheckout", "function Get-RuntimeCodeState"),
        ("function Invoke-RuntimeRollback", "function Test-CloudflareReleasePlacement"),
        ("function Invoke-ReleaseWindowsRestore", "function Invoke-ReverseStable"),
        ("function Invoke-ReverseStable", "function Complete-ReleaseReverse"),
    ):
        body = source.split(start, 1)[1].split(end, 1)[0]
        assert "Sync-StableRuntimeControlFiles" not in body


def test_release_gui_exposes_only_explicit_stable_candidate_controls() -> None:
    source = (ROOT / "scripts" / "xauusd_control_center.ps1").read_text(encoding="utf-8")

    assert 'New-ReleaseCard -Title "Stable"' in source
    assert 'New-ReleaseCard -Title "Release Candidate" -Emphasized $true' in source
    assert 'New-ReleaseCard -Title "Previous Stable"' in source
    assert 'New-UiButton -Text "Promote Candidate"' in source
    assert 'New-UiButton -Text "Reverse Stable"' in source
    assert 'Git: $($state.candidate.git_sha)' in source
    assert 'Worker: $($state.candidate.worker_version_id)' in source
    assert 'Windows: $($state.candidate.windows_revision)' in source
    assert 'Git: $($state.previous_stable.git_sha)' in source
    assert 'Worker: $($state.previous_stable.worker_version_id)' in source
    assert 'Windows: $($state.previous_stable.windows_revision)' in source
    assert "CLOUDFLARE_RELEASE_OBSERVABILITY_TOKEN" not in source.split(
        "function Show-ControlCenter", 1,
    )[1]


def test_release_gui_presentation_explains_action_eligibility(tmp_path) -> None:
    result = _run_control_center_contract(
        tmp_path,
        "$stable=New-ReleaseIdentity -GitSha ('a'*40) -WorkerVersionId 'stable-worker' "
        "-WindowsRevision ('a'*40);"
        "$candidate=New-ReleaseIdentity -GitSha ('b'*40) -WorkerVersionId 'candidate-worker' "
        "-WindowsRevision ('b'*40) -ValidationState 'PASSED' "
        "-ArtifactKind 'PRODUCTION_CANDIDATE' -Branch 'main';"
        "$candidate.compatibility_state='PASSED';"
        "$candidate.validation=[pscustomobject]@{key=$candidate.validation_key};"
        "$release=New-ReleaseControlState -Stable $stable -Candidate $candidate;"
        "$release|Add-Member control_bundle_revision ('d'*40);"
        "$release|Add-Member control_bundle_exact_revision $true;"
        "$release|Add-Member control_bundle_hash_verified $true;"
        "$release.previous_stable=New-ReleaseIdentity -GitSha ('c'*40) "
        "-WorkerVersionId 'previous-worker' -WindowsRevision ('c'*40);"
        "$release.previous_stable_rollback_eligible=$true;"
        "$passed=Get-ControlCenterReleasePresentation $release;"
        "$release.candidate.artifact_kind='PREVIEW';"
        "$preview=Get-ControlCenterReleasePresentation $release;"
        "$release.candidate.artifact_kind='PRODUCTION_CANDIDATE';"
        "$release.candidate.validation_state='FAILED';"
        "$release.candidate.validation=[pscustomobject]@{error='Worker CPU evidence failed'};"
        "$failed=Get-ControlCenterReleasePresentation $release;"
        "$release.transaction=[pscustomobject]@{type='PROMOTE'};"
        "$busy=Get-ControlCenterReleasePresentation $release;"
        "$missing=Get-ControlCenterReleasePresentation $null;"
        "@($passed,$preview,$failed,$busy,$missing) | ConvertTo-Json -Compress",
    )

    passed, preview, failed, busy, missing = json.loads(result)
    assert passed["can_promote"] is True
    assert passed["can_reverse"] is True
    assert passed["promote_reason"] == "Ready to promote"
    assert preview["can_promote"] is False
    assert preview["promote_reason"] == "Preview cannot be promoted"
    assert failed["can_promote"] is False
    assert failed["candidate_detail"] == "Worker CPU evidence failed"
    assert failed["promote_reason"] == "Candidate failed validation"
    assert busy["can_promote"] is False
    assert busy["can_reverse"] is False
    assert busy["promote_reason"] == "A release transaction is already in progress"
    assert missing["candidate_state"] == "UNAVAILABLE"
    assert missing["promote_reason"] == "Not bootstrapped"


def test_control_center_summary_separates_runtime_health_from_candidate_state(
    tmp_path,
) -> None:
    result = _run_control_center_contract(
        tmp_path,
        "$stable=New-ReleaseIdentity -GitSha ('a'*40) -WorkerVersionId 'stable-worker' "
        "-WindowsRevision ('a'*40);"
        "$candidate=New-ReleaseIdentity -GitSha ('b'*40) -WorkerVersionId 'candidate-worker' "
        "-WindowsRevision ('b'*40) -ValidationState 'TESTING';"
        "$release=New-ReleaseControlState -Stable $stable -Candidate $candidate;"
        "$snapshot=[pscustomobject]@{captured_at='2026-08-20T12:00:00+08:00';release=$release;"
        "services=@([pscustomobject]@{State='RUNNING'},[pscustomobject]@{State='STOPPED'})};"
        "Get-ControlCenterSummaryPresentation $snapshot | ConvertTo-Json -Compress",
    )

    summary = json.loads(result)
    assert summary["overall"] == "DEGRADED"
    assert summary["local_runtime"] == "PARTIAL"
    assert summary["candidate_state"] == "TESTING"
    assert summary["last_refresh"] == "12:00:00"


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


def test_api_and_sync_load_operator_bridge_from_user_environment(tmp_path) -> None:
    repo = tmp_path / "repo"
    (repo / ".local" / "forward" / "logs").mkdir(parents=True)
    script = ROOT / "scripts" / "xauusd_control_center.ps1"
    command = (
        f"$null = . '{script}' -Action CodeRevision -RuntimeRoot '{repo}' "
        f"-RepositoryRoot '{repo}'; "
        "function Get-UserEnvironmentValue { param($Name) return 'bridge-secret-from-user-environment-123456' }; "
        "$script:captured = @(); "
        "function Start-Process { param($FilePath,$ArgumentList,$WorkingDirectory,$WindowStyle,"
        "$RedirectStandardOutput,$RedirectStandardError); "
        "$script:captured += $env:DASHBOARD_OPERATOR_BRIDGE_TOKEN }; "
        "$api = [pscustomobject]@{ Key='api'; Kind='Python'; Script='api.py'; Arguments=@() }; "
        "$sync = [pscustomobject]@{ Key='sync'; Kind='Python'; Script='sync.py'; Arguments=@() }; "
        "Start-ForecasterService -Service $api -SkipExistingCheck; "
        "Start-ForecasterService -Service $sync -SkipExistingCheck; "
        "Write-Output ($script:captured -join ',')"
    )
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command],
        capture_output=True, text=True, check=True,
    ).stdout.strip()

    assert result == (
        "bridge-secret-from-user-environment-123456,"
        "bridge-secret-from-user-environment-123456"
    )
