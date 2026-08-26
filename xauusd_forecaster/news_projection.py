"""Deterministic, bounded generation materialization for the 60-day news mirror."""

from __future__ import annotations

import hashlib
import json
import math
import struct
from dataclasses import dataclass
from typing import Iterable

NEWS_PROJECTION_CONTRACT_VERSION = "news-projection-generation-v3"
NEWS_MIRROR_CONTRACT_VERSION = NEWS_PROJECTION_CONTRACT_VERSION
NEWS_PROJECTION_MAX_ITEMS = 10_000
NEWS_INDEX_BATCH_ITEMS = 4
NEWS_DETAIL_BATCH_ITEMS = 8
NEWS_DETAIL_BATCH_LIMIT_BYTES = 400_000
NEWS_INDEX_BATCH_LIMIT_BYTES = 100_000
EMPTY_RECEIPT_DIGEST = hashlib.sha256(b"").hexdigest()

NEWS_INDEX_FIELDS = (
    "category", "source", "source_item_id", "revision_number", "cluster_id",
    "source_published_time", "collector_first_seen_time", "headline",
    "content_characters", "content_status", "content_fetch_status",
    "content_error_type", "annotation_status", "annotation_reason_code",
    "annotation_reason", "model_visibility", "parsed_at", "emerging_topic_zh",
    "impact_status", "impact_class", "impact_event_state",
    "impact_update_type", "impact_assessed_at", "impact_expires_at",
    "impact_event_at", "impact_clock_source", "impact_reason_zh",
    "mirror_updated_at",
)


def compact_json(value: object, *, sort_keys: bool = False) -> str:
    return json.dumps(
        value, ensure_ascii=False, allow_nan=False, separators=(",", ":"),
        sort_keys=sort_keys,
    )


def sha256_json(value: object, *, sort_keys: bool = False) -> str:
    return hashlib.sha256(
        compact_json(value, sort_keys=sort_keys).encode("utf-8")
    ).hexdigest()


def canonical_receipt_bytes(value: object) -> bytes:
    """Encode one JSON value identically across Python and JavaScript runtimes."""
    if value is None:
        return b"n;"
    if value is True:
        return b"t;"
    if value is False:
        return b"f;"
    if isinstance(value, (int, float)):
        if isinstance(value, int) and abs(value) > 9_007_199_254_740_991:
            raise ValueError("receipt integer exceeds the JSON safe-integer range")
        number = float(value)
        if not math.isfinite(number):
            raise ValueError("receipt number must be finite")
        if number == 0:
            number = 0.0
        return b"d" + struct.pack(">d", number).hex().encode("ascii") + b";"
    if isinstance(value, str):
        encoded = value.encode("utf-8")
        return b"s" + str(len(encoded)).encode("ascii") + b":" + encoded + b";"
    if isinstance(value, (list, tuple)):
        return (
            b"a" + str(len(value)).encode("ascii") + b":"
            + b"".join(canonical_receipt_bytes(item) for item in value)
            + b";"
        )
    if isinstance(value, dict):
        if any(not isinstance(key, str) for key in value):
            raise ValueError("receipt object keys must be strings")
        keys = sorted(value, key=lambda key: key.encode("utf-8"))
        return (
            b"o" + str(len(keys)).encode("ascii") + b":"
            + b"".join(
                canonical_receipt_bytes(key) + canonical_receipt_bytes(value[key])
                for key in keys
            )
            + b";"
        )
    raise ValueError(f"unsupported receipt value type: {type(value).__name__}")


def receipt_payload_hash(value: object) -> str:
    return hashlib.sha256(canonical_receipt_bytes(value)).hexdigest()


def stable_news_key(row: dict) -> str:
    identity = "\0".join((
        str(row.get("source", "")), str(row.get("source_item_id", "")),
        str(row.get("revision_number", "")),
    ))
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def content_addressed_detail_key(row: dict, detail_payload: dict) -> str:
    """Bind derived detail identity to both source revision and exact content."""
    return sha256_json({
        "source_identity": stable_news_key(row),
        "detail_hash": sha256_json(detail_payload),
    }, sort_keys=True)


