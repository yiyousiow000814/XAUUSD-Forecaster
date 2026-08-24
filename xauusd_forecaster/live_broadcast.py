"""Bounded PUBLIC_LIVE_V1 projection and opt-in broadcast publisher."""

from __future__ import annotations

import copy
import json
import urllib.error
import urllib.request
from collections.abc import Mapping
from pathlib import Path
from typing import Any

LIVE_SCHEMA_VERSION = "PUBLIC_LIVE_V1"
LIVE_BROADCAST_ORIGIN = "https://aurum-live-broadcast.yiyousiow1234.workers.dev"
MAX_LIVE_BYTES = 16_384
MAX_RECENT_DECISIONS = 18
FORECAST_FIELDS = (
    "model_identity", "model_version", "recommended_action",
    "prediction_status", "ev_long_u5", "ev_short_u5", "interval_width",
    "decision_time", "signal_expiry_seconds", "forecast_horizon_seconds",
    "directional_bias", "frozen_record",
)
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
    if state["forecast"].get("recommended_action") not in {"LONG", "SHORT", "WAIT"}:
        raise LiveBroadcastContractError("invalid forecast.recommended_action")
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
    forecast = _mapping(status.get("research_forecast"))
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
            for key in FORECAST_FIELDS
        },
        "health": {
            "status": operational.get("status", "UNKNOWN"),
            "alerts": bounded_alerts,
        },
    }
    state["forecast"]["recommended_action"] = (
        forecast.get("recommended_action")
        if forecast.get("recommended_action") in {"LONG", "SHORT", "WAIT"}
        else "WAIT"
    )
    state["forecast"]["prediction_status"] = forecast.get(
        "prediction_status", "UNAVAILABLE",
    )
    state["forecast"]["signal_expiry_seconds"] = forecast.get(
        "signal_expiry_seconds", 20,
    )
    state["forecast"]["forecast_horizon_seconds"] = forecast.get(
        "forecast_horizon_seconds", 1_800,
    )
    state["forecast"]["directional_bias"] = forecast.get(
        "directional_bias", "NEUTRAL",
    )
    state["forecast"]["frozen_record"] = bool(forecast.get("frozen_record"))
    if isinstance(decisions, list):
        state["recent_decisions"] = compact_decisions
    return validate_live_state(state)


def publish_live_state(
    token: str, state: Mapping[str, Any], *, dry_run: bool = True,
    allow_production_publish: bool = False, timeout_seconds: int = 10,
) -> dict[str, Any]:
    """Publish only through an explicit activation boundary; Preview stays dry-run."""
    if not token:
        raise ValueError("LIVE_BROADCAST_PUBLISH_TOKEN is required")
    if not dry_run and not allow_production_publish:
        raise PermissionError("production broadcast publisher is not activated")
    payload = serialize_live_state(validate_live_state(state))
    endpoint = f"{LIVE_BROADCAST_ORIGIN}/publish"
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


def read_broadcast_health(*, timeout_seconds: int = 10) -> dict[str, Any]:
    """Read sequence authority only from the pinned broadcast service."""
    request = urllib.request.Request(
        f"{LIVE_BROADCAST_ORIGIN}/health", method="GET",
        headers={"accept": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        return json.loads(response.read().decode("utf-8"))


class LiveSequenceStore:
    """Persist the last accepted sequence under the single Windows owner."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def read(self) -> int:
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
            sequence = value.get("last_sequence")
            return sequence if isinstance(sequence, int) and sequence > 0 else 0
        except (OSError, ValueError, TypeError):
            return 0

    def write(self, sequence: int) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(f"{self.path.suffix}.tmp")
        temporary.write_text(
            json.dumps({"last_sequence": sequence}, sort_keys=True),
            encoding="utf-8",
        )
        temporary.replace(self.path)


class ContinuousLivePublisher:
    """One restart-safe publisher; network failure affects only this owner."""

    def __init__(
        self,
        token: str,
        sequence_store: LiveSequenceStore,
        *,
        health_reader=read_broadcast_health,
        publisher=publish_live_state,
    ) -> None:
        self.token = token
        self.sequence_store = sequence_store
        self.health_reader = health_reader
        self.publisher = publisher
        self._last_sequence: int | None = None

    def reconcile_sequence(self) -> int:
        health = self.health_reader()
        if (
            health.get("service") != "aurum-live-broadcast"
            or health.get("schema_version") != LIVE_SCHEMA_VERSION
            or health.get("binding_ready") is not True
        ):
            raise LiveBroadcastContractError("broadcast sequence authority is not ready")
        remote = health.get("latest_sequence")
        remote_sequence = remote if isinstance(remote, int) and remote > 0 else 0
        self._last_sequence = max(self.sequence_store.read(), remote_sequence)
        return self._last_sequence

    def publish(
        self, status: Mapping[str, Any], *, source_revision: str,
        dry_run: bool = False, allow_production_publish: bool = False,
    ) -> dict[str, Any]:
        if self._last_sequence is None:
            self.reconcile_sequence()
        sequence = int(self._last_sequence or 0) + 1
        state = public_live_state(
            status, sequence=sequence, source_revision=source_revision,
        )
        try:
            result = self.publisher(
                self.token, state, dry_run=dry_run,
                allow_production_publish=allow_production_publish,
            )
        except urllib.error.HTTPError as error:
            if error.code != 409:
                raise
            try:
                rejection = json.loads(error.read().decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                rejection = {}
            latest = rejection.get("latest_sequence")
            if not isinstance(latest, int) or latest < sequence:
                raise
            self._last_sequence = latest
            sequence = latest + 1
            state = public_live_state(
                status, sequence=sequence, source_revision=source_revision,
            )
            result = self.publisher(
                self.token, state, dry_run=dry_run,
                allow_production_publish=allow_production_publish,
            )
        if not dry_run:
            accepted = result.get("sequence", sequence)
            if not isinstance(accepted, int) or accepted != sequence:
                raise LiveBroadcastContractError("publisher sequence acknowledgement mismatch")
            self._last_sequence = accepted
            self.sequence_store.write(accepted)
        return result
