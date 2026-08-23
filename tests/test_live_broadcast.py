from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from xauusd_forecaster.live_broadcast import (
    MAX_LIVE_BYTES,
    LiveBroadcastContractError,
    public_live_state,
    publish_live_state,
    serialize_live_state,
    validate_live_state,
)


def status() -> dict:
    return {
        "generated_at": "2026-08-23T05:00:00.000Z",
        "system": {"online": True, "market_session": "OPEN"},
        "latest": {
            "bid": 3370.1, "ask": 3370.3, "source_received_time": "2026-08-23T04:59:59.000Z",
        },
        "research_forecast": {"action": "WAIT", "hold_minutes": 30, "decision_time": "2026-08-23T04:55:00.000Z"},
        "operational_health": {
            "status": "WARNING",
            "alerts": [{"code": f"A{index}", "severity": "WARNING", "scope": "PUBLIC", "evidence": {"private": True}} for index in range(8)],
        },
        "recent_decisions": [
            {"decision_id": str(index), "decision_time": "2026-08-23T04:55:00.000Z", "effective_action": "WAIT", "features": {"secret": index}, "predictions": [1]}
            for index in range(12)
        ],
        "gemini_quota": {"tokens": 100}, "annotation_queue": {"jobs": 5},
        "learning_history": [1, 2, 3],
    }


def test_public_live_projection_is_bounded_and_private_free() -> None:
    state = public_live_state(status(), sequence=9, source_revision="abc123")
    encoded = serialize_live_state(state)
    assert len(encoded) < MAX_LIVE_BYTES
    assert len(state["recent_decisions"]) == 6
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