def split_news_rows(rows: Iterable[dict]) -> tuple[list[dict], list[dict]]:
    """Return deterministically ordered index and detail projections."""
    projected: list[tuple[str, dict, dict]] = []
    for raw in rows:
        row = dict(raw)
        detail_payload = {
            key: value for key, value in row.items() if key not in NEWS_INDEX_FIELDS
        }
        detail_key = content_addressed_detail_key(row, detail_payload)
        index = {key: row.get(key) for key in NEWS_INDEX_FIELDS}
        index["cluster_id"] = str(index.get("cluster_id") or detail_key)
        index.update({
            "detail_key": detail_key,
            "mirror_contract": NEWS_MIRROR_CONTRACT_VERSION,
        })
        detail = {
            "detail_key": detail_key,
            "detail_hash": sha256_json(detail_payload),
            "payload": detail_payload,
        }
        projected.append((detail_key, index, detail))
    projected.sort(key=lambda item: item[0])
    return (
        [item[1] for item in projected],
        [item[2] for item in projected],
    )


def bounded_batches(
    rows: list[dict], limit_bytes: int, *, max_items: int,
) -> list[list[dict]]:
    batches: list[list[dict]] = []
    current: list[dict] = []
    for row in rows:
        candidate = [*current, row]
        size = len(compact_json(candidate).encode("utf-8"))
        if current and (size > limit_bytes or len(candidate) > max_items):
            batches.append(current)
            current = [row]
        else:
            current = candidate
        if len(compact_json(current).encode("utf-8")) > limit_bytes:
            raise ValueError("one news projection row exceeds its transport bound")
    if current:
        batches.append(current)
    return batches


def receipt_digest(
    detail_batches: list[list[dict]], index_batches: list[list[dict]],
) -> str:
    digest = EMPTY_RECEIPT_DIGEST
    for kind, batches in (("detail", detail_batches), ("index", index_batches)):
        offset = 0
        for batch in batches:
            payload_hash = receipt_payload_hash(batch)
            digest = hashlib.sha256(
                f"{digest}\n{kind}|{offset}|{len(batch)}|{payload_hash}".encode("utf-8")
            ).hexdigest()
            offset += len(batch)
    return digest


@dataclass(frozen=True)
class NewsProjectionGeneration:
    manifest: dict
    index_rows: tuple[dict, ...]
    detail_rows: tuple[dict, ...]
    index_batches: tuple[tuple[dict, ...], ...]
    detail_batches: tuple[tuple[dict, ...], ...]


def build_news_projection_generation(
    rows: list[dict], withdrawals: list[dict], *, window_start: str, watermark: str,
) -> NewsProjectionGeneration:
    if len(rows) > NEWS_PROJECTION_MAX_ITEMS:
        raise ValueError("news projection exceeds the 10,000-row generation bound")
    index_rows, detail_rows = split_news_rows(rows)
    withdrawal_keys = sorted({stable_news_key(row) for row in withdrawals})
    source_digest = sha256_json({
        "index": index_rows, "details": detail_rows,
        "withdrawal_keys": withdrawal_keys,
    }, sort_keys=True)
    snapshot_id = sha256_json({
        "contract_version": NEWS_PROJECTION_CONTRACT_VERSION,
        "window_start": window_start, "watermark": watermark,
        "source_digest": source_digest,
    }, sort_keys=True)
    generation_id = sha256_json({
        "snapshot_id": snapshot_id,
        "contract_version": NEWS_PROJECTION_CONTRACT_VERSION,
    }, sort_keys=True)
    detail_batches = bounded_batches(
        detail_rows, NEWS_DETAIL_BATCH_LIMIT_BYTES,
        max_items=NEWS_DETAIL_BATCH_ITEMS,
    )
    index_batches = bounded_batches(
        index_rows, NEWS_INDEX_BATCH_LIMIT_BYTES,
        max_items=NEWS_INDEX_BATCH_ITEMS,
    )
    manifest = {
        "generation_id": generation_id,
        "snapshot_id": snapshot_id,
        "contract_version": NEWS_PROJECTION_CONTRACT_VERSION,
        "window_start": window_start,
        "watermark": watermark,
        "expected_index_count": len(index_rows),
        "expected_detail_count": len(detail_rows),
        "withdrawal_count": len(withdrawal_keys),
        "source_digest": source_digest,
        "expected_receipt_digest": receipt_digest(detail_batches, index_batches),
    }
    return NewsProjectionGeneration(
        manifest=manifest,
        index_rows=tuple(index_rows), detail_rows=tuple(detail_rows),
        index_batches=tuple(tuple(batch) for batch in index_batches),
        detail_batches=tuple(tuple(batch) for batch in detail_batches),
    )
