from __future__ import annotations

import hashlib
import ctypes
from ctypes import wintypes
from datetime import datetime, timedelta, timezone
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import shutil
import subprocess
import textwrap
import uuid
import sys
import ssl
import threading
import time

import pytest


pytestmark = pytest.mark.skipif(
    shutil.which("powershell.exe") is None,
    reason="Windows PowerShell is required for Control Plane contracts",
)


ROOT = Path(__file__).resolve().parents[1]
OLD_START_TOKEN = "2026-08-26T03:20:00.0000000+00:00"
NEW_START_TOKEN = "2026-08-26T03:22:48.0603020+00:00"
CURRENT_START_TOKEN = "2026-08-26T03:25:00.0000000+00:00"
DEAD_INSTALLER_START_TOKEN = "2026-08-26T03:15:00.0000000+00:00"
CONTROL_FILES = tuple(json.loads(
    (ROOT / "scripts" / "runtime-control-files.json").read_text(encoding="utf-8")
)["files"])
BUNDLE_DIGEST_ALGORITHM = "xauusd.control-bundle.sha256.v1"
BUNDLE_SCHEMA_VERSION = 3


def _canonical_bundle_digest(revision: str, hashes: dict[str, str]) -> str:
    lines = [
        BUNDLE_DIGEST_ALGORITHM,
        f"schema_version={BUNDLE_SCHEMA_VERSION}",
        f"source_revision={revision}",
        f"file_count={len(hashes)}",
        *(f"file={name}\thash={hashes[name].lower()}" for name in sorted(hashes)),
    ]
    return hashlib.sha256("\n".join(lines).encode()).hexdigest()


def _legacy_v2_bundle_digest(hashes: dict[str, str]) -> str:
    return hashlib.sha256(
        "\n".join(f"{name}={hashes[name].lower()}" for name in sorted(hashes)).encode()
    ).hexdigest()


