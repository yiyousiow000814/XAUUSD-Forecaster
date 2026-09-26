"""Pure, bounded Dashboard resource serialization shared by API and Sync.

Inputs and producer identity belong to callers. This owner performs no reads,
scheduling, transport, checkpoint updates, or runtime discovery.
"""
from __future__ import annotations

import copy
import json
import math
from datetime import datetime

from xauusd_forecaster.dashboard.payloads import (
    audit_briefs_payload,
    audit_stories_payload,
    audit_status_payload,
    critical_status_payload,
    valid_audit_detail_payload,
)
from xauusd_forecaster.news_projection import (
    NEWS_DETAIL_BATCH_ITEMS,
    NEWS_DETAIL_BATCH_LIMIT_BYTES,
    NEWS_INDEX_FIELDS,
    NEWS_INDEX_BATCH_LIMIT_BYTES,
    NEWS_MIRROR_CONTRACT_VERSION,
    NEWS_INDEX_BATCH_ITEMS as NEWS_WRITE_BATCH_ITEMS,
    bounded_batches as _projection_bounded_batches,
    sha256_json as _projection_json_hash,
    split_news_rows,
    stable_news_key,
)


REMOTE_PAYLOAD_LIMIT_BYTES = 750_000
REMOTE_NEWS_LIMIT = 200
REMOTE_DAILY_BRIEF_LIMIT = 14
REMOTE_MARKET_CANDLE_LIMIT = 576
REMOTE_MARKET_OVERVIEW_LIMITS = (480, 240, 120, 80, 40)
MARKET_CHART_SNAPSHOT_LIMIT_BYTES = 230_000
AUDIT_FIRST_PAGE_LIMIT_BYTES = 16_000
AUDIT_DETAIL_LIMIT_BYTES = 120_000


class PayloadContractError(ValueError):
    """A bounded payload still violates the remote transport contract."""

    error_code = "PAYLOAD_CONTRACT_REJECTED"




def _stable_news_key(row: dict) -> str:
    return stable_news_key(row)


def news_withdrawal_keys(payload: dict) -> list[str]:
    """Return stable keys withdrawn by a completed semantic decision."""
    rows = payload.get("withdrawals")
    if not isinstance(rows, list):
        return []
    keys: list[str] = []
    for row in rows:
        if not isinstance(row, dict) or not all(
            key in row for key in ("source", "source_item_id", "revision_number")
        ):
            raise PayloadContractError("invalid news withdrawal identity")
        keys.append(_stable_news_key(row))
    return keys


def _json_hash(value: object) -> str:
    return _projection_json_hash(value, sort_keys=True)


def news_mirror_parts(payload: dict) -> tuple[list[dict], list[dict]]:
    """Split the complete news rows into a compact index and lazy details."""
    rows = payload.get("items")
    if not isinstance(rows, list):
        rows = payload.get("recent_news", [])[:REMOTE_NEWS_LIMIT]
    return split_news_rows(rows)


def news_detail_batches(rows: list[dict]) -> list[list[dict]]:
    return _projection_bounded_batches(
        rows, NEWS_DETAIL_BATCH_LIMIT_BYTES, max_items=NEWS_DETAIL_BATCH_ITEMS,
    )


def news_index_batches(rows: list[dict]) -> list[list[dict]]:
    return _projection_bounded_batches(
        rows, NEWS_INDEX_BATCH_LIMIT_BYTES, max_items=NEWS_WRITE_BATCH_ITEMS,
    )


