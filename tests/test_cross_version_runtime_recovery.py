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
pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows only")

def _port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


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