def _run_contract_with_runtime(
    tmp_path: Path, body: str, runtime_executable: str, *, environment=None,
) -> str:
    runtime = tmp_path / "runtime"
    repository = tmp_path / "repository"
    runtime.mkdir(exist_ok=True)
    repository.mkdir(exist_ok=True)
    script = ROOT / "scripts" / "xauusd_control_center.ps1"
    task_prefix = f"XAUUSD-Contract-{uuid.uuid4().hex}"
    # Temporary filesystem roots do not isolate machine-global scheduled tasks.
    # Contract tests must opt in through explicit stubs, never native mutations.
    scheduler_guard = r'''
        function Stop-ScheduledTask { throw 'TEST_UNMOCKED_SCHEDULER_MUTATION' };
        function Start-ScheduledTask { throw 'TEST_UNMOCKED_SCHEDULER_MUTATION' };
        function Enable-ScheduledTask { throw 'TEST_UNMOCKED_SCHEDULER_MUTATION' };
        function Disable-ScheduledTask { throw 'TEST_UNMOCKED_SCHEDULER_MUTATION' };
        function Register-ScheduledTask { throw 'TEST_UNMOCKED_SCHEDULER_MUTATION' };
        function Unregister-ScheduledTask { throw 'TEST_UNMOCKED_SCHEDULER_MUTATION' };
    '''
    command = (
        f"$null = . '{script}' -Action CodeRevision -RuntimeRoot '{runtime}' "
        f"-RepositoryRoot '{repository}'; "
        f"$taskName='{task_prefix}-Main'; $guardTaskName='{task_prefix}-Guard'; "
        f"{scheduler_guard}; {body}"
    )
    result = subprocess.run(
        [
            runtime_executable,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            command,
        ],
        capture_output=True,
        text=True,
        check=False,
        env=environment,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if result.returncode:
        raise AssertionError(
            f"{runtime_executable} control-plane contract failed\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return result.stdout.strip()


def _run_contract(tmp_path: Path, body: str) -> str:
    return _run_contract_with_runtime(tmp_path, body, "powershell.exe")


def _isolated_windows_environment():
    # A Python parent otherwise forwards PowerShell 7 module paths into 5.1.
    # No production credentials or endpoint environment enters the fixture tree.
    environment = {key: value for key, value in os.environ.items() if key.upper() in {
        "SYSTEMROOT", "WINDIR", "SYSTEMDRIVE", "COMSPEC", "USERPROFILE", "USERNAME",
        "USERDOMAIN", "APPDATA", "LOCALAPPDATA", "PROGRAMFILES", "PROGRAMFILES(X86)",
        "PROGRAMDATA", "TEMP", "TMP", "PATHEXT", "PATH",
    }}
    environment["PSModulePath"] = str(Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/Modules")
    return environment


def _exited_windows_child_identity():
    """Capture the kernel creation identity before asking our child to exit."""
    child = subprocess.Popen(
        [sys.executable, "-c", "import sys; sys.stdin.buffer.read()"],
        stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        env=_isolated_windows_environment(), creationflags=subprocess.CREATE_NO_WINDOW,
    )
    try:
        get_times = ctypes.WinDLL("kernel32", use_last_error=True).GetProcessTimes
        get_times.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
        get_times.restype = wintypes.BOOL
        created, exited, kernel, user = (wintypes.FILETIME() for _ in range(4))
        if not get_times(int(child._handle), *(ctypes.byref(value) for value in (
            created, exited, kernel, user,
        ))):
            raise ctypes.WinError(ctypes.get_last_error())
        assert child.poll() is None, "identity fixture exited before observation"
        ticks = (created.dwHighDateTime << 32) | created.dwLowDateTime
        assert ticks > 0, "Windows creation time unavailable"
        seconds, fraction = divmod(ticks, 10_000_000)
        instant = datetime(1601, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=seconds)
        identity = {"pid": child.pid, "token": f"{instant:%Y-%m-%dT%H:%M:%S}.{fraction:07d}Z"}
        child.stdin.close()
        assert child.wait(timeout=5) == 0, "identity fixture did not exit normally"
        return identity
    finally:
        if not child.stdin.closed:
            child.stdin.close()
        if child.poll() is None:
            child.terminate()
            child.wait(timeout=5)


@pytest.mark.parametrize("runtime_executable", ["powershell.exe", "pwsh.exe"])
def test_news_incident_evidence_and_live_resource_admission(tmp_path, runtime_executable):
    body = r'''
    $broken='ffe1de29c0891cc3a3cf3d602f3d3ee657faa9b8'; $target='b'*40;
    $evidence=[pscustomobject]@{
        incident='COLLECTOR_CLOCK_EVENT_ATOMICITY';broken_revision=$broken;target_revision=$target;
        failure=[pscustomobject]@{resource='news_evidence';route='/api/news-evidence';
            stage='LOCAL_GET_BEFORE_REMOTE_PREPARE';root_cause='NEWS_RECEIPT_ALIAS_CORRELATED_SCAN';
            evidence_sha256='86d7b591c06a295fa3cb4085bb47e6b42c40274878735602ea712c12b1234447'};
        copy_rehearsal=[pscustomobject]@{target_revision=$target;state='API_SYNC_COPY_PASSED';
            baseline_sha256='57add242f930671ff800733ef70290bf9186b8230d0134847285300dc7e3171c';
            old_query_reproduced=$true;semantic_equality_verified=$true;ack_verified=$true;
            records=1919;max_local_get_seconds=5.04;max_post_bytes=25972};
        obligation='EXACT_TARGET_NEWS_READ_AND_REMOTE_ACK_BEFORE_COMMIT'
    };
    $report=$evidence.copy_rehearsal;
    $report | Add-Member -NotePropertyName source_revision -NotePropertyValue $target;
    $report | Add-Member -NotePropertyName source_dirty -NotePropertyValue $false;
    $report | Add-Member -NotePropertyName execution_boundary -NotePropertyValue 'REAL_API_CONTINUOUS_SYNC_ISOLATED_ACK';
    $reportJson=$report | ConvertTo-Json -Depth 8;
    $reportPath=Join-Path $runtimeForwardRoot 'copy-rehearsal.json';
    Write-ControlCenterJsonAtomic -Path $reportPath -Value $report -Depth 8;
    $evidence.copy_rehearsal=[pscustomobject]@{path=$reportPath;sha256=(Get-FileHash $reportPath).Hash.ToLowerInvariant()};
    Assert-CollectorNewsRecoveryEvidence $evidence $broken $target;
    $original=$evidence | ConvertTo-Json -Depth 8;
    foreach($case in @('revision','resource','stage','cause-hash','hash','ack','ack-type','timeout','nan','size','dirty','source','boundary','tampered','missing')) {
        $bad=$original | ConvertFrom-Json;
        $bad.copy_rehearsal=$reportJson | ConvertFrom-Json;
        switch($case) {
            revision {$bad.target_revision='d'*40}
            resource {$bad.failure.resource='market_history'}
            stage {$bad.failure.stage='REMOTE_POST'}
            cause-hash {$bad.failure.evidence_sha256='c'*64}
            hash {$bad.copy_rehearsal.baseline_sha256='e'*64}
            ack {$bad.copy_rehearsal.ack_verified=$false}
            ack-type {$bad.copy_rehearsal.ack_verified='True'}
            timeout {$bad.copy_rehearsal.max_local_get_seconds=20}
            nan {$bad.copy_rehearsal.max_local_get_seconds=[double]::NaN}
            size {$bad.copy_rehearsal.max_post_bytes=80001}
            dirty {$bad.copy_rehearsal.source_dirty=$true}
            source {$bad.copy_rehearsal.source_revision='c'*40}
            boundary {$bad.copy_rehearsal.execution_boundary='DIRECT_HELPER_LOOP'}
        };
        Write-ControlCenterJsonAtomic -Path $reportPath -Value $bad.copy_rehearsal -Depth 8;
        $bad.copy_rehearsal=[pscustomobject]@{path=$reportPath;sha256=(Get-FileHash $reportPath).Hash.ToLowerInvariant()};
        if($case -eq 'tampered'){$bad.copy_rehearsal.sha256='0'*64};
        if($case -eq 'missing'){$bad.copy_rehearsal.path=Join-Path $runtimeForwardRoot 'missing.json'};
        try {Assert-CollectorNewsRecoveryEvidence $bad $broken $target;throw 'unsafe acceptance'}
        catch {if($_.Exception.Message -cne 'COLLECTOR_NEWS_RECOVERY_EVIDENCE_INVALID'){throw}}
    };
    Write-ControlCenterJsonAtomic -Path $reportPath -Value ($reportJson | ConvertFrom-Json) -Depth 8;
    $now=[DateTimeOffset]::UtcNow.ToString('o');
    $status=@{status='DEGRADED';last_success=$now;last_error=$null;
        degraded_resources=@(@{target='cloudflare';resource='news_evidence';error_type='TimeoutError';error_code='TRANSPORT_UNAVAILABLE';error='timed out'});
        resource_observations=@(@{target='cloudflare';resource='heartbeat';status='OK';completed_at=$now},
            @{target='cloudflare';resource='news_evidence';status='ERROR';completed_at=$now})};
    $path=Join-Path $runtimeForwardRoot 'dashboard-sync-status.json';
    $statusJson=$status | ConvertTo-Json -Depth 8;
    foreach($case in @('valid','stale','missing-heartbeat','other-resource','remote-invariant','second-error')) {
        $current=$statusJson | ConvertFrom-Json;
        switch($case) {
            stale {$current.last_success=[DateTimeOffset]::UtcNow.AddMinutes(-3).ToString('o')}
            missing-heartbeat {$current.resource_observations=@($current.resource_observations[1])}
            other-resource {$current.degraded_resources[0].resource='audit'}
            remote-invariant {$current.degraded_resources[0].error_code='REMOTE_STATE_INVARIANT_VIOLATION'}
            second-error {$current.degraded_resources+=@($current.degraded_resources[0])}
        };
        Write-ControlCenterJsonAtomic -Path $path -Value $current -Depth 8;
        $before=(Get-FileHash -LiteralPath $path).Hash;
        try {$result=Get-CollectorNewsDegradedObservation $evidence $broken $target;
            if($case -ne 'valid' -or $result.state -cne 'DEGRADED_RECOVERY_BASELINE'){throw 'unsafe acceptance'}}
        catch {if($case -eq 'valid' -or $_.Exception.Message -cne 'COLLECTOR_NEWS_RECOVERY_OBSERVATION_CHANGED'){throw}};
        if((Get-FileHash -LiteralPath $path).Hash -cne $before){throw 'status was rewritten'}
    };
    Write-Output 'incident evidence scoped; live observation checked; degradation retained'
    '''
    assert _run_contract_with_runtime(
        tmp_path, body, runtime_executable, environment=_isolated_windows_environment(),
    ) == (
        "incident evidence scoped; live observation checked; degradation retained"
    )


@pytest.mark.parametrize("runtime_executable", ["powershell.exe", "pwsh.exe"])
def test_incident_termination_preserves_explicit_collector_absence(tmp_path, runtime_executable):
    body = r'''
    $services=@([pscustomobject]@{Key='collector'});
    $script:held=$true; $script:present=$false;
    function Get-CollectorClockRecoveryContext { [pscustomobject]@{broken_revision=('a'*40);target_revision=('b'*40)} };
    function Test-CollectorClockRecoveryHold { return $script:held };
    function Get-ForecasterProcessSnapshot { param([switch]$RequireCompleteInventory);
        if(-not $RequireCompleteInventory){throw 'incomplete inventory'};
        if($script:present){[pscustomobject]@{ProcessId=123}}
    };
    function Get-ForecasterProcesses { @() };
    function Test-ForecasterServiceProcess { return $true };
    $before=@(Get-WatchdogBusinessOwnerBaseline);
    if($before.Count -ne 1 -or -not $before[0].incident_absence){throw 'absence not captured'};
    Assert-WatchdogBusinessOwnerBaselineUnchanged -Baseline $before;
    $script:present=$true;
    try{Assert-WatchdogBusinessOwnerBaselineUnchanged -Baseline $before;throw 'WRONG'}catch{
        if($_.Exception.Message -cne 'WATCHDOG_TERMINATION_CHANGED_BUSINESS_RUNTIME'){throw}
    };
    $script:present=$false; $script:held=$false;
    try{$null=Get-WatchdogBusinessOwnerBaseline;throw 'WRONG'}catch{
        if($_.Exception.Message -cne 'WATCHDOG_TERMINATION_BUSINESS_OWNER_INVALID:collector'){throw}
    };
    try{Assert-WatchdogBusinessOwnerBaselineUnchanged -Baseline $before;throw 'WRONG'}catch{
        if($_.Exception.Message -cne 'WATCHDOG_TERMINATION_CHANGED_BUSINESS_RUNTIME'){throw}
    };
    $script:held=$true;
    function Get-ForecasterProcessSnapshot { param([switch]$RequireCompleteInventory); throw 'INVENTORY_UNKNOWN' };
    try{$null=Get-WatchdogBusinessOwnerBaseline;throw 'WRONG'}catch{
        if($_.Exception.Message -cne 'INVENTORY_UNKNOWN'){throw}
    };
    'incident absence preserved; normal/changed/unknown rejected'
    '''
    assert _run_contract_with_runtime(tmp_path, body, runtime_executable) == (
        "incident absence preserved; normal/changed/unknown rejected"
    )


def _write_bundle(
    root: Path,
    revision: str,
    label: str,
    *,
    dependency_closed: bool = False,
    schema_version: int | None = None,
) -> None:
    root.mkdir(parents=True, exist_ok=True)
    hashes: dict[str, str] = {}
    for name in CONTROL_FILES:
        payload = (
            json.dumps(
                {
                    "schema_version": 1,
                    "entrypoints": [
                        "xauusd_control_center.ps1",
                        "xauusd_watchdog_guard.ps1",
                    ],
                    "files": list(CONTROL_FILES),
                }
            ).encode()
            if name == "runtime-control-files.json"
            else f"{label}|{name}\n".encode()
        )
        (root / name).write_bytes(payload)
        hashes[name] = hashlib.sha256(payload).hexdigest()
    schema_version = schema_version or (
        BUNDLE_SCHEMA_VERSION if dependency_closed else 1
    )
    file_digest = (
        _canonical_bundle_digest(revision, hashes)
        if schema_version == BUNDLE_SCHEMA_VERSION
        else _legacy_v2_bundle_digest(hashes)
    )
    manifest = {
        "schema_version": schema_version,
        "source_revision": revision,
        "exact_revision": True,
        "created_at": "2026-08-23T00:00:00+00:00",
        "dependency_closed": dependency_closed,
        "source_manifest_sha256": hashes["runtime-control-files.json"],
        "bundle_digest": file_digest,
        "files": hashes,
    }
    if schema_version == BUNDLE_SCHEMA_VERSION:
        manifest["bundle_digest_algorithm"] = BUNDLE_DIGEST_ALGORITHM
    (root / "runtime-control-bundle.json").write_text(
        json.dumps(manifest),
        encoding="utf-8",
    )


def _make_detached_source(
    root: Path,
    *,
    manifest_files: tuple[str, ...] = CONTROL_FILES,
    payloads: dict[str, str] | None = None,
) -> str:
    scripts = root / "scripts"
    scripts.mkdir(parents=True)
    payloads = payloads or {}
    source_files = tuple(dict.fromkeys((*manifest_files, *payloads)))
    for name in source_files:
        payload = (
            json.dumps(
                {
                    "schema_version": 1,
                    "entrypoints": [
                        "xauusd_control_center.ps1",
                        "xauusd_watchdog_guard.ps1",
                    ],
                    "files": list(manifest_files),
                }
            )
            if name == "runtime-control-files.json"
            else payloads.get(name, f"committed|{name}\n")
        )
        (scripts / name).write_text(payload, encoding="utf-8")
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


def _make_real_control_source(root: Path, *, boundary: str = "") -> str:
    scripts = root / "scripts"
    scripts.mkdir(parents=True)
    for name in CONTROL_FILES:
        shutil.copy2(ROOT / "scripts" / name, scripts / name)
    if boundary:
        shutil.copy2(ROOT / "scripts/windows-service-launch-contract.json", scripts / "windows-service-launch-contract.json")
        entrypoint = scripts / "xauusd_control_center.ps1"
        source = entrypoint.read_text(encoding="utf-8")
        assert source.count("switch ($Action) {") == 1
        diagnostic, boundary = boundary.split("$null = Get-Command Get-FileHash -ErrorAction Stop", 1)
        source = source.replace('$ErrorActionPreference = "Stop"',
                                '$ErrorActionPreference = "Stop"\n' + diagnostic, 1)
        boundary = "$null = Get-Command Get-FileHash -ErrorAction Stop" + boundary
        entrypoint.write_text(source.replace("switch ($Action) {", boundary + "\nswitch ($Action) {"), encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "Contract Test"], cwd=root, check=True)
    subprocess.run(
        ["git", "config", "user.email", "contract-test@example.invalid"],
        cwd=root,
        check=True,
    )
    subprocess.run(["git", "add", "scripts"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "real control bundle"], cwd=root, check=True)
    revision = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=root,
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    subprocess.run(["git", "checkout", "--detach", "-q", revision], cwd=root, check=True)
    return revision


def run_staged_installer_active_rehearsal(tmp_path):
    """Real lifecycle with child-inherited, fail-before-mutation environment adapters."""
    # Hosted Windows TEMP can use an 8.3 user alias while PowerShell resolves
    # the same directory to its long name. Compare one physical root identity.
    tmp_path = tmp_path.resolve(strict=True)
    boundary = (ROOT / "tests/fixtures/control_plane_staged_boundary.ps1").read_text(encoding="utf-8")
    boundary = boundary.replace("__FIXTURE_ROOT__", str(tmp_path)).replace("__FIXTURE_ID__", uuid.uuid4().hex)
    source = tmp_path / "source"
    revision = _make_real_control_source(source, boundary=boundary)
    runtime = tmp_path / "runtime"
    broken = _make_real_control_source(runtime)
    shutil.copyfile(ROOT / "scripts/windows-service-launch-contract.json", runtime / "scripts/windows-service-launch-contract.json")
    subprocess.run(["git", "add", "scripts/windows-service-launch-contract.json"], cwd=runtime, check=True)
    subprocess.run(["git", "commit", "-qm", "fixture business launch contract"], cwd=runtime, check=True)
    broken = subprocess.run(["git", "rev-parse", "HEAD"], cwd=runtime, check=True, capture_output=True, text=True).stdout.strip()
    repository = tmp_path / "repository"
    repository.mkdir()
    sleeper = tmp_path / "business.py"
    sleeper.write_text("import sys\nsys.stdin.buffer.read()\n", encoding="utf-8")
    sync_script = tmp_path / "staged_sync_owner.py"
    shutil.copyfile(ROOT / "tests/fixtures/staged_sync_owner.py", sync_script)
    owners = {}
    children = []
    environment = _isolated_windows_environment()
    (tmp_path / "fixture-owned.json").write_text(json.dumps({"fixture": str(tmp_path)}), encoding="utf-8")
    certificate, key = tmp_path / "loopback.crt", tmp_path / "loopback.key"
    openssl = shutil.which("openssl") or str(Path(shutil.which("git")).parents[1] / "usr/bin/openssl.exe")
    subprocess.run([openssl, "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
                    "-keyout", str(key), "-out", str(certificate), "-subj", "/CN=isolated-fixture",
                    "-addext", "subjectAltName=IP:127.0.0.1"], check=True, capture_output=True,
                   timeout=15, creationflags=subprocess.CREATE_NO_WINDOW)
    environment["SSL_CERT_FILE"] = str(certificate)
    timeout_mode, release_request = threading.Event(), threading.Event()
    requests = []

    class Provider(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def respond(self, value):
            payload = json.dumps(value).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            try:
                self.wfile.write(payload)
            except (ConnectionError, ssl.SSLError):
                if not (timeout_mode.is_set() and self.path.startswith("/api/news-evidence?")):
                    raise
                # Only the intentionally timed-out client is allowed to close.

        def do_GET(self):
            requests.append(("GET", self.path, 0))
            if self.path == "/api/critical-status":
                self.respond({"generated_at": "2026-09-05T00:00:00+00:00", "system": {"online": True}})
            elif self.path.startswith("/api/news-evidence?"):
                if timeout_mode.is_set():
                    release_request.wait(35)
                self.respond({"snapshot_id": "a" * 64, "total": 0, "items": [], "has_more": False})
            else:
                self.send_error(404)

        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0))
            assert length < 80_000
            raw = self.rfile.read(length)
            payload = json.loads(raw)
            requests.append(("POST", self.path, length))
            if self.path == "/api/ingest":
                self.respond({"ok": True})
            elif self.path == "/api/news-evidence":
                assert "prepare_snapshot" in payload or "cleanup_active_snapshot" in payload
                result = ({"active": True, "next_offset": 0} if "prepare_snapshot" in payload else {
                    "cleanup": "advanced", "cleanup_pending": False,
                    "deleted_records": 0, "deleted_batches": 0, "deleted_staging": 0,
                })
                self.respond({**result, "status": "OK", "snapshot_id": "a" * 64,
                              "contract_version": "news-evidence-paged-v2",
                              "request_sha256": hashlib.sha256(raw).hexdigest()})
            else:
                self.send_error(404)

    provider = ThreadingHTTPServer(("127.0.0.1", 0), Provider)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(certificate, key)
    provider.socket = context.wrap_socket(provider.socket, server_side=True)
    serving = threading.Thread(target=provider.serve_forever, daemon=True)
    serving.start()
    status_path = runtime / ".local/forward/dashboard-sync-status.json"

    def await_resource(expected):
        deadline = time.monotonic() + 40
        while time.monotonic() < deadline:
            if status_path.exists():
                try:
                    status = json.loads(status_path.read_text(encoding="utf-8"))
                except json.JSONDecodeError:
                    time.sleep(0.1)
                    continue
                news = next((row for row in status.get("resource_observations", []) if row["resource"] == "news_evidence"), {})
                if news.get("status") == expected:
                    return status
            time.sleep(0.1)
        raise AssertionError(f"real Sync resource did not reach {expected}")

    try:
        for key in ("quote", "annotator", "api", "sync"):
            command = [sys.executable, str(sleeper), key] if key != "sync" else [
                sys.executable, str(sync_script), "--fixture-root", str(tmp_path),
                "--source-root", str(ROOT), "--provider", f"https://127.0.0.1:{provider.server_port}",
            ]
            child = subprocess.Popen(command, creationflags=subprocess.CREATE_NO_WINDOW, env=environment,
                                     stdin=subprocess.PIPE if key != "sync" else subprocess.DEVNULL,
                                     stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            children.append(child)
            owners[key] = child.pid
        (tmp_path / "business.json").write_text(json.dumps(owners), encoding="utf-8")
        healthy = await_resource("OK")
        assert healthy["status"] == "OK"
        prior = _exited_windows_child_identity()
        body = rf'''
        $null = . '{source / 'scripts/xauusd_control_center.ps1'}' -Action CodeRevision -RuntimeRoot '{runtime}' -RepositoryRoot '{repository}';
        $control=Join-Path $repositoryRoot '.local\runtime-control';
        $bundle=New-VerifiedRuntimeControlBundleStage -SourceRoot '{source}' -SourceRevision '{revision}' -StageRoot $control -RequireImmutableSource;
        $descriptor=Get-WatchdogSingletonDescriptor;
        $prior=[pscustomobject]@{{schema_version='watchdog-owner-v2';instance_id=[guid]::NewGuid().ToString('N');
            process_id={prior['pid']};process_start_token='{prior['token']}';launcher_pid={prior['pid']};launcher_start_token='{prior['token']}';
            user_sid=$descriptor.user_sid;runtime_root_hash=$descriptor.runtime_root_hash;repository_root_hash=$descriptor.repository_root_hash;
            mutex_identity_hash=$descriptor.mutex_identity_hash;installed_control_revision='{revision}';bundle_digest=$bundle.bundle_digest;
            mode='ACTIVE';acquired_at='{prior['token']}';install_transaction_id=$null}};
        $null=Write-WatchdogOwnerReceipt -Receipt $prior;
        Write-ControlCenterJsonAtomic -Path $releaseControlStatePath -Value @{{stable=@{{windows_revision='{broken}';worker_version_id='fixture-worker'}};transaction=$null}} -Depth 6;
        # Only source/provider/snapshot admission is supplied by the fixture.
        # Isolation assertions, mutex reservation, install, handoff and commit are real.
        function Get-CollectorClockRecoveryBaseline {{
            param($VerifiedSourceRoot,$TargetRevision)
            Assert-FixturePath $VerifiedSourceRoot;
            $snapshot=Get-ControlPlaneIsolationSnapshot -RequireCompleteInventory;
            [pscustomobject]@{{incident='COLLECTOR_CLOCK_EVENT_ATOMICITY';state='DEGRADED_RECOVERY_BASELINE';
                broken_revision='{broken}';target_revision=$TargetRevision;user_sid=$descriptor.user_sid;
                runtime_root_hash=$descriptor.runtime_root_hash;repository_root_hash=$descriptor.repository_root_hash;
                previous_watchdog_receipt=$prior;services=$snapshot.services;
                snapshot=[pscustomobject]@{{decision_time='2026-09-04T16:05:00.000000+00:00';snapshot_hash='b139c8a9d913c237e8e9e3ebc677a1144cd8ad2f9e0adee6b62ed8cd2a7fa5ee'}}}}
        }};
        $owner=$null;
        try {{
            $before=Get-ControlPlaneIsolationSnapshot -RequireCompleteInventory;
            $result=Invoke-ControlPlaneInstall -VerifiedSourceRoot '{source}' -TargetRevision '{revision}' -CollectorClockRecovery;
            if($result.status -cne 'COMMITTED'){{throw 'install not committed'}};
            $owner=$result.new_watchdog_identity;
            $heartbeat=Assert-CurrentWatchdogHeartbeat -Owner $owner -ExpectedRevision '{revision}';
            if($heartbeat.supervision_mode -cne 'ACTIVE'){{throw 'not active'}};
            Assert-ControlPlaneIsolationSnapshot -Before $before -After (Get-ControlPlaneIsolationSnapshot -RequireCompleteInventory);
            # An additional real launcher cannot acquire the held OS mutex.
            $second=Start-WatchdogReplacement -PassThru;
            if(-not $second.WaitForExit(10000)){{throw 'second watchdog did not exit'}};
            $same=@(Get-VerifiedWatchdogOwners -RequireCompleteInventory);
            if($same.Count -ne 1 -or $same[0].process_id -ne $owner.process_id){{throw 'singleton changed'}};
            Write-Output 'ACTIVE_COMMITTED|BUSINESS_PRESERVED|SECOND_OWNER_REJECTED';
        }} finally {{
            foreach($live in @(Get-VerifiedWatchdogOwners -RequireCompleteInventory)){{ Stop-VerifiedWatchdogOwner -Identity $live }};
            if(@(Get-VerifiedWatchdogOwners -RequireCompleteInventory).Count -ne 0){{throw 'staged owner remained'}};
        }}
        '''
        script = tmp_path / "installer.ps1"
        script.write_text(body, encoding="utf-8")
        result = subprocess.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script)],
                                capture_output=True, text=True, timeout=150, creationflags=subprocess.CREATE_NO_WINDOW, env=environment)
        assert result.returncode == 0, result.stdout + result.stderr
        assert "ACTIVE_COMMITTED|BUSINESS_PRESERVED|SECOND_OWNER_REJECTED" in result.stdout
        assert all(child.poll() is None for child in children)
        # Advance only the isolated resource's due marker to exercise the same
        # live owner's next legal scheduled attempt, never call its worker twice.
        timeout_mode.set()
        schedule_path = runtime / ".local/forward/dashboard-resource-schedule-state-fixture.json"
        schedule = json.loads(schedule_path.read_text(encoding="utf-8"))
        schedule["resources"]["news_evidence"]["next_run_at"] = "2000-01-01T00:00:00+00:00"
        temporary = schedule_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(schedule), encoding="utf-8")
        temporary.replace(schedule_path)
        degraded = await_resource("ERROR")
        assert degraded["status"] == "DEGRADED"
        assert degraded["last_success"] != healthy["last_success"]
        assert children[-1].poll() is None
        failure = degraded["degraded_resources"]
        assert len(failure) == 1 and failure[0]["resource"] == "news_evidence"
        assert failure[0]["error_type"] == "TimeoutError"
        assert failure[0]["duration_ms"] >= 19_000
        assert len([row for row in requests if row[0] == "GET" and row[1].startswith("/api/news-evidence?")]) == 2
    finally:
        release_request.set()
        (tmp_path / "stop-sync").touch()
        # The OS owner receipt is also the emergency fixture cleanup authority.
        # Never rely on successful installer completion to contain its children.
        cleanup = rf'''
        $ErrorActionPreference='Stop';
        $fixture='{tmp_path}';
        $path=Join-Path $fixture 'runtime\.local\forward\watchdog-owner-v2.json';
        if(Test-Path -LiteralPath $path){{
            $r=Get-Content -LiteralPath $path -Raw -Encoding UTF8|ConvertFrom-Json;
            foreach($e in @(@{{id=$r.process_id;token=$r.process_start_token}},@{{id=$r.launcher_pid;token=$r.launcher_start_token}})){{
                $p=Get-CimInstance Win32_Process -Filter ('ProcessId='+[int]$e.id);
                if($p){{
                    if(-not $p.CommandLine.Contains($fixture+'\') -or ([DateTimeOffset]$p.CreationDate).UtcTicks -ne ([DateTimeOffset]$e.token).UtcTicks){{throw 'FIXTURE_CLEANUP_IDENTITY_MISMATCH'}};
                    Stop-Process -Id ([int]$e.id) -Force;
                }}
            }}
        }}
        '''
        cleaned = subprocess.run(["powershell.exe", "-NoProfile", "-Command", cleanup],
                                 capture_output=True, text=True, timeout=20, creationflags=subprocess.CREATE_NO_WINDOW)
        for child in children:
            if child.poll() is None:
                if child.pid == owners.get("sync"):
                    try:
                        child.wait(timeout=8)
                    except subprocess.TimeoutExpired:
                        child.terminate()
                else:
                    child.stdin.close()
            child.wait(timeout=10)
            child.stderr.close()
        provider.shutdown()
        provider.server_close()
        serving.join(timeout=3)
        assert cleaned.returncode == 0, cleaned.stderr


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
          [pscustomobject]@{{business_runtime_revision='runtime';services=[pscustomobject]@{{quote=@($p);collector=@($p);annotator=@($p);api=@($p);sync=@($p);broadcast=@()}}}}
        }};
        function Assert-ControlPlaneIsolationBaseline {{ param($Snapshot,$ReleaseState); $script:timeline+='baseline' }};
        function Assert-ControlPlaneIsolationSnapshot {{ param($Before,$After,$ReleaseState); $script:timeline+='isolation' }};
        function New-VerifiedRuntimeControlBundleStage {{ param($SourceRoot,$SourceRevision,$StageRoot,[switch]$RequireImmutableSource); $script:timeline+='stage'; [pscustomobject]@{{source_revision='{target_revision}'}} }};
        function Invoke-RuntimeControlBundleStartupPreflight {{ param($StageRoot,$ExpectedRevision,$RepositoryRootForPreflight); $script:timeline+='preflight'; [pscustomobject]@{{control_bundle_revision=$ExpectedRevision}} }};
        function Suspend-ControlPlaneSupervision {{ $script:timeline+='suspend'; @{{}} }};
        function Wait-ControlPlaneGuardQuiesced {{ $script:timeline+='guard' }};
        function Restore-ControlPlaneSupervision {{ param($State); $script:timeline+='supervision' }};
        function Stop-VerifiedWatchdogOwner {{ param($Identity); $script:timeline+='stop'; $script:owners=@() }};
        function Stop-ScheduledTask {{ param($TaskName); if ($TaskName -notlike 'XAUUSD-Contract-*') {{throw 'unsafe task name'}} }};
        function Install-VerifiedRuntimeControlBundleStage {{ param($StageRoot,$ControlRoot,$BackupRoot); if($script:owners.Count-ne 0){{throw 'two owners'}}; $script:timeline+='install'; [pscustomobject]@{{source_revision='{target_revision}'}} }};
        function Start-WatchdogReplacement {{ param([switch]$PassThru,$InstallTransactionId); if($script:owners.Count-ne 0){{throw 'two owners'}}; $script:timeline+='start'; $script:owners=@({new}); [pscustomobject]@{{Id=201}} }};
        function Wait-VerifiedWatchdogHandoff {{ param($ExpectedRevision,$PreviousIdentity,$ExpectedMode,$ExpectedInstallTransactionId,$Timeout); if($script:owners.Count-ne 1){{throw 'owner count'}}; $script:timeline+="heartbeat:$ExpectedMode"; return $script:owners[0] }};
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


