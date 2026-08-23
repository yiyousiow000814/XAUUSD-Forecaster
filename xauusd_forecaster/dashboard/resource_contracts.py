"""Deterministic, byte-bounded Dashboard resource projections.

This module owns serialization contracts only. It has no transport, cursor,
scheduler, process, thread, or authoritative state ownership.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
from datetime import UTC, datetime

from xauusd_forecaster.dashboard_payloads import (
    audit_briefs_payload,
    audit_decisions_payload,
    audit_stories_payload,
    audit_status_payload,
    critical_status_payload,
)

REMOTE_PAYLOAD_LIMIT_BYTES = 750_000

REMOTE_NEWS_LIMIT = 200

REMOTE_DECISION_LIMIT = 20

REMOTE_DAILY_BRIEF_LIMIT = 14

NEWS_DETAIL_BATCH_LIMIT_BYTES = 160_000

NEWS_INDEX_BATCH_LIMIT_BYTES = 100_000

NEWS_WRITE_BATCH_ITEMS = 4

NEWS_DETAIL_BATCH_ITEMS = 8

NEWS_READER_WINDOW_DAYS = 60

NEWS_MIRROR_CONTRACT_VERSION = "news-60-day-incremental-v10-publication-clock-skew"

LEARNING_HISTORY_CONTRACT_VERSION = "learning-history-d1-v2"

LEARNING_HISTORY_BATCH_LIMIT_BYTES = 60_000

LEARNING_SUMMARY_CURVE_POINTS = 48

LEARNING_SUMMARY_GROUPS_PER_IDENTITY = 6

LEARNING_SUMMARY_EXECUTION_RESULTS = 20

LEARNING_OVERVIEW_CURVE_POINTS = 240

LEARNING_OVERVIEW_GROUPS_PER_IDENTITY = 60

MARKET_OVERVIEW_DECISIONS_PER_SERIES = 240

REMOTE_MARKET_DECISION_LIMIT = 288 * 5

REMOTE_MARKET_CANDLE_LIMIT = 576

REMOTE_MARKET_DENSE_LIMITS = (1440, 1152, 864, 576, 288, 192, 144, 0)

REMOTE_MARKET_OVERVIEW_LIMITS = (480, 240, 120, 80, 40)

MARKET_CHART_SNAPSHOT_LIMIT_BYTES = 230_000

AUDIT_FIRST_PAGE_LIMIT_BYTES = 16_000

AUDIT_DETAIL_LIMIT_BYTES = 120_000

class PayloadContractError(ValueError):
    """A bounded payload still violates the remote transport contract."""

    error_code = "PAYLOAD_CONTRACT_REJECTED"

NEWS_INDEX_FIELDS = (
    "category", "source", "source_item_id", "revision_number", "cluster_id",
    "source_published_time", "collector_first_seen_time", "headline",
    "content_characters", "content_status", "content_fetch_status",
    "content_error_type", "annotation_status", "annotation_reason_code",
    "annotation_reason",
    "model_visibility", "parsed_at", "emerging_topic_zh",
    "impact_status", "impact_class", "impact_event_state",
    "impact_update_type", "impact_assessed_at", "impact_expires_at",
    "impact_event_at", "impact_clock_source", "impact_reason_zh",
    "mirror_updated_at",
)

MARKET_DECISION_FIELDS = (
    "source_decision_id", "decision_time", "model_identity",
    "recommended_action", "outcome_status", "ev_long_u5", "ev_short_u5",
    "long_quote_return", "short_quote_return",
)

def _stable_news_key(row: dict) -> str:
    identity = "\0".join((
        str(row.get("source", "")), str(row.get("source_item_id", "")),
        str(row.get("revision_number", "")),
    ))
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()

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
    encoded = json.dumps(
        value, ensure_ascii=False, allow_nan=False, separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()

def news_mirror_parts(payload: dict) -> tuple[list[dict], list[dict]]:
    """Split the complete news rows into a compact index and lazy details."""
    index_rows = []
    detail_rows = []
    rows = payload.get("items")
    if not isinstance(rows, list):
        rows = payload.get("recent_news", [])[:REMOTE_NEWS_LIMIT]
    for row in rows:
        detail_key = _stable_news_key(row)
        detail_payload = {
            key: value for key, value in row.items() if key not in NEWS_INDEX_FIELDS
        }
        encoded_detail = json.dumps(
            detail_payload, ensure_ascii=False, allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")
        detail_hash = hashlib.sha256(encoded_detail).hexdigest()
        index_rows.append({
            **{key: row.get(key) for key in NEWS_INDEX_FIELDS},
            "detail_key": detail_key,
            "mirror_contract": NEWS_MIRROR_CONTRACT_VERSION,
        })
        detail_rows.append({
            "detail_key": detail_key,
            "detail_hash": detail_hash,
            "payload": detail_payload,
        })
    return index_rows, detail_rows

def news_detail_batches(rows: list[dict]) -> list[list[dict]]:
    return _bounded_item_batches(
        rows, NEWS_DETAIL_BATCH_LIMIT_BYTES, max_items=NEWS_DETAIL_BATCH_ITEMS,
    )

def news_index_batches(rows: list[dict]) -> list[list[dict]]:
    return _bounded_item_batches(
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

def _learning_record(
    resource: str, record_key: str, sort_epoch: int, payload: dict
) -> dict:
    return {
        "resource": resource,
        "record_key": record_key,
        "sort_epoch": sort_epoch,
        "payload_hash": _json_hash(payload),
        "payload": payload,
    }

def _visual_curve_overview(
    points: list[dict], limit: int, expected_step_seconds: int = 300,
    infer_source_gaps: bool = True,
) -> list[dict]:
    """Keep curve shape plus real source gaps in a fixed-size overview."""
    ordered = sorted(points, key=lambda row: row.get("decision_time") or "")
    selected_indices: list[int] = []
    if len(ordered) <= limit:
        selected_indices = list(range(len(ordered)))
    else:
        bucket_count = max(1, limit // 4)
        bucket_size = math.ceil(len(ordered) / bucket_count)
        for start in range(0, len(ordered), bucket_size):
            bucket = ordered[start:start + bucket_size]
            indexed = list(enumerate(bucket))
            selected = {
                0,
                len(bucket) - 1,
                min(indexed, key=lambda item: float(
                    item[1].get("cumulative_quote_return") or 0.0
                ))[0],
                max(indexed, key=lambda item: float(
                    item[1].get("cumulative_quote_return") or 0.0
                ))[0],
            }
            selected_indices.extend(start + index for index in sorted(selected))
    selected_indices = selected_indices[:limit]
    gap_threshold = max(45 * 60, expected_step_seconds * 3)
    overview: list[dict] = []
    previous_index: int | None = None
    for index in selected_indices:
        source_gap_before = False
        if infer_source_gaps and previous_index is not None:
            source_gap_before = any(
                _epoch(ordered[current].get("decision_time"))
                - _epoch(ordered[current - 1].get("decision_time"))
                >= gap_threshold
                for current in range(previous_index + 1, index + 1)
            )
        overview.append({
            **ordered[index], "source_gap_before": source_gap_before,
        })
        previous_index = index
    return overview

def _visual_decision_overview(rows: list[dict], limit: int) -> list[dict]:
    """Retain the time span and action changes in a bounded marker summary."""
    ordered = sorted(rows, key=lambda row: (
        row.get("decision_time") or "", row.get("source_decision_id") or "",
    ))
    deduplicated = {
        str(row.get("source_decision_id") or ""): row
        for row in ordered if row.get("source_decision_id")
    }
    ordered = sorted(deduplicated.values(), key=lambda row: (
        row.get("decision_time") or "", row.get("source_decision_id") or "",
    ))
    if len(ordered) <= limit:
        return ordered
    bucket_count = max(1, limit // 4)
    bucket_size = math.ceil(len(ordered) / bucket_count)
    overview: list[dict] = []
    for start in range(0, len(ordered), bucket_size):
        bucket = ordered[start:start + bucket_size]
        selected = {0, len(bucket) - 1}
        for action in ("LONG", "SHORT"):
            match = next((
                index for index, row in enumerate(bucket)
                if row.get("recommended_action") == action
            ), None)
            if match is not None:
                selected.add(match)
        overview.extend(bucket[index] for index in sorted(selected))
    return overview[:limit]

def _version_metric(row: dict, cadence: str) -> float:
    metrics = row.get("cadence_metrics") or {}
    selected = metrics.get(cadence) if isinstance(metrics, dict) else None
    if isinstance(selected, dict):
        return float(selected.get("cumulative_quote_return") or 0.0)
    return float(row.get("cumulative_quote_return") or 0.0)

def _visual_version_overview(rows: list[dict], limit: int) -> list[dict]:
    """Preserve the full generation span and extrema for both chart cadences."""
    ordered = sorted(rows, key=lambda row: (
        row.get("created_at") or "", row.get("generation") or 0,
    ))
    if len(ordered) <= limit:
        return ordered
    bucket_count = max(1, limit // 6)
    bucket_size = math.ceil(len(ordered) / bucket_count)
    overview: list[dict] = []
    for start in range(0, len(ordered), bucket_size):
        bucket = ordered[start:start + bucket_size]
        indexed = list(enumerate(bucket))
        selected = {0, len(bucket) - 1}
        for cadence in ("EVERY_5M", "FIXED_30M"):
            selected.add(min(
                indexed, key=lambda item: _version_metric(item[1], cadence),
            )[0])
            selected.add(max(
                indexed, key=lambda item: _version_metric(item[1], cadence),
            )[0])
        overview.extend(bucket[index] for index in sorted(selected))
    return overview[:limit]

def _update_decision_overviews(
    summaries: dict, decisions: list[dict], after: str | None,
) -> dict:
    """Refresh sampled decisions and append only genuinely new observations."""
    updated = copy.deepcopy(summaries) if isinstance(summaries, dict) else {}
    for identity in sorted({
        str(row.get("model_identity") or "") for row in decisions
        if isinstance(row, dict) and row.get("model_identity")
    }):
        identity_rows = [
            row for row in decisions
            if isinstance(row, dict) and row.get("model_identity") == identity
        ]
        for frequency in ("5m", "30m"):
            incoming = identity_rows if frequency == "5m" else [
                row for row in identity_rows
                if _epoch(row.get("decision_time")) % 1_800 == 0
            ]
            key = f"{identity}\0{frequency}"
            previous = updated.get(key) if isinstance(updated.get(key), dict) else {}
            previous_rows = [
                row for row in previous.get("decisions", [])
                if isinstance(row, dict) and row.get("source_decision_id")
            ]
            previous_by_key = {
                str(row["source_decision_id"]): row for row in previous_rows
            }
            incoming_by_key = {
                str(row["source_decision_id"]): row for row in incoming
                if row.get("source_decision_id")
            }
            new_rows = [
                row for row in incoming
                if row.get("source_decision_id")
                and str(row["source_decision_id"]) not in previous_by_key
                and (not after or _epoch(row.get("decision_time")) >= _epoch(after))
            ]
            refreshed_rows = [
                incoming_by_key.get(str(row["source_decision_id"]), row)
                for row in previous_rows
            ]
            if not new_rows and refreshed_rows == previous_rows:
                continue
            merged = [*refreshed_rows, *new_rows]
            overview = _visual_decision_overview(
                merged, MARKET_OVERVIEW_DECISIONS_PER_SERIES,
            )
            updated[key] = {
                "model_identity": identity,
                "frequency": frequency,
                "source_decision_count": int(
                    previous.get("source_decision_count") or 0
                ) + len(new_rows),
                "decision_count": len(overview),
                "decision_downsampled": (
                    int(previous.get("source_decision_count") or 0)
                    + len(new_rows) > len(overview)
                ),
                "decisions": overview,
            }
    return updated

def _learning_overview_records(
    payload: dict, *, infer_source_gaps: bool = True,
) -> list[dict]:
    """Materialize fixed-size graph summaries before data reaches the Worker."""
    learning = payload.get("learning_curves") or {}
    records: list[dict] = []
    for curve in learning.get("identity_curves", []):
        if not isinstance(curve, dict):
            continue
        identity = str(curve.get("model_identity") or "")
        if not identity:
            continue
        for field, cadence in (("points", "5m"), ("points_30m", "30m")):
            points = [
                point for point in (curve.get(field, []) or [])
                if isinstance(point, dict) and point.get("decision_time")
            ]
            if not points:
                continue
            overview = _visual_curve_overview(
                points, LEARNING_OVERVIEW_CURVE_POINTS,
                expected_step_seconds=1_800 if cadence == "30m" else 300,
                infer_source_gaps=infer_source_gaps,
            )
            summary = {
                "model_identity": identity,
                "cadence": cadence,
                "source_point_count": len(points),
                "chart_point_count": len(overview),
                "chart_downsampled": len(overview) < len(points),
                "points": overview,
            }
            records.append(_learning_record(
                "curve-overview", f"{cadence}\0{identity}",
                _epoch(points[-1]["decision_time"]), summary,
            ))
    groups = learning.get("version_groups", [])
    identities = sorted({
        str(row.get("model_identity") or "") for row in groups
        if isinstance(row, dict) and row.get("model_identity")
    })
    for identity in identities:
        rows = sorted(
            (row for row in groups if isinstance(row, dict)
             and row.get("model_identity") == identity),
            key=lambda row: (row.get("created_at") or "", row.get("generation") or 0),
        )
        if not rows:
            continue
        overview = _visual_version_overview(
            rows, LEARNING_OVERVIEW_GROUPS_PER_IDENTITY,
        )
        summary = {
            "model_identity": identity,
            "source_group_count": len(rows),
            "chart_group_count": len(overview),
            "chart_downsampled": len(overview) < len(rows),
            "groups": overview,
        }
        records.append(_learning_record(
            "version-overview", identity, _epoch(rows[-1].get("created_at")), summary,
        ))
    return records

def learning_history_records(
    payload: dict, *, infer_source_gaps: bool = True,
) -> list[dict]:
    """Normalize append-only learning evidence into idempotent D1 records."""
    learning = payload.get("learning_curves") or {}
    records: list[dict] = []
    for row in learning.get("models", []):
        if not isinstance(row, dict):
            continue
        identity = str(row.get("model_identity") or "")
        version = str(row.get("model_version") or "")
        if identity and version:
            records.append(_learning_record(
                "model", f"{identity}\0{version}", _epoch(row.get("created_at")), row,
            ))
    for row in learning.get("version_groups", []):
        if not isinstance(row, dict):
            continue
        identity = str(row.get("model_identity") or "")
        dataset_hash = str(row.get("training_dataset_hash") or "")
        if identity and dataset_hash:
            records.append(_learning_record(
                "version-group", f"{identity}\0{dataset_hash}",
                _epoch(row.get("created_at")), row,
            ))
    for curve in learning.get("identity_curves", []):
        if not isinstance(curve, dict):
            continue
        identity = str(curve.get("model_identity") or "")
        if not identity:
            continue
        for field, resource in (("points", "curve-5m"), ("points_30m", "curve-30m")):
            for point in curve.get(field, []) or []:
                if not isinstance(point, dict) or not point.get("decision_time"):
                    continue
                record_payload = {"model_identity": identity, **point}
                records.append(_learning_record(
                    resource, f"{identity}\0{point['decision_time']}",
                    _epoch(point["decision_time"]), record_payload,
                ))
    for model in (payload.get("execution_learning") or {}).get("models", []):
        if not isinstance(model, dict):
            continue
        identity = str(model.get("model_identity") or "")
        evaluation = model.get("evaluation") or {}
        for point in evaluation.get("points", []) or []:
            if not isinstance(point, dict) or not point.get("time"):
                continue
            record_payload = {"model_identity": identity, **point}
            records.append(_learning_record(
                "execution-point", f"{identity}\0{point['time']}",
                _epoch(point["time"]), record_payload,
            ))
        for index, result in enumerate(evaluation.get("results", []) or []):
            if not isinstance(result, dict):
                continue
            result_time = result.get("scored_at") or result.get("decision_time") or ""
            result_id = result.get("decision_id") or result.get("source_decision_id") or index
            record_payload = {"model_identity": identity, **result}
            records.append(_learning_record(
                "execution-result", f"{identity}\0{result_id}\0{result_time}",
                _epoch(result_time), record_payload,
            ))
    records.extend(_learning_overview_records(
        payload, infer_source_gaps=infer_source_gaps,
    ))
    return records

def learning_history_batches(rows: list[dict]) -> list[list[dict]]:
    return _bounded_item_batches(
        rows, LEARNING_HISTORY_BATCH_LIMIT_BYTES, envelope="records"
    )

def _learning_summary(payload: dict) -> dict:
    """Return a fixed-size first page; D1 owns every older learning record."""
    learning = copy.deepcopy(payload.get("learning_curves") or {})
    models = learning.get("models")
    if isinstance(models, list):
        learning["archived_model_count"] = sum(
            row.get("lifecycle_status") not in {"LATEST", "PREVIOUS"}
            for row in models
        )
        learning["model_detail_total"] = len(models)
        learning["models"] = [
            row for row in models
            if row.get("active_rank") is not None
            or row.get("lifecycle_status") in {"LATEST", "PREVIOUS"}
        ]
    version_groups = learning.get("version_groups")
    if isinstance(version_groups, list):
        learning["version_group_total"] = len(version_groups)
        retained_groups = []
        identities = sorted({
            str(row.get("model_identity") or "") for row in version_groups
            if isinstance(row, dict)
        })
        for identity in identities:
            rows = sorted(
                (row for row in version_groups if row.get("model_identity") == identity),
                key=lambda row: (row.get("generation") or 0, row.get("created_at") or ""),
                reverse=True,
            )
            retained_groups.extend(rows[:LEARNING_SUMMARY_GROUPS_PER_IDENTITY])
        learning["version_groups"] = retained_groups
    curves = learning.get("identity_curves")
    if isinstance(curves, list):
        for curve in curves:
            if not isinstance(curve, dict):
                continue
            for field in ("points", "points_30m"):
                if isinstance(curve.get(field), list):
                    curve[field] = curve[field][-LEARNING_SUMMARY_CURVE_POINTS:]
    for field in ("full_minus_market", "broad_full_minus_core_full"):
        if isinstance(learning.get(field), list):
            learning[field] = learning[field][-LEARNING_SUMMARY_CURVE_POINTS:]

    execution = copy.deepcopy(payload.get("execution_learning") or {})
    for model in execution.get("models", []) if isinstance(execution, dict) else []:
        evaluation = model.get("evaluation") if isinstance(model, dict) else None
        if isinstance(evaluation, dict) and isinstance(evaluation.get("points"), list):
            evaluation["points"] = evaluation["points"][-LEARNING_SUMMARY_CURVE_POINTS:]
        if isinstance(evaluation, dict) and isinstance(evaluation.get("results"), list):
            evaluation["result_total"] = len(evaluation["results"])
            evaluation["results"] = evaluation["results"][-LEARNING_SUMMARY_EXECUTION_RESULTS:]
    return {
        "learning_curves": learning,
        "execution_learning": execution,
        "learning_history_resource": "/api/learning-history",
        "learning_history_manifest": {
            "contract_version": LEARNING_HISTORY_CONTRACT_VERSION,
            "model_total": len(payload.get("learning_curves", {}).get("models", [])),
            "version_group_total": len(payload.get("learning_curves", {}).get("version_groups", [])),
            "record_total": len(learning_history_records(payload)),
        },
    }

def _decision_key(row: dict) -> tuple[str, str, str]:
    return (
        str(row.get("source_decision_id") or ""),
        str(row.get("model_identity") or ""),
        str(row.get("model_version") or ""),
    )

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
    dense_limit: int = REMOTE_MARKET_DECISION_LIMIT,
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
    compact_decisions = []
    for row in market.get("decisions", []):
        compact = {
            key: row.get(key)
            for key in MARKET_DECISION_FIELDS
            if row.get(key) is not None
        }
        for key in ("ev_long_u5", "ev_short_u5"):
            if key in compact:
                compact[key] = round(float(compact[key]), 6)
        if row.get("model_version"):
            compact["model_version"] = str(row["model_version"])[-12:]
        if str(compact.get("decision_time") or "").endswith("+00:00"):
            compact["decision_time"] = str(compact["decision_time"])[:-6] + "Z"
        if row.get("prediction_status") != "PROVISIONAL_POST_COST_EV":
            compact["prediction_status"] = row.get("prediction_status")
        if row.get("outcome_reason_codes"):
            compact["outcome_reason_codes"] = row["outcome_reason_codes"]
        compact_decisions.append(compact)
    compact_decisions.sort(key=lambda row: (
        row.get("decision_time") or "", row.get("model_identity") or ""
    ))
    retained = {}
    if dense_limit:
        for row in compact_decisions[-dense_limit:]:
            retained[_decision_key(row)] = row
    market["decisions"] = sorted(retained.values(), key=lambda row: (
        row.get("decision_time") or "", row.get("model_identity") or ""
    ))
    return market

def market_chart_snapshot(payload: dict) -> bytes:
    last_size = 0
    for dense_limit in REMOTE_MARKET_DENSE_LIMITS:
        for overview_limit in REMOTE_MARKET_OVERVIEW_LIMITS:
            encoded = json.dumps(
                compact_market_chart(payload, dense_limit, overview_limit),
                ensure_ascii=False, allow_nan=False, separators=(",", ":"),
            ).encode("utf-8")
            last_size = len(encoded)
            if last_size <= MARKET_CHART_SNAPSHOT_LIMIT_BYTES:
                return encoded
    raise PayloadContractError(
        f"half-hour market chart payload is {last_size} bytes "
        f"(limit {MARKET_CHART_SNAPSHOT_LIMIT_BYTES})"
    )

def learning_snapshot(payload: dict) -> bytes:
    """Build the bounded first page after history has been stored in D1."""
    encoded = json.dumps(
        _learning_summary(payload), ensure_ascii=False, allow_nan=False,
        separators=(",", ":"),
    ).encode("utf-8")
    if len(encoded) > REMOTE_PAYLOAD_LIMIT_BYTES:
        raise PayloadContractError(
            f"bounded learning summary is {len(encoded)} bytes "
            f"(limit {REMOTE_PAYLOAD_LIMIT_BYTES})"
        )
    return encoded

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
    return _bounded_audit_snapshot(
        _with_projection_producer(
            audit_briefs_payload(payload, brief_limit=REMOTE_DAILY_BRIEF_LIMIT),
            producer_revision,
        ),
        label="audit briefs", limit=AUDIT_DETAIL_LIMIT_BYTES,
    )

def audit_decisions_snapshot(
    payload: dict, producer_revision: str | None = None,
) -> bytes:
    return _bounded_audit_snapshot(
        _with_projection_producer(
            audit_decisions_payload(payload, decision_limit=REMOTE_DECISION_LIMIT),
            producer_revision,
        ),
        label="audit decisions", limit=AUDIT_DETAIL_LIMIT_BYTES,
    )

def audit_stories_snapshot(
    payload: dict, producer_revision: str | None = None,
) -> bytes:
    return _bounded_audit_snapshot(
        _with_projection_producer(
            audit_stories_payload(payload), producer_revision,
        ), label="audit stories",
        limit=AUDIT_DETAIL_LIMIT_BYTES,
    )
