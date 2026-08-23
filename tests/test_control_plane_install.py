from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import textwrap

import pytest


pytestmark = pytest.mark.skipif(
    shutil.which("powershell.exe") is None,
    reason="Windows PowerShell is required for Control Plane contracts",
)


ROOT = Path(__file__).resolve().parents[1]
CONTROL_FILES = (
    "xauusd_control_center.ps1",
    "control_center.xaml",
    "xauusd_control_center_launcher.vbs",
    "xauusd_watchdog_launcher.vbs",
    "xauusd_watchdog_guard.ps1",
    "xauusd_watchdog_guard_launcher.vbs",
)


def _run_contract(tmp_path: Path, body: str) -> str:
    runtime = tmp_path / "runtime"
    repository = tmp_path / "repository"
    runtime.mkdir(exist_ok=True)
    repository.mkdir(exist_ok=True)
    script = ROOT / "scripts" / "xauusd_control_center.ps1"
    command = (
        f"$null = . '{script}' -Action CodeRevision -RuntimeRoot '{runtime}' "
        f"-RepositoryRoot '{repository}'; {body}"
    )
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        raise AssertionError(
            "PowerShell control-plane contract failed\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return result.stdout.strip()


def _write_bundle(root: Path, revision: str, label: str) -> None:
    root.mkdir(parents=True, exist_ok=True)
    hashes: dict[str, str] = {}
    for name in CONTROL_FILES:
        payload = f"{label}|{name}\n".encode()
        (root / name).write_bytes(payload)
        hashes[name] = hashlib.sha256(payload).hexdigest()
    (root / "runtime-control-bundle.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "source_revision": revision,
                "exact_revision": True,
                "created_at": "2026-08-23T00:00:00+00:00",
                "files": hashes,
            }
        ),
        encoding="utf-8",
    )