@pytest.mark.skipif(shutil.which("pwsh.exe") is None, reason="PowerShell 7 is required")
def test_legacy_v2_bundle_digest_is_reconstructed_identically_across_runtimes(
    tmp_path: Path,
) -> None:
    control = tmp_path / "legacy-control"
    revision = "a" * 40
    _write_bundle(
        control,
        revision,
        "legacy",
        dependency_closed=True,
        schema_version=2,
    )
    body = (
        f"$bundle=Get-RuntimeControlBundleIdentityAtRoot -ControlRoot '{control}' "
        "-RequireDependencyClosure; "
        'Write-Output "$($bundle.bundle_digest)|$($bundle.legacy_v2_digest_verified)"'
    )
    expected = json.loads(
        (control / "runtime-control-bundle.json").read_text(encoding="utf-8")
    )["bundle_digest"]
    assert _run_contract_with_runtime(tmp_path, body, "powershell.exe") == (
        f"{expected}|True"
    )
    assert _run_contract_with_runtime(tmp_path, body, "pwsh.exe") == (
        f"{expected}|True"
    )

    manifest_path = control / "runtime-control-bundle.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["bundle_digest"] = "0" * 64
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    rejected = body.replace(
        'Write-Output "$($bundle.bundle_digest)|$($bundle.legacy_v2_digest_verified)"',
        "if($bundle){Write-Output accepted}else{Write-Output rejected}",
    )
    assert _run_contract_with_runtime(tmp_path, rejected, "powershell.exe") == "rejected"
    assert _run_contract_with_runtime(tmp_path, rejected, "pwsh.exe") == "rejected"


