from __future__ import annotations

import json
import urllib.error
from io import BytesIO
from unittest.mock import patch

import pytest

from xauusd_forecaster.live_broadcast import (
    ContinuousLivePublisher,
    MAX_LIVE_BYTES,
    MAX_RECENT_DECISIONS,
    LiveBroadcastContractError,
    LiveSequenceStore,
    public_live_state,
    publish_live_state,
    serialize_live_state,
    validate_live_state,
)
from scripts.run_live_broadcast_publisher import run_publisher_loop


def status() -> dict:
    return {
        "generated_at": "2026-08-23T05:00:00.000Z",
        "system": {"online": True, "market_session": "OPEN"},
        "latest": {
            "bid": 3370.1, "ask": 3370.3, "source_received_time": "2026-08-23T04:59:59.000Z",
        },
        "research_forecast": {
            "model_identity": "FULL", "model_version": "v18",
            "recommended_action": "WAIT", "prediction_status": "READY",
            "ev_long_u5": 0.1, "ev_short_u5": -0.1, "interval_width": 0.2,
            "decision_time": "2026-08-23T04:55:00.000Z",
            "signal_expiry_seconds": 20, "forecast_horizon_seconds": 1800,
            "directional_bias": "NEUTRAL", "frozen_record": True,
        },
        "operational_health": {
            "status": "WARNING",
            "alerts": [{"code": f"A{index}", "severity": "WARNING", "scope": "PUBLIC", "evidence": {"private": True}} for index in range(8)],
        },
        "recent_decisions": [
            {"decision_id": str(index), "decision_time": "2026-08-23T04:55:00.000Z", "effective_action": "WAIT", "features": {"secret": index}, "predictions": [1]}
            for index in range(24)
        ],
        "gemini_quota": {"tokens": 100}, "annotation_queue": {"jobs": 5},
        "learning_history": [1, 2, 3],
    }


def test_public_live_projection_is_bounded_and_private_free() -> None:
    state = public_live_state(status(), sequence=9, source_revision="abc123")
    encoded = serialize_live_state(state)
    assert len(encoded) < MAX_LIVE_BYTES
    assert len(state["recent_decisions"]) == MAX_RECENT_DECISIONS
    assert len(state["health"]["alerts"]) == 4
    assert b"features" not in encoded
    assert b"gemini" not in encoded
    assert state["quote"]["spread"] == pytest.approx(0.2)
    source_without_decisions = status()
    source_without_decisions.pop("recent_decisions")
    assert "recent_decisions" not in public_live_state(
        source_without_decisions, sequence=10, source_revision="abc123",
    )


def test_contract_rejects_private_fields_and_oversize() -> None:
    state = public_live_state(status(), sequence=1, source_revision="abc")
    with pytest.raises(LiveBroadcastContractError, match="private"):
        validate_live_state({**state, "llm_routing": {}})
    with pytest.raises(LiveBroadcastContractError, match="oversized"):
        validate_live_state({**state, "health": {"status": "OK", "padding": "x" * MAX_LIVE_BYTES}})


def test_publisher_is_dry_run_by_default_and_never_places_token_in_url() -> None:
    state = public_live_state(status(), sequence=1, source_revision="abc")

    class Response:
        def __enter__(self): return self
        def __exit__(self, *_args): return None
        def read(self): return json.dumps({"valid": True, "dry_run": True}).encode()

    with patch("urllib.request.urlopen", return_value=Response()) as opened:
        result = publish_live_state("secret-token", state)
    request = opened.call_args.args[0]
    assert request.full_url == (
        "https://aurum-live-broadcast.yiyousiow1234.workers.dev/publish?dry_run=true"
    )
    assert "secret-token" not in request.full_url
    assert request.headers["Authorization"] == "Bearer secret-token"
    assert result["dry_run"] is True


def test_live_publish_requires_explicit_future_activation() -> None:
    state = public_live_state(status(), sequence=1, source_revision="abc")
    with pytest.raises(PermissionError, match="not activated"):
        publish_live_state("secret", state, dry_run=False)