def _make_detached_source(root: Path) -> str:
    scripts = root / "scripts"
    scripts.mkdir(parents=True)
    for name in CONTROL_FILES:
        (scripts / name).write_text(f"committed|{name}\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "Contract Test"], cwd=root, check=True)
    subprocess.run(
        ["git", "config", "user.email", "contract-test@example.invalid"],
        cwd=root,
        check=True,
    )
    subprocess.run(["git", "add", "scripts"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "immutable bundle"], cwd=root, check=True)
    revision = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=root,
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    subprocess.run(["git", "checkout", "--detach", "-q", revision], cwd=root, check=True)
    return revision


def _identity(pid: int, token: str) -> str:
    return (
        f"[pscustomobject]@{{process_id={pid};parent_process_id={pid + 1};"
        f"process_start_token='{token}';launcher_identity=[pscustomobject]@{{"
        f"process_id={pid + 1};process_start_token='{token}-launcher'}}}}"
    )


def _state_machine_mocks(old_revision: str, target_revision: str) -> str:
    old = _identity(100, "old-token")
    new = _identity(200, "new-token")
    return textwrap.dedent(
        f"""
        $script:timeline=@(); $script:owners=@({old});
        function Get-RuntimeControlBundleIdentityAtRoot {{ param($ControlRoot); [pscustomobject]@{{source_revision='{old_revision}';exact_revision=$true}} }};
        function Get-VerifiedControlCenterGuiOwners {{ @() }};
        function Get-ReleaseControlState {{ $null }};
        function Enter-ReleaseTransactionLock {{ $script:timeline+='lock'; return $true }};
        function Exit-ReleaseTransactionLock {{ $script:timeline+='unlock' }};
        function Get-VerifiedWatchdogOwners {{ @($script:owners) }};
        function Assert-CurrentWatchdogHeartbeat {{ param($Owner,$ExpectedRevision); [pscustomobject]@{{process_id=$Owner.process_id;control_bundle_revision=$ExpectedRevision}} }};
        function Get-ControlPlaneIsolationSnapshot {{
          $p=[pscustomobject]@{{process_id=10;process_start_token='service-token'}};
          [pscustomobject]@{{business_runtime_revision='runtime';services=[pscustomobject]@{{quote=@($p);collector=@($p);annotator=@($p);api=@($p);sync=@($p)}}}}
        }};
        function Assert-ControlPlaneIsolationSnapshot {{ param($Before,$After); $script:timeline+='isolation' }};
        function New-VerifiedRuntimeControlBundleStage {{ param($SourceRoot,$SourceRevision,$StageRoot,[switch]$RequireImmutableSource); $script:timeline+='stage'; [pscustomobject]@{{source_revision='{target_revision}'}} }};
        function Suspend-ControlPlaneSupervision {{ $script:timeline+='suspend'; @{{}} }};
        function Wait-ControlPlaneGuardQuiesced {{ $script:timeline+='guard' }};
        function Restore-ControlPlaneSupervision {{ param($State); $script:timeline+='supervision' }};
        function Stop-VerifiedWatchdogOwner {{ param($Identity); $script:timeline+='stop'; $script:owners=@() }};
        function Install-VerifiedRuntimeControlBundleStage {{ param($StageRoot,$ControlRoot,$BackupRoot); if($script:owners.Count-ne 0){{throw 'two owners'}}; $script:timeline+='install'; [pscustomobject]@{{source_revision='{target_revision}'}} }};
        function Start-WatchdogReplacement {{ param([switch]$PassThru); if($script:owners.Count-ne 0){{throw 'two owners'}}; $script:timeline+='start'; $script:owners=@({new}); [pscustomobject]@{{Id=201}} }};
        function Wait-VerifiedWatchdogHandoff {{ param($ExpectedRevision,$PreviousIdentity,$Timeout); if($script:owners.Count-ne 1){{throw 'owner count'}}; $script:timeline+='heartbeat'; return $script:owners[0] }};
        """
    ).replace("\n", " ")


def test_repository_entrypoint_bootstraps_from_exact_origin_main_worktree() -> None:
    installer = (ROOT / "scripts" / "install_control_plane.ps1").read_text(encoding="utf-8")
    assert "fetch origin main" in installer
    assert "merge-base --is-ancestor" in installer
    assert "CONTROL_PLANE_TARGET_MUST_EQUAL_ORIGIN_MAIN" in installer
    assert "worktree add --detach" in installer
    assert "-File $controlScript -Action InstallControlPlane" in installer
    assert "-SourceRoot $temporaryRoot -SourceRevision $Revision" in installer
    assert ".local\\runtime-control" not in installer
    assert "InstallRuntime" not in installer


def test_repository_entrypoint_ignores_old_bundle_and_dirty_checkout(tmp_path: Path) -> None:
    origin = tmp_path / "origin.git"
    checkout = tmp_path / "checkout"
    runtime = tmp_path / "runtime"
    subprocess.run(["git", "init", "--bare", "-q", origin], check=True)
    subprocess.run(["git", "init", "-q", checkout], check=True)
    subprocess.run(["git", "config", "user.name", "Contract Test"], cwd=checkout, check=True)
    subprocess.run(
        ["git", "config", "user.email", "contract-test@example.invalid"],
        cwd=checkout,
        check=True,
    )
    scripts = checkout / "scripts"
    scripts.mkdir()
    (scripts / "payload.txt").write_text("committed\n", encoding="utf-8")
    (scripts / "xauusd_control_center.ps1").write_text(
        textwrap.dedent(
            """
            param($Action,$RuntimeRoot,$RepositoryRoot,$SourceRoot,$SourceRevision)
            $payload=(Get-Content -LiteralPath (Join-Path $SourceRoot 'scripts\\payload.txt') -Raw).Trim()
            [pscustomobject]@{action=$Action;revision=$SourceRevision;payload=$payload;source_root=$SourceRoot} |
              ConvertTo-Json | Set-Content -LiteralPath (Join-Path $RepositoryRoot 'bootstrap-result.json')
            """
        ),
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "scripts"], cwd=checkout, check=True)
    subprocess.run(["git", "commit", "-qm", "bootstrap target"], cwd=checkout, check=True)
    subprocess.run(["git", "branch", "-M", "main"], cwd=checkout, check=True)
    subprocess.run(["git", "remote", "add", "origin", str(origin)], cwd=checkout, check=True)
    subprocess.run(["git", "push", "-qu", "origin", "main"], cwd=checkout, check=True)
    revision = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=checkout,
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    (scripts / "payload.txt").write_text("dirty\n", encoding="utf-8")
    old_control = checkout / ".local" / "runtime-control"
    old_control.mkdir(parents=True)
    (old_control / "xauusd_control_center.ps1").write_text(
        "throw 'old installed controller was called'\n", encoding="utf-8"
    )
    installer = ROOT / "scripts" / "install_control_plane.ps1"
    command = (
        f". '{installer}' -TargetRevision '{revision}' -RuntimeRoot '{runtime}' "
        f"-RepositoryRoot '{checkout}'; "
        f"Invoke-ExactControlPlaneInstaller -CheckoutRoot '{checkout}' "
        f"-RuntimePath '{runtime}' -Revision '{revision}'"
    )
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr
    evidence = json.loads((checkout / "bootstrap-result.json").read_text(encoding="utf-8-sig"))
    assert evidence["action"] == "InstallControlPlane"
    assert evidence["revision"] == revision
    assert evidence["payload"] == "committed"
    assert not Path(evidence["source_root"]).exists()


