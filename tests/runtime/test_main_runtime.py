from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import time

import pytest

from scripts.validation.check_public_health import read_runtime_json

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.skipif(os.name != "nt", reason="actual Windows runtime contract")


def ps_quote(value: Path | str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def powershell(tmp_path: Path, body: str) -> dict:
    script = tmp_path / "contract.ps1"
    script.write_text(
        "$ErrorActionPreference = 'Stop'\n"
        f". {ps_quote(ROOT / 'scripts/main_runtime.ps1')}\n" + body,
        encoding="utf-8-sig",
    )
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(script)],
        capture_output=True, text=True, timeout=60,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def git(root: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(root), *args], text=True, stderr=subprocess.PIPE).strip()


@pytest.mark.parametrize("legacy_launch", [False, True])
def test_actual_service_launch_quoting_deduplication_and_stop(tmp_path: Path, legacy_launch: bool) -> None:
    runtime = tmp_path / ("legacy" if legacy_launch else "runtime with spaces")
    runtime.mkdir()
    output = runtime / "args.json"
    worker = runtime / "fixture.py"
    worker.write_text("import json,sys,time\nfrom pathlib import Path\nPath(sys.argv[1]).write_text(json.dumps(sys.argv[2:]))\ntime.sleep(45)\n")
    values = ["space value", 'embedded"quote', "trailing\\", "", "$(literal)"]
    first_launch = "Start-RuntimeService $service"
    if legacy_launch:
        # Reproduce the currently retained production launcher's unquoted script.
        first_launch = "$legacyArgs = @($service.ScriptPath) + @($service.Arguments | ForEach-Object { ConvertTo-RuntimeArgument $_ }); Start-Process -FilePath 'python.exe' -ArgumentList $legacyArgs -WindowStyle Hidden"
    result = powershell(tmp_path, f"""
$script:RuntimeRoot = {ps_quote(runtime)}
$script:RepositoryRoot = $script:RuntimeRoot
$script:LogRoot = $script:RuntimeRoot
$service = [pscustomobject]@{{Key='fixture'; Kind='Python'; ScriptPath={ps_quote(worker)}; Arguments=@({', '.join(ps_quote(v) for v in [str(output), *values])})}}
try {{
    {first_launch}
    $deadline = [DateTime]::UtcNow.AddSeconds(10)
    while (-not (Test-Path -LiteralPath {ps_quote(output)}) -and [DateTime]::UtcNow -lt $deadline) {{ Start-Sleep -Milliseconds 100 }}
    Start-RuntimeService $service
    $count = @(Get-RuntimeServiceProcesses $service).Count
}} finally {{ Stop-RuntimeService $service }}
@{{count=$count; remaining=@(Get-RuntimeServiceProcesses $service).Count}} | ConvertTo-Json
"""
    )
    assert result == {"count": 1, "remaining": 0}
    assert json.loads(output.read_text()) == values