@pytest.mark.skipif(shutil.which("pwsh.exe") is None, reason="PowerShell 7 is required")
def test_canonical_bundle_install_is_runtime_format_and_root_independent(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    revision = _make_detached_source(source)
    stage5 = tmp_path / "stage-ps5"
    stage7 = tmp_path / "stage-ps7"

    def stage_with(runtime: str, stage: Path) -> str:
        return _run_contract_with_runtime(
            tmp_path,
            f"$bundle=New-VerifiedRuntimeControlBundleStage -SourceRoot '{source}' "
            f"-SourceRevision '{revision}' -StageRoot '{stage}' "
            "-RequireImmutableSource; Write-Output $bundle.bundle_digest",
            runtime,
        )

    digest5 = stage_with("powershell.exe", stage5)
    digest7 = stage_with("pwsh.exe", stage7)
    assert digest5 == digest7

    hashes = json.loads(
        (stage5 / "runtime-control-bundle.json").read_text(encoding="utf-8-sig")
    )["files"]
    entries = list(reversed(list(hashes.items())))
    powershell_entries = ";".join(
        f"$reversed['{name}']='{digest}'" for name, digest in entries
    )
    digest_body = (
        "$forward=@{};$reversed=@{};"
        + ";".join(f"$forward['{name}']='{digest}'" for name, digest in hashes.items())
        + ";"
        + powershell_entries
        + f";$a=Get-RuntimeControlBundleDigest -SchemaVersion 3 "
        f"-SourceRevision '{revision}' -Hashes $forward;"
        f"$b=Get-RuntimeControlBundleDigest -SchemaVersion 3 "
        f"-SourceRevision '{revision}' -Hashes $reversed;"
        'Write-Output "$a|$b"'
    )
    for runtime in ("powershell.exe", "pwsh.exe"):
        assert _run_contract_with_runtime(tmp_path, digest_body, runtime) == (
            f"{digest5}|{digest5}"
        )

    manifest_path = stage5 / "runtime-control-bundle.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    reordered = {
        "files": dict(reversed(list(manifest["files"].items()))),
        "bundle_digest": manifest["bundle_digest"],
        "source_manifest_sha256": manifest["source_manifest_sha256"],
        "bundle_digest_algorithm": manifest["bundle_digest_algorithm"],
        "dependency_closed": manifest["dependency_closed"],
        "created_at": manifest["created_at"],
        "exact_revision": manifest["exact_revision"],
        "source_revision": manifest["source_revision"],
        "schema_version": manifest["schema_version"],
    }
    manifest_path.write_text(
        json.dumps(reordered, indent=3).replace("\n", "\r\n") + "\r\n",
        encoding="utf-8",
        newline="",
    )
    verify_body = (
        f"$bundle=Get-RuntimeControlBundleIdentityAtRoot -ControlRoot '{stage5}' "
        "-RequireDependencyClosure; Write-Output $bundle.bundle_digest"
    )
    assert _run_contract_with_runtime(tmp_path, verify_body, "powershell.exe") == digest5
    assert _run_contract_with_runtime(tmp_path, verify_body, "pwsh.exe") == digest5

    control = tmp_path / "control"
    backup = tmp_path / "backup"
    _write_bundle(control, "b" * 40, "old")
    install_body = (
        f"$bundle=Install-VerifiedRuntimeControlBundleStage -StageRoot '{stage5}' "
        f"-ControlRoot '{control}' -BackupRoot '{backup}'; "
        "Write-Output $bundle.bundle_digest"
    )
    assert _run_contract_with_runtime(tmp_path, install_body, "pwsh.exe") == digest5
    moved = tmp_path / "moved-control"
    shutil.copytree(control, moved)
    moved_body = (
        f"$bundle=Get-RuntimeControlBundleIdentityAtRoot -ControlRoot '{moved}' "
        "-RequireDependencyClosure; Write-Output $bundle.bundle_digest"
    )
    assert _run_contract_with_runtime(tmp_path, moved_body, "powershell.exe") == digest5


@pytest.mark.parametrize(
    "mutation",
    (
        "changed_file",
        "changed_hash",
        "changed_path",
        "missing_file",
        "extra_file_entry",
        "changed_revision",
        "malformed_manifest",
    ),
)
def test_canonical_bundle_identity_mutations_fail_closed(
    tmp_path: Path, mutation: str,
) -> None:
    source = tmp_path / f"source-{mutation}"
    revision = _make_detached_source(source)
    stage = tmp_path / f"stage-{mutation}"
    _run_contract(
        tmp_path,
        f"New-VerifiedRuntimeControlBundleStage -SourceRoot '{source}' "
        f"-SourceRevision '{revision}' -StageRoot '{stage}' "
        "-RequireImmutableSource | Out-Null",
    )
    manifest_path = stage / "runtime-control-bundle.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    first = CONTROL_FILES[0]
    if mutation == "changed_file":
        (stage / first).write_text("tampered\n", encoding="utf-8")
    elif mutation == "changed_hash":
        manifest["files"][first] = "0" * 64
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    elif mutation == "changed_path":
        manifest["files"][f"renamed-{first}"] = manifest["files"].pop(first)
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    elif mutation == "missing_file":
        (stage / first).unlink()
    elif mutation == "extra_file_entry":
        manifest["files"]["unexpected.ps1"] = "0" * 64
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    elif mutation == "changed_revision":
        manifest["source_revision"] = "f" * 40
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    elif mutation == "malformed_manifest":
        manifest_path.write_text("{not-json", encoding="utf-8")
    result = _run_contract(
        tmp_path,
        f"$bundle=Get-RuntimeControlBundleIdentityAtRoot -ControlRoot '{stage}' "
        "-RequireDependencyClosure; if($bundle){Write-Output accepted}else{Write-Output rejected}",
    )
    assert result == "rejected"


def test_canonical_digest_binds_revision_and_relative_path(tmp_path: Path) -> None:
    first_hash = "1" * 64
    revision = "a" * 40
    body = (
        f"$base=@{{'control.ps1'='{first_hash}'}};"
        f"$renamed=@{{'renamed.ps1'='{first_hash}'}};"
        "$a=Get-RuntimeControlBundleDigest -SchemaVersion 3 "
        f"-SourceRevision '{revision}' -Hashes $base;"
        "$b=Get-RuntimeControlBundleDigest -SchemaVersion 3 "
        f"-SourceRevision '{'b' * 40}' -Hashes $base;"
        "$c=Get-RuntimeControlBundleDigest -SchemaVersion 3 "
        f"-SourceRevision '{revision}' -Hashes $renamed;"
        'Write-Output "$($a-ne$b)|$($a-ne$c)"'
    )
    assert _run_contract(tmp_path, body) == "True|True"


def test_bundle_manifest_owns_direct_and_transitive_runtime_dependencies(
    tmp_path: Path,
) -> None:
    without_worker = tuple(
        name for name in CONTROL_FILES if name != "worker_cpu_evidence.ps1"
    )
    direct = tmp_path / "direct"
    direct_revision = _make_detached_source(
        direct,
        manifest_files=without_worker,
        payloads={
            "xauusd_control_center.ps1": (
                '. (Join-Path $PSScriptRoot "worker_cpu_evidence.ps1")\n'
            ),
            "worker_cpu_evidence.ps1": "# present but undeclared\n",
        },
    )
    rejected = _run_contract(
        tmp_path,
        f"try {{ New-VerifiedRuntimeControlBundleStage -SourceRoot '{direct}' "
        f"-SourceRevision '{direct_revision}' -StageRoot '{tmp_path / 'direct-stage'}' "
        "| Out-Null; Write-Output accepted } catch { Write-Output $_.Exception.Message }",
    )
    assert rejected == (
        "CONTROL_BUNDLE_UNDECLARED_DEPENDENCY:"
        "xauusd_control_center.ps1:worker_cpu_evidence.ps1"
    )

    transitive = tmp_path / "transitive"
    transitive_revision = _make_detached_source(
        transitive,
        payloads={
            "worker_cpu_evidence.ps1": (
                '. (Join-Path $PSScriptRoot "runtime_nested.ps1")\n'
            ),
            "runtime_nested.ps1": "# present but undeclared\n",
        },
    )
    rejected = _run_contract(
        tmp_path,
        f"try {{ New-VerifiedRuntimeControlBundleStage -SourceRoot '{transitive}' "
        f"-SourceRevision '{transitive_revision}' "
        f"-StageRoot '{tmp_path / 'transitive-stage'}' | Out-Null; "
        "Write-Output accepted } catch { Write-Output $_.Exception.Message }",
    )
    assert rejected == (
        "CONTROL_BUNDLE_UNDECLARED_DEPENDENCY:"
        "worker_cpu_evidence.ps1:runtime_nested.ps1"
    )