def test_immutable_stage_requires_exact_clean_detached_source(tmp_path: Path) -> None:
    source = tmp_path / "source"
    revision = _make_detached_source(source)
    stage = tmp_path / "stage"
    result = _run_contract(
        tmp_path,
        f"$bundle=New-VerifiedRuntimeControlBundleStage -SourceRoot '{source}' "
        f"-SourceRevision '{revision}' -StageRoot '{stage}' -RequireImmutableSource; "
        '$count=@($bundle.files.PSObject.Properties).Count; '
        'Write-Output "$($bundle.source_revision),$count"',
    )
    assert result == f"{revision},{len(CONTROL_FILES)}"

    (source / "scripts" / CONTROL_FILES[0]).write_text("dirty\n", encoding="utf-8")
    rejected = _run_contract(
        tmp_path,
        f"try {{ New-VerifiedRuntimeControlBundleStage -SourceRoot '{source}' "
        f"-SourceRevision '{revision}' -StageRoot '{tmp_path / 'dirty-stage'}' "
        "-RequireImmutableSource | Out-Null; Write-Output accepted } "
        "catch { Write-Output $_.Exception.Message }",
    )
    assert rejected == "CONTROL_BUNDLE_IMMUTABLE_SOURCE_REQUIRED"


def test_bundle_install_is_complete_and_restorable(tmp_path: Path) -> None:
    old_revision, new_revision = "a" * 40, "b" * 40
    control = tmp_path / "control"
    stage = tmp_path / "stage"
    backup = tmp_path / "backup"
    _write_bundle(control, old_revision, "old")
    _write_bundle(stage, new_revision, "new")
    result = _run_contract(
        tmp_path,
        f"$new=Install-VerifiedRuntimeControlBundleStage -StageRoot '{stage}' "
        f"-ControlRoot '{control}' -BackupRoot '{backup}'; "
        f"$old=Restore-RuntimeControlBundleBackup -BackupRoot '{backup}' "
        f"-ControlRoot '{control}'; Write-Output \"$($new.source_revision),$($old.source_revision)\"",
    )
    assert result == f"{new_revision},{old_revision}"
    assert all((control / name).read_text() == f"old|{name}\n" for name in CONTROL_FILES)


