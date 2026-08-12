import json

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
    assert not path.with_suffix(".json.tmp").exists()
