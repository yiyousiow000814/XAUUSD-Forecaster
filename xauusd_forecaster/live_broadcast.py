"""Bounded PUBLIC_LIVE_V1 projection and opt-in broadcast publisher."""

from __future__ import annotations

import copy
import json
import urllib.parse
import urllib.request
from collections.abc import Mapping
from typing import Any

LIVE_SCHEMA_VERSION = "PUBLIC_LIVE_V1"
MAX_LIVE_BYTES = 16_384
MAX_RECENT_DECISIONS = 6
PRIVATE_FIELDS = {
    "gemini_quota", "gemini_31_quota", "gemma_quota",
    "gemini_embedding_quota", "annotation_queue", "llm_routing", "admin",
    "features", "tokens", "secrets", "learning_history", "market_history",
    "news_archive",
}


class LiveBroadcastContractError(ValueError):
    """The compact public state violates PUBLIC_LIVE_V1."""


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _contains_private_field(value: Any) -> bool:
    if isinstance(value, Mapping):
        return any(
            str(key).lower() in PRIVATE_FIELDS or _contains_private_field(nested)
            for key, nested in value.items()
        )
    if isinstance(value, list):
        return any(_contains_private_field(item) for item in value)
    return False


def serialize_live_state(value: Mapping[str, Any]) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, allow_nan=False, sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def validate_live_state(value: Mapping[str, Any]) -> dict[str, Any]:
    state = copy.deepcopy(dict(value))
    if state.get("schema_version") != LIVE_SCHEMA_VERSION:
        raise LiveBroadcastContractError("invalid schema_version")
    sequence = state.get("sequence")
    if not isinstance(sequence, int) or isinstance(sequence, bool) or sequence < 1:
        raise LiveBroadcastContractError("invalid sequence")
    for key in ("generated_at", "source_revision", "market_session"):
        if not isinstance(state.get(key), str) or not state[key]:
            raise LiveBroadcastContractError(f"invalid {key}")
    freshness = _mapping(state.get("freshness"))
    if not isinstance(freshness.get("online"), bool):
        raise LiveBroadcastContractError("invalid freshness")
    quote = _mapping(state.get("quote"))
    for key in ("bid", "ask", "spread"):
        if not isinstance(quote.get(key), (int, float)) or isinstance(quote[key], bool):
            raise LiveBroadcastContractError(f"invalid quote.{key}")
    if quote["ask"] < quote["bid"]:
        raise LiveBroadcastContractError("crossed quote")
    if not isinstance(quote.get("source_received_time"), str):
        raise LiveBroadcastContractError("invalid quote.source_received_time")
    if not isinstance(state.get("forecast"), Mapping) or not isinstance(state.get("health"), Mapping):
        raise LiveBroadcastContractError("invalid public summaries")
    decisions = state.get("recent_decisions")
    if decisions is not None and (
        not isinstance(decisions, list) or len(decisions) > MAX_RECENT_DECISIONS
    ):
        raise LiveBroadcastContractError("recent_decisions is not bounded")
    if _contains_private_field(state):
        raise LiveBroadcastContractError("private field is forbidden")
    if len(serialize_live_state(state)) > MAX_LIVE_BYTES:
        raise LiveBroadcastContractError("live state is oversized")
    return state


def public_live_state(status: Mapping[str, Any], *, sequence: int, source_revision: str) -> dict[str, Any]:
    """Project only delivery state; growing research remains on existing APIs."""
    system = _mapping(status.get("system"))
    latest = _mapping(status.get("latest"))
    forecast = _mapping(status.get("research_forecast")) or latest
    operational = _mapping(status.get("operational_health"))
    decisions = status.get("recent_decisions")
    compact_decisions = []
    if isinstance(decisions, list):
        for row in decisions[:MAX_RECENT_DECISIONS]:
            if not isinstance(row, Mapping):
                continue
            compact_decisions.append({
                key: copy.deepcopy(row.get(key))
                for key in ("decision_id", "decision_time", "effective_action", "outcome_status")
                if key in row
            })
    bid = latest.get("bid", system.get("bid"))
    ask = latest.get("ask", system.get("ask"))
    spread = latest.get("spread")
    if spread is None and isinstance(bid, (int, float)) and isinstance(ask, (int, float)):
        spread = ask - bid
    alerts = operational.get("alerts")
    bounded_alerts = []
    if isinstance(alerts, list):
        bounded_alerts = [
            {key: item.get(key) for key in ("code", "severity", "scope") if key in item}
            for item in alerts[:4] if isinstance(item, Mapping)
        ]
    generated_at = status.get("generated_at")
    state = {
        "schema_version": LIVE_SCHEMA_VERSION,
        "sequence": sequence,
        "generated_at": generated_at,
        "source_revision": source_revision,
        "market_session": system.get("market_session", "DATA_UNAVAILABLE"),
        "freshness": {
            "online": bool(system.get("online")),
            "state": "FRESH" if system.get("online") else "STALE",
        },
        "quote": {
            "bid": bid, "ask": ask, "spread": spread,
            "source_received_time": latest.get("source_received_time", generated_at),
        },
        "forecast": {
            key: copy.deepcopy(forecast.get(key))
            for key in ("action", "effective_action", "decision_time", "hold_minutes", "confidence")
            if key in forecast
        },
        "health": {
            "status": operational.get("status", "UNKNOWN"),
            "alerts": bounded_alerts,
        },
    }
    if isinstance(decisions, list):
        state["recent_decisions"] = compact_decisions
    return validate_live_state(state)


def publish_live_state(
    url: str, token: str, state: Mapping[str, Any], *, dry_run: bool = True,
    allow_production_publish: bool = False, timeout_seconds: int = 10,
) -> dict[str, Any]:
    """Publish only through an explicit activation boundary; Preview stays dry-run."""
    if not token:
        raise ValueError("LIVE_BROADCAST_PUBLISH_TOKEN is required")
    if not dry_run and not allow_production_publish:
        raise PermissionError("production broadcast publisher is not activated")
    payload = serialize_live_state(validate_live_state(state))
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https" or not parsed.netloc or parsed.query:
        raise ValueError("broadcast URL must be a query-free HTTPS origin")
    endpoint = f"{url.rstrip('/')}/publish"
    if dry_run:
        endpoint += "?dry_run=true"
    request = urllib.request.Request(
        endpoint, data=payload, method="POST",
        headers={
            "authorization": f"Bearer {token}",
            "content-type": "application/json",
            "content-length": str(len(payload)),
        },
    )
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        return json.loads(response.read().decode("utf-8"))