def test_staged_hash_mismatch_stops_before_watchdog_termination(tmp_path: Path) -> None:
    old_revision, target_revision = "a" * 40, "b" * 40
    body = _state_machine_mocks(old_revision, target_revision) + textwrap.dedent(
        f"""
        function New-VerifiedRuntimeControlBundleStage {{ throw 'CONTROL_BUNDLE_STAGED_HASH_VERIFICATION_FAILED' }};
        try {{ Invoke-ControlPlaneInstall -VerifiedSourceRoot 'immutable' -TargetRevision '{target_revision}' | Out-Null }} catch {{ }};
        Write-Output (($script:timeline -contains 'stop').ToString())
        """
    ).replace("\n", " ")
    assert _run_contract(tmp_path, body) == "False"


def test_handoff_orders_single_ownership_and_preserves_runtime(tmp_path: Path) -> None:
    old_revision, target_revision = "a" * 40, "b" * 40
    body = _state_machine_mocks(old_revision, target_revision) + (
        f"$result=Invoke-ControlPlaneInstall -VerifiedSourceRoot 'immutable' "
        f"-TargetRevision '{target_revision}'; "
        'Write-Output "$($result.status)|$($script:timeline -join ",")"'
    )
    result = _run_contract(tmp_path, body)
    assert result == (
        "COMMITTED|lock,stage,suspend,guard,stop,install,start,heartbeat,"
        "isolation,supervision,unlock"
    )


def test_partial_supervision_quiesce_restores_task_enablement(tmp_path: Path) -> None:
    body = textwrap.dedent(
        """
        $script:disabled=@(); $script:enabled=@(); $script:stopped=@(); $script:mainTask=$taskName;
        function Get-ScheduledTask { param($TaskName,$ErrorAction); [pscustomobject]@{Settings=[pscustomobject]@{Enabled=$true}} };
        function Disable-ScheduledTask { param($TaskName); $script:disabled+=$TaskName; if($TaskName-eq $script:mainTask){throw 'disable failed'} };
        function Enable-ScheduledTask { param($TaskName); $script:enabled+=$TaskName };
        function Stop-ScheduledTask { param($TaskName,$ErrorAction); $script:stopped+=$TaskName };
        try { Suspend-ControlPlaneSupervision | Out-Null } catch { };
        Write-Output "$($script:disabled.Count),$($script:enabled.Count),$($script:stopped -join '|')"
        """
    ).replace("\n", " ")
    result = _run_contract(tmp_path, body)
    assert result == "2,2,XAUUSD-Forecaster-Watchdog-Guard"


@pytest.mark.parametrize(
    ("exact", "hashed", "heartbeat_token", "expected"),
    [
        ("$true", "$true", "old-token", "CONTROL_PLANE_NEW_WATCHDOG_HEARTBEAT_TIMEOUT"),
        ("$false", "$true", "new-token", "CONTROL_PLANE_NEW_WATCHDOG_HEARTBEAT_TIMEOUT"),
        ("$true", "$false", "new-token", "CONTROL_PLANE_NEW_WATCHDOG_HEARTBEAT_TIMEOUT"),
        ("$true", "$true", "new-token", "new-token"),
    ],
)
def test_heartbeat_requires_new_process_exact_revision_and_hashes(
    tmp_path: Path, exact: str, hashed: str, heartbeat_token: str, expected: str,
) -> None:
    revision = "b" * 40
    previous = _identity(100, "old-token")
    owner_pid = 100 if heartbeat_token == "old-token" else 200
    owner = _identity(owner_pid, heartbeat_token)
    body = textwrap.dedent(
        f"""
        $previous={previous}; $owner={owner};
        function Start-Sleep {{ }};
        function Get-VerifiedWatchdogOwners {{ @($owner) }};
        New-Item -ItemType Directory -Path (Split-Path -Parent $watchdogHeartbeatPath) -Force | Out-Null;
        [pscustomobject]@{{control_bundle_revision='{revision}';control_bundle_exact_revision={exact};control_bundle_hash_verified={hashed};process_id={owner_pid};process_start_token='{heartbeat_token}'}} | ConvertTo-Json | Set-Content -LiteralPath $watchdogHeartbeatPath;
        try {{ $accepted=Wait-VerifiedWatchdogHandoff -ExpectedRevision '{revision}' -PreviousIdentity $previous -Timeout ([TimeSpan]::FromMilliseconds(20)); Write-Output $accepted.process_start_token }} catch {{ Write-Output $_.Exception.Message }}
        """
    ).replace("\n", " ")
    assert _run_contract(tmp_path, body) == expected


