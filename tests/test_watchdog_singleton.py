from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
import time
import uuid

import pytest


ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "scripts" / "control_center_watchdog_singleton.ps1"
GUARD = ROOT / "scripts" / "xauusd_watchdog_guard.ps1"
CONTROL_CENTER = ROOT / "scripts" / "xauusd_control_center.ps1"


@pytest.mark.parametrize("powershell", ["powershell.exe", "pwsh.exe"])
def test_twenty_concurrent_watchdogs_have_one_kernel_owner(
    tmp_path: Path, powershell: str,
) -> None:
    if shutil.which(powershell) is None:
        pytest.skip(f"{powershell} unavailable")
    runtime = tmp_path / "production-runtime"
    repository = tmp_path / "repository"
    control = repository / ".local" / "runtime-control"
    outcomes = tmp_path / "outcomes"
    ready = tmp_path / "ready"
    for path in (runtime, control, outcomes, ready):
        path.mkdir(parents=True)
    child = tmp_path / "contender.ps1"
    child.write_text(
        r'''param($Module,$Runtime,$Repository,$OutcomeRoot,$ReadyRoot,$StartEvent,$ReleaseEvent)
$ErrorActionPreference='Stop'
$moduleRoot=$Runtime
$repositoryRoot=$Repository
$watchdogSingletonContractVersion='watchdog-machine-singleton-v2'
$watchdogOwnerReceiptPath=Join-Path $Runtime 'watchdog-owner-v2.json'
function ConvertFrom-ReleaseControlJson { process { $_ | ConvertFrom-Json } }
function Write-ReleaseEvidenceUtf8Atomic { param($Path,$Content); [IO.File]::WriteAllText($Path,$Content,[Text.UTF8Encoding]::new($false)) }
function Test-ControlPlaneStartTokenEqual { param($Left,$Right); return [string]$Left -eq [string]$Right }
function Get-ControlPlaneProcessIdentity { param($ProcessId)
    if([int]$ProcessId-eq $PID){ return [pscustomobject]@{process_id=$PID;parent_process_id=900001;process_start_token="token-$PID";name='powershell.exe';command_line='watchdog'} }
    if([int]$ProcessId-eq 900001){ return [pscustomobject]@{process_id=900001;parent_process_id=0;process_start_token='launcher-token';name='wscript.exe';command_line=((Join-Path $Repository '.local\runtime-control\xauusd_watchdog_launcher.vbs')+' '+$Runtime+' '+$Repository)} }
    return $null
}
function Assert-ActiveControlBundle { [pscustomobject]@{source_revision=('a'*40);bundle_digest=('b'*64);exact_revision=$true} }
. $Module
$start=[Threading.EventWaitHandle]::new($false,[Threading.EventResetMode]::ManualReset,$StartEvent)
$release=[Threading.EventWaitHandle]::new($false,[Threading.EventResetMode]::ManualReset,$ReleaseEvent)
[IO.File]::WriteAllText((Join-Path $ReadyRoot "$PID.ready"),'ready')
if(-not $start.WaitOne(30000)){exit 91}
$context=Enter-WatchdogSingletonOwnership
if($context.acquired){
    [IO.File]::WriteAllText((Join-Path $OutcomeRoot "$PID.json"),'{"result":"OWNER"}')
    if(-not $release.WaitOne(30000)){exit 92}
    Exit-WatchdogSingletonOwnership -Context $context
} else {
    [IO.File]::WriteAllText((Join-Path $OutcomeRoot "$PID.json"),'{"result":"DUPLICATE_OWNER"}')
}
''',
        encoding="utf-8",
    )
    start_event = f"Local\\xauusd-watchdog-test-start-{uuid.uuid4().hex}"
    release_event = f"Local\\xauusd-watchdog-test-release-{uuid.uuid4().hex}"
    processes = [
        subprocess.Popen(
            [
                powershell, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                "-File", str(child), "-Module", str(MODULE), "-Runtime", str(runtime),
                "-Repository", str(repository), "-OutcomeRoot", str(outcomes),
                "-ReadyRoot", str(ready), "-StartEvent", start_event,
                "-ReleaseEvent", release_event,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            text=True,
        )
        for _ in range(20)
    ]
    try:
        deadline = time.monotonic() + 30
        while len(list(ready.glob("*.ready"))) != 20 and time.monotonic() < deadline:
            time.sleep(0.05)
        assert len(list(ready.glob("*.ready"))) == 20
        _signal_event(powershell, start_event)
        deadline = time.monotonic() + 30
        while len(list(outcomes.glob("*.json"))) != 20 and time.monotonic() < deadline:
            time.sleep(0.05)
        rows = [json.loads(path.read_text()) for path in outcomes.glob("*.json")]
        assert [row["result"] for row in rows].count("OWNER") == 1
        assert [row["result"] for row in rows].count("DUPLICATE_OWNER") == 19
        receipt = json.loads((runtime / "watchdog-owner-v2.json").read_text())
        assert receipt["schema_version"] == "watchdog-owner-v2"
        assert receipt["mutex_identity_hash"]
        _signal_event(powershell, release_event)
        results = [process.communicate(timeout=30) for process in processes]
        assert all(process.returncode == 0 for process in processes), results
        assert not (runtime / "watchdog-owner-v2.json").exists()
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()


def _signal_event(powershell: str, name: str) -> None:
    command = (
        f"$event=[Threading.EventWaitHandle]::OpenExisting('{name}');"
        "try{$null=$event.Set()}finally{$event.Dispose()}"
    )
    subprocess.run(
        [powershell, "-NoProfile", "-NonInteractive", "-Command", command],
        check=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def test_watchdog_does_no_shared_work_before_kernel_ownership() -> None:
    source = (ROOT / "scripts" / "control_center_runtime_supervision.ps1").read_text(
        encoding="utf-8",
    )
    body = source.split("function Invoke-ForecasterWatchdog {", 1)[1].split(
        "\nfunction ", 1,
    )[0]
    assert body.index("Enter-WatchdogSingletonOwnership") < body.index(
        "Invoke-ForecasterWatchdogOwned"
    )
    assert "if (-not $ownership.acquired) { return 0 }" in body
    launcher = (ROOT / "scripts" / "xauusd_watchdog_launcher.vbs").read_text(
        encoding="utf-8",
    )
    assert "Loop While exitCode = 75" in launcher
    assert "exitCode = 0" not in launcher.split("Loop While", 1)[1]


def test_watchdog_replacement_has_no_recursive_kill_path() -> None:
    production = "\n".join(
        (ROOT / "scripts" / name).read_text(encoding="utf-8")
        for name in (
            "control_center_watchdog_singleton.ps1",
            "control_center_install.ps1",
            "control_center_runtime_supervision.ps1",
            "control_center_recovery_engine.ps1",
            "repair_stable_runtime_artifact_paths.ps1",
            "xauusd_watchdog_guard.ps1",
        )
    )
    assert "Stop-WatchdogExactProcessTree" not in production
    assert "taskkill" not in production.lower()
    assert "Stop-WatchdogControllerOwner" in production
    controller = (ROOT / "scripts" / "xauusd_control_center.ps1").read_text(encoding="utf-8")
    termination_action = controller.split('"TerminateWatchdogOwner" {', 1)[1].split(
        '\n    "CodeRevision"', 1,
    )[0]
    assert "Enter-ReleaseTransactionLock" in termination_action
    assert "Exit-ReleaseTransactionLock" in termination_action


@pytest.mark.parametrize("powershell", ["powershell.exe", "pwsh.exe"])
def test_unknown_watchdog_descendant_blocks_root_termination(
    tmp_path: Path, powershell: str,
) -> None:
    if shutil.which(powershell) is None:
        pytest.skip(f"{powershell} unavailable")
    runtime = tmp_path / "runtime"
    repository = tmp_path / "repository"
    control = repository / ".local" / "runtime-control"
    runtime.mkdir()
    control.mkdir(parents=True)
    command = rf'''
$moduleRoot='{runtime}';$repositoryRoot='{repository}'
$releaseControlStatePath='{tmp_path / 'state.json'}';$releaseHistoryPath='{tmp_path / 'history.jsonl'}'
$watchdogOwnerReceiptPath='{runtime / 'watchdog-owner-v2.json'}';$watchdogSingletonContractVersion='watchdog-machine-singleton-v2'
$scriptPath=Join-Path '{control}' 'xauusd_control_center.ps1'
$sid=[Security.Principal.WindowsIdentity]::GetCurrent().User.Value
$root=[pscustomobject]@{{process_id=101;parent_process_id=0;process_start_token='root-token';name='powershell.exe';command_line=('powershell -File "'+$scriptPath+'" -Action Watchdog -RuntimeRoot "{runtime}" -RepositoryRoot "{repository}"');owner_sid=$sid}}
$mystery=[pscustomobject]@{{process_id=102;parent_process_id=101;process_start_token='unknown-token';name='mystery.exe';command_line='mystery';owner_sid=$sid}}
function Test-ControlPlaneStartTokenEqual {{ param($Left,$Right);[string]$Left-eq[string]$Right }}
function ConvertFrom-ReleaseControlJson {{ process {{ $_|ConvertFrom-Json }} }}
function Get-ControlPlaneProcessIdentity {{ param($ProcessId);if([int]$ProcessId-eq 101){{$root}}elseif([int]$ProcessId-eq 102){{$mystery}}else{{$null}} }}
function Get-RuntimeControlBundleIdentityAtRoot {{ [pscustomobject]@{{source_revision=('a'*40);bundle_digest=('b'*64)}} }}
. '{MODULE}'
function Get-WatchdogBusinessOwnerBaseline {{ @() }}
function Get-WatchdogProcessTreeSnapshot {{ @($root,$mystery) }}
try {{ $null=Get-WatchdogControllerTerminationPlan -RootIdentity $root -AllowLegacyReceiptless; 'WRONG' }} catch {{ $_.Exception.Message }}
'''
    assert _run_powershell(powershell, command) == "WATCHDOG_TERMINATION_DESCENDANT_UNKNOWN"


@pytest.mark.parametrize("powershell", ["powershell.exe", "pwsh.exe"])
def test_guard_never_starts_over_multiple_or_unresolved_owner(
    tmp_path: Path, powershell: str,
) -> None:
    if shutil.which(powershell) is None:
        pytest.skip(f"{powershell} unavailable")
    common = _guard_dot_source(tmp_path)
    command = common + r'''
$script:starts=0
function Start-ScheduledTask { $script:starts++ }
function Stop-ScheduledTask { }
$owner=[pscustomobject]@{process_id=101;process_start_token='2026-09-01T00:00:00+00:00'}
function Get-GuardWatchdogProcesses { @($owner,$owner) }
try { Invoke-WatchdogGuard | Out-Null; $multiple='WRONG' } catch { $multiple=$_.Exception.Message }
function Get-GuardWatchdogProcesses { @($owner) }
function Read-GuardJson { param($Path,$MaximumBytes); if($Path-eq $OwnerReceiptPath){
 [pscustomobject]@{instance_id=('a'*32);process_id=101;process_start_token=$owner.process_start_token;mutex_identity_hash=('b'*64)}
 } else { [pscustomobject]@{instance_id=('a'*32);process_id=101;process_start_token=$owner.process_start_token;mutex_identity_hash=('b'*64);owner_receipt_digest=('c'*64);observed_at='2020-01-01T00:00:00+00:00'} } }
function Get-VerifiedGuardOwner { return $owner }
function Get-GuardOwnerReceiptDigest { return ('c'*64) }
function Stop-GuardVerifiedOwner { throw 'WATCHDOG_TERMINATION_UNRESOLVED' }
try { Invoke-WatchdogGuard | Out-Null; $unresolved='WRONG' } catch { $unresolved=$_.Exception.Message }
Write-Output "$multiple|$unresolved|$script:starts"
'''
    result = _run_powershell(powershell, command)
    assert result == "WATCHDOG_MULTIPLE_OWNERS|WATCHDOG_TERMINATION_UNRESOLVED|0"


@pytest.mark.parametrize("powershell", ["powershell.exe", "pwsh.exe"])
def test_guard_cleans_dead_exact_receipt_then_starts_once(
    tmp_path: Path, powershell: str,
) -> None:
    if shutil.which(powershell) is None:
        pytest.skip(f"{powershell} unavailable")
    receipt = tmp_path / "watchdog-owner-v2.json"
    receipt.write_text("{}", encoding="utf-8")
    command = _guard_dot_source(tmp_path) + rf'''
$script:starts=0
function Get-GuardWatchdogProcesses {{ @() }}
function Read-GuardJson {{ [pscustomobject]@{{instance_id=('a'*32);process_id=101}} }}
function Get-VerifiedGuardOwner {{ return $null }}
function Stop-ScheduledTask {{ }}
function Start-ScheduledTask {{ $script:starts++ }}
$started=Invoke-WatchdogGuard
Write-Output "$started|$script:starts|$(Test-Path -LiteralPath '{receipt}')"
'''
    result = _run_powershell(powershell, command)
    assert result == "True|1|False"


@pytest.mark.parametrize("powershell", ["powershell.exe", "pwsh.exe"])
def test_controller_root_termination_preserves_business_descendant(
    tmp_path: Path, powershell: str,
) -> None:
    if shutil.which(powershell) is None:
        pytest.skip(f"{powershell} unavailable")
    identity_file = tmp_path / "tree.json"
    root_script = tmp_path / "tree-root.ps1"
    root_script.write_text(
        rf'''$child=Start-Process -FilePath "$env:SystemRoot\System32\ping.exe" -ArgumentList @('-t','127.0.0.1') -WindowStyle Hidden -PassThru
[IO.File]::WriteAllText('{identity_file}',(@{{root=$PID;child=$child.Id}}|ConvertTo-Json))
Wait-Process -Id $child.Id
''',
        encoding="utf-8",
    )
    root = subprocess.Popen(
        [powershell, "-NoProfile", "-NonInteractive", "-File", str(root_script)],
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    try:
        deadline = time.monotonic() + 15
        while not identity_file.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        ids = json.loads(identity_file.read_text())
        command = rf'''
function Get-ControlPlaneProcessIdentity {{ param($ProcessId); $p=Get-CimInstance Win32_Process -Filter "ProcessId=$ProcessId" -ErrorAction SilentlyContinue; if(-not $p){{return $null}}; [pscustomobject]@{{process_id=[int]$p.ProcessId;parent_process_id=[int]$p.ParentProcessId;process_start_token=([DateTimeOffset]$p.CreationDate).ToUniversalTime().ToString('o');name=[string]$p.Name;command_line=[string]$p.CommandLine}} }}
function Test-ControlPlaneStartTokenEqual {{ param($Left,$Right); return [string]$Left-eq[string]$Right }}
$releaseControlStatePath='{tmp_path / 'missing-state.json'}'
$releaseHistoryPath='{tmp_path / 'missing-history.jsonl'}'
. '{MODULE}'
$root=Get-ControlPlaneProcessIdentity -ProcessId {ids['root']}
$child=Get-ControlPlaneProcessIdentity -ProcessId {ids['child']}
function Get-WatchdogControllerTerminationPlan {{ [pscustomobject]@{{root=$root;launcher=$null;business=@([pscustomobject]@{{root=$child}});control_plane_transient=@()}} }}
function Test-WatchdogSingletonMutexAvailable {{ return $true }}
$answer=(Stop-WatchdogControllerOwner -RootIdentity $root).status
$rootAlive=[bool](Get-Process -Id {ids['root']} -ErrorAction SilentlyContinue)
$childAlive=[bool](Get-Process -Id {ids['child']} -ErrorAction SilentlyContinue)
Write-Output "$answer|$rootAlive|$childAlive"
'''
        assert _run_powershell(powershell, command) == "TERMINATED|False|True"
    finally:
        if root.poll() is None:
            root.kill()


def _guard_dot_source(tmp_path: Path) -> str:
    return (
        f"$null=. '{GUARD}' -TaskName 'test-watchdog' "
        f"-HeartbeatPath '{tmp_path / 'heartbeat.json'}' "
        f"-OwnerReceiptPath '{tmp_path / 'watchdog-owner-v2.json'}' "
        "-ControlScript 'C:\\control\\xauusd_control_center.ps1' "
        "-RuntimeRoot 'C:\\runtime' -RepositoryRoot 'C:\\repository';"
    )


def _run_powershell(powershell: str, command: str) -> str:
    result = subprocess.run(
        [powershell, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", command],
        capture_output=True,
        text=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def test_windowless_guard_launcher_preserves_complete_authority_arguments(
    tmp_path: Path,
) -> None:
    marker = tmp_path / "guard-arguments.json"
    probe = tmp_path / "guard-probe.ps1"
    probe.write_text(
        rf'''param($TaskName,$HeartbeatPath,$OwnerReceiptPath,$ControlScript,$RuntimeRoot,$RepositoryRoot)
[IO.File]::WriteAllText('{marker}',(@{{task=$TaskName;heartbeat=$HeartbeatPath;receipt=$OwnerReceiptPath;control=$ControlScript;runtime=$RuntimeRoot;repository=$RepositoryRoot}}|ConvertTo-Json),[Text.UTF8Encoding]::new($false))
''',
        encoding="utf-8",
    )
    launcher = ROOT / "scripts" / "xauusd_watchdog_guard_launcher.vbs"
    values = [
        "XAUUSD-Forecaster-Autostart",
        str(tmp_path / "heartbeat.json"),
        str(tmp_path / "watchdog-owner-v2.json"),
        str(tmp_path / "control" / "xauusd_control_center.ps1"),
        str(tmp_path / "runtime"),
        str(tmp_path / "repository"),
    ]
    subprocess.run(
        ["cscript.exe", "//NoLogo", str(launcher), str(probe), *values],
        check=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    actual = json.loads(marker.read_text(encoding="utf-8"))
    assert actual == dict(zip(
        ("task", "heartbeat", "receipt", "control", "runtime", "repository"),
        values,
    ))


@pytest.mark.parametrize("powershell", ["powershell.exe", "pwsh.exe"])
def test_repair_four_verified_duplicates_to_one_without_business_mutation(
    tmp_path: Path, powershell: str,
) -> None:
    if shutil.which(powershell) is None:
        pytest.skip(f"{powershell} unavailable")
    runtime = tmp_path / "runtime"
    repository = tmp_path / "repository"
    runtime.mkdir()
    repository.mkdir()
    command = rf'''
$null=. '{CONTROL_CENTER}' -Action CodeRevision -RuntimeRoot '{runtime}' -RepositoryRoot '{repository}'
$script:lockExited=$false;$script:stopped=0;$script:inventoryRead=0;$script:registered='';$script:heartbeats=0
function Enter-ReleaseTransactionLock {{ return $true }}
function Exit-ReleaseTransactionLock {{ $script:lockExited=$true }}
function Get-ReleaseControlState {{ [pscustomobject]@{{transaction=$null;stable=[pscustomobject]@{{worker_version_id='11111111-1111-4111-8111-111111111111'}}}} }}
function Get-ForecasterProcesses {{ @([pscustomobject]@{{ProcessId=900}}) }}
function Get-ReleaseProviderRuntimeFacts {{ [pscustomobject]@{{active_worker_observation=[pscustomobject]@{{status='AVAILABLE';traffic_percent=100;version_id='11111111-1111-4111-8111-111111111111'}}}} }}
$duplicate=[pscustomobject]@{{process_id=101;process_start_token='2026-09-01T00:00:00+00:00';launcher_identity=[pscustomobject]@{{process_id=201;process_start_token='2026-09-01T00:00:00+00:00'}}}}
function Get-WatchdogOwnershipInventory {{ $script:inventoryRead++; if($script:inventoryRead-eq 1){{[pscustomobject]@{{authoritative=@();duplicate_shaped=@($duplicate,$duplicate,$duplicate,$duplicate);legacy_orphaned=@();unknown=@();receipt=$null}}}}else{{[pscustomobject]@{{authoritative=@();duplicate_shaped=@();legacy_orphaned=@();unknown=@();receipt=$null}}}} }}
function Get-ScheduledTask {{ [pscustomobject]@{{Settings=[pscustomobject]@{{Enabled=$true}}}} }}
function Disable-ScheduledTask {{ }}; function Stop-ScheduledTask {{ }}; function Enable-ScheduledTask {{ }}
function Stop-VerifiedWatchdogOwner {{ $script:stopped++ }}
function Register-AutoStartTask {{ param($ControlScript,$RuntimePath,$SourceRepository);$script:registered=$ControlScript }}
$script:testOwner=[pscustomobject]@{{process_id=301;process_start_token='2026-09-01T00:01:00+00:00'}}
function Get-VerifiedWatchdogOwners {{ @($script:testOwner) }}
function Get-RuntimeControlBundleIdentityAtRoot {{ [pscustomobject]@{{source_revision=('a'*40)}} }}
function Assert-CurrentWatchdogHeartbeat {{ $script:heartbeats++;[pscustomobject]@{{observed_at="beat-$script:heartbeats"}} }}
function Start-Sleep {{ }}
$result=Invoke-WatchdogOwnershipRepair
$expected=Join-Path '{repository}' '.local\runtime-control\xauusd_control_center.ps1'
Write-Output "$($result.status)|$script:stopped|$($script:registered-eq$expected)|$script:heartbeats|$script:lockExited"
'''
    assert _run_powershell(powershell, command) == "REPAIRED|4|True|2|True"


@pytest.mark.parametrize("powershell", ["powershell.exe", "pwsh.exe"])
def test_repair_one_verified_legacy_orphan_to_live_launcher_owner(
    tmp_path: Path, powershell: str,
) -> None:
    if shutil.which(powershell) is None:
        pytest.skip(f"{powershell} unavailable")
    runtime = tmp_path / "runtime"
    repository = tmp_path / "repository"
    runtime.mkdir()
    repository.mkdir()
    command = rf'''
$null=. '{CONTROL_CENTER}' -Action CodeRevision -RuntimeRoot '{runtime}' -RepositoryRoot '{repository}'
$script:inventoryRead=0;$script:exactStops=0;$script:verifiedStops=0;$script:guardEnabled=0;$script:guardDisabled=0;$script:heartbeats=0;$script:allowLegacy=$false
function Enter-ReleaseTransactionLock {{ return $true }}; function Exit-ReleaseTransactionLock {{ }}
function Get-ReleaseControlState {{ [pscustomobject]@{{transaction=$null;stable=[pscustomobject]@{{worker_version_id='11111111-1111-4111-8111-111111111111'}}}} }}
function Get-ForecasterProcesses {{ @([pscustomobject]@{{ProcessId=900}}) }}
function Get-ReleaseProviderRuntimeFacts {{ [pscustomobject]@{{active_worker_observation=[pscustomobject]@{{status='AVAILABLE';traffic_percent=100;version_id='11111111-1111-4111-8111-111111111111'}}}} }}
$orphan=[pscustomobject]@{{process_id=101;process_start_token='2026-09-01T00:00:00+00:00';owner_sid=[Security.Principal.WindowsIdentity]::GetCurrent().User.Value}}
function Get-WatchdogOwnershipInventory {{
  $script:inventoryRead++
  if($script:inventoryRead-eq 1){{[pscustomobject]@{{authoritative=@();duplicate_shaped=@();legacy_orphaned=@($orphan);unknown=@();receipt=$null}}}}
  else{{[pscustomobject]@{{authoritative=@();duplicate_shaped=@();legacy_orphaned=@();unknown=@();receipt=$null}}}}
}}
function Get-ScheduledTask {{ [pscustomobject]@{{Settings=[pscustomobject]@{{Enabled=$true}}}} }}
function Disable-ScheduledTask {{ $script:guardDisabled++ }}; function Stop-ScheduledTask {{ }}
function Enable-ScheduledTask {{ $script:guardEnabled++ }}
function Stop-VerifiedWatchdogOwner {{ $script:verifiedStops++ }}
    function Stop-WatchdogControllerOwner {{ param($RootIdentity,[switch]$AllowLegacyReceiptless);$script:exactStops++; return [pscustomobject]@{{status='TERMINATED'}} }}
function Register-AutoStartTask {{ }}
$legacy=[pscustomobject]@{{process_id=301;process_start_token='2026-09-01T00:01:00+00:00';watchdog_owner_state='LEGACY_SINGLE_OWNER'}}
function Get-VerifiedWatchdogOwners {{ param([switch]$AllowLegacySingleOwner);$script:allowLegacy=[bool]$AllowLegacySingleOwner;@($legacy) }}
function Get-RuntimeControlBundleIdentityAtRoot {{ [pscustomobject]@{{source_revision=('a'*40)}} }}
function Assert-CurrentWatchdogHeartbeat {{ $script:heartbeats++;[pscustomobject]@{{observed_at="beat-$script:heartbeats"}} }}
function Start-Sleep {{ }}
$result=Invoke-WatchdogOwnershipRepair
Write-Output "$($result.status)|$script:exactStops|$script:verifiedStops|$script:allowLegacy|$script:heartbeats|$($result.guard_enabled)|$script:guardEnabled"
'''
    assert _run_powershell(powershell, command) == (
        "LEGACY_REPAIRED_REQUIRES_CONTROL_PLANE_INSTALL|1|0|True|2|False|0"
    )


@pytest.mark.parametrize("powershell", ["powershell.exe", "pwsh.exe"])
def test_inventory_classifies_only_same_user_receiptless_orphan_as_legacy(
    tmp_path: Path, powershell: str,
) -> None:
    if shutil.which(powershell) is None:
        pytest.skip(f"{powershell} unavailable")
    runtime = tmp_path / "runtime"
    repository = tmp_path / "repository"
    runtime.mkdir()
    repository.mkdir()
    control = repository / ".local" / "runtime-control" / "xauusd_control_center.ps1"
    command = rf'''
$null=. '{CONTROL_CENTER}' -Action CodeRevision -RuntimeRoot '{runtime}' -RepositoryRoot '{repository}'
$sameSid=[Security.Principal.WindowsIdentity]::GetCurrent().User.Value
$candidate=[pscustomobject]@{{ProcessId=101;Name='powershell.exe';CommandLine='powershell -File "{control}" -Action Watchdog -RuntimeRoot "{runtime}" -RepositoryRoot "{repository}"'}}
function Get-CimInstance {{ param($ClassName,$Filter);if($Filter){{return $null}};@($candidate) }}
$script:identitySid=$sameSid
function Get-ControlPlaneProcessIdentity {{ param($ProcessId);if([int]$ProcessId-eq 101){{[pscustomobject]@{{process_id=101;parent_process_id=201;process_start_token='token';name='powershell.exe';command_line=$candidate.CommandLine;owner_sid=$script:identitySid}}}}else{{$null}} }}
function Read-WatchdogOwnerReceipt {{ return $null }}
$same=Get-WatchdogOwnershipInventory
$script:identitySid='S-1-5-21-999'
$other=Get-WatchdogOwnershipInventory
function Read-WatchdogOwnerReceipt {{ [pscustomobject]@{{schema_version='watchdog-owner-v2'}} }}
$script:identitySid=$sameSid
$withReceipt=Get-WatchdogOwnershipInventory
function Read-WatchdogOwnerReceipt {{ throw 'malformed receipt' }}
$receiptUnreadable=Get-WatchdogOwnershipInventory
Write-Output "$($same.legacy_orphaned.Count)|$($same.unknown.Count)|$($other.legacy_orphaned.Count)|$($other.unknown.Count)|$($withReceipt.legacy_orphaned.Count)|$($withReceipt.unknown.Count)|$($receiptUnreadable.legacy_orphaned.Count)|$($receiptUnreadable.unknown.Count)"
'''
    assert _run_powershell(powershell, command) == "1|0|0|1|0|1|0|1"
