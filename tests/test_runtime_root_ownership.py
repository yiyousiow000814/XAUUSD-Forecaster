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


@pytest.mark.parametrize("variable", [
    "XAUUSD_ISOLATED_CONFIGURATION", "XAUUSD_ISOLATED_CONFIGURATION_SHA256",
])
def test_quote_bridge_rejects_retired_release_context_before_credentials(variable: str) -> None:
    launcher = ROOT / "ctrader" / "XauusdForwardQuoteBridge" / "run_live_quote_bridge.ps1"
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
         "-File", str(launcher), "-BuildOnly"],
        env={**os.environ, variable: "retired-context"},
        capture_output=True, text=True, check=False, timeout=15,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    assert result.returncode != 0
    assert "RETIRED_RELEASE_FIXTURE_CONFIGURATION" in result.stderr
    assert "control_center_common" not in result.stderr