def _bounded_item_batches(
    rows: list[dict], limit_bytes: int, *, envelope: str = "items",
    max_items: int | None = None,
) -> list[list[dict]]:
    batches: list[list[dict]] = []
    current: list[dict] = []
    for row in rows:
        candidate = [*current, row]
        size = len(json.dumps(
            {envelope: candidate}, ensure_ascii=False, allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8"))
        if current and (
            size > limit_bytes or (max_items is not None and len(candidate) > max_items)
        ):
            batches.append(current)
            current = [row]
        else:
            current = candidate
    if current:
        batches.append(current)
    return batches


def _epoch(value: object) -> int:
    try:
        return int(datetime.fromisoformat(
            str(value).replace("Z", "+00:00")
        ).timestamp())
    except (TypeError, ValueError):
        return 0
















def _downsample_market_overview(rows: list[dict], limit: int) -> list[dict]:
    if len(rows) <= limit:
        return rows
    chunk_size = math.ceil(len(rows) / limit)
    compacted = []
    for start in range(0, len(rows), chunk_size):
        chunk = rows[start:start + chunk_size]
        compacted.append({
            "time": chunk[0]["time"], "open": chunk[0]["open"],
            "high": max(row["high"] for row in chunk),
            "low": min(row["low"] for row in chunk),
            "close": chunk[-1]["close"],
            "source_candles": sum(int(row.get("source_candles") or 1) for row in chunk),
        })
    return compacted


def compact_market_chart(
    payload: dict,
    overview_limit: int = REMOTE_MARKET_OVERVIEW_LIMITS[0],
) -> dict:
    """Keep a bounded recent chart; D1 owns the complete market history."""
    market = copy.deepcopy(payload.get("market_chart") or {})
    for candle_key in ("candles", "overview_candles"):
        compact_candles = []
        source_rows = market.get(candle_key, [])
        if candle_key == "overview_candles":
            source_rows = _downsample_market_overview(source_rows, overview_limit)
        else:
            source_rows = source_rows[-REMOTE_MARKET_CANDLE_LIMIT:]
        for row in source_rows:
            compact = {key: value for key, value in row.items() if key != "ticks"}
            if str(compact.get("time") or "").endswith("+00:00"):
                compact["time"] = str(compact["time"])[:-6] + "Z"
            for key in ("open", "high", "low", "close"):
                if compact.get(key) is not None:
                    compact[key] = round(float(compact[key]), 3)
            compact_candles.append(compact)
        market[candle_key] = compact_candles
    for retired in ("decisions", "training_markers", "prediction_history_start"):
        market.pop(retired, None)
    return market


def market_chart_snapshot(payload: dict) -> bytes:
    last_size = 0
    for overview_limit in REMOTE_MARKET_OVERVIEW_LIMITS:
        encoded = json.dumps(
            compact_market_chart(payload, overview_limit),
            ensure_ascii=False, allow_nan=False, separators=(",", ":"),
        ).encode("utf-8")
        last_size = len(encoded)
        if last_size <= MARKET_CHART_SNAPSHOT_LIMIT_BYTES:
            return encoded
    raise PayloadContractError(
        f"half-hour market chart payload is {last_size} bytes "
        f"(limit {MARKET_CHART_SNAPSHOT_LIMIT_BYTES})"
    )




def _encoded_snapshot(snapshot: dict, *, label: str) -> bytes:
    encoded = json.dumps(
        snapshot, ensure_ascii=False, allow_nan=False, separators=(",", ":")
    ).encode("utf-8")
    if len(encoded) > REMOTE_PAYLOAD_LIMIT_BYTES:
        raise PayloadContractError(
            f"{label} payload is {len(encoded)} bytes "
            f"(limit {REMOTE_PAYLOAD_LIMIT_BYTES})"
        )
    return encoded


def remote_snapshot(payload: dict) -> bytes:
    """Project the bounded critical state; unknown fields are optional by default."""
    return _encoded_snapshot(
        critical_status_payload(payload), label="critical dashboard status",
    )


def audit_snapshot(payload: dict) -> bytes:
    """Build a fixed summary independently of all growing audit detail."""
    return _bounded_audit_snapshot(
        audit_status_payload(payload), label="audit summary",
        limit=AUDIT_FIRST_PAGE_LIMIT_BYTES,
    )


def _bounded_audit_snapshot(payload: dict, *, label: str, limit: int) -> bytes:
    encoded = json.dumps(
        payload, ensure_ascii=False, allow_nan=False, separators=(",", ":"),
    ).encode("utf-8")
    if len(encoded) > limit:
        raise PayloadContractError(
            f"{label} payload is {len(encoded)} bytes (limit {limit})"
        )
    return encoded


def _with_projection_producer(snapshot: dict, producer_revision: str | None) -> dict:
    projected = dict(snapshot)
    if producer_revision:
        projected["producer_revision"] = producer_revision
    return projected


def audit_briefs_snapshot(
    payload: dict, producer_revision: str | None = None,
) -> bytes:
    return _audit_detail_snapshot(
        _with_projection_producer(
            audit_briefs_payload(payload, brief_limit=REMOTE_DAILY_BRIEF_LIMIT),
            producer_revision,
        ),
        family="briefs",
    )




def audit_stories_snapshot(
    payload: dict, producer_revision: str | None = None,
) -> bytes:
    return _audit_detail_snapshot(
        _with_projection_producer(
            audit_stories_payload(payload), producer_revision,
        ), family="stories",
    )


def _audit_detail_snapshot(payload: dict, *, family: str) -> bytes:
    if not valid_audit_detail_payload(payload, family):
        raise PayloadContractError(f"audit {family} source detail is unavailable or invalid")
    return _bounded_audit_snapshot(
        payload, label=f"audit {family}", limit=AUDIT_DETAIL_LIMIT_BYTES,
    )