def test_fixed_revision_update_preserves_data_and_refuses_dirty_checkout(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    git(source, "init", "-b", "main")
    git(source, "config", "user.name", "Runtime contract")
    git(source, "config", "user.email", "fixture@example.invalid")
    (source / ".gitignore").write_text(".local/\n")
    (source / "pyproject.toml").write_text("# fixture dependencies\n")
    (source / "version.txt").write_text("first")
    git(source, "add", ".")
    git(source, "commit", "-m", "first")
    first = git(source, "rev-parse", "HEAD")
    runtime = tmp_path / "runtime"
    git(tmp_path, "clone", str(source), str(runtime))
    forward = runtime / ".local/forward"
    forward.mkdir(parents=True)
    evidence = forward / "evidence.txt"
    evidence.write_text("immutable fixture evidence")
    (source / "version.txt").write_text("second")
    git(source, "commit", "-am", "second")
    expected = git(source, "rev-parse", "HEAD")
    git(runtime, "fetch", "origin", "main")
    result = powershell(tmp_path, f"""
$script:RuntimeRoot = {ps_quote(runtime)}
$script:ForwardRoot = {ps_quote(forward)}
$script:LogRoot = $script:ForwardRoot
$hash = (Get-RuntimeFileHash (Join-Path $script:RuntimeRoot 'pyproject.toml'))
[IO.File]::WriteAllText((Join-Path $script:ForwardRoot 'main-installed-dependencies.sha256'), $hash)
$changed = Set-RuntimeRevision -Services @() -Revision {ps_quote(expected)}
$noop = Set-RuntimeRevision -Services @() -Revision {ps_quote(expected)}
@{{changed=$changed; noop=$noop; head=(Invoke-RuntimeGit -Arguments @('rev-parse', 'HEAD'))}} | ConvertTo-Json
"""
    )
    assert result == {"changed": True, "noop": False, "head": expected}
    assert evidence.read_text() == "immutable fixture evidence"
    (runtime / "version.txt").write_text("user WIP")
    (source / "version.txt").write_text("third")
    git(source, "commit", "-am", "third")
    git(runtime, "fetch", "origin", "main")
    third = git(source, "rev-parse", "HEAD")
    result = powershell(tmp_path, f"""
$script:RuntimeRoot = {ps_quote(runtime)}
$script:ForwardRoot = {ps_quote(forward)}
$script:LogRoot = $script:ForwardRoot
try {{ $null = Set-RuntimeRevision -Services @() -Revision {ps_quote(third)}; throw 'Expected dirty rejection' }}
catch {{ @{{error=$_.Exception.Message}} | ConvertTo-Json }}
"""
    )
    assert "local changes" in result["error"]
    assert (runtime / "version.txt").read_text() == "user WIP"
    assert git(runtime, "rev-parse", "HEAD") == expected
    # Update forward after new authoritative facts arrive; never restore old data.
    (runtime / "version.txt").write_text("second")
    evidence.write_text("newest authoritative fixture facts")
    result = powershell(tmp_path, f"""
$script:RuntimeRoot = {ps_quote(runtime)}
$script:ForwardRoot = {ps_quote(forward)}
$script:LogRoot = $script:ForwardRoot
$changed = Set-RuntimeRevision -Services @() -Revision {ps_quote(third)}
@{{changed=$changed; head=(Invoke-RuntimeGit -Arguments @('rev-parse', 'HEAD'))}} | ConvertTo-Json
""")
    assert result == {"changed": True, "head": third}
    assert evidence.read_text() == "newest authoritative fixture facts"


def test_actual_control_entrypoint_reads_status_without_creating_runtime(tmp_path: Path) -> None:
    runtime = tmp_path / "not created"
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-File", str(ROOT / "scripts/run_main_services.ps1"),
         "-Action", "StatusJson", "-RuntimeRoot", str(runtime)],
        capture_output=True, text=True, timeout=20, creationflags=subprocess.CREATE_NO_WINDOW,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["state"] == "not_started"
    assert not runtime.exists()


def test_native_command_reports_exit_code_and_bounds_a_hung_process(tmp_path: Path) -> None:
    child = tmp_path / "native child.py"
    child.write_text("import sys,time\nprint('started', flush=True)\nif sys.argv[1]=='hang': time.sleep(60)\nelse: sys.exit(7)\n")
    result = powershell(tmp_path, f"""
$result = Invoke-RuntimeNative -FilePath 'python.exe' -Arguments @({ps_quote(child)}, 'fail') -WorkingDirectory {ps_quote(tmp_path)}
$timedOut = $false
try {{ $null = Invoke-RuntimeNative -FilePath 'python.exe' -Arguments @({ps_quote(child)}, 'hang') -WorkingDirectory {ps_quote(tmp_path)} -TimeoutMilliseconds 300 }}
catch {{ $timedOut = $_.Exception.Message -match 'timed out' }}
$service = [pscustomobject]@{{ScriptPath={ps_quote(child)}}}
@{{code=$result.exit_code; output=$result.stdout.Trim(); timed_out=$timedOut; remaining=@(Get-RuntimeServiceProcesses $service).Count}} | ConvertTo-Json
""")
    assert result == {"code": 7, "output": "started", "timed_out": True, "remaining": 0}