def test_clean_staged_bundle_produces_quiesced_preflight_without_checkout_fallback(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    revision = _make_real_control_source(source)
    stage = tmp_path / "stage"
    result = _run_contract(
        tmp_path,
        f"$bundle=New-VerifiedRuntimeControlBundleStage -SourceRoot '{source}' "
        f"-SourceRevision '{revision}' -StageRoot '{stage}' -RequireImmutableSource; "
        f"$receipt=Invoke-RuntimeControlBundleStartupPreflight -StageRoot '{stage}' "
        f"-ExpectedRevision '{revision}' -RepositoryRootForPreflight '{ROOT}'; "
        'Write-Output "$($receipt.supervision_mode)|$($receipt.dependency_closed)"',
    )
    assert result == "QUIESCED|True"

    (stage / "worker_cpu_evidence.ps1").unlink()
    rejected = _run_contract(
        tmp_path,
        f"try {{ Invoke-RuntimeControlBundleStartupPreflight -StageRoot '{stage}' "
        f"-ExpectedRevision '{revision}' -RepositoryRootForPreflight '{ROOT}' "
        "| Out-Null; Write-Output accepted } "
        "catch { Write-Output $_.Exception.Message }",
    )
    assert rejected == "CONTROL_BUNDLE_STARTUP_PREFLIGHT_FAILED"


def run_staged_activation_withdrawal_rehearsal(tmp_path):
    """Real launcher/bundle/mutex/heartbeat; withdraw before granting ACTIVE."""
    tmp_path = tmp_path.resolve(strict=True)
    source = tmp_path / "source"
    boundary = (ROOT / "tests/fixtures/control_plane_staged_boundary.ps1").read_text(encoding="utf-8")
    boundary = boundary.replace("__FIXTURE_ROOT__", str(tmp_path)).replace("__FIXTURE_ID__", uuid.uuid4().hex)
    (tmp_path / "business.json").write_text("{}", encoding="utf-8")
    revision = _make_real_control_source(source, boundary=boundary)
    runtime = tmp_path / "runtime"
    _make_real_control_source(runtime)
    shutil.copyfile(ROOT / "scripts/windows-service-launch-contract.json",
                    runtime / "scripts/windows-service-launch-contract.json")
    subprocess.run(["git", "add", "scripts/windows-service-launch-contract.json"], cwd=runtime, check=True)
    subprocess.run(["git", "commit", "-qm", "fixture runtime launch authority"], cwd=runtime, check=True)
    prior = _exited_windows_child_identity()
    # Living stand-ins prove preservation, not the health of real business services.
    preserved = [subprocess.Popen(
        [sys.executable, "-c", "import sys; sys.stdin.buffer.read()"],
        stdin=subprocess.PIPE,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    ) for _ in range(4)]
    body = rf'''
    $null = . '{source / 'scripts/xauusd_control_center.ps1'}' -Action CodeRevision -RuntimeRoot $moduleRoot -RepositoryRoot $repositoryRoot;
    $control=Join-Path $repositoryRoot '.local\runtime-control';
    $bundle=New-VerifiedRuntimeControlBundleStage -SourceRoot '{source}' -SourceRevision '{revision}' -StageRoot $control -RequireImmutableSource;
    $descriptor=Get-WatchdogSingletonDescriptor;
    $old=[pscustomobject]@{{schema_version='watchdog-owner-v2';instance_id=[guid]::NewGuid().ToString('N');
        process_id={prior['pid']};process_start_token='{prior['token']}';launcher_pid={prior['pid']};launcher_start_token='{prior['token']}';
        user_sid=$descriptor.user_sid;runtime_root_hash=$descriptor.runtime_root_hash;repository_root_hash=$descriptor.repository_root_hash;
        mutex_identity_hash=$descriptor.mutex_identity_hash;installed_control_revision='{revision}';bundle_digest=$bundle.bundle_digest;
        mode='ACTIVE';acquired_at='{prior['token']}';install_transaction_id=$null}};
    $null=Write-WatchdogOwnerReceipt -Receipt $old;
    $transaction=[guid]::NewGuid().ToString('N');
    $installer=Get-ControlPlaneProcessIdentity -ProcessId $PID -RequireCompleteInventory;
    Write-ControlPlaneInstallState @{{transaction_id=$transaction;phase='VERIFY_QUIESCED_HANDOFF';target_revision='{revision}';install_owner_identity=$installer}};
    $launcher=$null; $owner=$null;
    try {{
        $launcher=Start-WatchdogReplacement -InstallTransactionId $transaction -PassThru;
        $owner=Wait-VerifiedWatchdogHandoff -ExpectedRevision '{revision}' -PreviousIdentity $old `
            -ExpectedMode QUIESCED -ExpectedInstallTransactionId $transaction -RequireCompleteInventory;
        if ([int]$owner.process_id -eq [int]$old.process_id -and $owner.process_start_token -eq $old.process_start_token) {{throw 'stale owner reused'}};
        if ($owner.watchdog_owner_receipt.mode -cne 'QUIESCED_INSTALL') {{throw 'unsafe activation'}};
        Write-ControlPlaneInstallState @{{phase='FAILED';failure='fixture withdrawal before activation'}};
        if (-not $launcher.WaitForExit(10000)) {{throw 'launcher did not exit after withdrawal'}};
        if (Get-ControlPlaneProcessIdentity -ProcessId ([int]$owner.process_id)) {{throw 'watchdog survived withdrawal'}};
        if (Test-Path -LiteralPath $watchdogOwnerReceiptPath) {{throw 'receipt remained after exact exit'}};
        Write-Output 'real quiesced handoff and clean withdrawal passed'
    }} catch {{
        $details=[ordered]@{{failure=$_.Exception.Message;heartbeat=$null;launcher_exited=$null}};
        if(Test-Path -LiteralPath $watchdogHeartbeatPath){{
            $details.heartbeat=Get-Content -LiteralPath $watchdogHeartbeatPath -Raw -Encoding UTF8 | ConvertFrom-Json
        }};
        if($launcher){{$details.launcher_exited=$launcher.HasExited}};
        Write-ControlCenterJsonAtomic -Path (Join-Path $script:fixtureRoot 'handoff-failure.json') -Value $details;
        throw
    }} finally {{
        Write-ControlPlaneInstallState @{{phase='FAILED';failure='fixture cleanup'}};
        if ($owner -and (Get-ControlPlaneProcessIdentity -ProcessId ([int]$owner.process_id))) {{
            Stop-VerifiedWatchdogOwner -Identity $owner
        }};
        if ($launcher -and -not $launcher.HasExited) {{
            if (-not $launcher.WaitForExit(10000)) {{throw 'staged launcher containment unresolved'}}
        }}
    }}
    '''
    try:
        assert _run_contract_with_runtime(tmp_path, body, "powershell.exe", environment=_isolated_windows_environment()) == "real quiesced handoff and clean withdrawal passed"
        assert all(process.poll() is None for process in preserved)
    finally:
        for process in preserved:
            process.stdin.close()
            if process.poll() is None:
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.terminate()
            process.wait(timeout=5)


def test_bundle_install_is_complete_and_restorable(tmp_path: Path) -> None:
    old_revision, new_revision = "a" * 40, "b" * 40
    control = tmp_path / "control"
    stage = tmp_path / "stage"
    backup = tmp_path / "backup"
    _write_bundle(control, old_revision, "old")
    _write_bundle(stage, new_revision, "new", dependency_closed=True)
    result = _run_contract(
        tmp_path,
        f"$new=Install-VerifiedRuntimeControlBundleStage -StageRoot '{stage}' "
        f"-ControlRoot '{control}' -BackupRoot '{backup}'; "
        f"$old=Restore-RuntimeControlBundleBackup -BackupRoot '{backup}' "
        f"-ControlRoot '{control}'; Write-Output \"$($new.source_revision),$($old.source_revision)\"",
    )
    assert result == f"{new_revision},{old_revision}"
    assert all(
        (control / name).read_text() == f"old|{name}\n"
        for name in CONTROL_FILES
        if name != "runtime-control-files.json"
    )
    assert json.loads((control / "runtime-control-files.json").read_text())[
        "files"
    ] == list(CONTROL_FILES)


def test_watchdog_repairs_interrupted_bundle_copy_after_installer_exit(
    tmp_path: Path,
) -> None:
    target_revision = "b" * 40
    repository = tmp_path / "repository"
    control = repository / ".local" / "runtime-control"
    stage = repository / ".local" / ".cps-recovery"
    backup = repository / ".local" / ".cpb-recovery"
    _write_bundle(control, "a" * 40, "old")
    _write_bundle(stage, target_revision, "new")
    _write_bundle(backup, "a" * 40, "old")
    (control / CONTROL_FILES[0]).write_text("interrupted\n", encoding="utf-8")
    body = textwrap.dedent(
        f"""
        Write-ControlPlaneInstallState @{{transaction_id='txn';target_revision='{target_revision}';phase='INSTALL_BUNDLE';stage_root='{stage}';backup_root='{backup}';install_owner_identity=[pscustomobject]@{{process_id=999999;process_start_token='gone'}}}};
        function Get-ControlPlaneProcessIdentity {{ param($ProcessId); return $null }};
        $repaired=Repair-AbandonedControlPlaneBundleForWatchdog;
        Write-Output "$($repaired.source_revision)|$((Get-ControlPlaneInstallState).recovery)"
        """
    ).replace("\n", " ")
    assert _run_contract(tmp_path, body) == (
        f"{target_revision}|FORWARD_REPAIRED_INTERRUPTED_BUNDLE_COPY"
    )


def _abandoned_activation_mocks(
    target_revision: str,
    phase: str,
    *,
    transaction_id: str = "txn",
    bundle_verified: str = "$true",
) -> str:
    return textwrap.dedent(
        f"""
        $script:phase='{phase}'; $script:checks=@();
        $current=[pscustomobject]@{{process_id=$PID;process_start_token='{CURRENT_START_TOKEN}'}};
        $old=[pscustomobject]@{{process_id=100;process_start_token='{OLD_START_TOKEN}'}};
        $baseline=[pscustomobject]@{{business_runtime_revision='runtime';release_state_hash='state';release_history_hash='history';services=[pscustomobject]@{{}}}};
        $script:state=[pscustomobject]@{{transaction_id='{transaction_id}';phase=$script:phase;target_revision='{target_revision}';previous_revision='{'a' * 40}';bundle_hash_verified={bundle_verified};install_owner_identity=[pscustomobject]@{{process_id=999999;process_start_token='{DEAD_INSTALLER_START_TOKEN}'}};old_watchdog_identity=$old;isolation_before=$baseline;backup_root='backup';supervision_state=$null}};
        function Get-ControlPlaneInstallState {{ $script:state.phase=$script:phase; $script:state }};
        function Write-ControlPlaneInstallState {{ param($Values); if($Values.phase){{$script:phase=[string]$Values.phase}} }};
        function Get-ControlPlaneProcessIdentity {{ param($ProcessId); if($ProcessId-eq $PID){{$current}}else{{$null}} }};
        function Get-RuntimeControlBundleIdentity {{ [pscustomobject]@{{source_revision='{target_revision}';exact_revision=$true}} }};
        function Get-VerifiedWatchdogOwners {{ @($current) }};
        function Get-ReleaseControlState {{ [pscustomobject]@{{transaction=$null}} }};
        function Assert-ControlPlaneIsolationBaseline {{ param($Snapshot,$ReleaseState); $script:checks+='baseline' }};
        function Get-ControlPlaneIsolationSnapshot {{ $baseline }};
        function Assert-ControlPlaneIsolationSnapshot {{ param($Before,$After); $script:checks+='isolation' }};
        function Write-WatchdogHeartbeat {{ param($SupervisionMode,$InstallTransactionId); New-Item -ItemType Directory -Path (Split-Path -Parent $watchdogHeartbeatPath) -Force | Out-Null; [pscustomobject]@{{install_transaction_id=$InstallTransactionId;supervision_mode=$SupervisionMode;control_bundle_revision='{target_revision}';control_bundle_exact_revision=$true;control_bundle_hash_verified=$true;process_id=$PID;process_start_token='{CURRENT_START_TOKEN}'}} | ConvertTo-Json | Set-Content -LiteralPath $watchdogHeartbeatPath }};
        """
    ).replace("\n", " ")


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
        "COMMITTED|lock,stage,preflight,suspend,guard,stop,baseline,install,start,"
        "heartbeat:QUIESCED,isolation,heartbeat:ACTIVE,supervision,unlock"
    )


def test_install_fails_closed_when_stable_sync_owner_is_missing(
    tmp_path: Path,
) -> None:
    old_revision, target_revision = "a" * 40, "b" * 40
    body = _state_machine_mocks(old_revision, target_revision) + textwrap.dedent(
        f"""
        function Get-ControlPlaneIsolationSnapshot {{
          $p=[pscustomobject]@{{process_id=10;process_start_token='service-token'}};
          [pscustomobject]@{{business_runtime_revision='runtime';services=[pscustomobject]@{{
            quote=@($p);collector=@($p);annotator=@($p);api=@($p);sync=@();broadcast=@()
          }}}}
        }};
        function Assert-ControlPlaneIsolationBaseline {{ param($Snapshot,$ReleaseState);
          if(@($Snapshot.services.sync).Count-ne 1){{throw 'CONTROL_PLANE_SERVICE_OWNER_REQUIRED:sync'}}
        }};
        try {{ Invoke-ControlPlaneInstall -VerifiedSourceRoot 'immutable'
          -TargetRevision '{target_revision}' | Out-Null }} catch {{ $reason=$_.Exception.Message }};
        Write-Output "$reason|$($script:timeline -join ',')"
        """
    ).replace("\n", " ")
    result = _run_contract(tmp_path, body)
    assert result.startswith(
        "CONTROL_PLANE_INSTALL_FAILED: "
        "CONTROL_PLANE_SERVICE_OWNER_REQUIRED:sync; ROLLED_BACK|"
    )
    assert "install" not in result


def test_install_captures_service_isolation_only_after_old_watchdog_stops(
    tmp_path: Path,
) -> None:
    old_revision, target_revision = "a" * 40, "b" * 40
    body = _state_machine_mocks(old_revision, target_revision) + textwrap.dedent(
        f"""
        $script:isolationCalls=0;
        function Get-ControlPlaneIsolationSnapshot {{
          $script:isolationCalls++;
          if($script:isolationCalls-eq 1 -and $script:owners.Count-ne 0){{
            throw 'ISOLATION_CAPTURED_BEFORE_QUIESCE'
          }};
          $p=[pscustomobject]@{{process_id=10;process_start_token='service-token'}};
          [pscustomobject]@{{business_runtime_revision='runtime';services=[pscustomobject]@{{
            quote=@($p);collector=@($p);annotator=@($p);api=@($p);sync=@($p);broadcast=@()
          }}}}
        }};
        $result=Invoke-ControlPlaneInstall -VerifiedSourceRoot 'immutable'
          -TargetRevision '{target_revision}';
        Write-Output "$($result.status)|$script:isolationCalls"
        """
    ).replace("\n", " ")
    assert _run_contract(tmp_path, body) == "COMMITTED|2"


def test_install_reloads_release_context_after_old_supervisor_is_fenced(
    tmp_path: Path,
) -> None:
    old_revision, target_revision = "a" * 40, "b" * 40
    body = _state_machine_mocks(old_revision, target_revision) + textwrap.dedent(
        f"""
        $script:releaseReads=0;
        function Get-ReleaseControlState {{
          $script:releaseReads++;
          [pscustomobject]@{{marker=if($script:releaseReads-eq 1){{'stale'}}else{{'fresh'}};transaction=$null}}
        }};
        function Assert-ControlPlaneIsolationBaseline {{ param($Snapshot,$ReleaseState); if($ReleaseState.marker-ne 'fresh'){{throw 'STALE_RELEASE_CONTEXT'}}; $script:timeline+='baseline' }};
        $result=Invoke-ControlPlaneInstall -VerifiedSourceRoot 'immutable'
          -TargetRevision '{target_revision}';
        Write-Output "$($result.status)|$script:releaseReads"
        """
    ).replace("\n", " ")
    assert _run_contract(tmp_path, body) == "COMMITTED|2"


def test_supervision_quiesce_keeps_main_task_enabled_for_restart(tmp_path: Path) -> None:
    body = textwrap.dedent(
        """
        $script:disabled=@(); $script:enabled=@(); $script:stopped=@(); $script:mainTask=$taskName;
        function Get-ScheduledTask { param($TaskName,$ErrorAction); [pscustomobject]@{Settings=[pscustomobject]@{Enabled=$true}} };
        function Disable-ScheduledTask { param($TaskName); $script:disabled+=$TaskName };
        function Enable-ScheduledTask { param($TaskName); $script:enabled+=$TaskName };
        function Stop-ScheduledTask { param($TaskName,$ErrorAction); $script:stopped+=$TaskName };
        $state=Suspend-ControlPlaneSupervision;
        Write-Output "$($script:disabled.Count),$($script:enabled.Count),$($script:stopped.Count -eq 1 -and $script:stopped[0] -ceq $guardTaskName)"
        """
    ).replace("\n", " ")
    result = _run_contract(tmp_path, body)
    assert result == "1,0,True"


