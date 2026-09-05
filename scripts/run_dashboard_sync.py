#!/usr/bin/env python
"""Mirror the read-only dashboard snapshot to independent remote dashboards."""
from __future__ import annotations
import argparse
import copy
import hashlib
import http.client
import json
import math
import os
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime, timedelta
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
MODULE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MODULE_ROOT))
DEFERRED_PROJECTION_CONTRACT = "deferred-projection-sync-v1"
DEFERRED_PROJECTION_ROUTES = frozenset({
    "/api/audit-briefs", "/api/audit-stories", "/api/audit-decisions",
})
UUID_PATTERN = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}"
)
REMOTE_PAYLOAD_LIMIT_BYTES = 750_000
REMOTE_NEWS_LIMIT = 200
REMOTE_DECISION_LIMIT = 20
REMOTE_DAILY_BRIEF_LIMIT = 14
NEWS_PROJECTION_BATCHES_PER_CYCLE = 4
NEWS_EVIDENCE_WRITE_BATCH_ITEMS = 8
NEWS_EVIDENCE_BATCH_LIMIT_BYTES = 80_000
NEWS_EVIDENCE_PAGES_PER_CYCLE = 1
NEWS_EVIDENCE_CLEANUP_STEPS_PER_CYCLE = 8
MARKET_HISTORY_PAGES_PER_CYCLE = 1
MARKET_OVERVIEWS_PER_CYCLE = 2
HEAVY_RESOURCES_PER_CYCLE = 1
RESOURCE_BACKOFF_MAX_SECONDS = 3_600
NEWS_READER_WINDOW_DAYS = 60
NEWS_EVIDENCE_CONTRACT_VERSION = "news-evidence-paged-v2"
MARKET_HISTORY_CONTRACT_VERSION = "market-history-d1-v2"
MARKET_HISTORY_BATCH_LIMIT_BYTES = 350_000
MARKET_HISTORY_BATCH_ITEMS = 25
MARKET_HISTORY_OVERLAP_SECONDS = 2 * 3_600
LEARNING_HISTORY_CONTRACT_VERSION = "learning-history-d1-v2"
LEARNING_HISTORY_BATCH_LIMIT_BYTES = 60_000
LEARNING_HISTORY_FULL_REFRESH_SECONDS = 86_400
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

_RESOURCE_SCHEDULE_LOCK = threading.Lock()

from xauusd_forecaster.dashboard_payloads import (
    audit_briefs_payload,
    audit_decisions_payload,
    audit_stories_payload,
    audit_status_payload,
    critical_status_payload,
)
from xauusd_forecaster.news_projection import (
    NEWS_DETAIL_BATCH_ITEMS,
    NEWS_DETAIL_BATCH_LIMIT_BYTES,
    NEWS_INDEX_FIELDS,
    NEWS_INDEX_BATCH_LIMIT_BYTES,
    NEWS_MIRROR_CONTRACT_VERSION,
    NewsProjectionGeneration,
    NEWS_INDEX_BATCH_ITEMS as NEWS_WRITE_BATCH_ITEMS,
    bounded_batches as _projection_bounded_batches,
    sha256_json as _projection_json_hash,
    split_news_rows,
    stable_news_key,
)
from xauusd_forecaster.runtime_paths import (
    authoritative_runtime_root,
    runtime_child_path,
)
from xauusd_forecaster.dashboard.sync.progress import (
    OPERATOR_RETRY_COMMANDS_PER_CYCLE,
    RUNTIME_STATE_ROOT_KEY,
    AllTargetsRejected,
    SyncResourceResults,
    sync_error_code,
)
from xauusd_forecaster.dashboard.sync.transport import (
    LOCAL_STATUS_TIMEOUT_SECONDS,
    RemoteInvariantViolation,
    _assistant_worker_id,
    _get_json,
    _get_local_json,
    _local_retry_url,
    _operator_retry_worker_url,
    _post_json as _transport_post_json,
    _post_local_json,
    _validated_sync_state_path,
    configure_runtime_state,
    configured_targets,
)


class PayloadContractError(ValueError):
    """A bounded payload still violates the remote transport contract."""

    error_code = "PAYLOAD_CONTRACT_REJECTED"


MARKET_DECISION_FIELDS = (
    "source_decision_id", "decision_time", "model_identity",
    "recommended_action", "outcome_status", "ev_long_u5", "ev_short_u5",
    "long_quote_return", "short_quote_return",
)


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


def _learning_record_identity(row: dict) -> str:
    return f"{row['resource']}\0{row['record_key']}"


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


def _projection_producer_revision() -> str:
    try:
        revision = subprocess.check_output(
            ["git", "-C", str(MODULE_ROOT), "rev-parse", "HEAD"],
            text=True, timeout=5,
        ).strip().lower()
    except (OSError, subprocess.SubprocessError):
        return ""
    return revision if re.fullmatch(r"[0-9a-f]{40}", revision) else ""


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


