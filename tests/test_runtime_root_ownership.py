from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
import sys
import textwrap

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
        "$script:active=@{};"
        "function Stop-All {$script:active=@{}};"
        "function Get-ForecasterProcesses {param($Service);"
        "if($script:active[$Service.Key]){@([pscustomobject]@{ProcessId=7})}else{@()}};"
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


def test_watchdog_promote_and_rollback_share_the_global_service_contract() -> None:
    source = CONTROL_CENTER.read_text(encoding="utf-8")

    assert "Start-ForecasterService $service -SkipExistingCheck" in source
    assert "Restart-CodeReloadableServices -Revision $Revision" in source
    assert "$services | Where-Object" in source
    assert source.count("$services = @(") == 1
