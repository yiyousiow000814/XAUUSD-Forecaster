import json
import time
from concurrent.futures import ThreadPoolExecutor

from xauusd_forecaster.runtime_health import (
    RuntimeHeartbeatPulse,
    write_runtime_heartbeat,
)


def test_runtime_heartbeat_is_atomic_and_identifies_service(tmp_path) -> None:
    path = tmp_path / "nested" / "collector-status.json"

    write_runtime_heartbeat(
        path, service="collector", state="STARTING", work_items=3,
    )

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["service"] == "collector"
    assert payload["state"] == "STARTING"
    assert payload["work_items"] == 3
    assert payload["last_success"]
    assert payload["last_error"] is None
    assert not list(path.parent.glob(f".{path.name}.*.tmp"))


def test_runtime_heartbeat_supports_overlapping_candidate_and_rollback_writers(
    tmp_path,
) -> None:
    path = tmp_path / "forward" / "annotator-status.json"

    def write(index: int) -> None:
        write_runtime_heartbeat(
            path, service="annotator", work_items=index,
        )

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(write, range(40)))

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["service"] == "annotator"
    assert payload["state"] == "RUNNING"
    assert payload["work_items"] in range(40)
    assert not list(path.parent.glob(f".{path.name}.*.tmp"))


def test_runtime_heartbeat_pulse_stays_fresh_during_blocking_work(tmp_path) -> None:
    path = tmp_path / "forward" / "annotator-status.json"

    def read_last_success() -> str:
        for attempt in range(50):
            try:
                return json.loads(path.read_text(encoding="utf-8"))["last_success"]
            except PermissionError:
                if attempt == 49:
                    raise
                time.sleep(0.002)
        raise AssertionError("heartbeat remained unreadable")

    with RuntimeHeartbeatPulse(
        path,
        service="annotator",
        work_items=2,
        interval_seconds=0.02,
    ) as pulse:
        first = read_last_success()
        deadline = time.monotonic() + 1.0
        current = first
        while current == first and time.monotonic() < deadline:
            time.sleep(0.01)
            current = read_last_success()
        pulse.update(work_items=3)

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert current != first
    assert payload["service"] == "annotator"
    assert payload["state"] == "RUNNING"
    assert payload["work_items"] == 3
    assert not list(path.parent.glob(f".{path.name}.*.tmp"))


def test_runtime_heartbeat_manual_lifecycle_supports_supervised_loops(
    tmp_path,
) -> None:
    path = tmp_path / "forward" / "collector-status.json"
    pulse = RuntimeHeartbeatPulse(
        path, service="collector", interval_seconds=0.02,
    )

    pulse.start()
    time.sleep(0.04)
    pulse.update(work_items=4)
    pulse.close()

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["service"] == "collector"
    assert payload["state"] == "RUNNING"
    assert payload["work_items"] == 4