@pytest.mark.parametrize("action", ["LONG", "SHORT", "WAIT"])
def test_forecast_projection_matches_public_live_room_contract(action: str) -> None:
    source = status()
    source["research_forecast"]["recommended_action"] = action
    state = public_live_state(source, sequence=1, source_revision="abc")
    assert state["forecast"] == source["research_forecast"]


def test_continuous_publisher_recovers_sequence_across_restart(tmp_path) -> None:
    sequence_store = LiveSequenceStore(tmp_path / "sequence.json")
    accepted = []

    def send(_token, state, **_kwargs):
        accepted.append(state["sequence"])
        return {"stored": True, "sequence": state["sequence"]}

    first = ContinuousLivePublisher(
        "secret", sequence_store,
        health_reader=lambda: {
            "service": "aurum-live-broadcast", "schema_version": "PUBLIC_LIVE_V1",
            "binding_ready": True, "latest_sequence": 7,
        }, publisher=send,
    )
    first.publish(status(), source_revision="abc", allow_production_publish=True)
    restarted = ContinuousLivePublisher(
        "secret", sequence_store,
        health_reader=lambda: {
            "service": "aurum-live-broadcast", "schema_version": "PUBLIC_LIVE_V1",
            "binding_ready": True, "latest_sequence": 8,
        }, publisher=send,
    )
    restarted.publish(status(), source_revision="abc", allow_production_publish=True)
    assert accepted == [8, 9]
    assert sequence_store.read() == 9


def test_stale_sequence_rejection_repairs_only_sequence_and_retries(tmp_path) -> None:
    sent = []

    def send(_token, state, **_kwargs):
        sent.append(state["sequence"])
        if len(sent) == 1:
            raise urllib.error.HTTPError(
                "https://aurum-live-broadcast.yiyousiow1234.workers.dev/publish",
                409, "stale", {}, BytesIO(b'{"latest_sequence":7}'),
            )
        return {"stored": True, "sequence": state["sequence"]}

    store = LiveSequenceStore(tmp_path / "sequence.json")
    publisher = ContinuousLivePublisher(
        "secret", store,
        health_reader=lambda: {
            "service": "aurum-live-broadcast", "schema_version": "PUBLIC_LIVE_V1",
            "binding_ready": True, "latest_sequence": 5,
        }, publisher=send,
    )
    result = publisher.publish(
        status(), source_revision="abc", allow_production_publish=True,
    )
    assert sent == [6, 8]
    assert result["sequence"] == 8
    assert store.read() == 8


def test_failed_interval_reuses_unaccepted_sequence_without_false_gap(tmp_path) -> None:
    sent = []

    def send(_token, state, **_kwargs):
        sent.append(state["sequence"])
        if len(sent) == 1:
            raise OSError("broadcast unavailable")
        return {"stored": True, "sequence": state["sequence"]}

    store = LiveSequenceStore(tmp_path / "sequence.json")
    publisher = ContinuousLivePublisher(
        "secret", store,
        health_reader=lambda: {
            "service": "aurum-live-broadcast", "schema_version": "PUBLIC_LIVE_V1",
            "binding_ready": True, "latest_sequence": 5,
        }, publisher=send,
    )
    with pytest.raises(OSError, match="unavailable"):
        publisher.publish(
            status(), source_revision="abc", allow_production_publish=True,
        )
    publisher.publish(
        status(), source_revision="abc", allow_production_publish=True,
    )

    assert sent == [6, 6]
    assert store.read() == 6


def test_continuous_owner_isolates_failure_and_keeps_cadence(tmp_path) -> None:
    class Publisher:
        calls = 0

        def publish(self, *_args, **_kwargs):
            self.calls += 1
            if self.calls == 1:
                raise OSError("broadcast unavailable")
            return {"sequence": 12}

    publisher = Publisher()
    sleeps = []
    run_publisher_loop(
        publisher,
        source_revision="abc",
        status_path=tmp_path / "publisher-status.json",
        interval_seconds=30,
        status_reader=status,
        sleep=sleeps.append,
        max_cycles=2,
    )
    recorded = json.loads((tmp_path / "publisher-status.json").read_text())
    assert publisher.calls == 2
    assert sleeps == [30]
    assert recorded["state"] == "RUNNING"
    assert recorded["last_sequence"] == 12