@pytest.mark.parametrize(
    ("exact", "hashed", "heartbeat_token", "expected"),
    [
        ("$true", "$true", OLD_START_TOKEN, "CONTROL_PLANE_NEW_WATCHDOG_HEARTBEAT_TIMEOUT"),
        ("$false", "$true", NEW_START_TOKEN, "CONTROL_PLANE_NEW_WATCHDOG_HEARTBEAT_TIMEOUT"),
        ("$true", "$false", NEW_START_TOKEN, "CONTROL_PLANE_NEW_WATCHDOG_HEARTBEAT_TIMEOUT"),
        ("$true", "$true", NEW_START_TOKEN, NEW_START_TOKEN),
    ],
)
def test_heartbeat_requires_new_process_exact_revision_and_hashes(
    tmp_path: Path, exact: str, hashed: str, heartbeat_token: str, expected: str,
) -> None:
    revision = "b" * 40
    previous = _identity(100, OLD_START_TOKEN)
    owner_pid = 100 if heartbeat_token == OLD_START_TOKEN else 200
    owner = _identity(owner_pid, heartbeat_token)
    body = textwrap.dedent(
        f"""
        $previous={previous}; $owner={owner};
        $receipt=[pscustomobject]@{{instance_id=('a'*32);mode='ACTIVE';install_transaction_id=$null}};
        $owner | Add-Member -NotePropertyName watchdog_owner_receipt -NotePropertyValue $receipt -Force;
        function Get-WatchdogOwnerReceiptDigest {{ return ('c'*64) }};
        function Start-Sleep {{ }};
        function Get-VerifiedWatchdogOwners {{ @($owner) }};
        New-Item -ItemType Directory -Path (Split-Path -Parent $watchdogHeartbeatPath) -Force | Out-Null;
        [pscustomobject]@{{control_bundle_revision='{revision}';control_bundle_exact_revision={exact};control_bundle_hash_verified={hashed};supervision_mode='ACTIVE';install_transaction_id=$null;process_id={owner_pid};process_start_token='{heartbeat_token}';instance_id=('a'*32);owner_receipt_digest=('c'*64)}} | ConvertTo-Json | Set-Content -LiteralPath $watchdogHeartbeatPath;
        try {{ $accepted=Wait-VerifiedWatchdogHandoff -ExpectedRevision '{revision}' -PreviousIdentity $previous -Timeout ([TimeSpan]::FromMilliseconds(20)); Write-Output $accepted.process_start_token }} catch {{ Write-Output $_.Exception.Message }}
        """
    ).replace("\n", " ")
    assert _run_contract(tmp_path, body) == expected


def test_handoff_requires_quiesced_ack_from_exact_install_transaction(tmp_path: Path) -> None:
    revision = "b" * 40
    previous = _identity(100, OLD_START_TOKEN)
    owner = _identity(200, NEW_START_TOKEN)
    body = textwrap.dedent(
        f"""
        $previous={previous}; $owner={owner};
        function Start-Sleep {{ }};
        function Get-VerifiedWatchdogOwners {{ @($owner) }};
        New-Item -ItemType Directory -Path (Split-Path -Parent $watchdogHeartbeatPath) -Force | Out-Null;
        [pscustomobject]@{{control_bundle_revision='{revision}';control_bundle_exact_revision=$true;control_bundle_hash_verified=$true;supervision_mode='QUIESCED';install_transaction_id='wrong';process_id=200;process_start_token='{NEW_START_TOKEN}'}} | ConvertTo-Json | Set-Content -LiteralPath $watchdogHeartbeatPath;
        try {{ Wait-VerifiedWatchdogHandoff -ExpectedRevision '{revision}' -PreviousIdentity $previous -ExpectedMode 'QUIESCED' -ExpectedInstallTransactionId 'expected' -Timeout ([TimeSpan]::FromMilliseconds(20)) | Out-Null; Write-Output accepted }} catch {{ Write-Output $_.Exception.Message }}
        """
    ).replace("\n", " ")
    assert _run_contract(tmp_path, body) == "CONTROL_PLANE_NEW_WATCHDOG_HEARTBEAT_TIMEOUT"


@pytest.mark.parametrize(
    "phase",
    ("START_NEW_WATCHDOG", "VERIFY_QUIESCED_HANDOFF", "ACTIVATE_NEW_WATCHDOG"),
)
def test_installer_death_rechecks_every_fact_before_active_grant(
    tmp_path: Path, phase: str,
) -> None:
    target_revision = "b" * 40
    body = _abandoned_activation_mocks(target_revision, phase) + textwrap.dedent(
        """
        $result=Wait-ControlPlaneInstallActivation -TransactionId 'txn';
        Write-Output "$result|$script:phase|$($script:checks -join ',')"
        """
    ).replace("\n", " ")
    assert _run_contract(tmp_path, body) == (
        "RECOVERED|ACTIVATE_NEW_WATCHDOG|baseline,isolation"
    )


def test_installer_death_before_bundle_swap_restores_safe_supervisor_path(
    tmp_path: Path,
) -> None:
    old_revision, target_revision = "a" * 40, "b" * 40
    repository = tmp_path / "repository"
    control = repository / ".local" / "runtime-control"
    _write_bundle(control, old_revision, "old")
    body = textwrap.dedent(
        f"""
        Write-ControlPlaneInstallState @{{transaction_id='txn';target_revision='{target_revision}';previous_revision='{old_revision}';phase='STOP_OLD_WATCHDOG';install_owner_identity=[pscustomobject]@{{process_id=999999;process_start_token='gone'}};supervision_state=[pscustomobject]@{{}}}};
        function Get-ControlPlaneProcessIdentity {{ param($ProcessId); return $null }};
        function Get-RuntimeControlBundleIdentity {{ Get-RuntimeControlBundleIdentityAtRoot -ControlRoot '{control}' }};
        function Restore-ControlPlaneSupervision {{ param($State); $script:restored=$true }};
        $bundle=Repair-AbandonedControlPlaneBundleForWatchdog;
        $state=Get-ControlPlaneInstallState;
        Write-Output "$($bundle.source_revision)|$($state.phase)|$script:restored"
        """
    ).replace("\n", " ")
    assert _run_contract(tmp_path, body) == f"{old_revision}|ROLLED_BACK|True"


@pytest.mark.parametrize(
    ("mutation", "expected"),
    (
        ("$script:state.transaction_id='wrong'", "CONTROL_PLANE_INSTALL_FENCE_LOST"),
        ("$script:state.bundle_hash_verified=$false", "CONTROL_PLANE_ABANDONED_BUNDLE_NOT_VERIFIED"),
        ("function Get-RuntimeControlBundleIdentity { [pscustomobject]@{source_revision='wrong';exact_revision=$true} }", "CONTROL_PLANE_ABANDONED_BUNDLE_IDENTITY_MISMATCH"),
        ("function Get-ControlPlaneProcessIdentity { param($ProcessId); if($ProcessId-eq 100){$old}elseif($ProcessId-eq $PID){$current}else{$null} }", "CONTROL_PLANE_OLD_WATCHDOG_STILL_OWNS"),
        ("function Get-VerifiedWatchdogOwners { @($current,$current) }", "CONTROL_PLANE_RECOVERY_EXACTLY_ONE_REPLACEMENT_REQUIRED"),
        ("function Assert-ControlPlaneIsolationSnapshot { param($Before,$After); throw 'CONTROL_PLANE_INSTALL_CHANGED_SERVICE_SYNC' }", "CONTROL_PLANE_INSTALL_CHANGED_SERVICE_SYNC"),
        ("function Get-ReleaseControlState { [pscustomobject]@{transaction=[pscustomobject]@{type='PROMOTE'}} }", "CONTROL_PLANE_RECOVERY_RELEASE_TRANSACTION_APPEARED"),
        ("New-Item -ItemType Directory -Path $releaseLockPath -Force | Out-Null; [pscustomobject]@{owner_pid=123} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $releaseLockPath 'owner.json')", "CONTROL_PLANE_RECOVERY_CONCURRENT_RELEASE_LOCK"),
    ),
)
def test_abandoned_install_safety_facts_fail_closed(
    tmp_path: Path, mutation: str, expected: str,
) -> None:
    target_revision = "b" * 40
    body = _abandoned_activation_mocks(
        target_revision, "VERIFY_QUIESCED_HANDOFF"
    ) + f"Write-WatchdogHeartbeat -SupervisionMode 'QUIESCED' -InstallTransactionId 'txn'; {mutation}; try {{ Assert-AbandonedControlPlaneInstallActivation -State $script:state -TransactionId 'txn' | Out-Null; Write-Output accepted }} catch {{ Write-Output $_.Exception.Message }}"
    assert _run_contract(tmp_path, body) == expected


def test_abandoned_install_accepts_only_exact_dead_installer_lock_identity(
    tmp_path: Path,
) -> None:
    target_revision = "b" * 40
    body = _abandoned_activation_mocks(
        target_revision, "VERIFY_QUIESCED_HANDOFF"
    ) + textwrap.dedent(
        f"""
        Write-WatchdogHeartbeat -SupervisionMode 'QUIESCED' -InstallTransactionId 'txn';
        New-Item -ItemType Directory -Path $releaseLockPath -Force | Out-Null;
        [pscustomobject]@{{owner_pid=999999;owner_process_start_token='{DEAD_INSTALLER_START_TOKEN}'}} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $releaseLockPath 'owner.json');
        $verified=Assert-AbandonedControlPlaneInstallActivation -State $script:state -TransactionId 'txn';
        Write-Output "$($verified.owner.process_start_token)|$($script:checks -join ',')"
        """
    ).replace("\n", " ")
    assert _run_contract(tmp_path, body) == (
        f"{CURRENT_START_TOKEN}|baseline,isolation"
    )


def test_failed_abandoned_activation_restores_verified_backup(tmp_path: Path) -> None:
    old_revision, target_revision = "a" * 40, "b" * 40
    repository = tmp_path / "repository"
    control = repository / ".local" / "runtime-control"
    backup = repository / ".local" / ".cpb-recovery"
    _write_bundle(control, target_revision, "new")
    _write_bundle(backup, old_revision, "old")
    body = _abandoned_activation_mocks(
        target_revision, "VERIFY_QUIESCED_HANDOFF", bundle_verified="$false"
    ) + textwrap.dedent(
        f"""
        $script:state.backup_root='{backup}';
        function Restore-ControlPlaneSupervision {{ param($State); $script:restored=$true }};
        try {{ Wait-ControlPlaneInstallActivation -TransactionId 'txn' | Out-Null }} catch {{ $message=$_.Exception.Message }};
        $bundle=Get-RuntimeControlBundleIdentityAtRoot -ControlRoot '{control}';
        Write-Output "$message|$($bundle.source_revision)|$script:phase|$script:restored"
        """
    ).replace("\n", " ")
    result = _run_contract(tmp_path, body)
    assert result == (
        "CONTROL_PLANE_ABANDONED_INSTALL_ROLLED_BACK: "
        "CONTROL_PLANE_ABANDONED_BUNDLE_NOT_VERIFIED|"
        f"{old_revision}|ROLLED_BACK|True"
    )


def test_failure_starting_new_watchdog_restores_old_bundle_and_owner(tmp_path: Path) -> None:
    old_revision, target_revision = "a" * 40, "b" * 40
    body = _state_machine_mocks(old_revision, target_revision) + textwrap.dedent(
        f"""
        $script:startCount=0;
        function Start-WatchdogReplacement {{ param([switch]$PassThru,$InstallTransactionId); $script:startCount++; if($script:startCount-eq 1){{throw 'new start failed'}}; $script:timeline+='restore-start'; $script:owners=@({_identity(300, 'restored-token')}) }};
        function Restore-RuntimeControlBundleBackup {{ param($BackupRoot,$ControlRoot); $script:timeline+='restore-bundle'; [pscustomobject]@{{source_revision='{old_revision}'}} }};
        function Wait-VerifiedWatchdogHandoff {{ param($ExpectedRevision,$PreviousIdentity,$Timeout); $script:timeline+="restore-heartbeat:$ExpectedRevision"; return $script:owners[0] }};
        try {{ Invoke-ControlPlaneInstall -VerifiedSourceRoot 'immutable' -TargetRevision '{target_revision}' | Out-Null }} catch {{ $message=$_.Exception.Message }};
        Write-Output "$message|$($script:timeline -join ',')"
        """
    ).replace("\n", " ")
    result = _run_contract(tmp_path, body)
    assert "ROLLED_BACK" in result
    assert "install,restore-bundle,isolation,restore-start" in result
    assert f"restore-heartbeat:{old_revision}" in result


def test_baseline_capture_failure_can_restart_previous_supervisor(tmp_path: Path) -> None:
    old_revision, target_revision = "a" * 40, "b" * 40
    body = _state_machine_mocks(old_revision, target_revision) + textwrap.dedent(
        f"""
        function Get-ControlPlaneIsolationSnapshot {{ throw 'baseline unavailable' }};
        function Start-WatchdogReplacement {{ param([switch]$PassThru,$InstallTransactionId); $script:timeline+='restore-start'; $script:owners=@({_identity(300, 'restored-token')}) }};
        function Wait-VerifiedWatchdogHandoff {{ param($ExpectedRevision,$PreviousIdentity,$ExpectedMode,$ExpectedInstallTransactionId,$Timeout); $script:timeline+='restore-heartbeat'; return $script:owners[0] }};
        try {{ Invoke-ControlPlaneInstall -VerifiedSourceRoot 'immutable' -TargetRevision '{target_revision}' | Out-Null }} catch {{ $message=$_.Exception.Message }};
        Write-Output "$message|$($script:timeline -join ',')"
        """
    ).replace("\n", " ")
    result = _run_contract(tmp_path, body)
    assert "ROLLED_BACK" in result
    assert "stop,restore-start,restore-heartbeat" in result