def test_actual_supervisor_preserves_stop_and_rejects_duplicate_owner(tmp_path: Path) -> None:
    runtime = tmp_path / "isolated runtime"
    scripts = runtime / "scripts"
    scripts.mkdir(parents=True)
    for name in ("main_runtime.ps1", "run_main_services.ps1", "main_services_launcher.vbs"):
        (scripts / name).write_bytes((ROOT / "scripts" / name).read_bytes())
    (scripts / "fixture.py").write_text("import time\ntime.sleep(120)\n")
    (scripts / "windows-service-launch-contract.json").write_text(json.dumps({
        "schema_version": "windows-service-launch-contract-v1",
        "services": [{"key": "fixture", "kind": "Python", "script": "scripts\\fixture.py", "arguments": []}],
    }))
    (runtime / ".gitignore").write_text(".local/\n")
    (runtime / "pyproject.toml").write_text("# fixture dependencies\n")
    git(runtime, "init", "-b", "main")
    git(runtime, "config", "user.name", "Runtime contract")
    git(runtime, "config", "user.email", "fixture@example.invalid")
    git(runtime, "add", ".")
    git(runtime, "commit", "-m", "isolated runtime")
    forward = runtime / ".local/forward"
    git(runtime, "remote", "add", "origin", str(runtime))
    forward.mkdir(parents=True)
    (forward / "main-installed-dependencies.sha256").write_text(hashlib.sha256((runtime / "pyproject.toml").read_bytes()).hexdigest().upper())
    status_path = forward / "main-runtime-status.json"
    command = ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(scripts / "run_main_services.ps1"), "-RuntimeRoot", str(runtime)]
    owner = subprocess.Popen([*command, "-Action", "Run"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=subprocess.CREATE_NO_WINDOW)

    def wait_state(state: str) -> dict:
        deadline = time.monotonic() + 20
        latest = {}
        while time.monotonic() < deadline:
            if status_path.exists():
                latest = read_runtime_json(status_path)
                if latest.get("state") == state:
                    return latest
            if owner.poll() is not None:
                raise AssertionError((latest, owner.communicate()))
            time.sleep(0.1)
        raise AssertionError(latest)

    try:
        status = wait_state("stopped")
        assert status["services"]["fixture"] == []
        second = subprocess.run([*command, "-Action", "Run"], capture_output=True, timeout=15, creationflags=subprocess.CREATE_NO_WINDOW)
        assert second.returncode == 0, second.stderr
        assert read_runtime_json(status_path)["controller_pid"] == owner.pid
        # Exercise the actual CLI without making its launcher the initial owner.
        start = subprocess.run([*command, "-Action", "Start"], capture_output=True, timeout=15, creationflags=subprocess.CREATE_NO_WINDOW)
        assert start.returncode == 0, start.stderr
        status = wait_state("running")
        assert len(status["services"]["fixture"]) == 1
        stop = subprocess.run([*command, "-Action", "Stop"], capture_output=True, timeout=15, creationflags=subprocess.CREATE_NO_WINDOW)
        assert stop.returncode == 0, stop.stderr
        assert wait_state("stopped")["services"]["fixture"] == []
        time.sleep(5.5)
        assert wait_state("stopped")["services"]["fixture"] == []
    finally:
        # Only fixture-owned controller/processes are touched, even on failure.
        if owner.poll() is None:
            try:
                latest = read_runtime_json(status_path) if status_path.exists() else {}
                if latest.get("state") != "stopped":
                    subprocess.run([*command, "-Action", "Stop"], capture_output=True, timeout=15, creationflags=subprocess.CREATE_NO_WINDOW)
                    time.sleep(5.5)
            finally:
                owner.terminate()
        owner.communicate(timeout=15)
        powershell(tmp_path, f"""
$script:RuntimeRoot = {ps_quote(runtime)}
$script:RepositoryRoot = $script:RuntimeRoot
$script:ForwardRoot = {ps_quote(forward)}
$service = @(Get-RuntimeServices)[0]
Stop-RuntimeService $service
@{{remaining=@(Get-RuntimeServiceProcesses $service).Count}} | ConvertTo-Json
""")


def test_real_scheduled_task_registration_preserves_scoped_hidden_entry(tmp_path: Path) -> None:
    import uuid
    scripts = tmp_path / "runtime with spaces" / "scripts"
    scripts.mkdir(parents=True)
    for name in ("main_runtime.ps1", "run_main_services.ps1", "main_services_launcher.vbs"):
        (scripts / name).write_bytes((ROOT / "scripts" / name).read_bytes())
    task_name = "XAUUSD-Test-Main-" + uuid.uuid4().hex
    result = powershell(tmp_path, f"""
$script:RuntimeRoot = {ps_quote(scripts.parent)}
$script:RepositoryRoot = {ps_quote(tmp_path / 'configuration root')}
try {{
    Install-MainRuntimeTask -TaskName {ps_quote(task_name)}
    $task = Get-ScheduledTask -TaskName {ps_quote(task_name)}
    @{{execute=$task.Actions[0].Execute; arguments=$task.Actions[0].Arguments; working_directory=$task.Actions[0].WorkingDirectory; triggers=@($task.Triggers).Count; multiple_instances=[string]$task.Settings.MultipleInstances; limit=$task.Settings.ExecutionTimeLimit}} | ConvertTo-Json
}} finally {{
    Unregister-ScheduledTask -TaskName {ps_quote(task_name)} -Confirm:$false -ErrorAction SilentlyContinue
}}
""")
    assert result["execute"].lower().endswith("system32\\wscript.exe")
    assert str(scripts / "main_services_launcher.vbs") in result["arguments"]
    assert str(scripts / "run_main_services.ps1") in result["arguments"]
    assert str(tmp_path / "configuration root") in result["arguments"]
    assert result["working_directory"] == str(scripts.parent)
    assert result["triggers"] == 2
    assert result["multiple_instances"] == "IgnoreNew"
    assert result["limit"] == "PT0S"