def test_failure_starting_new_watchdog_restores_old_bundle_and_owner(tmp_path: Path) -> None:
    old_revision, target_revision = "a" * 40, "b" * 40
    body = _state_machine_mocks(old_revision, target_revision) + textwrap.dedent(
        f"""
        $script:startCount=0;
        function Start-WatchdogReplacement {{ param([switch]$PassThru); $script:startCount++; if($script:startCount-eq 1){{throw 'new start failed'}}; $script:timeline+='restore-start'; $script:owners=@({_identity(300, 'restored-token')}) }};
        function Restore-RuntimeControlBundleBackup {{ param($BackupRoot,$ControlRoot); $script:timeline+='restore-bundle'; [pscustomobject]@{{source_revision='{old_revision}'}} }};
        function Wait-VerifiedWatchdogHandoff {{ param($ExpectedRevision,$PreviousIdentity,$Timeout); $script:timeline+="restore-heartbeat:$ExpectedRevision"; return $script:owners[0] }};
        try {{ Invoke-ControlPlaneInstall -VerifiedSourceRoot 'immutable' -TargetRevision '{target_revision}' | Out-Null }} catch {{ $message=$_.Exception.Message }};
        Write-Output "$message|$($script:timeline -join ',')"
        """
    ).replace("\n", " ")
    result = _run_contract(tmp_path, body)
    assert "ROLLED_BACK" in result
    assert "install,restore-bundle,restore-start" in result
    assert f"restore-heartbeat:{old_revision}" in result


def test_release_transaction_blocks_control_plane_install(tmp_path: Path) -> None:
    target_revision = "b" * 40
    body = _state_machine_mocks("a" * 40, target_revision) + (
        "function Get-ReleaseControlState { [pscustomobject]@{transaction=[pscustomobject]@{type='PROMOTE'}} }; "
        f"try {{ Invoke-ControlPlaneInstall -VerifiedSourceRoot 'immutable' -TargetRevision '{target_revision}' | Out-Null }} "
        "catch { Write-Output $_.Exception.Message }"
    )
    assert _run_contract(tmp_path, body) == "CONTROL_PLANE_INSTALL_BLOCKED_BY_RELEASE_TRANSACTION"


def test_control_plane_isolation_and_visible_identity_are_explicit() -> None:
    source = (ROOT / "scripts" / "xauusd_control_center.ps1").read_text(encoding="utf-8")
    xaml = (ROOT / "scripts" / "control_center.xaml").read_text(encoding="utf-8")
    launcher = (ROOT / "scripts" / "xauusd_control_center_launcher.vbs").read_text(encoding="utf-8")
    install_body = source.split("function Invoke-ControlPlaneInstall", 1)[1].split(
        "function Invoke-ForecasterWatchdog", 1
    )[0]
    assert "InstallRuntime" not in install_body
    assert "Stop-All" not in install_body
    assert "Restart-All" not in install_body
    assert "Restart-CodeReloadableServices" not in install_body
    assert "Assert-ControlPlaneIsolationSnapshot" in install_body
    supervision = source.split("function Suspend-ControlPlaneSupervision", 1)[1].split(
        "function Restore-ControlPlaneSupervision", 1
    )[0]
    assert 'if ($name -eq $guardTaskName)' in supervision
    assert "Stop-ScheduledTask -TaskName $name" in supervision
    assert "ControlPlaneIdentity" in xaml
    assert "BusinessRuntimeIdentity" in xaml
    assert "EXACT · HASH VERIFIED" in source
    assert 'BuildPath(scriptDirectory, "xauusd_control_center.ps1")' in launcher