def test_release_transaction_blocks_control_plane_install(tmp_path: Path) -> None:
    target_revision = "b" * 40
    body = _state_machine_mocks("a" * 40, target_revision) + (
        "function Get-ReleaseControlState { [pscustomobject]@{transaction=[pscustomobject]@{type='PROMOTE'}} }; "
        f"try {{ Invoke-ControlPlaneInstall -VerifiedSourceRoot 'immutable' -TargetRevision '{target_revision}' | Out-Null }} "
        "catch { Write-Output $_.Exception.Message }"
    )
    assert _run_contract(tmp_path, body) == "CONTROL_PLANE_INSTALL_BLOCKED_BY_RELEASE_TRANSACTION"


def test_operator_lifecycle_collapses_internal_release_states(tmp_path: Path) -> None:
    body = textwrap.dedent(
        """
        $stable=[pscustomobject]@{deployment_status='READY';candidate=$null;transaction=$null};
        $prepare=[pscustomobject]@{deployment_status='READY';candidate=[pscustomobject]@{validation_state='NEW';validation=$null};transaction=$null};
        $migration=[pscustomobject]@{deployment_status='READY';candidate=[pscustomobject]@{validation_state='REVIEW_REQUIRED';validation=[pscustomobject]@{reason='COORDINATED_STORAGE_MIGRATION_REQUIRED'}};transaction=$null};
        $verify=[pscustomobject]@{deployment_status='READY';candidate=[pscustomobject]@{validation_state='REVIEW_REQUIRED';validation=[pscustomobject]@{reason='CPU_REVIEW_REQUIRED'}};transaction=$null};
        $switch=[pscustomobject]@{deployment_status='PROMOTING';candidate=$verify.candidate;transaction=[pscustomobject]@{phase='CUTOVER'}};
        $observe=[pscustomobject]@{deployment_status='OBSERVING';candidate=$verify.candidate;transaction=[pscustomobject]@{phase='OBSERVING'}};
        Write-Output (@($stable,$prepare,$migration,$verify,$switch,$observe | ForEach-Object { Get-ReleaseLifecyclePhase $_ }) -join ',')
        """
    ).replace("\n", " ")
    assert _run_contract(tmp_path, body) == "STABLE,PREPARE,PREPARE,VERIFY,SWITCH,OBSERVE"


def test_control_plane_isolation_and_visible_identity_are_explicit() -> None:
    install_source = (
        ROOT / "scripts" / "control_center_install.ps1"
    ).read_text(encoding="utf-8")
    source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (
            ROOT / "scripts" / "control_center_install.ps1",
            ROOT / "scripts" / "control_center_runtime_supervision.ps1",
            ROOT / "scripts" / "control_center_presentation.ps1",
            ROOT / "scripts" / "xauusd_control_center.ps1",
        )
    )
    xaml = (ROOT / "scripts" / "control_center.xaml").read_text(encoding="utf-8")
    launcher = (ROOT / "scripts" / "xauusd_control_center_launcher.vbs").read_text(encoding="utf-8")
    install_body = install_source.split("function Invoke-ControlPlaneInstall", 1)[1]
    assert "InstallRuntime" not in install_body
    assert "Stop-All" not in install_body
    assert "Restart-All" not in install_body
    assert "Restart-CodeReloadableServices" not in install_body
    assert "Assert-ControlPlaneIsolationSnapshot" in install_body
    supervision = install_source.split("function Suspend-ControlPlaneSupervision", 1)[1].split(
        "function Restore-ControlPlaneSupervision", 1
    )[0]
    assert "Disable-ScheduledTask -TaskName $guardTaskName" in supervision
    assert "Stop-ScheduledTask -TaskName $guardTaskName" in supervision
    # Normal installation retains its reboot entrypoint; the explicit zero-owner
    # incident is covered by the executable two-mode task containment contract.
    assert "[switch]$CollectorClockRecovery" in supervision
    assert "ControlPlaneIdentity" in xaml
    assert "BusinessRuntimeIdentity" in xaml
    assert "EXACT | HASH VERIFIED" in source
    assert 'BuildPath(scriptDirectory, "xauusd_control_center.ps1")' in launcher


@pytest.mark.parametrize(
    ("enabled", "before_broadcast", "after_broadcast", "expected"),
    (
        ("$false", "@()", "@()", "PASSED"),
        ("$false", "@($p)", "@($p)", "CONTROL_PLANE_UNEXPECTED_SERVICE_OWNER:broadcast"),
        ("$true", "@()", "@()", "CONTROL_PLANE_SERVICE_OWNER_REQUIRED:broadcast"),
    ),
)
def test_control_plane_isolation_respects_optional_broadcast_ownership(
    tmp_path: Path,
    enabled: str,
    before_broadcast: str,
    after_broadcast: str,
    expected: str,
) -> None:
    body = textwrap.dedent(
        f"""
        function Test-BroadcastPublisherEnabled {{ return {enabled} }};
        $p=[pscustomobject]@{{process_id=10;process_start_token='same'}};
        $before=[pscustomobject]@{{business_runtime_revision='runtime';release_state_hash='state';release_history_hash='history';services=[pscustomobject]@{{quote=@($p);collector=@($p);annotator=@($p);api=@($p);sync=@($p);broadcast={before_broadcast}}}}};
        $after=[pscustomobject]@{{business_runtime_revision='runtime';release_state_hash='state';release_history_hash='history';services=[pscustomobject]@{{quote=@($p);collector=@($p);annotator=@($p);api=@($p);sync=@($p);broadcast={after_broadcast}}}}};
        try {{ Assert-ControlPlaneIsolationBaseline -Snapshot $before; Write-Output 'PASSED' }} catch {{ Write-Output $_.Exception.Message }}
        """
    ).replace("\n", " ")
    assert _run_contract(tmp_path, body) == expected


def test_control_plane_isolation_always_requires_stable_sync_owner(
    tmp_path: Path,
) -> None:
    body = textwrap.dedent(
        f"""
        function Test-BroadcastPublisherEnabled {{ return $false }};
        $p=[pscustomobject]@{{process_id=10;process_start_token='same'}};
        $before=[pscustomobject]@{{business_runtime_revision='runtime';release_state_hash='state';release_history_hash='history';services=[pscustomobject]@{{quote=@($p);collector=@($p);annotator=@($p);api=@($p);sync=@();broadcast=@()}}}};
        try {{ Assert-ControlPlaneIsolationBaseline -Snapshot $before; Write-Output 'PASSED' }} catch {{ Write-Output $_.Exception.Message }}
        """
    ).replace("\n", " ")
    assert _run_contract(tmp_path, body) == "CONTROL_PLANE_SERVICE_OWNER_REQUIRED:sync"


@pytest.mark.parametrize("runtime_executable", ["powershell.exe", "pwsh.exe"])
def test_collector_incident_baseline_is_read_only_and_rejects_uncertain_owners(tmp_path, runtime_executable):
    if shutil.which(runtime_executable) is None:
        pytest.skip(f"{runtime_executable} is unavailable")
    body = r'''
    $script:scenario = 'valid';
    function Assert-ControlPlaneSourceRevision { param($SourceRoot,$SourceRevision,[switch]$RequireImmutableSource); if (-not $RequireImmutableSource) {throw 'immutable source required'} };
    function Get-ReleaseControlState { [pscustomobject]@{transaction=$null;stable=[pscustomobject]@{windows_revision=('a'*40);worker_version_id='stable'}} };
    function Get-ControlPlaneInstallState { $null };
    function Get-CodeRevision { 'a'*40 };
    function Get-CimInstance {
        [CmdletBinding()]param($ClassName, $Filter);
        if ($script:scenario -eq 'enumeration') { throw 'enumeration failed' };
        foreach ($key in @('quote','annotator','api','sync')) {
            if ($script:scenario -eq 'missing' -and $key -eq 'api') { continue };
            [pscustomobject]@{Name='python.exe';ProcessId=10;CommandLine=$key;Key=$key};
            if ($script:scenario -eq 'duplicate' -and $key -eq 'sync') {
                [pscustomobject]@{Name='python.exe';ProcessId=11;CommandLine=$key;Key=$key}
            }
        }
    };
    function Get-WatchdogOwnershipInventory { param([switch]$RequireCompleteInventory);
        if (-not $RequireCompleteInventory) { throw 'strict inventory omitted' };
        [pscustomobject]@{authoritative=@();duplicate_shaped=@();legacy_orphaned=@();unknown=@();receipt=[pscustomobject]@{process_id=99;process_start_token='old'}}
    };
    function Get-WatchdogSingletonDescriptor { [pscustomobject]@{user_sid='sid';runtime_root_hash='runtime';repository_root_hash='repository'} };
    function Test-WatchdogOwnerReceiptShape { $true };
    function Get-ControlPlaneProcessIdentity { param($ProcessId,[switch]$RequireCompleteInventory);
        if (-not $RequireCompleteInventory) { throw 'strict identity omitted' };
        if ($ProcessId -eq 99 -and $script:scenario -ne 'old-alive') { return $null };
        [pscustomobject]@{process_id=$ProcessId;process_start_token='old';owner_sid='sid'}
    };
    function Test-ControlPlaneStartTokenEqual { $true };
    function Test-ForecasterServiceProcess { param($Process,$Service); $Process.Key -eq $Service.Key };
    function Test-ControlPlaneServiceOwnerRequired { param($Service); $Service.Key -ne 'broadcast' };
    function Get-ServiceState { if ($script:scenario -eq 'unhealthy') { 'SYNC STALE' } else { 'RUNNING' } };
    function Get-BrokerMarketSession { [pscustomobject]@{IsOpen=$false} };
    function Get-ReleaseProviderRuntimeFacts { [pscustomobject]@{active_worker_observation=[pscustomobject]@{
        status='AVAILABLE';traffic_percent=100;version_id=$(if ($script:scenario -eq 'traffic') {'wrong'} else {'stable'})}}
    };
    function Invoke-Utf8NativeProcess { param($FilePath,$Arguments,$WorkingDirectory,$TimeoutMilliseconds);
        if ('--inspect-snapshot-only' -notin $Arguments) { throw 'mutation attempted' };
        [pscustomobject]@{exit_code=0;stdout=(@{decision_time='2026-09-04T16:05:00.000000+00:00';snapshot_hash=$(if ($script:scenario -eq 'snapshot') {'wrong'} else {'b139c8a9d913c237e8e9e3ebc677a1144cd8ad2f9e0adee6b62ed8cd2a7fa5ee'})}|ConvertTo-Json -Compress)}
    };
    function Write-ControlPlaneInstallState { throw 'mutation attempted' };
    function Start-WatchdogReplacement { throw 'mutation attempted' };
    $expected = @{
        valid='DEGRADED_RECOVERY_BASELINE'; enumeration='enumeration failed';
        missing='COLLECTOR_RECOVERY_SERVICE_OWNER_INVALID:api';
        duplicate='COLLECTOR_RECOVERY_SERVICE_OWNER_INVALID:sync';
        'old-alive'='COLLECTOR_RECOVERY_PRIOR_OWNER_ALIVE';
        unhealthy='COLLECTOR_RECOVERY_SERVICE_UNHEALTHY:quote:SYNC STALE';
        traffic='COLLECTOR_RECOVERY_STABLE_TRAFFIC_UNPROVED';
        snapshot='COLLECTOR_RECOVERY_SNAPSHOT_INSPECTION_MISMATCH'
    };
    foreach ($case in $expected.Keys) {
        $script:scenario=$case;
        try { $actual=(Get-CollectorClockRecoveryBaseline -VerifiedSourceRoot $repositoryRoot -TargetRevision ('b'*40)).state }
        catch { $actual=$_.Exception.Message };
        if ($actual -cne $expected[$case]) { throw "${case}: expected $($expected[$case]); actual $actual" }
    };
    Write-Output '8 baseline cases passed; production mutation=0'
    '''
    assert _run_contract_with_runtime(tmp_path, body, runtime_executable) == (
        "8 baseline cases passed; production mutation=0"
    )


