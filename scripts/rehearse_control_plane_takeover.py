"""Required, isolated real-process rehearsal; separate from 30-second unit tests."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    spec = importlib.util.spec_from_file_location(
        "control_plane_rehearsal", ROOT / "tests/test_control_plane_install.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with tempfile.TemporaryDirectory(prefix="xauusd-staged-active-") as temporary:
        module.run_staged_installer_active_rehearsal(Path(temporary))
    print("STAGED_ACTIVE_TAKEOVER_PASSED; SYNC_TIMEOUT_ISOLATED; OWNED_PROCESSES_CLEANED")


if __name__ == "__main__":
    main()
