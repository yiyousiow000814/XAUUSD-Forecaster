"""Required, isolated real-process rehearsal; separate from 30-second unit tests."""
from __future__ import annotations

import importlib.util
import json
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
        root = Path(temporary).resolve(strict=True)
        withdrawal = root / "withdrawal"
        withdrawal.mkdir()
        try:
            module.run_staged_activation_withdrawal_rehearsal(withdrawal)
            print("STAGED_ACTIVATION_WITHDRAWAL_PASSED", flush=True)
            active = root / "active"
            active.mkdir()
            active = module.run_staged_installer_active_rehearsal(active, sealed_configuration=True)
            for fixture in (withdrawal, active):
                attestations = [json.loads(path.read_text(encoding="utf-8"))
                               for path in (fixture / "environment-attestations").glob("*.json")]
                if len({row["pid"] for row in attestations}) < 2:
                    raise AssertionError("CHILD_CONFIGURATION_INHERITANCE_NOT_PROVEN")
                if len({row["configuration_sha256"] for row in attestations}) != 1:
                    raise AssertionError("CHILD_CONFIGURATION_IDENTITY_MISMATCH")
                if not any(row["action"] == "Watchdog" for row in attestations):
                    raise AssertionError("WATCHDOG_CONFIGURATION_NOT_PROVEN")
                if fixture == active and not any(row["action"] == "BusinessSync" for row in attestations):
                    raise AssertionError("BUSINESS_CONFIGURATION_NOT_PROVEN")
            print("CONTROL_CHILD_CONFIGURATION_INHERITANCE_PASSED", flush=True)
        except Exception:
            for phase in ("withdrawal", "active"):
                fixture = root / phase
                if phase == "active":
                    owned = list(fixture.glob("xauusd-rehearsal-*"))
                    if len(owned) == 1:
                        fixture = owned[0]
                diagnostic = fixture / "child-failure.txt"
                if diagnostic.exists():
                    print(diagnostic.read_text(encoding="utf-8")[:8192], flush=True)
                handoff = fixture / "handoff-failure.json"
                if handoff.exists():
                    print(handoff.read_text(encoding="utf-8")[:8192], flush=True)
                runtime = "profile/XAUUSD-Forecaster-runtime" if phase == "active" else "runtime"
                state = fixture / runtime / ".local/forward/control-watchdog-heartbeat.json"
                if state.exists():
                    print(state.read_text(encoding="utf-8")[:8192], flush=True)
            raise
    print("STAGED_ACTIVE_TAKEOVER_PASSED; SYNC_TIMEOUT_ISOLATED; OWNED_PROCESSES_CLEANED")


if __name__ == "__main__":
    main()
