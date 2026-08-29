from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import time

import pytest

from xauusd_forecaster.forward_ledger import ForwardLedger


ROOT = Path(__file__).resolve().parents[1]
CONTROL = ROOT / "scripts" / "xauusd_control_center.ps1"
OLD_STABLE = "783d25314b090dd7fbbf124777c3b8de517d2b85"
pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows only")


def _port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture(scope="module")
def old_stable_root(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("actual-revisions") / "old-stable"
    result = subprocess.run(
        ["git", "worktree", "add", "--detach", str(root), OLD_STABLE],
        cwd=ROOT, capture_output=True, text=True, check=False,
    )
    if result.returncode:
        pytest.skip(f"exact old Stable object unavailable: {result.stderr}")
    try:
        yield root
    finally:
        subprocess.run(
            ["git", "worktree", "remove", "--force", str(root)],
            cwd=ROOT, capture_output=True, check=False,
        )


@pytest.mark.parametrize(
    "shell", [name for name in ("powershell.exe", "pwsh.exe") if shutil.which(name)],
)
def test_real_new_controller_to_old_stable_parser_contracts(
    old_stable_root: Path, shell: str,
) -> None:
    command = (
        f"$null=. '{CONTROL}' -Action CodeRevision -RuntimeRoot "
        f"'{old_stable_root}' -RepositoryRoot '{ROOT}';"
        "$services|ConvertTo-Json -Depth 7"
    )
    resolved = subprocess.run(
        [shell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command],
        capture_output=True, text=True, check=True,
    )
    services = {item["Key"]: item for item in json.loads(resolved.stdout)}

    numeric_failure = {
        "collector": ("--poll-seconds", "not-a-number"),
        "annotator": ("--interval-seconds", "not-a-number"),
        "api": ("--port", "not-a-number"),
        "sync": ("--interval-seconds", "not-a-number"),
    }
    for key, (flag, invalid) in numeric_failure.items():
        service = services[key]
        result = subprocess.run(
            [sys.executable, service["ScriptPath"], *service["Arguments"], flag, invalid],
            cwd=old_stable_root, capture_output=True, text=True, check=False,
            timeout=15,
        )
        assert result.returncode == 2
        assert "unrecognized arguments" not in result.stderr.lower()
        assert "invalid" in result.stderr.lower()


@pytest.mark.parametrize(
    "revision_root,is_current", [(ROOT, True)],
)
def test_real_current_api_startup_and_health_under_explicit_runtime_authority(
    tmp_path: Path, revision_root: Path, is_current: bool,
) -> None:
    home = tmp_path / "home"
    state = home / "XAUUSD-Forecaster-runtime" / ".local" / "forward"
    state.mkdir(parents=True)
    database = state / "forward-evidence.sqlite3"
    ForwardLedger(database).close()
    port = _port()
    env = {**os.environ, "HOME": str(home), "USERPROFILE": str(home), "PYTHONUTF8": "1"}
    args = [
        sys.executable, str(revision_root / "scripts" / "run_dashboard_api.py"),
        "--state-root", str(state), "--database", str(database),
        "--host", "127.0.0.1", "--port", str(port),
    ]
    process = subprocess.Popen(
        args, cwd=revision_root, env=env,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    try:
        import urllib.request

        deadline = time.monotonic() + 25
        while time.monotonic() < deadline:
            if process.poll() is not None:
                stdout, stderr = process.communicate()
                raise AssertionError(f"API exited\n{stdout}\n{stderr}")
            try:
                with urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/api/health", timeout=1,
                ) as response:
                    assert response.status == 200
                    break
            except OSError:
                time.sleep(0.2)
        else:
            raise AssertionError("current API did not produce real health evidence")
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()


def test_real_old_stable_api_startup_and_health(
    tmp_path: Path, old_stable_root: Path,
) -> None:
    database = tmp_path / "forward-evidence.sqlite3"
    ForwardLedger(database).close()
    port = _port()
    process = subprocess.Popen(
        [sys.executable, str(old_stable_root / "scripts" / "run_dashboard_api.py"),
         "--database", str(database), "--host", "127.0.0.1", "--port", str(port)],
        cwd=old_stable_root, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    try:
        import urllib.request

        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if process.poll() is not None:
                stdout, stderr = process.communicate()
                raise AssertionError(f"legacy API exited\n{stdout}\n{stderr}")
            try:
                with urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/api/health", timeout=1,
                ) as response:
                    assert response.status == 200
                    break
            except OSError:
                time.sleep(0.2)
        else:
            raise AssertionError("old Stable API did not produce real health evidence")
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()


def test_candidate_checkout_failure_restores_captured_old_stable_and_health(
    tmp_path: Path,
) -> None:
    runtime = tmp_path / "candidate-runtime"
    subprocess.run(
        ["git", "worktree", "add", "--detach", str(runtime), "HEAD"],
        cwd=ROOT, capture_output=True, text=True, check=True,
    )
    state = runtime / ".local" / "forward"
    state.mkdir(parents=True)
    database = state / "forward-evidence.sqlite3"
    ForwardLedger(database).close()
    port = _port()
    script_path = runtime / "scripts" / "run_dashboard_api.py"
    command = (
        f"$null=. '{CONTROL}' -Action CodeRevision -RuntimeRoot '{runtime}' "
        f"-RepositoryRoot '{ROOT}';"
        "$body=[ordered]@{schema='runtime-recovery-plan-v1';"
        f"stable_revision='{OLD_STABLE}';stable_worker_version='stable-worker';"
        f"runtime_root='{runtime}';runtime_state_root='{state}';"
        f"config_root='{ROOT / '.local' / 'config'}';running_service_keys=@('api');"
        "process_baseline=[ordered]@{api=@()};service_contracts=@([ordered]@{"
        f"revision='{OLD_STABLE}';code_root='{runtime}';key='api';label='Dashboard API';"
        "match='run_dashboard_api.py';kind='Python';script='scripts\\run_dashboard_api.py';"
        f"script_path='{script_path}';arguments=@('--database','{database}','--host','127.0.0.1','--port','{port}')"
        f"}});rollback_target='{OLD_STABLE}'}};"
        "$json=$body|ConvertTo-Json -Depth 9 -Compress;"
        "$plan=[pscustomobject]@{body=[pscustomobject]$body;digest=Get-Sha256BytesHex "
        "-Bytes ([Text.Encoding]::UTF8.GetBytes($json))};"
        "Restore-RuntimeRecoveryPlan -Plan $plan|Out-Null"
    )
    try:
        launched = subprocess.run(
            ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command],
            cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            check=False, timeout=30,
        )
        assert launched.returncode == 0
        assert subprocess.run(
            ["git", "-C", str(runtime), "rev-parse", "HEAD"],
            capture_output=True, text=True, check=True,
        ).stdout.strip() == OLD_STABLE

        import urllib.request

        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            try:
                with urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/api/health", timeout=1,
                ) as response:
                    assert response.status == 200
                    break
            except OSError:
                time.sleep(0.2)
        else:
            logs = []
            for path in sorted((state / "logs").glob("control-api-*.stderr.log"))[-2:]:
                logs.append(path.read_text(encoding="utf-8", errors="replace")[-2000:])
            raise AssertionError(
                "captured old Stable did not regain API health\n" + "\n".join(logs)
            )
    finally:
        sockets = subprocess.run(
            ["netstat", "-ano", "-p", "tcp"], capture_output=True, text=True,
            check=False,
        ).stdout
        for line in sockets.splitlines():
            fields = line.split()
            if len(fields) >= 5 and fields[1].endswith(f":{port}") and fields[3] == "LISTENING":
                subprocess.run(
                    ["taskkill", "/PID", fields[4], "/T", "/F"],
                    capture_output=True, check=False,
                )
        subprocess.run(
            ["git", "worktree", "remove", "--force", str(runtime)],
            cwd=ROOT, capture_output=True, check=False,
        )