def write_sync_status(
    path: Path,
    *,
    success: bool,
    attempts_used: int | None = None,
    error: Exception | None = None,
    degraded_resources: list[dict] | None = None,
    resource_observations: list[dict] | None = None,
) -> None:
    """Atomically publish the synchronizer's actual operational heartbeat."""
    existing: dict = {}
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            existing = {}
    now = datetime.now(UTC).isoformat()
    if success:
        degraded_resources = degraded_resources or []
        existing.update(
            {
                "last_success": now,
                "last_attempt": now,
                "last_error": None,
                "last_error_type": None,
                "last_error_code": None,
                "attempts_used": attempts_used,
                "status": "DEGRADED" if degraded_resources else "OK",
                "degraded_resources": degraded_resources,
                "resource_observations": resource_observations or [],
            }
        )
    else:
        current_degraded = list(getattr(error, "degraded_resources", None) or [])
        current_observations = list(
            getattr(error, "resource_observations", None) or []
        )
        existing.update(
            {
                "last_attempt": now,
                "last_error": str(error)[:500] if error else "Unknown sync error",
                "last_error_type": type(error).__name__ if error else "UnknownError",
                "last_error_code": sync_error_code(error),
                "status": "ERROR",
                "degraded_resources": current_degraded,
                "resource_observations": current_observations,
            }
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(existing, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)


def _write_runtime_signal(payload: object, config: dict) -> None:
    if not isinstance(payload, dict):
        return
    revision = str(payload.get("main_revision") or "").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        return
    target = Path(config["runtime_signal_file"])
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(
        json.dumps(
            {
                "main_revision": revision,
                "observed_at": datetime.now(UTC).isoformat(),
                "source": "CLOUDFLARE_MAIN_DEPLOYMENT",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    temporary.replace(target)


def _post_json(url: str, payload: bytes, config: dict) -> dict:
    result = _transport_post_json(url, payload, config)
    _write_runtime_signal(result, config)
    return result


def _sync_operator_retry_mirror(
    items: object, worker_url: str, config: dict,
) -> None:
    jobs = items if isinstance(items, list) else []
    payload = json.dumps(
        {"action": "SYNC_JOBS", "items": jobs}, ensure_ascii=False,
        allow_nan=False, separators=(",", ":"),
    ).encode("utf-8")
    source_digest = hashlib.sha256(payload).hexdigest()
    state_path_value = config.get("operator_retry_state_file")
    state_path = Path(state_path_value) if state_path_value else None
    state = _read_news_sync_state(state_path) if state_path else {}
    if state.get("source_digest") == source_digest:
        return
    result = _post_json(worker_url, payload, config)
    if result.get("complete") is True and state_path:
        _write_news_sync_state(state_path, {
            "contract_version": "operator-retry-delta-v1",
            "source_digest": source_digest,
            "item_count": len(jobs),
            "last_success": datetime.now(UTC).isoformat(),
        })


def _sync_operator_retries(_local_payload: dict, config: dict) -> None:
    local_jobs = _get_local_json(_local_retry_url(config, "/api/retry-jobs"))
    worker_url = _operator_retry_worker_url(config)
    _sync_operator_retry_mirror(local_jobs.get("items", []), worker_url, config)
    worker_id = _assistant_worker_id()
    processed = False
    for _ in range(OPERATOR_RETRY_COMMANDS_PER_CYCLE):
        command = _get_json(
            f"{worker_url}?{urllib.parse.urlencode({'worker_id': worker_id})}", config,
        ).get("item")
        if not isinstance(command, dict):
            break
        local_result = _post_local_json(
            _local_retry_url(config, "/api/retry-overrides"),
            {
                "operator_id": command.get("operator_id"),
                "items": [{
                    "request_id": command.get("request_id"),
                    "job_id": command.get("job_id"),
                    "mode": command.get("mode"),
                    "reason": command.get("reason"),
                    "expected_state": command.get("expected_state"),
                    "expected_available_at": command.get("expected_available_at"),
                    "requested_available_at": command.get("requested_available_at"),
                }],
            },
        )
        result = (local_result.get("results") or [{}])[0]
        status = str(result.get("status") or "REJECTED")
        _post_json(
            worker_url,
            json.dumps({
                "action": "FINISH",
                "request_id": command.get("request_id"),
                "lease_token": command.get("lease_token"),
                "status": status,
                "result": result,
            }).encode(),
            config,
        )
        processed = True
    if processed:
        # A command result and the scheduler mirror advance in the same bounded
        # sync pass; the browser need not wait for an unrelated later cycle.
        refreshed_jobs = _get_local_json(_local_retry_url(config, "/api/retry-jobs"))
        _sync_operator_retry_mirror(
            refreshed_jobs.get("items", []), worker_url, config,
        )


def _sync_assistant_chat(_local_payload: dict, _config: dict):
    """Assistant is intentionally paused until an API model is configured."""
    return {"status": "PAUSED_NO_MODEL"}


def _sync_news_questions(_local_payload: dict, _config: dict) -> None:
    # Private Assistant Q&A, titles, compaction, and memory indexing are paused
    # together. News annotation, impact, and Daily Brief use separate workers.
    return None


def _read_news_sync_state(path: Path) -> dict:
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
        return state if isinstance(state, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _write_news_sync_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)


def _learning_payload(local_payload: dict, config: dict) -> dict:
    if not local_payload and config.get("local_status_url"):
        return _read_local_resource(config, "/api/learning")
    return local_payload


def _sync_learning_history(local_payload: dict, config: dict) -> None:
    local_payload = _learning_payload(local_payload, config)
    remote_host = urllib.parse.urlsplit(config["remote_ingest_url"]).hostname or ""
    if remote_host.lower().endswith(".chatgpt.site"):
        return
    history_url = config.get("remote_learning_history_url") or (
        config["remote_ingest_url"].rsplit("/", 1)[0] + "/learning-history"
    )
    history_state_path = Path(config["learning_history_state_file"])
    history_state = _read_news_sync_state(history_state_path)
    hashes = history_state.get("hashes", {})
    if not isinstance(hashes, dict):
        hashes = {}
    now = datetime.now(UTC)
    last_full = history_state.get("last_full_sync")
    refresh_in_progress = bool(history_state.get("full_refresh_started_at"))
    try:
        full_refresh_due = (
            history_state.get("contract_version") != LEARNING_HISTORY_CONTRACT_VERSION
            or not last_full
            or (now - datetime.fromisoformat(str(last_full))).total_seconds()
            >= LEARNING_HISTORY_FULL_REFRESH_SECONDS
        )
    except (TypeError, ValueError):
        full_refresh_due = True
    if full_refresh_due and not refresh_in_progress:
        hashes = {}
        history_state["full_refresh_started_at"] = now.isoformat()

    records = learning_history_records(local_payload)
    current_keys = {_learning_record_identity(row) for row in records}
    hashes = {
        key: value for key, value in hashes.items()
        if key in current_keys
    }
    pending = [
        row for row in records
        if hashes.get(_learning_record_identity(row))
        != row["payload_hash"]
    ]
    batches = learning_history_batches(pending)
    if batches:
        batch = batches[0]
        encoded = json.dumps(
            {"records": batch}, ensure_ascii=False, allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")
        _post_json(history_url, encoded, config)
        for row in batch:
            hashes[_learning_record_identity(row)] = row["payload_hash"]
        _write_news_sync_state(history_state_path, {
            "contract_version": LEARNING_HISTORY_CONTRACT_VERSION,
            "hashes": hashes,
            "last_full_sync": last_full,
            "full_refresh_started_at": history_state.get("full_refresh_started_at"),
            "pending_record_count": len(pending) - len(batch),
            "last_progress": now.isoformat(),
        })
        return

    _write_news_sync_state(history_state_path, {
        "contract_version": LEARNING_HISTORY_CONTRACT_VERSION,
        "hashes": hashes,
        "last_full_sync": now.isoformat() if full_refresh_due else last_full,
        "last_success": now.isoformat(),
        "pending_record_count": 0,
    })


def _sync_learning_summary(local_payload: dict, config: dict) -> None:
    local_payload = _learning_payload(local_payload, config)
    learning_url = config.get("remote_learning_url") or (
        config["remote_ingest_url"].rsplit("/", 1)[0] + "/learning"
    )
    learning_state_path = Path(config["learning_state_file"])
    learning_state = _read_news_sync_state(learning_state_path)
    learning_payload = learning_snapshot(local_payload)
    learning_hash = hashlib.sha256(learning_payload).hexdigest()
    if learning_state.get("payload_hash") != learning_hash:
        _post_json(learning_url, learning_payload, config)
        _write_news_sync_state(learning_state_path, {
            "payload_hash": learning_hash,
            "last_success": datetime.now(UTC).isoformat(),
        })


def _sync_learning(local_payload: dict, config: dict) -> None:
    """Compatibility helper; the scheduler owns these as separate resources."""
    payload = _learning_payload(local_payload, config)
    _sync_learning_history(payload, config)
    _sync_learning_summary(payload, config)


def _sync_market(local_payload: dict, config: dict) -> None:
    if not local_payload and config.get("local_status_url"):
        local_payload = _read_local_resource(config, "/api/market-chart")
    market_url = config.get("remote_market_chart_url") or (
        config["remote_ingest_url"].rsplit("/", 1)[0] + "/market-chart"
    )
    snapshot = market_chart_snapshot(local_payload)
    _post_json(market_url, snapshot, config)
    if (urllib.parse.urlsplit(config["remote_ingest_url"]).hostname or "").lower().endswith(
        ".chatgpt.site"
    ):
        return
    market = json.loads(snapshot)
    overview_candles = market.get("overview_candles") or _downsample_market_overview(
        market.get("candles", []), REMOTE_MARKET_OVERVIEW_LIMITS[0],
    )
    history_url = config.get("remote_market_history_url") or (
        config["remote_ingest_url"].rsplit("/", 1)[0] + "/market-history"
    )
    overview = {
        "candles": overview_candles,
        "source_candle_count": int(market.get("source_candle_count") or len(overview_candles)),
        "history_start": market.get("history_start"),
        "history_end": market.get("history_end"),
    }
    _post_json(history_url, json.dumps(
        {"overview": overview}, ensure_ascii=False, allow_nan=False,
        separators=(",", ":"),
    ).encode("utf-8"), config)


def _market_history_payloads(candles: list[dict], decisions: list[dict]) -> list[bytes]:
    """Keep D1 ingest requests bounded while preserving every row."""
    compacted = compact_market_chart({
        "market_chart": {
            "candles": candles, "overview_candles": [], "decisions": decisions,
        },
    }, dense_limit=max(1, len(decisions)), overview_limit=1)
    candles = compacted["candles"]
    decisions = compacted["decisions"]
    payloads = []
    for key, rows in (("candles", candles), ("decisions", decisions)):
        current: list[dict] = []
        for row in rows:
            candidate = [*current, row]
            encoded = json.dumps(
                {key: candidate}, ensure_ascii=False, allow_nan=False,
                separators=(",", ":"),
            ).encode("utf-8")
            if current and (
                len(candidate) > MARKET_HISTORY_BATCH_ITEMS
                or len(encoded) > MARKET_HISTORY_BATCH_LIMIT_BYTES
            ):
                payloads.append(json.dumps(
                    {key: current}, ensure_ascii=False, allow_nan=False,
                    separators=(",", ":"),
                ).encode("utf-8"))
                current = [row]
            else:
                current = candidate
        if current:
            payloads.append(json.dumps(
                {key: current}, ensure_ascii=False, allow_nan=False,
                separators=(",", ":"),
            ).encode("utf-8"))
    return payloads


def _market_decision_overview_payload(summary: dict) -> bytes:
    """Bound a replace-in-place overview without splitting its D1 row."""
    source = summary.get("decisions", [])
    decisions = [row for row in source if isinstance(row, dict)]
    limit = min(len(decisions), MARKET_OVERVIEW_DECISIONS_PER_SERIES)
    while True:
        bounded = {
            **summary,
            "decisions": _visual_decision_overview(decisions, limit),
        }
        bounded["decision_count"] = len(bounded["decisions"])
        bounded["decision_downsampled"] = (
            int(bounded.get("source_decision_count") or 0)
            > bounded["decision_count"]
        )
        encoded = json.dumps(
            {"decision_overviews": [bounded]}, ensure_ascii=False,
            allow_nan=False, separators=(",", ":"),
        ).encode("utf-8")
        if len(encoded) <= MARKET_HISTORY_BATCH_LIMIT_BYTES:
            return encoded
        if limit <= 1:
            raise PayloadContractError(
                "market decision overview row exceeds payload limit"
            )
        limit = max(1, limit // 2)


def _local_market_history_url(config: dict, after: str | None) -> str:
    status_url = urllib.parse.urlsplit(config["local_status_url"])
    query = {"limit": "500"}
    if after:
        query["after"] = after
    return urllib.parse.urlunsplit((
        status_url.scheme, status_url.netloc, "/api/market-history",
        urllib.parse.urlencode(query), "",
    ))


def _overlap_cursor(cursor: str | None) -> str | None:
    if not cursor:
        return None
    try:
        value = datetime.fromisoformat(cursor.replace("Z", "+00:00"))
        return datetime.fromtimestamp(
            value.timestamp() - MARKET_HISTORY_OVERLAP_SECONDS, UTC,
        ).isoformat()
    except (TypeError, ValueError):
        return None


def _sync_market_history(config: dict) -> None:
    remote_host = urllib.parse.urlsplit(config["remote_ingest_url"]).hostname or ""
    if remote_host.lower().endswith(".chatgpt.site"):
        return  # Sites remains on the bounded compatibility snapshot; D1 is Cloudflare-only.
    remote_url = config.get("remote_market_history_url") or (
        config["remote_ingest_url"].rsplit("/", 1)[0] + "/market-history"
    )
    state_path = Path(config["market_history_state_file"])
    state = _read_news_sync_state(state_path)
    cursor = (
        state.get("cursor")
        if state.get("contract_version") == MARKET_HISTORY_CONTRACT_VERSION
        else None
    )
    decision_overviews = (
        state.get("decision_overviews", {})
        if state.get("contract_version") == MARKET_HISTORY_CONTRACT_VERSION
        else {}
    )
    new_after = cursor
    after = _overlap_cursor(cursor)
    pages = 0
    while pages < MARKET_HISTORY_PAGES_PER_CYCLE:
        with urllib.request.urlopen(
            _local_market_history_url(config, after),
            timeout=LOCAL_STATUS_TIMEOUT_SECONDS,
        ) as response:
            page = json.loads(response.read())
        candles = page.get("candles") if isinstance(page.get("candles"), list) else []
        decisions = page.get("decisions") if isinstance(page.get("decisions"), list) else []
        decision_overviews = _update_decision_overviews(
            decision_overviews, decisions, new_after,
        )
        for payload in _market_history_payloads(candles, decisions):
            _post_json(remote_url, payload, config)
        next_cursor = page.get("next_cursor")
        if next_cursor:
            cursor = str(next_cursor)
            _write_news_sync_state(state_path, {
                "contract_version": MARKET_HISTORY_CONTRACT_VERSION,
                "cursor": cursor,
                "decision_overviews": decision_overviews,
                "last_success": datetime.now(UTC).isoformat(),
            })
        pages += 1
        if not page.get("has_more") or not next_cursor or next_cursor == after:
            break
        after = str(next_cursor)
    summaries = sorted(decision_overviews.items())
    overview_offset = int(state.get("overview_offset") or 0)
    selected_summaries = []
    if summaries:
        for index in range(min(MARKET_OVERVIEWS_PER_CYCLE, len(summaries))):
            selected_summaries.append(
                summaries[(overview_offset + index) % len(summaries)][1]
            )
        overview_offset = (
            overview_offset + len(selected_summaries)
        ) % len(summaries)
    for summary in selected_summaries:
        _post_json(
            remote_url, _market_decision_overview_payload(summary), config,
        )
    _write_news_sync_state(state_path, {
        "contract_version": MARKET_HISTORY_CONTRACT_VERSION,
        "cursor": cursor,
        "decision_overviews": decision_overviews,
        "overview_offset": overview_offset,
        "has_more": bool(page.get("has_more")),
        "last_success": datetime.now(UTC).isoformat(),
    })


def _local_news_archive_url(
    config: dict, *, mode: str, snapshot_id: str | None = None,
    kind: str | None = None, offset: int | None = None,
    activated_snapshot_id: str | None = None,
) -> str:
    status_url = urllib.parse.urlsplit(config["local_status_url"])
    query = {"mode": mode}
    if snapshot_id:
        query["snapshot_id"] = snapshot_id
    if kind:
        query["kind"] = kind
    if offset is not None:
        query["offset"] = str(offset)
    if activated_snapshot_id:
        query["activated_snapshot_id"] = activated_snapshot_id
    return urllib.parse.urlunsplit((
        status_url.scheme, status_url.netloc, "/api/news-archive",
        urllib.parse.urlencode(query), "",
    ))


def _verify_news_projection_state(
    news_index_url: str, config: dict, manifest: dict,
) -> dict:
    payload = _get_json(news_index_url + "?health_check=1", config)
    expected = {
        "status": "OK",
        "projection_state": "CURRENT",
        "verified_complete": True,
        "active_generation_id": manifest["generation_id"],
        "snapshot_id": manifest["snapshot_id"],
        "source_digest": manifest["source_digest"],
        "receipt_digest": manifest["expected_receipt_digest"],
        "index_count": manifest["expected_index_count"],
        "detail_count": manifest["expected_detail_count"],
        "missing_detail_count": 0,
        "invariant_violation_count": 0,
    }
    contradictions = {
        key: {"expected": value, "received": payload.get(key)}
        for key, value in expected.items() if payload.get(key) != value
    }
    if contradictions:
        raise RemoteInvariantViolation({
            "status": "ERROR", "error_code": "NEWS_PROJECTION_HEALTH_MISMATCH",
            "violation_count": len(contradictions), "contradictions": contradictions,
        })
    return payload


def _frozen_news_projection_batch(
    generation: NewsProjectionGeneration, *, kind: str, offset: int,
) -> list[dict]:
    batches = (
        generation.detail_batches if kind == "detail"
        else generation.index_batches if kind == "index"
        else None
    )
    if batches is None or offset < 0:
        raise PayloadContractError("frozen news generation batch request is invalid")
    next_offset = 0
    for batch in batches:
        if next_offset == offset:
            return list(batch)
        next_offset += len(batch)
    raise PayloadContractError("frozen news generation offset is not contiguous")


def _sync_news(
    _local_payload: dict, config: dict, *,
    frozen_generation: NewsProjectionGeneration | None = None,
) -> None:
    """Advance one immutable generation without exposing partial replacement."""
    state_path = Path(config["news_state_file"])
    state = _read_news_sync_state(state_path)
    if state.get("contract_version") != NEWS_MIRROR_CONTRACT_VERSION:
        state = {"contract_version": NEWS_MIRROR_CONTRACT_VERSION}
    news_index_url = config.get("remote_news_index_url") or (
        config["remote_ingest_url"].rsplit("/", 1)[0] + "/news-index"
    )
    news_url = config.get("remote_news_ingest_url") or (
        config["remote_ingest_url"].rsplit("/", 1)[0] + "/news-content"
    )

    if frozen_generation is not None:
        manifest = frozen_generation.manifest
    elif config.get("local_status_url"):
        manifest_page = _get_local_json(_local_news_archive_url(
            config, mode="manifest",
            activated_snapshot_id=state.get("active_snapshot_id"),
        ))
        manifest = manifest_page.get("manifest")
    else:
        raise PayloadContractError(
            "news generation sync requires local_status_url for frozen batch replay"
        )
    if not isinstance(manifest, dict):
        raise PayloadContractError("local news projection manifest is missing")
    generation_id = str(manifest.get("generation_id") or "")
    previous_generation = state.get("generation_id")
    if (
        previous_generation and previous_generation != generation_id
        and state.get("projection_state") != "CURRENT"
    ):
        raise PayloadContractError(
            "pinned news generation changed before reaching CURRENT"
        )

    prepare_payload = json.dumps({
        "action": "prepare", "generation_id": generation_id,
        "manifest": manifest,
    }, ensure_ascii=False, separators=(",", ":")).encode()
    # A busy generation may belong to another exact producer (for example the
    # still-active Stable mirror while a Candidate bootstrap is replaying).
    # Preserve a foreign staging generation and let the caller retry after the
    # owning producer advances it. Abandonment requires explicit recovery.
    prepare = _post_json(news_index_url, prepare_payload, config)
    detail_offset = int(prepare.get("next_detail_offset", 0))
    index_offset = int(prepare.get("next_index_offset", 0))
    work = 0
    snapshot_id = str(manifest["snapshot_id"])
    while (
        not prepare.get("active") and work < NEWS_PROJECTION_BATCHES_PER_CYCLE
        and detail_offset < int(manifest["expected_detail_count"])
    ):
        if frozen_generation is not None:
            items = _frozen_news_projection_batch(
                frozen_generation, kind="detail", offset=detail_offset,
            )
        else:
            page = _get_local_json(_local_news_archive_url(
                config, mode="batch", snapshot_id=snapshot_id,
                kind="detail", offset=detail_offset,
            ))
            items = page.get("items")
        if not isinstance(items, list) or not items:
            raise PayloadContractError("local news detail batch did not advance")
        result = _post_json(news_url, json.dumps({
            "action": "stage_details", "generation_id": generation_id,
            "offset": detail_offset, "items": items,
        }, ensure_ascii=False, separators=(",", ":")).encode(), config)
        detail_offset += len(items)
        if int(result.get("received", -1)) != len(items):
            raise PayloadContractError("remote news detail receipt count mismatched")
        work += 1
    while (
        not prepare.get("active") and work < NEWS_PROJECTION_BATCHES_PER_CYCLE
        and detail_offset == int(manifest["expected_detail_count"])
        and index_offset < int(manifest["expected_index_count"])
    ):
        if frozen_generation is not None:
            items = _frozen_news_projection_batch(
                frozen_generation, kind="index", offset=index_offset,
            )
        else:
            page = _get_local_json(_local_news_archive_url(
                config, mode="batch", snapshot_id=snapshot_id,
                kind="index", offset=index_offset,
            ))
            items = page.get("items")
        if not isinstance(items, list) or not items:
            raise PayloadContractError("local news index batch did not advance")
        result = _post_json(news_index_url, json.dumps({
            "action": "stage_index", "generation_id": generation_id,
            "offset": index_offset, "items": items,
        }, ensure_ascii=False, separators=(",", ":")).encode(), config)
        index_offset += len(items)
        if int(result.get("received", -1)) != len(items):
            raise PayloadContractError("remote news index receipt count mismatched")
        work += 1

    complete = (
        detail_offset == int(manifest["expected_detail_count"])
        and index_offset == int(manifest["expected_index_count"])
    )
    if not prepare.get("active") and complete:
        _post_json(news_index_url, json.dumps({
            "action": "activate", "generation_id": generation_id,
        }, separators=(",", ":")).encode(), config)
        _post_json(news_index_url, json.dumps({
            "action": "verify", "generation_id": generation_id,
        }, separators=(",", ":")).encode(), config)
    if prepare.get("active") or complete:
        _verify_news_projection_state(news_index_url, config, manifest)
        state["active_snapshot_id"] = snapshot_id
        state["projection_state"] = "CURRENT"
        state["last_success"] = datetime.now(UTC).isoformat()
    else:
        state["projection_state"] = "REPLAYING"
    state.update({
        "generation_id": generation_id, "snapshot_id": snapshot_id,
        "source_digest": manifest["source_digest"],
        "expected_receipt_digest": manifest["expected_receipt_digest"],
        "next_detail_offset": detail_offset, "next_index_offset": index_offset,
        "expected_detail_count": manifest["expected_detail_count"],
        "expected_index_count": manifest["expected_index_count"],
        "updated_at": datetime.now(UTC).isoformat(),
    })
    _write_news_sync_state(state_path, state)


def _sync_audit(local_payload: dict, config: dict) -> None:
    if not local_payload and config.get("local_status_url"):
        local_payload = _read_local_resource(config, "/api/audit")
    audit_url = config.get("remote_audit_url") or (
        config["remote_ingest_url"].rsplit("/", 1)[0] + "/audit"
    )
    _post_json(audit_url, audit_snapshot(local_payload), config)
    root = audit_url.rsplit("/", 1)[0]
    producer_revision = _projection_producer_revision()
    if not producer_revision:
        raise PayloadContractError("projection producer revision is unavailable")
    for resource, snapshot in (
        ("audit-briefs", audit_briefs_snapshot(local_payload, producer_revision)),
        ("audit-stories", audit_stories_snapshot(local_payload, producer_revision)),
        ("audit-decisions", audit_decisions_snapshot(local_payload, producer_revision)),
    ):
        _post_json(f"{root}/{resource}", snapshot, config)


def _deferred_projection_request_digest(request: dict) -> str:
    encoded = json.dumps(
        request, ensure_ascii=False, allow_nan=False, sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _deferred_projection_paths(config: dict) -> tuple[Path, Path]:
    return (
        Path(config["deferred_projection_request_file"]),
        Path(config["deferred_projection_receipt_file"]),
    )


def _read_deferred_projection_request(config: dict) -> dict | None:
    request_path, _receipt_path = _deferred_projection_paths(config)
    if not request_path.exists():
        return None
    try:
        request = json.loads(request_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise PayloadContractError(
            "deferred projection request is unreadable"
        ) from error
    if not isinstance(request, dict):
        raise PayloadContractError("deferred projection request must be an object")
    routes = request.get("routes")
    if (
        request.get("schema_version") != DEFERRED_PROJECTION_CONTRACT
        or not re.fullmatch(
            r"[0-9a-f]{40}", str(request.get("producer_revision") or "")
        )
        or not UUID_PATTERN.fullmatch(str(request.get("worker_version_id") or ""))
        or not UUID_PATTERN.fullmatch(str(request.get("request_id") or ""))
        or not UUID_PATTERN.fullmatch(str(request.get("transaction_id") or ""))
        or not str(request.get("validation_key") or "")
        or request.get("target") != "cloudflare"
        or not isinstance(routes, list)
        or not routes
        or len(routes) != len(set(routes))
        or any(route not in DEFERRED_PROJECTION_ROUTES and route != "/api/news-evidence" for route in routes)
    ):
        raise PayloadContractError("deferred projection request contract mismatch")
    if "/api/news-evidence" in routes:
        incident = request.get("collector_recovery")
        if not isinstance(incident, dict) or (
            incident.get("incident") != "COLLECTOR_CLOCK_EVENT_ATOMICITY"
            or incident.get("broken_revision") != "ffe1de29c0891cc3a3cf3d602f3d3ee657faa9b8"
            or incident.get("target_revision") != request["producer_revision"]
            or incident.get("target_revision") == incident.get("broken_revision")
        ):
            raise PayloadContractError("deferred News recovery incident mismatch")
    try:
        timestamps = [
            datetime.fromisoformat(str(request[field]).replace("Z", "+00:00"))
            for field in ("required_after", "created_at")
        ]
    except (KeyError, ValueError) as error:
        raise PayloadContractError(
            "deferred projection request freshness boundary is invalid"
        ) from error
    if any(value.tzinfo is None for value in timestamps):
        raise PayloadContractError(
            "deferred projection request freshness boundary must be timezone-aware"
        )
    return request


def _deferred_projection_pending(config: dict) -> bool:
    if (
        "deferred_projection_request_file" not in config
        or "deferred_projection_receipt_file" not in config
    ):
        return False
    request = _read_deferred_projection_request(config)
    if request is None:
        return False
    _request_path, receipt_path = _deferred_projection_paths(config)
    receipt = _read_news_sync_state(receipt_path)
    return not (
        receipt.get("schema_version") == DEFERRED_PROJECTION_CONTRACT
        and receipt.get("request_id") == request["request_id"]
        and receipt.get("request_digest")
        == _deferred_projection_request_digest(request)
        and receipt.get("state") == "COMPLETED"
    )


def sync_deferred_projection_once(
    targets: list[dict], config: dict,
) -> SyncResourceResults:
    """Advance one exact post-cutover projection through the existing owner."""
    started = time.perf_counter()
    try:
        request = _read_deferred_projection_request(config)
        if request is None or not _deferred_projection_pending(config):
            return SyncResourceResults([], [])
        producer_revision = _projection_producer_revision()
        if producer_revision != request["producer_revision"]:
            raise PayloadContractError(
                "deferred projection producer revision mismatch"
            )
        matching_targets = [
            target for target in targets if target.get("name") == request["target"]
        ]
        if len(matching_targets) != 1:
            raise RuntimeError("deferred projection target is not healthy and unique")
        target = matching_targets[0]
        required_after = datetime.fromisoformat(
            str(request["required_after"]).replace("Z", "+00:00")
        )
        _request_path, receipt_path = _deferred_projection_paths(config)
        prior = _read_news_sync_state(receipt_path)
        request_digest = _deferred_projection_request_digest(request)
        reusable = (
            prior.get("schema_version") == DEFERRED_PROJECTION_CONTRACT
            and prior.get("request_id") == request["request_id"]
            and prior.get("request_digest") == request_digest
            and prior.get("producer_revision") == producer_revision
            and prior.get("state") == "PARTIAL"
        )
        hashes = dict(prior.get("projection_hashes", {})) if reusable else {}
        audit_routes = [route for route in request["routes"] if route != "/api/news-evidence"]
        generated_at = datetime.fromisoformat(prior["generated_at"]) if reusable else datetime.now(UTC)
        if any(route not in hashes for route in audit_routes):
            local_payload = _read_local_resource(target, "/api/audit")
            generated_at = datetime.fromisoformat(
                str(local_payload.get("generated_at") or "").replace("Z", "+00:00")
            )
            if generated_at.tzinfo is None:
                raise PayloadContractError(
                    "deferred projection source timestamp must be timezone-aware"
                )
            if generated_at.astimezone(UTC) < required_after.astimezone(UTC):
                return SyncResourceResults([], [])
            builders = {
                "/api/audit-briefs": audit_briefs_snapshot,
                "/api/audit-stories": audit_stories_snapshot,
                "/api/audit-decisions": audit_decisions_snapshot,
            }
            hashes.update({route: hashlib.sha256(
                builders[route](local_payload, producer_revision),
            ).hexdigest() for route in audit_routes})
            _sync_audit(local_payload, target)
            _persist_resource_schedule_result(
                _resource_schedule_path(target), target, "audit", 300,
                now=datetime.now(UTC), success=True,
            )
        receipt = {
            "schema_version": DEFERRED_PROJECTION_CONTRACT,
            "state": "PARTIAL",
            "request_id": request["request_id"],
            "transaction_id": request["transaction_id"],
            "request_digest": request_digest,
            "validation_key": request["validation_key"],
            "worker_version_id": request["worker_version_id"],
            "producer_revision": producer_revision,
            "required_after": required_after.astimezone(UTC).isoformat(),
            "generated_at": generated_at.astimezone(UTC).isoformat(),
            "routes": list(request["routes"]),
            "projection_hashes": hashes,
        }
        observations = []
        if "/api/news-evidence" in request["routes"]:
            # Preserve accepted Audit work while the existing News cursor moves
            # by its normal one-page budget. There is still one serial Sync owner.
            _write_news_sync_state(receipt_path, receipt)
            prior_news = _read_news_sync_state(Path(target["news_evidence_state_file"]))
            snapshot = _sync_news_evidence({}, target)
            ack = _read_news_sync_state(Path(target["news_evidence_state_file"]))
            if not isinstance(snapshot, str) or not re.fullmatch(r"[a-f0-9]{64}", snapshot):
                raise PayloadContractError("deferred News snapshot identity invalid")
            if (
                ack.get("contract_version") != NEWS_EVIDENCE_CONTRACT_VERSION
                or ack.get("active_snapshot_id") != snapshot
            ):
                # Only real accepted page progress drains immediately. Cleanup
                # debt or an unchanged cursor must not create a busy retry loop.
                if (
                    ack.get("contract_version") == NEWS_EVIDENCE_CONTRACT_VERSION
                    and ack.get("staging_snapshot_id") == snapshot
                    and int(ack.get("staged_count") or 0) > (
                        int(prior_news.get("staged_count") or 0)
                        if prior_news.get("staging_snapshot_id") == snapshot else 0
                    )
                ):
                    return SyncResourceResults([], [{
                        "target": request["target"], "resource": "deferred_projection",
                        "status": "PROGRESS",
                        "duration_ms": round((time.perf_counter()-started)*1000, 1),
                        "completed_at": datetime.now(UTC).isoformat(),
                    }])
                return SyncResourceResults([], [])
            hashes["/api/news-evidence"] = snapshot
            receipt["news_recovery"] = {
                "snapshot_id": snapshot, "record_count": ack.get("record_count"),
                "local_read_completed_at": datetime.now(UTC).isoformat(),
                "contract_version": NEWS_EVIDENCE_CONTRACT_VERSION,
            }
            _persist_resource_schedule_result(
                _resource_schedule_path(target), target, "news_evidence", 300,
                now=datetime.now(UTC), success=True,
            )
            observations.append({
                "target": request["target"], "resource": "news_evidence", "status": "OK",
                "duration_ms": round((time.perf_counter()-started)*1000, 1),
                "completed_at": datetime.now(UTC).isoformat(),
            })
        completed_at = datetime.now(UTC)
        receipt.update(state="COMPLETED", completed_at=completed_at.isoformat())
        _write_news_sync_state(receipt_path, receipt)
        return SyncResourceResults([], [*observations, {
            "target": request["target"],
            "resource": "deferred_projection",
            "status": "OK",
            "duration_ms": round((time.perf_counter() - started) * 1000, 1),
            "completed_at": completed_at.isoformat(),
        }])
    except Exception as error:
        completed_at = datetime.now(UTC).isoformat()
        failure = {
            "target": "cloudflare",
            "resource": "deferred_projection",
            "error_type": type(error).__name__,
            "error_code": sync_error_code(error),
            "error": str(error)[:500],
            "duration_ms": round((time.perf_counter() - started) * 1000, 1),
        }
        return SyncResourceResults([failure], [{
            "target": "cloudflare", "resource": "deferred_projection",
            "status": "ERROR", "duration_ms": failure["duration_ms"],
            "completed_at": completed_at,
        }])


def _local_news_evidence_url(
    config: dict, cursor: str | None, *, activated_snapshot_id: str | None = None,
) -> str:
    status_url = urllib.parse.urlsplit(config["local_status_url"])
    query = {"limit": str(NEWS_EVIDENCE_WRITE_BATCH_ITEMS)}
    if cursor:
        query["cursor"] = cursor
    if activated_snapshot_id:
        query["activated_snapshot_id"] = activated_snapshot_id
    return urllib.parse.urlunsplit((
        status_url.scheme, status_url.netloc, "/api/news-evidence",
        urllib.parse.urlencode(query), "",
    ))


def _local_resource_url(config: dict, path: str) -> str:
    status_url = urllib.parse.urlsplit(config["local_status_url"])
    return urllib.parse.urlunsplit((
        status_url.scheme, status_url.netloc, path, "", "",
    ))


def _read_local_resource(config: dict, path: str) -> dict:
    with urllib.request.urlopen(
        _local_resource_url(config, path),
        timeout=LOCAL_STATUS_TIMEOUT_SECONDS,
    ) as response:
        payload = json.loads(response.read())
    if not isinstance(payload, dict):
        raise PayloadContractError(f"local resource {path} is not an object")
    return payload


def _local_critical_status_url(config: dict) -> str:
    return _local_resource_url(config, "/api/critical-status")


def _post_news_evidence(remote_url: str, payload: bytes, config: dict) -> dict:
    """Advance only on an exact, operation-complete remote acknowledgement."""
    request = json.loads(payload)
    result = _post_json(remote_url, payload, config)
    snapshot_id = next((request[key] for key in (
        "prepare_snapshot", "snapshot_id", "activate_snapshot", "cleanup_active_snapshot",
    ) if key in request), None)

    def require(condition: bool) -> None:
        if not condition:
            raise PayloadContractError("NEWS_EVIDENCE_ACK_INVALID")

    def integer(name: str, maximum: int) -> int:
        value = result.get(name)
        require(type(value) is int and 0 <= value <= maximum)
        return value

    require(isinstance(result, dict))
    require(result.get("status") == "OK")
    require(result.get("contract_version") == NEWS_EVIDENCE_CONTRACT_VERSION)
    require(result.get("snapshot_id") == snapshot_id)
    require(result.get("request_sha256") == hashlib.sha256(payload).hexdigest())
    if "prepare_snapshot" in request:
        total = request["expected_count"]
        offset = integer("next_offset", total)
        require(type(result.get("active")) is bool)
        require(not result["active"] or offset == total)
        if "repaired_from" in result:
            require(not result["active"] and integer("repaired_from", total) > offset)
    elif "items" in request:
        require(integer("received", len(request["items"])) == len(request["items"]))
        if "duplicate" in result:
            require(type(result["duplicate"]) is bool)
    elif "activate_snapshot" in request:
        require(result.get("activated") == snapshot_id)
        require(integer("count", request["expected_count"]) == request["expected_count"])
    else:
        require(result.get("cleanup") in {"advanced", "budget_exhausted"})
        require(type(result.get("cleanup_pending")) is bool)
        for field, limit in (("deleted_records", 200), ("deleted_batches", 20),
                             ("deleted_staging", 20)):
            integer(field, limit)
        if "cleanup_budget_exhausted" in result:
            require(type(result["cleanup_budget_exhausted"]) is bool)
        exhausted = result.get("cleanup_budget_exhausted") is True
        require(exhausted == (result["cleanup"] == "budget_exhausted"))
        require(not exhausted or (result["cleanup_pending"] and all(
            result[field] == 0 for field in
            ("deleted_records", "deleted_batches", "deleted_staging")
        )))
    return result


def _cleanup_news_evidence_snapshots(
    remote_url: str, snapshot_id: str, config: dict,
) -> bool:
    """Advance bounded cleanup debt until the D1-owned daily budget stops it."""
    payload = json.dumps({
        "contract_version": NEWS_EVIDENCE_CONTRACT_VERSION,
        "cleanup_active_snapshot": snapshot_id,
    }, separators=(",", ":")).encode("utf-8")
    cleanup_pending = False
    for _ in range(NEWS_EVIDENCE_CLEANUP_STEPS_PER_CYCLE):
        result = _post_news_evidence(remote_url, payload, config)
        cleanup_pending = result.get("cleanup_pending") is True
        if result.get("cleanup_budget_exhausted") is True:
            return True
        if not cleanup_pending:
            return False
    return cleanup_pending


def _sync_news_evidence(_local_payload: dict, config: dict) -> str | None:
    """Advance a bounded staging window and activate only a complete snapshot."""
    if not config.get("local_status_url"):
        return
    remote_url = config.get("remote_news_evidence_url") or (
        config["remote_ingest_url"].rsplit("/", 1)[0] + "/news-evidence"
    )
    state_path = Path(config["news_evidence_state_file"])
    state = _read_news_sync_state(state_path)
    # Older target-owned state still owns cleanup debt, but cannot use the
    # no-change fast path until a complete new acknowledgement is obtained.
    ack_target_matches = state.get("ack_remote_url", remote_url) == remote_url
    cursor = None
    snapshot_id = None
    total = None
    received = 0
    first_page = None
    with urllib.request.urlopen(
        _local_news_evidence_url(
            config,
            None,
            activated_snapshot_id=(
                str(state.get("active_snapshot_id"))
                if state.get("contract_version") == NEWS_EVIDENCE_CONTRACT_VERSION
                and ack_target_matches
                and state.get("active_snapshot_id") else None
            ),
        ),
        timeout=LOCAL_STATUS_TIMEOUT_SECONDS,
    ) as response:
        first_page = json.loads(response.read())
    first_snapshot = str(first_page.get("snapshot_id") or "")
    if not re.fullmatch(r"[a-f0-9]{64}", first_snapshot):
        raise PayloadContractError("local news evidence snapshot id is invalid")
    active_snapshot = (
        str(state.get("active_snapshot_id"))
        if state.get("contract_version") == NEWS_EVIDENCE_CONTRACT_VERSION
        and ack_target_matches
        and state.get("active_snapshot_id") else ""
    )
    if active_snapshot and _cleanup_news_evidence_snapshots(
        remote_url, active_snapshot, config,
    ):
        return first_snapshot
    if (
        state.get("contract_version") == NEWS_EVIDENCE_CONTRACT_VERSION
        and state.get("active_snapshot_id") == first_snapshot
        and state.get("ack_remote_url") == remote_url
        and isinstance(state.get("ack_request_sha256"), str)
        and re.fullmatch(r"[a-f0-9]{64}", state["ack_request_sha256"])
    ):
        return first_snapshot
    snapshot_id = first_snapshot
    total = first_page.get("total")
    if type(total) is not int or total < 0:
        raise PayloadContractError("local news evidence count is invalid")
    prepared = _post_news_evidence(remote_url, json.dumps({
        "contract_version": NEWS_EVIDENCE_CONTRACT_VERSION,
        "prepare_snapshot": snapshot_id,
        "expected_count": total,
    }, separators=(",", ":")).encode("utf-8"), config)
    if prepared.get("active") is True:
        _write_news_sync_state(state_path, {
            "contract_version": NEWS_EVIDENCE_CONTRACT_VERSION,
            "active_snapshot_id": snapshot_id,
            "record_count": total,
            "ack_remote_url": remote_url,
            "ack_request_sha256": prepared["request_sha256"],
            "last_success": datetime.now(UTC).isoformat(),
        })
        return snapshot_id
    received = prepared["next_offset"]
    if received < 0 or received > total:
        raise PayloadContractError("remote news evidence staging offset is invalid")
    cursor = f"{snapshot_id}:{received}" if received else None

    for page_number in range(NEWS_EVIDENCE_PAGES_PER_CYCLE):
        if page_number == 0 and cursor is None:
            page = first_page
        else:
            with urllib.request.urlopen(
                _local_news_evidence_url(config, cursor),
                timeout=LOCAL_STATUS_TIMEOUT_SECONDS,
            ) as response:
                page = json.loads(response.read())
        page_snapshot = str(page.get("snapshot_id") or "")
        if not re.fullmatch(r"[a-f0-9]{64}", page_snapshot):
            raise PayloadContractError("local news evidence snapshot id is invalid")
        if page_snapshot != snapshot_id:
            raise PayloadContractError("local news evidence snapshot changed during paging")
        items = page.get("items")
        if not isinstance(items, list):
            raise PayloadContractError("local news evidence page has invalid items")
        if items:
            encoded = json.dumps({
                "contract_version": NEWS_EVIDENCE_CONTRACT_VERSION,
                "snapshot_id": snapshot_id,
                "offset": received,
                "items": items,
            }, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
            if len(encoded) > NEWS_EVIDENCE_BATCH_LIMIT_BYTES:
                raise PayloadContractError(
                    f"news evidence batch is {len(encoded)} bytes "
                    f"(limit {NEWS_EVIDENCE_BATCH_LIMIT_BYTES})"
                )
            _post_news_evidence(remote_url, encoded, config)
            received += len(items)
        next_cursor = page.get("next_cursor")
        if not page.get("has_more"):
            if total is None or received != total:
                raise PayloadContractError(
                    f"news evidence snapshot expected {total} rows but staged {received}"
                )
            activated = _post_news_evidence(remote_url, json.dumps({
                "contract_version": NEWS_EVIDENCE_CONTRACT_VERSION,
                "activate_snapshot": snapshot_id,
                "expected_count": total,
            }, separators=(",", ":")).encode("utf-8"), config)
            _write_news_sync_state(state_path, {
                "contract_version": NEWS_EVIDENCE_CONTRACT_VERSION,
                "active_snapshot_id": snapshot_id,
                "record_count": total,
                "ack_remote_url": remote_url,
                "ack_request_sha256": activated["request_sha256"],
                "last_success": datetime.now(UTC).isoformat(),
            })
            _cleanup_news_evidence_snapshots(remote_url, snapshot_id, config)
            return snapshot_id
        if not isinstance(next_cursor, str) or not next_cursor or next_cursor == cursor:
            raise PayloadContractError("local news evidence cursor did not advance")
        cursor = next_cursor
    _write_news_sync_state(state_path, {
        "contract_version": NEWS_EVIDENCE_CONTRACT_VERSION,
        "staging_snapshot_id": snapshot_id,
        "record_count": total,
        "staged_count": received,
        "next_cursor": cursor,
        "last_progress": datetime.now(UTC).isoformat(),
    })
    return snapshot_id


RESOURCE_POLICIES = (
    # Control-plane commands are bounded independently from historical mirrors.
    ("operator_retries", "_sync_operator_retries", 30, False),
    ("news_questions", "_sync_news_questions", 300, False),
    # At most one of these accumulated resources runs in a sync cycle.
    ("audit", "_sync_audit", 300, True),
    ("learning", "_sync_learning_summary", 300, True),
    ("learning_history", "_sync_learning_history", 300, True),
    ("market_chart", "_sync_market", 60, True),
    ("market_history", "_sync_market_history", 120, True),
    ("news", "_sync_news", 60, True),
    ("news_evidence", "_sync_news_evidence", 300, True),
)


def _schedule_epoch(value: object) -> float:
    try:
        return datetime.fromisoformat(str(value)).timestamp()
    except (TypeError, ValueError):
        return 0.0


def _due_resource_policies(
    state: dict, now: datetime, *, lane: str | None = None,
) -> list[tuple]:
    resources = state.get("resources")
    if not isinstance(resources, dict):
        resources = {}
    due = []
    for policy_index, policy in enumerate(RESOURCE_POLICIES):
        resource = policy[0]
        resource_state = resources.get(resource)
        if not isinstance(resource_state, dict):
            resource_state = {}
        due_at = _schedule_epoch(resource_state.get("next_run_at"))
        if due_at <= now.timestamp():
            due.append((due_at, policy_index, policy))
    controls = [entry[2] for entry in due if not entry[2][3]]
    heavy = [
        entry[2] for entry in sorted(
            (entry for entry in due if entry[2][3]),
            key=lambda entry: (entry[0], entry[1]),
        )[:HEAVY_RESOURCES_PER_CYCLE]
    ]
    if lane == "control":
        return controls
    if lane == "heavy":
        return heavy
    return [*controls, *heavy]


def _record_resource_schedule(
    state: dict,
    resource: str,
    cadence_seconds: int,
    *,
    now: datetime,
    success: bool,
) -> None:
    resources = state.setdefault("resources", {})
    current = resources.get(resource)
    if not isinstance(current, dict):
        current = {}
    failures = 0 if success else int(current.get("consecutive_failures") or 0) + 1
    delay = cadence_seconds if success else min(
        RESOURCE_BACKOFF_MAX_SECONDS,
        max(cadence_seconds, 30 * (2 ** min(failures - 1, 7))),
    )
    if success:
        previous_due = _schedule_epoch(current.get("next_run_at"))
        next_run_epoch = (
            previous_due + cadence_seconds
            if previous_due > 0 else now.timestamp() + cadence_seconds
        )
        while next_run_epoch <= now.timestamp():
            next_run_epoch += cadence_seconds
        next_run_at = datetime.fromtimestamp(next_run_epoch, tz=UTC)
    else:
        next_run_at = now + timedelta(seconds=delay)
    resources[resource] = {
        **current,
        "last_attempt_at": now.isoformat(),
        "last_success_at": now.isoformat() if success else current.get("last_success_at"),
        "consecutive_failures": failures,
        "next_run_at": next_run_at.isoformat(),
    }
    state["schema_version"] = 1
    state["updated_at"] = now.isoformat()


def _resource_schedule_path(config: dict) -> Path:
    return Path(config["resource_schedule_state_file"])


def _persist_resource_schedule_result(
    path: Path,
    config: dict,
    resource: str,
    cadence_seconds: int,
    *,
    now: datetime,
    success: bool,
) -> None:
    """Merge one lane's result without overwriting another lane's progress."""
    with _RESOURCE_SCHEDULE_LOCK:
        state = _read_news_sync_state(path)
        _record_resource_schedule(
            state, resource, cadence_seconds, now=now, success=success,
        )
        _write_news_sync_state(path, state)


def sync_heartbeat_once(config: dict) -> tuple[list[dict], SyncResourceResults]:
    """Publish only the critical heartbeat and return currently healthy targets."""
    with urllib.request.urlopen(
        _local_critical_status_url(config), timeout=LOCAL_STATUS_TIMEOUT_SECONDS
    ) as response:
        critical_payload = json.loads(response.read())

    degraded = []
    observations = []
    healthy = []
    live_payload = remote_snapshot(critical_payload)
    for target in configured_targets(config):
        target_name = target["name"]
        started = time.perf_counter()
        try:
            _post_json(target["remote_ingest_url"], live_payload, target)
            healthy.append(target)
            observations.append({
                "target": target_name,
                "resource": "heartbeat",
                "status": "OK",
                "duration_ms": round((time.perf_counter() - started) * 1000, 1),
                "completed_at": datetime.now(UTC).isoformat(),
            })
        except Exception as error:
            duration_ms = round((time.perf_counter() - started) * 1000, 1)
            degraded.append({
                "target": target_name,
                "resource": "heartbeat",
                "error_type": type(error).__name__,
                "error_code": sync_error_code(error),
                "error": str(error)[:500],
                "duration_ms": duration_ms,
            })
            observations.append({
                "target": target_name,
                "resource": "heartbeat",
                "status": "ERROR",
                "duration_ms": duration_ms,
                "completed_at": datetime.now(UTC).isoformat(),
            })
    if not healthy:
        error = AllTargetsRejected(degraded)
        error.resource_observations = observations
        raise error
    return healthy, SyncResourceResults(degraded, observations)


def sync_resource_lane(
    targets: list[dict], *, lane: str | None = None,
) -> SyncResourceResults:
    """Advance control or accumulated resources independently of heartbeat."""
    degraded = []
    observations = []
    for target in targets:
        target_name = target["name"]
        schedule_path = _resource_schedule_path(target)
        with _RESOURCE_SCHEDULE_LOCK:
            schedule_state = _read_news_sync_state(schedule_path)
        now = datetime.now(UTC)
        for resource, operation_name, cadence_seconds, _heavy in (
            _due_resource_policies(schedule_state, now, lane=lane)
        ):
            started = time.perf_counter()
            try:
                operation = globals()[operation_name]
                if operation_name == "_sync_market_history":
                    operation(target)
                else:
                    operation({}, target)
                completed_at = datetime.now(UTC)
                _persist_resource_schedule_result(
                    schedule_path, target, resource, cadence_seconds,
                    now=completed_at, success=True,
                )
                observations.append({
                    "target": target_name,
                    "resource": resource,
                    "status": "OK",
                    "duration_ms": round(
                        (time.perf_counter() - started) * 1000, 1,
                    ),
                    "completed_at": datetime.now(UTC).isoformat(),
                })
            except Exception as error:
                duration_ms = round((time.perf_counter() - started) * 1000, 1)
                completed_at = datetime.now(UTC)
                _persist_resource_schedule_result(
                    schedule_path, target, resource, cadence_seconds,
                    now=completed_at, success=False,
                )
                failure = {
                    "target": target_name,
                    "resource": resource,
                    "error_type": type(error).__name__,
                    "error_code": sync_error_code(error),
                    "error": str(error)[:500],
                    "duration_ms": duration_ms,
                }
                evidence = getattr(error, "evidence", None)
                if isinstance(evidence, dict):
                    failure["evidence"] = evidence
                degraded.append(failure)
                observations.append({
                    "target": target_name,
                    "resource": resource,
                    "status": "ERROR",
                    "duration_ms": duration_ms,
                    "completed_at": datetime.now(UTC).isoformat(),
                })
    return SyncResourceResults(degraded, observations)


def sync_once(config: dict) -> SyncResourceResults:
    healthy, heartbeat = sync_heartbeat_once(config)
    optional = sync_resource_lane(healthy)
    return SyncResourceResults(
        [*heartbeat, *optional],
        [*heartbeat.resource_observations, *optional.resource_observations],
    )


def sync_with_retry(config: dict, *, attempts: int = 3) -> tuple[int, list[dict]]:
    """Retry transient transport failures without waiting for the next sync cycle."""
    for attempt in range(1, attempts + 1):
        try:
            degraded = sync_once(config)
            if degraded is None:
                degraded = []
            return attempt, degraded
        except Exception as error:
            transient = isinstance(
                error,
                (ConnectionError, TimeoutError, http.client.RemoteDisconnected),
            ) or (
                isinstance(error, urllib.error.HTTPError)
                and (error.code == 429 or error.code >= 500)
            )
            if not transient or attempt >= attempts:
                raise
            print(
                json.dumps(
                    {
                        "event": "DASHBOARD_SYNC_RETRY",
                        "attempt": attempt,
                        "error_type": type(error).__name__,
                        "error": str(error)[:500],
                    }
                ),
                flush=True,
            )
            time.sleep(float(attempt * 2))
    raise RuntimeError("dashboard sync retry loop exhausted")


def _lane_failure(lane: str, error: Exception) -> SyncResourceResults:
    failure = {
        "target": "scheduler",
        "resource": f"{lane}_lane",
        "error_type": type(error).__name__,
        "error_code": sync_error_code(error),
        "error": str(error)[:500],
    }
    observation = {
        "target": "scheduler",
        "resource": f"{lane}_lane",
        "status": "ERROR",
        "duration_ms": None,
        "completed_at": datetime.now(UTC).isoformat(),
    }
    return SyncResourceResults([failure], [observation])


def _consume_lane_future(
    lane: str, future: Future | None,
) -> tuple[Future | None, SyncResourceResults | None]:
    if future is None or not future.done():
        return future, None
    try:
        return None, future.result()
    except Exception as error:
        return None, _lane_failure(lane, error)


def _merge_lane_results(
    previous: SyncResourceResults, current: SyncResourceResults,
) -> SyncResourceResults:
    """Retain one bounded latest status per target/resource across a drain."""
    observations = {
        (str(row.get("target")), str(row.get("resource"))): row
        for row in previous.resource_observations
    }
    failures = {
        (str(row.get("target")), str(row.get("resource"))): row
        for row in previous
    }
    for row in current.resource_observations:
        key = (str(row.get("target")), str(row.get("resource")))
        observations[key] = row
        if row.get("status") == "OK":
            failures.pop(key, None)
    for row in current:
        failures[(str(row.get("target")), str(row.get("resource")))] = row
    return SyncResourceResults(list(failures.values()), list(observations.values()))


def _submit_resource_lane(
    executor: ThreadPoolExecutor,
    lane: str,
    healthy: list[dict],
    config: dict,
) -> Future:
    if lane == "heavy":
        try:
            deferred_pending = _deferred_projection_pending(config)
        except Exception:
            deferred_pending = True
        if deferred_pending:
            return executor.submit(sync_deferred_projection_once, healthy, config)
    return executor.submit(sync_resource_lane, healthy, lane=lane)


def run_continuous_sync(
    config: dict,
    *,
    status_file: Path,
    interval_seconds: float = 30.0,
    stop_event: threading.Event | None = None,
    max_heartbeats: int | None = None,
) -> int:
    """Keep heartbeat, control work, and accumulated work on separate owners.

    The serial heavy owner drains already-overdue work immediately after each
    completion. Heartbeats remain the discovery/wakeup boundary when no heavy
    work is due, but they are not an artificial one-operation admission limit.
    """
    stop = stop_event or threading.Event()
    interval = max(5.0, interval_seconds)
    latest_lane_results: dict[str, SyncResourceResults] = {
        "control": SyncResourceResults([], []),
        "heavy": SyncResourceResults([], []),
    }
    futures: dict[str, Future | None] = {"control": None, "heavy": None}
    executors = {
        lane: ThreadPoolExecutor(max_workers=1, thread_name_prefix=f"dashboard-{lane}")
        for lane in futures
    }
    heartbeat_count = 0
    try:
        while not stop.is_set():
            cycle_started = time.monotonic()
            for lane in futures:
                futures[lane], completed = _consume_lane_future(
                    lane, futures[lane],
                )
                if completed is not None:
                    latest_lane_results[lane] = _merge_lane_results(
                        latest_lane_results[lane], completed,
                    )
            try:
                healthy, heartbeat = sync_heartbeat_once(config)
                degraded = [
                    *heartbeat,
                    *latest_lane_results["control"],
                    *latest_lane_results["heavy"],
                ]
                observations = [
                    *heartbeat.resource_observations,
                    *latest_lane_results["control"].resource_observations,
                    *latest_lane_results["heavy"].resource_observations,
                ]
                write_sync_status(
                    status_file,
                    success=True,
                    attempts_used=1,
                    degraded_resources=degraded,
                    resource_observations=observations,
                )
                for lane in futures:
                    if futures[lane] is None:
                        futures[lane] = _submit_resource_lane(
                            executors[lane], lane, healthy, config,
                        )
                print(json.dumps({
                    "event": "DASHBOARD_HEARTBEAT_OK",
                    "heartbeat_sequence": heartbeat_count + 1,
                    "degraded_resources": degraded,
                }), flush=True)
            except Exception as error:
                write_sync_status(status_file, success=False, error=error)
                print(json.dumps({
                    "event": "DASHBOARD_HEARTBEAT_ERROR",
                    "heartbeat_sequence": heartbeat_count + 1,
                    "error_type": type(error).__name__,
                    "error": str(error)[:500],
                }), flush=True)
            heartbeat_count += 1
            if max_heartbeats is not None and heartbeat_count >= max_heartbeats:
                break
            deadline = cycle_started + interval
            while not stop.is_set():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                stop.wait(min(0.25, remaining))
                for lane in futures:
                    futures[lane], completed = _consume_lane_future(
                        lane, futures[lane],
                    )
                    if completed is None:
                        continue
                    latest_lane_results[lane] = _merge_lane_results(
                        latest_lane_results[lane], completed,
                    )
                    if (
                        lane == "heavy"
                        and completed.resource_observations
                        and not completed
                        and not stop.is_set()
                        and time.monotonic() < deadline
                    ):
                        futures[lane] = _submit_resource_lane(
                            executors[lane], lane, healthy, config,
                        )
    finally:
        for executor in executors.values():
            executor.shutdown(wait=True, cancel_futures=False)
    return heartbeat_count


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-root", type=Path, required=True)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--status-file", type=Path)
    parser.add_argument("--interval-seconds", type=float, default=30.0)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    state_root = authoritative_runtime_root(args.state_root)
    config_path = runtime_child_path(
        state_root, args.config, name="dashboard-sync.json",
    )
    status_file = _validated_sync_state_path(
        args.status_file or Path("dashboard-sync-status.json"), state_root,
    )
    config = configure_runtime_state(
        json.loads(config_path.read_text(encoding="utf-8")), state_root,
    )
    if not args.once:
        return run_continuous_sync(
            config,
            status_file=status_file,
            interval_seconds=args.interval_seconds,
        )
    while True:
        try:
            attempts_used, degraded_resources = sync_with_retry(config)
            write_sync_status(
                status_file,
                success=True,
                attempts_used=attempts_used,
                degraded_resources=degraded_resources,
                resource_observations=getattr(
                    degraded_resources, "resource_observations", [],
                ),
            )
            print(
                json.dumps(
                    {
                        "event": (
                            "DASHBOARD_SYNC_DEGRADED"
                            if degraded_resources else "DASHBOARD_SYNC_OK"
                        ),
                        "attempts_used": attempts_used,
                        "degraded_resources": degraded_resources,
                    }
                ),
                flush=True,
            )
        except Exception as error:
            write_sync_status(status_file, success=False, error=error)
            print(
                json.dumps(
                    {
                        "event": "DASHBOARD_SYNC_ERROR",
                        "error_type": type(error).__name__,
                        "error": str(error)[:500],
                    }
                ),
                flush=True,
            )
        break
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
