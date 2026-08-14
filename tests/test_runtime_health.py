import json
from concurrent.futures import ThreadPoolExecutor

from xauusd_forecaster.runtime_health import write_runtime_heartbeat


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