@pytest.mark.parametrize("runtime_executable", ["powershell.exe", "pwsh.exe"])
def test_incident_hold_survives_reload_and_only_exact_normal_switch_can_release_it(tmp_path, runtime_executable):
    if shutil.which(runtime_executable) is None:
        pytest.skip(f"{runtime_executable} is unavailable")
    body = r'''
    function Get-WatchdogSingletonDescriptor { [pscustomobject]@{user_sid='sid';runtime_root_hash='runtime';repository_root_hash='repository'} };
    $context = [pscustomobject]@{
        incident='COLLECTOR_CLOCK_EVENT_ATOMICITY';state='DEGRADED_RECOVERY_BASELINE';
        broken_revision=('a'*40);target_revision=('b'*40);user_sid='sid';
        runtime_root_hash='runtime';repository_root_hash='repository';
        snapshot=[pscustomobject]@{decision_time='2026-09-04T16:05:00.000000+00:00';snapshot_hash='b139c8a9d913c237e8e9e3ebc677a1144cd8ad2f9e0adee6b62ed8cd2a7fa5ee'}
    };
    Write-ControlPlaneInstallState @{phase='COMMITTED';collector_clock_recovery=$context};
    $script:businessRevision='a'*40;
    $script:release=[pscustomobject]@{stable=[pscustomobject]@{windows_revision=('a'*40)};transaction=$null};
    function Get-CodeRevision { $script:businessRevision };
    function Get-ReleaseControlState { $script:release };
    if (-not (Test-CollectorClockRecoveryHold)) { throw 'old collector was not held' };
    try { Start-ForecasterService ($services | Where-Object Key -eq collector); throw 'old collector started' }
    catch { if ($_.Exception.Message -cne 'COLLECTOR_CLOCK_RECOVERY_REQUIRED') { throw } };
    if (-not (Test-WatchdogRecoverySuppressed -ServiceKey collector -ServiceState STOPPED)) { throw 'watchdog may restart broken collector' };
    $script:businessRevision='b'*40;
    try { $null=Test-CollectorClockRecoveryHold; throw 'uncoordinated target accepted' }
    catch { if ($_.Exception.Message -cne 'COLLECTOR_RECOVERY_RUNTIME_TRANSITION_UNPROVED') { throw } };
    $script:release.transaction=[pscustomobject]@{type='PROMOTE';target=[pscustomobject]@{windows_revision=('b'*40)}};
    if (Test-CollectorClockRecoveryHold) { throw 'normal exact switch did not release hold' };
    $script:release.transaction=$null; $script:release.stable.windows_revision='b'*40;
    if (Test-CollectorClockRecoveryHold) { throw 'committed target held' };
    $script:businessRevision='a'*40;
    if (-not (Test-CollectorClockRecoveryHold)) { throw 'rollback falsely restarted broken code' };
    $context.runtime_root_hash='wrong';
    Write-ControlPlaneInstallState @{collector_clock_recovery=$context};
    try { $null=Get-CollectorClockRecoveryContext; throw 'wrong root accepted' }
    catch { if ($_.Exception.Message -cne 'COLLECTOR_RECOVERY_CONTEXT_INVALID') { throw } };
    Write-Output 'incident hold verified'
    '''
    assert _run_contract_with_runtime(tmp_path, body, runtime_executable) == "incident hold verified"


@pytest.mark.parametrize("runtime_executable", ["powershell.exe", "pwsh.exe"])
def test_install_task_containment_preserves_normal_restart_but_fences_incident_bootstrap(tmp_path, runtime_executable):
    if shutil.which(runtime_executable) is None:
        pytest.skip(f"{runtime_executable} is unavailable")
    body = r'''
    function Get-ScheduledTask { [pscustomobject]@{Settings=[pscustomobject]@{Enabled=$true}} };
    function Disable-ScheduledTask { param($TaskName); $script:disabled+=@($TaskName) };
    function Stop-ScheduledTask { param($TaskName); $script:stopped+=@($TaskName) };
    foreach ($incident in @($false,$true)) {
        $script:disabled=@(); $script:stopped=@();
        $state=Suspend-ControlPlaneSupervision -CollectorClockRecovery:$incident;
        $expected=@($guardTaskName); if ($incident) { $expected+=@($taskName) };
        if (($script:disabled -join ',') -cne ($expected -join ',') -or
            ($script:stopped -join ',') -cne ($expected -join ',')) { throw 'task containment mismatch' };
        if (-not $state[$taskName] -or -not $state[$guardTaskName]) { throw 'prior task state lost' }
    };
    Write-Output 'both task containment modes passed'
    '''
    assert _run_contract_with_runtime(tmp_path, body, runtime_executable) == "both task containment modes passed"


@pytest.mark.parametrize("fail_start", [False, True])
def test_zero_owner_install_releases_real_mutex_before_handoff_and_never_starts_old_code(tmp_path, fail_start):
    body = _state_machine_mocks("a" * 40, "b" * 40) + r'''
    $script:owners=@();
    $script:mutexName='Local\XAUUSD-Contract-'+[guid]::NewGuid().ToString('N');
    function Get-WatchdogSingletonDescriptor { [pscustomobject]@{mutex_name=$script:mutexName} };
    function Test-OtherProcessCanAcquire {
        $command='$m=[Threading.Mutex]::new($false,"'+$script:mutexName+'");$held=$m.WaitOne(0);try{if($held){"FREE"}else{"BUSY"}}finally{if($held){$m.ReleaseMutex()};$m.Dispose()}';
        $result=Invoke-Utf8NativeProcess -FilePath powershell.exe -Arguments @('-NoProfile','-NonInteractive','-Command',$command) -TimeoutMilliseconds 10000;
        if ($result.exit_code -ne 0) { throw $result.stderr };
        return $result.stdout.Trim()
    };
    function Get-CollectorClockRecoveryBaseline {
        if ((Test-OtherProcessCanAcquire) -cne 'BUSY') { throw 'bootstrap reservation missing' };
        [pscustomobject]@{incident='COLLECTOR_CLOCK_EVENT_ATOMICITY';broken_revision=('a'*40);target_revision=('b'*40);
            previous_watchdog_receipt=[pscustomobject]@{process_id=100;process_start_token='old-token'}}
    };
    function Start-WatchdogReplacement {
        if ((Test-OtherProcessCanAcquire) -cne 'FREE') { throw 'handoff deadlock' };
        $script:timeline+='start-target';
        if ($script:failStart) { throw 'fixture target startup failure' };
        $script:owners=@([pscustomobject]@{process_id=200;process_start_token='new-token'})
    };
    function Restore-RuntimeControlBundleBackup { $script:timeline+='restore-bundle'; [pscustomobject]@{source_revision=('a'*40)} };
    function Disable-ScheduledTask { param($TaskName); if ($TaskName -notlike 'XAUUSD-Contract-*') {throw 'unsafe task name'}; $script:disabled+=@($TaskName) };
    $script:disabled=@();
    ''' + f"$script:failStart=${str(fail_start).lower()};" + r'''
    try { $result=Invoke-ControlPlaneInstall -VerifiedSourceRoot 'immutable' -TargetRevision ('b'*40) -CollectorClockRecovery }
    catch { if (-not $script:failStart) { throw }; $failure=$_.Exception.Message };
    if ($script:timeline -contains 'stop') { throw 'stale old PID was terminated' };
    if (@($script:timeline | Where-Object {$_ -eq 'start-target'}).Count -ne 1) { throw 'repeated bootstrap' };
    if ((Test-OtherProcessCanAcquire) -cne 'FREE') { throw 'mutex leak' };
    $state=Get-ControlPlaneInstallState;
    if ($script:failStart) {
        if ($state.rollback_result -cne 'ROLLED_BACK_DEGRADED_BASELINE' -or
            $script:owners.Count -ne 0 -or $script:disabled.Count -ne 2) { throw 'unsafe rollback' };
        Write-Output 'degraded baseline restored; old collector not started'
    } else {
        if ($result.status -cne 'COMMITTED' -or $script:owners.Count -ne 1) { throw 'handoff failed' };
        Write-Output 'single replacement; mutex handoff verified'
    }
    '''
    expected = ("degraded baseline restored; old collector not started" if fail_start
                else "single replacement; mutex handoff verified")
    assert _run_contract(tmp_path, body) == expected


@pytest.mark.parametrize("runtime_executable", ["powershell.exe", "pwsh.exe"])
def test_clock_recovery_operation_requires_lock_and_preserves_accepted_repair(tmp_path, runtime_executable):
    if shutil.which(runtime_executable) is None:
        pytest.skip(f"{runtime_executable} is unavailable")
    body = r'''
    $script:releaseTransactionLockHeld=$false; $script:calls=@();
    function Get-CollectorClockRecoveryContext {
        [pscustomobject]@{target_revision=('b'*40);snapshot=[pscustomobject]@{
            decision_time='2026-09-04T16:05:00.000000+00:00';snapshot_hash=('c'*64)}}
    };
    function Invoke-Utf8NativeProcess {
        param($FilePath,$Arguments,$WorkingDirectory,$TimeoutMilliseconds);
        $script:calls+=@($FilePath);
        if ($FilePath -cne 'git.exe') { throw 'unexpected repair replay' };
        [pscustomobject]@{exit_code=0;stdout=''}
    };
    function Get-CollectorClockRecoveryBaseline {
        param($VerifiedSourceRoot,$TargetRevision,[switch]$SupervisionRecovered);
        if (-not $SupervisionRecovered -or $TargetRevision -cne ('b'*40)) { throw 'wrong admission' };
        [pscustomobject]@{snapshot=[pscustomobject]@{exclusion_recorded=$script:repaired}}
    };
    try { $null=Invoke-CollectorClockRecoveryOperation -Apply; throw 'lock bypass' }
    catch { if ($_.Exception.Message -cne 'COLLECTOR_RECOVERY_RELEASE_LOCK_REQUIRED') { throw } };
    if ($script:calls.Count -ne 0) { throw 'work started before lock' };
    $script:releaseTransactionLockHeld=$true;
    $script:repaired=$true;
    foreach ($apply in @($false,$true)) {
        $result=Invoke-CollectorClockRecoveryOperation -Apply:$apply;
        if (-not $result.snapshot.exclusion_recorded) { throw 'accepted evidence lost' }
    };
    $script:repaired=$false;
    try { $null=Invoke-CollectorClockRecoveryOperation; throw 'unrepaired baseline admitted' }
    catch { if ($_.Exception.Message -cne 'COLLECTOR_RECOVERY_EXISTING_STATE_NOT_REPAIRED') { throw } };
    if ($script:calls.Count -ne 6) { throw 'owned checkout cleanup missing' };
    Write-Output 'lock required; accepted repair reused; unrepaired inspection rejected'
    '''
    assert _run_contract_with_runtime(tmp_path, body, runtime_executable) == (
        "lock required; accepted repair reused; unrepaired inspection rejected"
    )


@pytest.mark.parametrize("runtime_executable", ["powershell.exe", "pwsh.exe"])
def test_incident_rollback_reports_degraded_and_requires_live_session_and_inventory(tmp_path, runtime_executable):
    if shutil.which(runtime_executable) is None:
        pytest.skip(f"{runtime_executable} is unavailable")
    body = r'''
    $plan=[pscustomobject]@{body=[pscustomobject]@{stable_revision=('a'*40);
        collector_clock_recovery=[pscustomobject]@{incident='fixture'};running_service_keys=@('quote')}};
    function Convert-RecoveryPlanContracts { @([pscustomobject]@{Key='quote'},[pscustomobject]@{Key='collector'}) };
    $script:scenario='valid';
    function Get-ForecasterProcesses {
        param($Service,[switch]$RequireCompleteInventory);
        if (-not $RequireCompleteInventory) { throw 'strict inventory required' };
        if ($script:scenario -eq 'enumeration') { throw 'fixture enumeration failed' };
        if ($Service.Key -eq 'quote') { [pscustomobject]@{ProcessId=1} }
    };
    function Test-CodeReloadHealth { $true };
    function Get-ServiceState { 'MARKET CLOSED' };
    function Get-BrokerMarketSession { if ($script:scenario -ne 'stale-session') { [pscustomobject]@{IsOpen=$false} } };
    function Write-WatchdogEvent { param($Event,$Service,$State); $script:event=$Event };
    function Start-Sleep {};
    $serviceStartupTimeout=[TimeSpan]::Zero;
    $result=Wait-RuntimeRecoveryPlanHealth -Plan $plan;
    if ($result.baseline_health -cne 'DEGRADED_RECOVERY_BASELINE' -or
        $script:event -cne 'RUNTIME_RECOVERY_DEGRADED_BASELINE_RESTORED') { throw 'false healthy rollback' };
    foreach ($case in @('stale-session','enumeration')) {
        $script:scenario=$case;
        try { $null=Wait-RuntimeRecoveryPlanHealth -Plan $plan; throw 'unsafe baseline accepted' }
        catch {
            $expected=if ($case -eq 'enumeration') {'fixture enumeration failed'} else {'RUNTIME_RECOVERY_HEALTH_FAILED'};
            if ($_.Exception.Message -cne $expected) { throw }
        }
    };
    Write-Output 'degraded truth preserved; unknown inventory and stale session rejected'
    '''
    assert _run_contract_with_runtime(tmp_path, body, runtime_executable) == (
        "degraded truth preserved; unknown inventory and stale session rejected"
    )
