from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import textwrap
import time

import pytest


ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(
    sys.platform != "win32", reason="Windows runtime ownership contract",
)
CONTROL_CENTER = ROOT / "scripts" / "xauusd_control_center.ps1"
POWERSHELLS = [
    shell for shell in ("powershell.exe", "pwsh.exe") if shutil.which(shell)
]
MUTABLE_SERVICE_FILES = {
    "quote": (),
    "collector": ("quotes", "collector-status.json"),
    "annotator": ("forward-evidence.sqlite3", "news-annotator-status.json"),
    "api": ("forward-evidence.sqlite3",),
    "sync": (
        "dashboard-sync.json",
        "dashboard-sync-status.json",
    ),
    "broadcast": (),
}


def _prepare_roots(tmp_path: Path) -> tuple[Path, Path]:
    runtime = tmp_path / "production runtime"
    repository = tmp_path / "candidate checkout"
    runtime.mkdir(exist_ok=True)
    (repository / "web").mkdir(parents=True, exist_ok=True)
    (repository / "web" / "worker-validation-manifest.json").write_bytes(
        (ROOT / "web" / "worker-validation-manifest.json").read_bytes()
    )
    return runtime, repository


def _run_control_contract(
    tmp_path: Path,
    body: str,
    *,
    powershell: str = "powershell.exe",
) -> str:
    runtime, repository = _prepare_roots(tmp_path)
    command = (
        f"$null = . '{CONTROL_CENTER}' -Action CodeRevision "
        f"-RuntimeRoot '{runtime}' -RepositoryRoot '{repository}'; {body}"
    )
    result = subprocess.run(
        [powershell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        raise AssertionError(
            f"{powershell} runtime-root contract failed\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return result.stdout.strip()


@pytest.mark.parametrize("powershell", POWERSHELLS)
def test_switch_observe_service_map_uses_one_runtime_state_authority(
    tmp_path: Path, powershell: str,
) -> None:
    runtime, repository = _prepare_roots(tmp_path)
    command = (
        f"$null = . '{CONTROL_CENTER}' -Action CodeRevision "
        f"-RuntimeRoot '{runtime}' -RepositoryRoot '{repository}'; "
        "$services | Select-Object Key,Kind,Script,Arguments | ConvertTo-Json -Depth 5"
    )
    result = subprocess.run(
        [powershell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command],
        capture_output=True,
        text=True,
        check=True,
    )
    services = {item["Key"]: item for item in json.loads(result.stdout)}
    state_root = runtime / ".local" / "forward"

    assert set(services) == set(MUTABLE_SERVICE_FILES)
    for key, expected_names in MUTABLE_SERVICE_FILES.items():
        arguments = [str(value) for value in services[key]["Arguments"]]
        state_flag = "-StateRoot" if key == "quote" else "--state-root"
        assert arguments[arguments.index(state_flag) + 1] == str(state_root)
        for name in expected_names:
            assert str(state_root / name) in arguments
        assert not any(str(repository / ".local" / "forward") in value for value in arguments)
    assert str(repository / ".local" / "config") in services["quote"]["Arguments"]


def _write_sync_rehearsal(code_root: Path, runtime_root: Path) -> Path:
    (code_root / "scripts").mkdir(parents=True)
    shutil.copy2(
        ROOT / "scripts" / "run_dashboard_sync.py",
        code_root / "scripts" / "run_dashboard_sync.py",
    )
    shutil.copytree(ROOT / "xauusd_forecaster", code_root / "xauusd_forecaster")
    state_root = runtime_root / ".local" / "forward"
    state_root.mkdir(parents=True, exist_ok=True)
    (state_root / "dashboard-sync.json").write_text("{}", encoding="utf-8")
    existing = state_root / "dashboard-news-sync-state.json"
    existing.write_text('{"generation":"preserved"}', encoding="utf-8")
    runner = code_root / "run-rehearsal.py"
    runner.write_text(
        textwrap.dedent(
            f"""
            import importlib.util
            import sys
            from pathlib import Path
            from xauusd_forecaster import runtime_paths

            script = {str(code_root / 'scripts' / 'run_dashboard_sync.py')!r}
            runtime_paths.PRODUCTION_RUNTIME_STATE_ROOT = Path({str(state_root)!r})
            spec = importlib.util.spec_from_file_location("runtime_sync", script)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            module.sync_with_retry = lambda config: (
                1, module.SyncResourceResults([], [])
            )
            sys.argv = [
                script,
                "--state-root", {str(state_root)!r},
                "--config", {str(state_root / 'dashboard-sync.json')!r},
                "--status-file", {str(state_root / 'dashboard-sync-status.json')!r},
                "--once",
            ]
            raise SystemExit(module.main())
            """
        ),
        encoding="utf-8",
    )
    result = subprocess.run(
        [sys.executable, str(runner)],
        cwd=code_root,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        raise AssertionError(f"sync rehearsal failed\n{result.stdout}\n{result.stderr}")
    assert json.loads(existing.read_text(encoding="utf-8")) == {
        "generation": "preserved"
    }
    return state_root / "dashboard-sync-status.json"


def test_real_sync_entrypoint_owns_heartbeat_under_distinct_runtime_root(
    tmp_path: Path,
) -> None:
    code_root = tmp_path / "isolated candidate checkout"
    runtime_root = tmp_path / "production runtime"
    status_path = _write_sync_rehearsal(code_root, runtime_root)

    status = json.loads(status_path.read_text(encoding="utf-8"))
    assert status["status"] == "OK"
    assert status["last_attempt"]
    assert not (code_root / ".local" / "forward").exists()


def test_code_checkout_movement_cannot_redirect_sync_state(tmp_path: Path) -> None:
    runtime_root = tmp_path / "production runtime"
    first = _write_sync_rehearsal(tmp_path / "candidate A", runtime_root)
    first_attempt = json.loads(first.read_text(encoding="utf-8"))["last_attempt"]
    second = _write_sync_rehearsal(tmp_path / "candidate B", runtime_root)
    second_attempt = json.loads(second.read_text(encoding="utf-8"))["last_attempt"]

    assert first == second
    assert second_attempt >= first_attempt
    assert not (tmp_path / "candidate A" / ".local").exists()
    assert not (tmp_path / "candidate B" / ".local").exists()


def test_sync_entrypoint_rejects_status_outside_runtime_authority(tmp_path: Path) -> None:
    state_root = tmp_path / "runtime" / ".local" / "forward"
    state_root.mkdir(parents=True)
    config = state_root / "dashboard-sync.json"
    config.write_text("{}", encoding="utf-8")
    outside = tmp_path / "candidate" / "dashboard-sync-status.json"
    probe = (
        "import runpy,sys;from pathlib import Path;"
        "from xauusd_forecaster import runtime_paths;"
        f"runtime_paths.PRODUCTION_RUNTIME_STATE_ROOT=Path({str(state_root)!r});"
        f"sys.argv=['run_dashboard_sync.py','--state-root',{str(state_root)!r},"
        f"'--config',{str(config)!r},'--status-file',{str(outside)!r},'--once'];"
        f"runpy.run_path({str(ROOT / 'scripts' / 'run_dashboard_sync.py')!r},run_name='__main__')"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert "must be one JSON file under" in result.stderr
    assert not outside.exists()


@pytest.mark.parametrize(
    ("service", "name"),
    [
        ("collector", "collector-status.json"),
        ("annotator", "news-annotator-status.json"),
        ("api", "forward-evidence.sqlite3"),
        ("broadcast", "live-broadcast-sequence.json"),
    ],
)
def test_fixed_runtime_children_reject_another_authority(
    tmp_path: Path, service: str, name: str,
) -> None:
    from xauusd_forecaster.runtime_paths import runtime_child_path

    state_root = tmp_path / "runtime" / ".local" / "forward"
    outside = tmp_path / "candidate" / name

    with pytest.raises(ValueError, match="runtime path must be"):
        runtime_child_path(state_root, outside, name=name)
    assert runtime_child_path(state_root, None, name=name) == state_root / name


def test_runtime_root_requires_exact_launcher_authority(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from xauusd_forecaster import runtime_paths

    authority = tmp_path / "runtime" / ".local" / "forward"
    monkeypatch.setattr(runtime_paths, "PRODUCTION_RUNTIME_STATE_ROOT", authority)
    assert runtime_paths.authoritative_runtime_root(authority) == authority
    with pytest.raises(ValueError, match="does not match contract authority"):
        runtime_paths.authoritative_runtime_root(
            tmp_path / "candidate" / ".local" / "forward"
        )


def test_quote_bridge_rejects_output_outside_runtime_authority(tmp_path: Path) -> None:
    state_root = (
        Path.home() / "XAUUSD-Forecaster-runtime" / ".local" / "forward"
    )
    outside = tmp_path / "candidate" / "quotes"
    launcher = (
        ROOT / "ctrader" / "XauusdForwardQuoteBridge" / "run_live_quote_bridge.ps1"
    )
    result = subprocess.run(
        [
            "powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
            "-File", str(launcher), "-StateRoot", str(state_root),
            "-OutputDirectory", str(outside),
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert "OutputDirectory must be" in result.stderr
    assert not outside.exists()


@pytest.mark.parametrize("powershell", POWERSHELLS)
def test_legacy_runtime_junction_migrates_forward_state_without_copying_config(
    tmp_path: Path, powershell: str,
) -> None:
    runtime, repository = _prepare_roots(tmp_path)
    source_local = repository / ".local"
    source_forward = source_local / "forward"
    source_forward.mkdir(parents=True)
    (source_forward / "runtime-code-state.json").write_text(
        '{"revision":"stable"}', encoding="utf-8",
    )
    (source_local / "config").mkdir()
    (source_local / "config" / "quote.json").write_text("{}", encoding="utf-8")
    runtime_local = runtime / ".local"
    setup = subprocess.run(
        [
            powershell,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            f"New-Item -ItemType Junction -Path '{runtime_local}' "
            f"-Target '{source_local}' | Out-Null",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if setup.returncode:
        pytest.skip(f"junction creation unavailable: {setup.stderr}")

    output = _run_control_contract(
        tmp_path,
        f"$result=Convert-LegacyRuntimeLocalJunction "
        f"-RuntimeLocal '{runtime_local}' -SourceLocal '{source_local}';"
        "$item=Get-Item -LiteralPath $result.state_root -Force;"
        'Write-Output "$($result.migrated),$([bool]($item.Attributes -band '
        '[System.IO.FileAttributes]::ReparsePoint))"',
        powershell=powershell,
    )

    assert output == "True,False"
    assert json.loads(
        (runtime_local / "forward" / "runtime-code-state.json").read_text(
            encoding="utf-8-sig"
        )
    ) == {"revision": "stable"}
    assert not source_forward.exists()
    assert (source_local / "config" / "quote.json").exists()
    assert not (runtime_local / "config").exists()


def test_unknown_runtime_state_link_target_fails_closed(tmp_path: Path) -> None:
    runtime, repository = _prepare_roots(tmp_path)
    wrong = tmp_path / "wrong authority"
    wrong.mkdir()
    runtime_local = runtime / ".local"
    subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            f"New-Item -ItemType Junction -Path '{runtime_local}' "
            f"-Target '{wrong}' | Out-Null",
        ],
        check=True,
    )
    command = (
        f"$null = . '{CONTROL_CENTER}' -Action CodeRevision "
        f"-RuntimeRoot '{runtime}' -RepositoryRoot '{repository}'; "
        f"Convert-LegacyRuntimeLocalJunction -RuntimeLocal '{runtime_local}' "
        f"-SourceLocal '{repository / '.local'}'"
    )
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert "does not target the authorized" in result.stderr
    assert runtime_local.is_dir()


@pytest.mark.parametrize("powershell", POWERSHELLS)
def test_controlled_state_migration_preserves_stable_code_and_restarts_services(
    tmp_path: Path, powershell: str,
) -> None:
    runtime, repository = _prepare_roots(tmp_path)
    source_local = repository / ".local"
    source_forward = source_local / "forward"
    source_forward.mkdir(parents=True)
    (source_forward / "release-control-state.json").write_text(
        '{"transaction":null}', encoding="utf-8",
    )
    runtime_local = runtime / ".local"
    setup = subprocess.run(
        [
            powershell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command",
            f"New-Item -ItemType Junction -Path '{runtime_local}' "
            f"-Target '{source_local}' | Out-Null",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if setup.returncode:
        pytest.skip(f"junction creation unavailable: {setup.stderr}")

    stable_revision = "a" * 40
    bundle_revision = "b" * 40
    body = (
        f"function Assert-ControlCenterProcessIdentity {{ [pscustomobject]@{{"
        f"source_revision='{bundle_revision}'}} }};"
        "function Get-ReleaseControlState { $null };"
        "function Get-VerifiedWatchdogOwners { [pscustomobject]@{process_id=41} };"
        "function Assert-CurrentWatchdogHeartbeat { [pscustomobject]@{} };"
        "$owned=[pscustomobject][ordered]@{};"
        "foreach($service in $services){$owned | Add-Member -NotePropertyName "
        "$service.Key -NotePropertyValue @([pscustomobject]@{process_id=1})};"
        f"function Get-ControlPlaneIsolationSnapshot {{ [pscustomobject]@{{"
        f"business_runtime_revision='{stable_revision}';services=$owned;"
        "release_state_hash=(Get-Sha256Hex -LiteralPath $releaseControlStatePath);"
        "release_history_hash=$null} };"
        "function Assert-ControlPlaneIsolationBaseline {};"
        "function Suspend-ControlPlaneSupervision { @{} };"
        "function Wait-ControlPlaneGuardQuiesced {};"
        "function Stop-VerifiedWatchdogOwner {};"
        "function Stop-ScheduledTask {};"
        f"foreach($service in $services){{$service.Revision='{stable_revision}';"
        f"$service.CodeRoot='{runtime}'}};"
        "$script:active=@{};foreach($service in $services){$script:active[$service.Key]=$true};"
        "function Stop-All {$script:active=@{}};"
        "function Stop-RuntimeProcessQuiescencePlan{param($Plan);$script:active=@{};$true};"
        "function Get-ForecasterProcesses {param($Service);"
        "if($script:active[$Service.Key]){@([pscustomobject]@{ProcessId=7})}else{@()}};"
        "function Get-ControlPlaneProcessIdentity {param($ProcessId);"
        "[pscustomobject]@{process_id=$ProcessId;"
        "process_start_token='2026-01-01T00:00:00.0000000+00:00'}};"
        "function Start-ForecasterService {param($Service,[switch]$SkipExistingCheck);"
        "$script:active[$Service.Key]=$true};"
        "function Test-CodeReloadHealth {$true};"
        f"function Get-CodeRevision {{'{stable_revision}'}};"
        "function Start-WatchdogReplacement {[pscustomobject]@{Id=88}};"
        "function Wait-VerifiedWatchdogHandoff {[pscustomobject]@{process_id=88}};"
        "function Restore-ControlPlaneSupervision {};"
        "$result=Invoke-RuntimeStateRootMigration;"
        "$item=Get-Item -LiteralPath $runtimeLocalRoot -Force;"
        "[pscustomobject]@{migrated=$result.migrated;"
        "preserved_revision=$result.preserved_revision;"
        "reparse=[bool]($item.Attributes -band "
        "[System.IO.FileAttributes]::ReparsePoint);"
        "started=@($script:active.Keys | Sort-Object)} | ConvertTo-Json -Depth 4"
    )
    output = _run_control_contract(tmp_path, body, powershell=powershell)
    evidence = json.loads(output)

    assert evidence == {
        "migrated": True,
        "preserved_revision": stable_revision,
        "reparse": False,
        "started": sorted(MUTABLE_SERVICE_FILES),
    }
    assert not source_forward.exists()
    assert (runtime_local / "forward" / "release-control-state.json").exists()
    update = json.loads(
        (runtime_local / "forward" / "runtime-update-state.json").read_text(
            encoding="utf-8-sig"
        )
    )
    assert update["preserved_revision"] == stable_revision


@pytest.mark.parametrize("powershell", POWERSHELLS)
def test_real_path_preflight_restores_stable_without_migrating_state(
    tmp_path: Path, powershell: str,
) -> None:
    runtime, repository = _prepare_roots(tmp_path)
    source_local = repository / ".local"
    source_forward = source_local / "forward"
    source_forward.mkdir(parents=True)
    state_file = source_forward / "state.json"
    state_file.write_text('{"owner":"stable"}', encoding="utf-8")
    runtime_local = runtime / ".local"
    setup = subprocess.run(
        [powershell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command",
         f"New-Item -ItemType Junction -Path '{runtime_local}' "
         f"-Target '{source_local}' | Out-Null"],
        capture_output=True, text=True, check=False,
    )
    if setup.returncode:
        pytest.skip(f"junction creation unavailable: {setup.stderr}")
    stable = "a" * 40
    body = (
        f"foreach($service in $services){{$service.Revision='{stable}';"
        f"$service.CodeRoot='{runtime}'}};"
        "$script:active=@{};foreach($service in $services){$script:active[$service.Key]=$true};"
        "$script:restored=$false;$script:recoveryHealthy=$false;"
        "function Assert-ControlCenterProcessIdentity{[pscustomobject]@{source_revision='" + "b" * 40 + "'}};"
        "function Get-ReleaseControlState{$null};"
        "function Get-VerifiedWatchdogOwners{[pscustomobject]@{process_id=41}};"
        "function Assert-CurrentWatchdogHeartbeat{[pscustomobject]@{}};"
        "$owned=[pscustomobject][ordered]@{};foreach($service in $services){"
        "$owned|Add-Member -NotePropertyName $service.Key -NotePropertyValue @([pscustomobject]@{process_id=1})};"
        f"function Get-ControlPlaneIsolationSnapshot{{[pscustomobject]@{{business_runtime_revision='{stable}';"
        "services=$owned;release_state_hash=$null;release_history_hash=$null}};"
        "function Assert-ControlPlaneIsolationBaseline{};"
        "function Suspend-ControlPlaneSupervision{@{}};function Wait-ControlPlaneGuardQuiesced{};"
        "function Stop-VerifiedWatchdogOwner{};function Stop-ScheduledTask{};"
        "function Stop-All{$script:active=@{}};"
        "function Stop-RuntimeProcessQuiescencePlan{param($Plan);$script:active=@{};$true};"
        "function Get-ForecasterProcesses{param($Service);if($script:active[$Service.Key])"
        "{@([pscustomobject]@{ProcessId=7;CommandLine=$Service.ScriptPath})}else{@()}};"
        "function Get-ControlPlaneProcessIdentity{param($ProcessId);"
        "[pscustomobject]@{process_id=$ProcessId;"
        "process_start_token='2026-01-01T00:00:00.0000000+00:00'}};"
        "function Restore-RuntimeRecoveryPlan{param($Plan);$script:restored=$true;$true};"
        "function Wait-RuntimeRecoveryPlanHealth{param($Plan,$RecoveryStarted);"
        "$script:recoveryHealthy=$true;[pscustomobject]@{revision=$Plan.body.stable_revision}};"
        "function Start-WatchdogReplacement{[pscustomobject]@{Id=88}};"
        "function Wait-VerifiedWatchdogHandoff{[pscustomobject]@{process_id=88}};"
        "function Restore-ControlPlaneSupervision{};"
        "$result=Invoke-RuntimeStateRootMigration -PreflightOnly;"
        "$item=Get-Item -LiteralPath $runtimeLocalRoot -Force;"
        "[pscustomobject]@{preflight=$result.preflight;restored=$script:restored;"
        "recovery_healthy=$script:recoveryHealthy;rename_capable=$result.rename_capable;"
        "reparse=[bool]($item.Attributes-band [System.IO.FileAttributes]::ReparsePoint);"
        "migration_receipt=Test-Path -LiteralPath $runtimeUpdateStatePath}|"
        "ConvertTo-Json -Compress"
    )
    evidence = json.loads(_run_control_contract(tmp_path, body, powershell=powershell))

    assert evidence == {
        "preflight": "PASSED",
        "restored": True,
        "recovery_healthy": True,
        "rename_capable": True,
        "reparse": True,
        "migration_receipt": False,
    }
    assert state_file.read_text(encoding="utf-8") == '{"owner":"stable"}'


@pytest.mark.parametrize("powershell", POWERSHELLS)
def test_external_holder_blocks_migration_before_supervision_or_service_stop(
    tmp_path: Path, powershell: str,
) -> None:
    runtime, repository = _prepare_roots(tmp_path)
    source_local = repository / ".local"
    source_forward = source_local / "forward"
    held = source_forward / "logs"
    held.mkdir(parents=True)
    runtime_local = runtime / ".local"
    setup = subprocess.run(
        [powershell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command",
         f"New-Item -ItemType Junction -Path '{runtime_local}' "
         f"-Target '{source_local}' | Out-Null"],
        capture_output=True, text=True, check=False,
    )
    if setup.returncode:
        pytest.skip(f"junction creation unavailable: {setup.stderr}")
    holder = _start_holder(held, "directory")
    stable = "a" * 40
    body = (
        f"foreach($service in $services){{$service.Revision='{stable}';"
        f"$service.CodeRoot='{runtime}'}};"
        "$script:suspended=$false;$script:stopped=$false;"
        "function Assert-ControlCenterProcessIdentity{[pscustomobject]@{source_revision='" + "b" * 40 + "'}};"
        "function Get-ReleaseControlState{$null};"
        "function Get-VerifiedWatchdogOwners{[pscustomobject]@{process_id=41}};"
        "function Assert-CurrentWatchdogHeartbeat{[pscustomobject]@{}};"
        f"function Get-ControlPlaneIsolationSnapshot{{[pscustomobject]@{{business_runtime_revision='{stable}';"
        "services=[pscustomobject][ordered]@{};release_state_hash=$null;release_history_hash=$null}};"
        "function Assert-ControlPlaneIsolationBaseline{};"
        "function Test-ControlPlaneServiceOwnerRequired{$false};"
        "function Suspend-ControlPlaneSupervision{$script:suspended=$true};"
        "function Stop-All{$script:stopped=$true};"
        "function Stop-RuntimeProcessQuiescencePlan{$script:stopped=$true;$true};"
        "function Get-ForecasterProcesses{@()};"
        "try{Invoke-RuntimeStateRootMigration|Out-Null}catch{$failure=$_.Exception.Message};"
        "[pscustomobject]@{failure=$failure;suspended=$script:suspended;"
        "stopped=$script:stopped}|ConvertTo-Json -Compress"
    )
    try:
        evidence = json.loads(
            _run_control_contract(tmp_path, body, powershell=powershell)
        )
    finally:
        _close_holder(holder)

    assert evidence["failure"].startswith("RUNTIME_STATE_EXTERNAL_HOLDER_ACTIVE:")
    assert f'"process_id":{holder.pid}' in evidence["failure"]
    assert evidence["suspended"] is False
    assert evidence["stopped"] is False


def test_release_entrypoints_fail_closed_while_state_root_migrates(
    tmp_path: Path,
) -> None:
    output = _run_control_contract(
        tmp_path,
        "New-Item -ItemType Directory -Path $runtimeStateMigrationLockPath | Out-Null;"
        "Write-Output (Enter-ReleaseTransactionLock)",
    )

    assert output == "False"


def test_state_only_migration_never_moves_the_runtime_checkout() -> None:
    source = CONTROL_CENTER.read_text(encoding="utf-8")
    start = source.index("function Invoke-RuntimeStateRootMigration")
    end = source.index("function Install-ProductionRuntime", start)
    migration = source[start:end]

    assert "git -C" not in migration
    assert '"git.exe"' not in migration
    assert "checkout --detach" not in migration.lower()
    assert "RUNTIME_STATE_MIGRATION_CHANGED_STABLE_REVISION" in migration


def test_switch_and_recovery_resolve_revision_owned_service_contracts() -> None:
    source = CONTROL_CENTER.read_text(encoding="utf-8")

    assert "Resolve-ServiceLaunchContracts -Revision $Revision" in source
    assert "Restore-RuntimeRecoveryPlan -Plan $recoveryPlan" in source
    assert "RUNTIME_ROLLBACK_CAPTURED_AUTHORITY_REQUIRED" in source
    assert "windows-service-launch-contract.json" in source
    assert "Get-LegacyStableServiceLaunchContracts" in source


@pytest.mark.parametrize("powershell", POWERSHELLS)
def test_new_controller_resolves_exact_old_stable_cli_contract(
    powershell: str,
) -> None:
    old_root = Path.home() / "XAUUSD-Forecaster-runtime"
    if not old_root.exists() or subprocess.run(
        ["git", "-C", str(old_root), "rev-parse", "HEAD"],
        capture_output=True, text=True, check=True,
    ).stdout.strip() != "783d25314b090dd7fbbf124777c3b8de517d2b85":
        pytest.skip("exact legacy Stable worktree is unavailable")
    command = (
        f"$null=. '{CONTROL_CENTER}' -Action CodeRevision "
        f"-RuntimeRoot '{old_root}' -RepositoryRoot '{ROOT}';"
        "$services|Select-Object Key,Revision,ScriptPath,Arguments|"
        "ConvertTo-Json -Depth 6"
    )
    result = subprocess.run(
        [powershell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command],
        capture_output=True, text=True, check=True,
    )
    services = {item["Key"]: item for item in json.loads(result.stdout)}

    assert set(services) == {"quote", "collector", "annotator", "api", "sync"}
    assert all(
        item["Revision"] == "783d25314b090dd7fbbf124777c3b8de517d2b85"
        for item in services.values()
    )
    assert "-OutputDirectory" in services["quote"]["Arguments"]
    assert "-StateRoot" not in services["quote"]["Arguments"]
    assert "--local-root" in services["collector"]["Arguments"]
    for key in ("annotator", "api", "sync"):
        assert "--state-root" not in services[key]["Arguments"]
    assert all(str(old_root) in item["ScriptPath"] for item in services.values())


@pytest.mark.parametrize("powershell", POWERSHELLS)
def test_legacy_quote_recovery_captures_external_config_authority(
    tmp_path: Path, powershell: str,
) -> None:
    runtime, _ = _prepare_roots(tmp_path)
    cli = tmp_path / "ctrader-cli.exe"
    secrets = tmp_path / "ctrader-secrets"
    body = (
        "function Get-UserEnvironmentValue{param($Name);"
        f"if($Name -eq 'CTRADER_CLI_PATH'){{'{cli}'}}"
        f"elseif($Name -eq 'CTRADER_SECRET_ROOT'){{'{secrets}'}}}};"
        "$legacy=@(Get-LegacyStableServiceLaunchContracts "
        "-Revision '783d25314b090dd7fbbf124777c3b8de517d2b85' "
        f"-CodeRoot '{runtime}');"
        "$legacy|Where-Object Key -eq 'quote'|Select-Object -ExpandProperty Arguments|"
        "ConvertTo-Json -Compress"
    )
    arguments = json.loads(_run_control_contract(tmp_path, body, powershell=powershell))

    assert arguments[arguments.index("-CliPath") + 1] == str(cli)
    assert arguments[arguments.index("-SecretRoot") + 1] == str(secrets)


def test_running_legacy_quote_without_captured_config_authority_fails_before_stop(
    tmp_path: Path,
) -> None:
    runtime, _ = _prepare_roots(tmp_path)
    stable = "783d25314b090dd7fbbf124777c3b8de517d2b85"
    body = (
        "$quote=[pscustomobject]@{"
        f"Revision='{stable}';CodeRoot='{runtime}';Key='quote';Label='Quote';"
        "Match='run_live_quote_bridge.ps1';Kind='PowerShell';"
        "Script='ctrader\\XauusdForwardQuoteBridge\\run_live_quote_bridge.ps1';"
        f"ScriptPath='{runtime / 'ctrader' / 'run_live_quote_bridge.ps1'}';"
        f"Arguments=@('-OutputDirectory','{runtime / '.local' / 'forward' / 'quotes'}')}};"
        "function Get-ForecasterProcesses{@([pscustomobject]@{ProcessId=7})};"
        "function Get-ControlPlaneProcessIdentity{[pscustomobject]@{process_id=7;"
        "process_start_token='2026-01-01T00:00:00.0000000+00:00'}};"
        f"try{{$null=New-RuntimeRecoveryPlan -StableRevision '{stable}' "
        "-ServiceContracts @($quote)}catch{Write-Output $_.Exception.Message}"
    )

    assert _run_control_contract(tmp_path, body) == (
        "RUNTIME_RECOVERY_QUOTE_AUTHORITY_UNAVAILABLE"
    )


def test_migration_has_bounded_native_handle_probe_and_all_failure_phases() -> None:
    source = CONTROL_CENTER.read_text(encoding="utf-8")
    phases = {
        "BEFORE_WATCHDOG_SUSPENSION", "AFTER_WATCHDOG_SUSPENSION",
        "AFTER_STOP_ALL", "AFTER_STATE_STAGED", "AFTER_JUNCTION_REMOVAL",
        "AFTER_RUNTIME_ROOT_CREATION", "DURING_STABLE_RESTART",
        "AFTER_PARTIAL_RESTART", "BEFORE_WATCHDOG_HANDOFF",
        "DURING_HEALTH_VERIFICATION",
    }
    assert all(phase in source for phase in phases)
    assert "XauusdRuntimeStateNativeProbe" in source
    assert "Get-RuntimeStateHolderInventory" in source
    assert "Stop-RuntimeProcessQuiescencePlan" in source
    assert ".WaitForExit($remaining)" in source
    assert "Directory]::Move($StateTree, $probePath)" in source
    assert "RUNTIME_STATE_EXTERNAL_HOLDER_ACTIVE" in source
    assert "RUNTIME_STATE_PROCESS_CWD_ACTIVE" in source
    assert "RUNTIME_STATE_DIRECTORY_HANDLE_ACTIVE" in source
    assert "RUNTIME_STATE_FILE_HANDLE_ACTIVE" in source
    assert "RUNTIME_STATE_PERMISSION_DENIED" in source
    assert "RUNTIME_STATE_QUIESCENCE_TIMEOUT" in source


_HOLDER_CODE = r"""
import ctypes
import os
import sys
import time

mode, path, delay = sys.argv[1], sys.argv[2], float(sys.argv[3])
handle = None
if mode == "cwd":
    os.chdir(path)
else:
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    create = kernel.CreateFileW
    create.argtypes = [
        ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32,
        ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p,
    ]
    create.restype = ctypes.c_void_p
    flags = 0x02000000 if mode == "directory" else 0
    handle = create(path, 0x80000000, 3, None, 3, flags, None)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
print("READY", flush=True)
time.sleep(delay)
if handle is not None:
    kernel.CloseHandle(ctypes.c_void_p(handle))
"""


def _start_holder(path: Path, mode: str, delay: float = 30) -> subprocess.Popen[str]:
    process = subprocess.Popen(
        [sys.executable, "-c", _HOLDER_CODE, mode, str(path), str(delay)],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert process.stdout is not None
    assert process.stdout.readline().strip() == "READY"
    return process


def _close_holder(process: subprocess.Popen[str]) -> None:
    if process.poll() is None:
        process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


@pytest.mark.parametrize("powershell", POWERSHELLS)
def test_quiescence_plan_never_claims_same_named_process_from_another_code_root(
    tmp_path: Path, powershell: str,
) -> None:
    actual = tmp_path / "actual" / "run_dashboard_api.py"
    expected = tmp_path / "expected" / "run_dashboard_api.py"
    actual.parent.mkdir()
    expected.parent.mkdir()
    actual.write_text("import time; time.sleep(30)\n", encoding="utf-8")
    expected.write_text("# revision-owned expected script\n", encoding="utf-8")
    process = subprocess.Popen(
        [sys.executable, str(actual)], cwd=actual.parent,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    try:
        body = (
            "$service=[pscustomobject]@{Key='api';Kind='Python';"
            "Match='run_dashboard_api.py';"
            f"ScriptPath='{expected}';CodeRoot='{expected.parent}'}};"
            "$plan=New-RuntimeProcessQuiescencePlan -ServiceContracts @($service);"
            "@($plan.entries).Count"
        )
        assert _run_control_contract(tmp_path, body, powershell=powershell) == "0"
        assert process.poll() is None
    finally:
        _close_holder(process)


@pytest.mark.parametrize("powershell", POWERSHELLS)
def test_quiescence_plan_captures_children_and_waits_for_real_exit_handles(
    tmp_path: Path, powershell: str,
) -> None:
    state = tmp_path / "child-process-state"
    state.mkdir()
    held = state / "state.json"
    held.write_text('{"owner":"stable"}', encoding="utf-8")
    script = tmp_path / "owner" / "run_forward_collector.py"
    script.parent.mkdir()
    child_code = (
        "import ctypes,sys,time;"
        "k=ctypes.WinDLL('kernel32',use_last_error=True);"
        "k.CreateFileW.restype=ctypes.c_void_p;"
        "h=k.CreateFileW(sys.argv[1],0x80000000,3,None,3,0,None);"
        "print('CHILD_READY',flush=True);time.sleep(30)"
    )
    script.write_text(
        "import subprocess,sys,time\n"
        f"child=subprocess.Popen([sys.executable,'-c',{child_code!r},sys.argv[1]], "
        "creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))\n"
        "print(child.pid,flush=True)\n"
        "time.sleep(30)\n",
        encoding="utf-8",
    )
    owner = subprocess.Popen(
        [sys.executable, str(script), str(held)], cwd=script.parent,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert owner.stdout is not None
    child_pid = int(owner.stdout.readline().strip())
    time.sleep(0.2)
    try:
        body = (
            "$service=[pscustomobject]@{Key='collector';Kind='Python';"
            "Match='run_forward_collector.py';"
            f"ScriptPath='{script}';CodeRoot='{script.parent}'}};"
            "$plan=New-RuntimeProcessQuiescencePlan -ServiceContracts @($service);"
            "$captured=@($plan.entries.process_id);"
            "$null=Stop-RuntimeProcessQuiescencePlan -Plan $plan;"
            "[pscustomobject]@{captured=$captured;active=@($plan.entries|Where-Object{"
            "Get-ControlPlaneProcessIdentity -ProcessId ([int]$_.process_id)}).Count}|"
            "ConvertTo-Json -Compress"
        )
        evidence = json.loads(
            _run_control_contract(tmp_path, body, powershell=powershell)
        )
    finally:
        _close_holder(owner)
        subprocess.run(
            ["taskkill.exe", "/PID", str(child_pid), "/T", "/F"],
            capture_output=True, text=True, check=False,
        )

    assert set(evidence["captured"]) >= {owner.pid, child_pid}
    assert evidence["active"] == 0


def _quiescence_result(
    tmp_path: Path,
    state: Path,
    process_ids: list[int],
    powershell: str,
    timeout_ms: int = 700,
) -> str:
    ids = ",".join(str(value) for value in process_ids)
    body = (
        f"try{{$result=Wait-RuntimeStateTreeQuiesced -StateTree '{state}' "
        f"-ControlledProcessIds @({ids}) -Timeout "
        f"([TimeSpan]::FromMilliseconds({timeout_ms}));"
        "if($result.quiesced){'PASSED'}else{'INVALID'}}"
        "catch{$_.Exception.Message}"
    )
    return _run_control_contract(tmp_path, body, powershell=powershell)


@pytest.mark.parametrize("powershell", POWERSHELLS)
@pytest.mark.parametrize(
    ("mode", "expected"),
    [
        ("file", "RUNTIME_STATE_FILE_HANDLE_ACTIVE"),
        ("directory", "RUNTIME_STATE_DIRECTORY_HANDLE_ACTIVE"),
        ("cwd", "RUNTIME_STATE_PROCESS_CWD_ACTIVE"),
    ],
)
def test_native_quiescence_classifies_real_windows_holder_family(
    tmp_path: Path, powershell: str, mode: str, expected: str,
) -> None:
    state = tmp_path / f"state-{mode}"
    state.mkdir()
    sentinel = state / "sentinel.json"
    sentinel.write_text('{"owner":"stable"}', encoding="utf-8")
    held = state if mode == "cwd" else (
        state / "child" if mode == "directory" else sentinel
    )
    if mode == "directory":
        held.mkdir()
    process = _start_holder(held, mode)
    try:
        result = _quiescence_result(
            tmp_path, state, [process.pid], powershell,
        )
    finally:
        _close_holder(process)

    assert result.startswith(expected + ":")
    assert f'"process_id":{process.pid}' in result
    assert sentinel.read_text(encoding="utf-8") == '{"owner":"stable"}'
    assert not Path(f"{state}.quiescence-probe").exists()


@pytest.mark.parametrize("powershell", POWERSHELLS)
def test_external_directory_holder_fails_before_controlled_shutdown(
    tmp_path: Path, powershell: str,
) -> None:
    state = tmp_path / "external-holder-state"
    held = state / "logs"
    held.mkdir(parents=True)
    process = _start_holder(held, "directory")
    try:
        result = _quiescence_result(tmp_path, state, [], powershell)
    finally:
        _close_holder(process)

    assert result.startswith("RUNTIME_STATE_EXTERNAL_HOLDER_ACTIVE:")
    assert f'"process_id":{process.pid}' in result
    assert state.is_dir()


@pytest.mark.parametrize("powershell", POWERSHELLS)
def test_multiple_holders_are_reported_without_state_mutation(
    tmp_path: Path, powershell: str,
) -> None:
    state = tmp_path / "multiple-holder-state"
    held_directory = state / "logs"
    held_directory.mkdir(parents=True)
    held_file = state / "state.json"
    held_file.write_text('{"owner":"stable"}', encoding="utf-8")
    processes = [
        _start_holder(held_file, "file"),
        _start_holder(held_directory, "directory"),
    ]
    try:
        result = _quiescence_result(
            tmp_path, state, [process.pid for process in processes], powershell,
        )
    finally:
        for process in processes:
            _close_holder(process)

    assert result.startswith("RUNTIME_STATE_DIRECTORY_HANDLE_ACTIVE:")
    assert '"holder_count":2' in result
    assert held_file.read_text(encoding="utf-8") == '{"owner":"stable"}'


@pytest.mark.parametrize("powershell", POWERSHELLS)
def test_delayed_controlled_handle_release_allows_bounded_quiescence(
    tmp_path: Path, powershell: str,
) -> None:
    state = tmp_path / "delayed-release-state"
    state.mkdir()
    held_file = state / "state.json"
    held_file.write_text('{"owner":"stable"}', encoding="utf-8")
    process = _start_holder(held_file, "file", delay=0.6)

    started = time.monotonic()
    result = _quiescence_result(
        tmp_path, state, [process.pid], powershell, timeout_ms=5000,
    )
    process.wait(timeout=5)

    assert result == "PASSED"
    assert time.monotonic() - started < 8
    assert held_file.read_text(encoding="utf-8") == '{"owner":"stable"}'


@pytest.mark.parametrize("powershell", POWERSHELLS)
def test_clean_real_directory_is_rename_capable_and_preserved(
    tmp_path: Path, powershell: str,
) -> None:
    state = tmp_path / "clean-state"
    state.mkdir()
    sentinel = state / "state.json"
    sentinel.write_text('{"owner":"stable"}', encoding="utf-8")

    assert _quiescence_result(tmp_path, state, [], powershell) == "PASSED"
    assert sentinel.read_text(encoding="utf-8") == '{"owner":"stable"}'
    assert not Path(f"{state}.quiescence-probe").exists()


@pytest.mark.parametrize("powershell", POWERSHELLS)
def test_acl_without_delete_right_fails_with_permission_diagnostic(
    tmp_path: Path, powershell: str,
) -> None:
    parent = tmp_path / "permission-parent"
    state = parent / "permission-state"
    state.mkdir(parents=True)
    sentinel = state / "state.json"
    sentinel.write_text('{"owner":"stable"}', encoding="utf-8")
    identity = f"{os.environ['COMPUTERNAME']}\\{os.environ['USERNAME']}"
    deny_parent = subprocess.run(
        ["icacls.exe", str(parent), "/deny", f"{identity}:(DC)"],
        capture_output=True, text=True, check=False,
    )
    deny_state = subprocess.run(
        ["icacls.exe", str(state), "/deny", f"{identity}:(D)"],
        capture_output=True, text=True, check=False,
    )
    if deny_parent.returncode or deny_state.returncode:
        pytest.skip("test ACL denial could not be installed")
    try:
        body = (
            f"try{{$null=Assert-RuntimeStatePermissions -StateTree '{state}';'INVALID'}}"
            "catch{$_.Exception.Message}"
        )
        result = _run_control_contract(tmp_path, body, powershell=powershell)
    finally:
        subprocess.run(
            ["icacls.exe", str(state), "/remove:d", identity],
            capture_output=True, text=True, check=False,
        )
        subprocess.run(
            ["icacls.exe", str(parent), "/remove:d", identity],
            capture_output=True, text=True, check=False,
        )

    assert result.startswith("RUNTIME_STATE_PERMISSION_DENIED:")
    assert sentinel.read_text(encoding="utf-8") == '{"owner":"stable"}'


@pytest.mark.parametrize("powershell", POWERSHELLS)
def test_recovery_plan_survives_release_state_json_round_trip(
    tmp_path: Path, powershell: str,
) -> None:
    runtime, _ = _prepare_roots(tmp_path)
    state = runtime / ".local" / "forward"
    script = runtime / "scripts" / "run_dashboard_api.py"
    stable = "a" * 40
    body = (
        "$body=[ordered]@{schema='runtime-recovery-plan-v1';"
        f"stable_revision='{stable}';stable_worker_version='worker';"
        f"runtime_root='{runtime}';runtime_state_root='{state}';config_root='config';"
        "running_service_keys=@('api');process_baseline=[ordered]@{"
        "api=@([ordered]@{process_id=7;process_start_token='2026-01-01T00:00:00.0000000+00:00'})};"
        "service_contracts=@([ordered]@{"
        f"revision='{stable}';code_root='{runtime}';key='api';label='Dashboard API';"
        f"match='run_dashboard_api.py';kind='Python';script='scripts\\run_dashboard_api.py';"
        f"script_path='{script}';arguments=@('--state-root','{state}')"
        f"}});rollback_target='{stable}'}};"
        "$canonical=$body|ConvertTo-Json -Depth 9 -Compress;"
        "$plan=[pscustomobject]@{body=[pscustomobject]$body;"
        "digest=Get-Sha256BytesHex -Bytes ([Text.Encoding]::UTF8.GetBytes($canonical))};"
        "$roundTrip=($plan|ConvertTo-Json -Depth 9 -Compress)|ConvertFrom-ReleaseControlJson;"
        "$null=Assert-RuntimeRecoveryPlan -Plan $roundTrip;"
        "$contracts=@(Convert-RecoveryPlanContracts -Plan $roundTrip);"
        "[pscustomobject]@{digest=$roundTrip.digest;count=$contracts.Count;"
        "key=$contracts[0].Key;argument_count=@($contracts[0].Arguments).Count}|"
        "ConvertTo-Json -Compress"
    )
    evidence = json.loads(_run_control_contract(tmp_path, body, powershell=powershell))

    assert len(evidence["digest"]) == 64
    assert evidence["count"] == 1
    assert evidence["key"] == "api"
    assert evidence["argument_count"] == 2


MIGRATION_FAILURE_PHASES = (
    "BEFORE_WATCHDOG_SUSPENSION", "AFTER_WATCHDOG_SUSPENSION",
    "AFTER_STOP_ALL", "AFTER_STATE_STAGED", "AFTER_JUNCTION_REMOVAL",
    "AFTER_RUNTIME_ROOT_CREATION", "DURING_STABLE_RESTART",
    "AFTER_PARTIAL_RESTART", "BEFORE_WATCHDOG_HANDOFF",
    "DURING_HEALTH_VERIFICATION",
)


@pytest.mark.parametrize("phase", MIGRATION_FAILURE_PHASES)
def test_every_migration_failure_phase_retains_one_state_authority_and_recovers(
    tmp_path: Path, phase: str,
) -> None:
    runtime, repository = _prepare_roots(tmp_path)
    source_local = repository / ".local"
    source_forward = source_local / "forward"
    source_forward.mkdir(parents=True)
    (source_forward / "state.json").write_text('{"owner":"stable"}', encoding="utf-8")
    runtime_local = runtime / ".local"
    setup = subprocess.run(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command",
         f"New-Item -ItemType Junction -Path '{runtime_local}' "
         f"-Target '{source_local}' | Out-Null"],
        capture_output=True, text=True, check=False,
    )
    if setup.returncode:
        pytest.skip(f"junction creation unavailable: {setup.stderr}")
    stable = "a" * 40
    body = (
        f"foreach($service in $services){{$service.Revision='{stable}';"
        f"$service.CodeRoot='{runtime}'}};"
        "$script:active=@{};foreach($service in $services){$script:active[$service.Key]=$true};"
        "$script:restored=$false;$script:recoveryHealthy=$false;$script:watchdog=$false;"
        "function Assert-ControlCenterProcessIdentity{[pscustomobject]@{source_revision='" + "b" * 40 + "'}};"
        "function Get-ReleaseControlState{$null};"
        "function Get-VerifiedWatchdogOwners{[pscustomobject]@{process_id=41}};"
        "function Assert-CurrentWatchdogHeartbeat{[pscustomobject]@{}};"
        "$owned=[pscustomobject][ordered]@{};foreach($service in $services){"
        "$owned|Add-Member -NotePropertyName $service.Key -NotePropertyValue @([pscustomobject]@{process_id=1})};"
        f"function Get-ControlPlaneIsolationSnapshot{{[pscustomobject]@{{business_runtime_revision='{stable}';"
        "services=$owned;release_state_hash=$null;release_history_hash=$null}};"
        "function Assert-ControlPlaneIsolationBaseline{};function Suspend-ControlPlaneSupervision{@{}};"
        "function Wait-ControlPlaneGuardQuiesced{};function Stop-VerifiedWatchdogOwner{};"
        "function Stop-ScheduledTask{};function Stop-All{$script:active=@{}};"
        "function Stop-RuntimeProcessQuiescencePlan{param($Plan);$script:active=@{};$true};"
        "function Get-ForecasterProcesses{param($Service);if($script:active[$Service.Key])"
        "{@([pscustomobject]@{ProcessId=7;CommandLine=$Service.ScriptPath})}else{@()}};"
        "function Get-ControlPlaneProcessIdentity{param($ProcessId);"
        "[pscustomobject]@{process_id=$ProcessId;"
        "process_start_token='2026-01-01T00:00:00.0000000+00:00'}};"
        "function Start-ForecasterService{param($Service,[switch]$SkipExistingCheck);"
        "$script:active[$Service.Key]=$true};function Test-CodeReloadHealth{$true};"
        f"function Get-CodeRevision{{'{stable}'}};"
        "function Restore-RuntimeRecoveryPlan{param($Plan);$script:restored=$true;$true};"
        "function Wait-RuntimeRecoveryPlanHealth{param($Plan,$RecoveryStarted);"
        "$script:recoveryHealthy=$true;[pscustomobject]@{revision=$Plan.body.stable_revision}};"
        "function Start-WatchdogReplacement{$script:watchdog=$true;[pscustomobject]@{Id=88}};"
        "function Wait-VerifiedWatchdogHandoff{[pscustomobject]@{process_id=88}};"
        "function Restore-ControlPlaneSupervision{};"
        f"try{{Invoke-RuntimeStateRootMigration -FailurePhase '{phase}'|Out-Null}}catch{{$failureText=$_.Exception.Message}};"
        "$runtimeItem=Get-Item -LiteralPath $runtimeLocalRoot -Force;"
        "$stateInRuntime=Test-Path -LiteralPath (Join-Path $runtimeLocalRoot 'forward\\state.json');"
        "$stateInSource=Test-Path -LiteralPath (Join-Path $repositoryLocalRoot 'forward\\state.json');"
        "[pscustomobject]@{error=$failureText;restored=$script:restored;"
        "recovery_healthy=$script:recoveryHealthy;watchdog=$script:watchdog;"
        "state_count=@($stateInRuntime,$stateInSource|Where-Object{$_}).Count;"
        "migration_lock=Test-Path -LiteralPath $runtimeStateMigrationLockPath;"
        "is_reparse=[bool]($runtimeItem.Attributes-band [System.IO.FileAttributes]::ReparsePoint)}|"
        "ConvertTo-Json -Compress"
    )
    evidence = json.loads(_run_control_contract(tmp_path, body))

    assert phase in evidence["error"]
    assert evidence["state_count"] == (2 if evidence["is_reparse"] else 1)
    assert evidence["migration_lock"] is False
    if phase in {"BEFORE_WATCHDOG_SUSPENSION", "AFTER_WATCHDOG_SUSPENSION"}:
        assert evidence["restored"] is False
        assert evidence["recovery_healthy"] is False
    else:
        assert evidence["restored"] is True
        assert evidence["recovery_healthy"] is True
