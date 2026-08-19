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
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path
MODULE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MODULE_ROOT))
DEFAULT_CONFIG = MODULE_ROOT / ".local" / "forward" / "dashboard-sync.json"
DEFAULT_STATUS = MODULE_ROOT / ".local" / "forward" / "dashboard-sync-status.json"
DEFAULT_RUNTIME_SIGNAL = (
    MODULE_ROOT / ".local" / "forward" / "remote-main-signal.json"
)
DEFAULT_NEWS_STATE = (
    MODULE_ROOT / ".local" / "forward" / "dashboard-news-sync-state.json"
)
DEFAULT_LEARNING_STATE = (
    MODULE_ROOT / ".local" / "forward" / "dashboard-learning-sync-state.json"
)
DEFAULT_LEARNING_HISTORY_STATE = (
    MODULE_ROOT / ".local" / "forward" / "dashboard-learning-history-sync-state.json"
)
DEFAULT_MARKET_HISTORY_STATE = (
    MODULE_ROOT / ".local" / "forward" / "dashboard-market-history-sync-state.json"
)
DEFAULT_NEWS_EVIDENCE_STATE = (
    MODULE_ROOT / ".local" / "forward" / "dashboard-news-evidence-sync-state.json"
)
REMOTE_PAYLOAD_LIMIT_BYTES = 750_000
LOCAL_STATUS_TIMEOUT_SECONDS = 20
REMOTE_POST_TIMEOUT_SECONDS = 30
REMOTE_NEWS_LIMIT = 200
REMOTE_DECISION_LIMIT = 20
NEWS_DETAIL_BATCH_LIMIT_BYTES = 400_000
NEWS_INDEX_BATCH_LIMIT_BYTES = 400_000
NEWS_WRITE_BATCH_ITEMS = 20
NEWS_EVIDENCE_WRITE_BATCH_ITEMS = 20
NEWS_EVIDENCE_PAGES_PER_CYCLE = 4
NEWS_READER_WINDOW_DAYS = 60
NEWS_MIRROR_CONTRACT_VERSION = "news-60-day-incremental-v9-semantic-projection"
NEWS_EVIDENCE_CONTRACT_VERSION = "news-evidence-paged-v1"
MARKET_HISTORY_CONTRACT_VERSION = "market-history-d1-v2"
MARKET_HISTORY_BATCH_LIMIT_BYTES = 350_000
MARKET_HISTORY_OVERLAP_SECONDS = 2 * 3_600
LEARNING_HISTORY_CONTRACT_VERSION = "learning-history-d1-v2"
LEARNING_HISTORY_BATCH_LIMIT_BYTES = 300_000
LEARNING_HISTORY_FULL_REFRESH_SECONDS = 86_400
LEARNING_SUMMARY_CURVE_POINTS = 48
LEARNING_SUMMARY_GROUPS_PER_IDENTITY = 6
LEARNING_SUMMARY_EXECUTION_RESULTS = 20
LEARNING_OVERVIEW_CURVE_POINTS = 240
LEARNING_OVERVIEW_GROUPS_PER_IDENTITY = 60
MARKET_OVERVIEW_DECISIONS_PER_SERIES = 240
REMOTE_MARKET_DECISION_LIMIT = 288 * 5
REMOTE_MARKET_CANDLE_LIMIT = 576
REMOTE_MARKET_DENSE_LIMITS = (1440, 1152, 864, 576, 288, 0)
REMOTE_MARKET_OVERVIEW_LIMITS = (480, 240, 120, 80, 40)

from xauusd_forecaster.dashboard_payloads import (
    audit_status_payload,
    critical_status_payload,
)


class PayloadContractError(ValueError):
    """A bounded payload still violates the remote transport contract."""

    error_code = "PAYLOAD_CONTRACT_REJECTED"


class AllTargetsRejected(RuntimeError):
    """Every configured target rejected the critical heartbeat."""

    def __init__(self, degraded_resources: list[dict]) -> None:
        super().__init__("all dashboard mirror targets rejected the heartbeat")
        self.degraded_resources = degraded_resources
        codes = {
            str(item.get("error_code"))
            for item in degraded_resources
            if item.get("error_code")
        }
        self.error_code = next(iter(codes)) if len(codes) == 1 else "ALL_TARGETS_REJECTED"


class SyncResourceResults(list[dict]):
    """Degraded resources plus bounded timing evidence for one sync cycle."""

    def __init__(
        self, degraded_resources: list[dict], resource_observations: list[dict],
    ) -> None:
        super().__init__(degraded_resources)
        self.resource_observations = resource_observations


class RemoteInvariantViolation(RuntimeError):
    """A remote resource answered but its persisted state is contradictory."""

    def __init__(self, payload: dict) -> None:
        self.error_code = str(
            payload.get("error_code") or "REMOTE_STATE_INVARIANT_VIOLATION"
        )
        checks = payload.get("checks")
        self.evidence = {
            "violation_count": int(payload.get("violation_count") or 0),
            "checks": checks[:12] if isinstance(checks, list) else [],
        }
        super().__init__(
            f"remote invariant check failed: {self.error_code} "
            f"({self.evidence['violation_count']} violations)"
        )

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
        rows, NEWS_DETAIL_BATCH_LIMIT_BYTES, max_items=NEWS_WRITE_BATCH_ITEMS,
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
            if last_size <= REMOTE_PAYLOAD_LIMIT_BYTES:
                return encoded
    raise PayloadContractError(
        f"half-hour market chart payload is {last_size} bytes "
        f"(limit {REMOTE_PAYLOAD_LIMIT_BYTES})"
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
    """Build a bounded optional first page independently of the heartbeat."""
    return _encoded_snapshot(
        audit_status_payload(payload, decision_limit=REMOTE_DECISION_LIMIT),
        label="bounded audit first page",
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
        current_degraded = list(
            getattr(error, "degraded_resources", None) or []
        )
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


def sync_error_code(error: Exception | None) -> str:
    """Classify transport failures once, before they enter persisted status."""
    if error is None:
        return "UNKNOWN"
    declared = getattr(error, "error_code", None)
    if declared:
        return str(declared)
    if isinstance(error, urllib.error.HTTPError):
        if error.code == 413:
            return "PAYLOAD_LIMIT_EXCEEDED"
        if error.code in {401, 403}:
            return "AUTH_REJECTED"
        if error.code == 429:
            return "RATE_LIMITED"
        if error.code >= 500:
            return "REMOTE_UNAVAILABLE"
        return "HTTP_REJECTED"
    if isinstance(error, (
        TimeoutError,
        ConnectionError,
        http.client.RemoteDisconnected,
        urllib.error.URLError,
    )):
        return "TRANSPORT_UNAVAILABLE"
    return "UNCLASSIFIED"


def _write_runtime_signal(payload: object) -> None:
    if not isinstance(payload, dict):
        return
    revision = str(payload.get("main_revision") or "").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        return
    target = DEFAULT_RUNTIME_SIGNAL
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


def _remote_request_headers(url: str, config: dict) -> dict[str, str]:
    headers = {
        "Authorization": f"Bearer {config['token']}",
        "User-Agent": "AurumSignalRoomMirror/1.0",
    }
    sites_bypass_token = os.environ.get("SITES_BYPASS_TOKEN", "").strip()
    remote_host = (urllib.parse.urlsplit(url).hostname or "").lower()
    if sites_bypass_token and remote_host.endswith(".chatgpt.site"):
        headers["OAI-Sites-Authorization"] = f"Bearer {sites_bypass_token}"
    return headers


def _post_json(url: str, payload: bytes, config: dict) -> dict:
    headers = {
        **_remote_request_headers(url, config),
        "Content-Type": "application/json",
    }
    request = urllib.request.Request(url, data=payload, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(
            request, timeout=REMOTE_POST_TIMEOUT_SECONDS
        ) as response:
            if response.status != 200:
                raise RuntimeError(f"dashboard sync returned HTTP {response.status}")
            body = response.read()
    except urllib.error.HTTPError as error:
        try:
            failure = json.loads(error.read())
        except (TypeError, ValueError):
            raise error
        if isinstance(failure, dict) and failure.get("error_code"):
            raise RemoteInvariantViolation(failure) from error
        raise error
    try:
        result = json.loads(body) if body else {}
    except (TypeError, ValueError):
        result = {}
    _write_runtime_signal(result)
    return result if isinstance(result, dict) else {}


def _get_json(
    url: str,
    config: dict,
    *,
    timeout_seconds: float = REMOTE_POST_TIMEOUT_SECONDS,
) -> dict:
    if (
        not isinstance(timeout_seconds, (int, float))
        or isinstance(timeout_seconds, bool)
        or not math.isfinite(float(timeout_seconds))
        or float(timeout_seconds) <= 0
    ):
        raise ValueError("dashboard GET timeout is invalid")
    request = urllib.request.Request(url, headers={
        **_remote_request_headers(url, config),
        "Accept": "application/json",
    })
    try:
        with urllib.request.urlopen(
            request, timeout=min(REMOTE_POST_TIMEOUT_SECONDS, float(timeout_seconds)),
        ) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as error:
        try:
            payload = json.loads(error.read())
        except (TypeError, ValueError):
            raise error
        if isinstance(payload, dict) and payload.get("error_code"):
            raise RemoteInvariantViolation(payload) from error
        raise error


def _assistant_worker_id() -> str:
    worker_suffix = re.sub(
        r"[^A-Za-z0-9._:-]", "-",
        os.environ.get("COMPUTERNAME", "windows-sync"),
    )[:64]
    return f"dashboard-sync:{worker_suffix}"


def _sync_assistant_chat(_local_payload: dict, _config: dict):
    """Assistant is intentionally paused until an API model is configured."""
    return {"status": "PAUSED_NO_MODEL"}


def _sync_news_questions(_local_payload: dict, _config: dict) -> None:
    # Private Assistant Q&A, titles, compaction, and memory indexing are paused
    # together. News annotation, impact, and Daily Brief use separate workers.
    return None

def _target_state_path(path: Path, target_name: str, *, legacy: bool) -> Path:
    if legacy or target_name == "sites":
        return path
    safe_name = "".join(
        character if character.isalnum() or character in "-_" else "-"
        for character in target_name.lower()
    ).strip("-") or "mirror"
    return path.with_name(f"{path.stem}-{safe_name}{path.suffix}")


def configured_targets(config: dict) -> list[dict]:
    """Resolve legacy or multi-target mirror configuration without sharing state."""
    declared = config.get("targets")
    if not isinstance(declared, list):
        declared = [{**config, "name": config.get("name", "sites"), "legacy": True}]
        cloudflare_url = os.environ.get("CLOUDFLARE_INGEST_URL", "").strip()
        cloudflare_token = os.environ.get("CLOUDFLARE_INGEST_TOKEN", "").strip()
        if cloudflare_url or cloudflare_token:
            declared.append({
                "name": "cloudflare",
                "remote_ingest_url": cloudflare_url,
                "token": cloudflare_token,
                "legacy": False,
            })

    targets = []
    for index, target in enumerate(declared):
        if not isinstance(target, dict):
            raise ValueError(f"dashboard target {index + 1} must be an object")
        if target.get("enabled") is False:
            continue
        name = str(target.get("name") or f"mirror-{index + 1}").strip()
        remote_url = str(target.get("remote_ingest_url") or "").strip()
        token_env = str(target.get("token_env") or "").strip()
        token = str(
            target.get("token") or (os.environ.get(token_env) if token_env else "") or ""
        ).strip()
        if not remote_url.startswith("https://") or not token:
            raise ValueError(f"dashboard target {name!r} needs https URL and token")
        scoped = {
            **config,
            **target,
            "name": name,
            "token": token,
            "legacy": bool(target.get("legacy", False)),
        }
        scoped.pop("targets", None)
        scoped["learning_state_file"] = str(_target_state_path(
            Path(target.get(
                "learning_state_file",
                config.get("learning_state_file", DEFAULT_LEARNING_STATE),
            )),
            name,
            legacy=scoped["legacy"],
        ))
        scoped["news_state_file"] = str(_target_state_path(
            Path(target.get(
                "news_state_file",
                config.get("news_state_file", DEFAULT_NEWS_STATE),
            )),
            name,
            legacy=scoped["legacy"],
        ))
        scoped["market_history_state_file"] = str(_target_state_path(
            Path(target.get(
                "market_history_state_file",
                config.get("market_history_state_file", DEFAULT_MARKET_HISTORY_STATE),
            )),
            name,
            legacy=scoped["legacy"],
        ))
        scoped["learning_history_state_file"] = str(_target_state_path(
            Path(target.get(
                "learning_history_state_file",
                config.get("learning_history_state_file", DEFAULT_LEARNING_HISTORY_STATE),
            )),
            name,
            legacy=scoped["legacy"],
        ))
        scoped["news_evidence_state_file"] = str(_target_state_path(
            Path(target.get(
                "news_evidence_state_file",
                config.get("news_evidence_state_file", DEFAULT_NEWS_EVIDENCE_STATE),
            )),
            name,
            legacy=scoped["legacy"],
        ))
        targets.append(scoped)
    if not targets:
        raise ValueError("dashboard sync has no configured targets")
    return targets


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


def _sync_learning(local_payload: dict, config: dict) -> None:
    learning_url = config.get("remote_learning_url") or (
        config["remote_ingest_url"].rsplit("/", 1)[0] + "/learning"
    )
    learning_state_path = Path(
        config.get("learning_state_file", DEFAULT_LEARNING_STATE)
    )
    remote_host = urllib.parse.urlsplit(config["remote_ingest_url"]).hostname or ""
    if not remote_host.lower().endswith(".chatgpt.site"):
        history_url = config.get("remote_learning_history_url") or (
            config["remote_ingest_url"].rsplit("/", 1)[0] + "/learning-history"
        )
        history_state_path = Path(config.get(
            "learning_history_state_file", DEFAULT_LEARNING_HISTORY_STATE,
        ))
        history_state = _read_news_sync_state(history_state_path)
        hashes = history_state.get("hashes", {})
        if not isinstance(hashes, dict):
            hashes = {}
        last_full = history_state.get("last_full_sync")
        try:
            full_refresh_due = (
                history_state.get("contract_version") != LEARNING_HISTORY_CONTRACT_VERSION
                or not last_full
                or (datetime.now(UTC) - datetime.fromisoformat(last_full)).total_seconds()
                >= LEARNING_HISTORY_FULL_REFRESH_SECONDS
            )
        except (TypeError, ValueError):
            full_refresh_due = True
        records = learning_history_records(local_payload)
        pending = [
            row for row in records
            if full_refresh_due
            or hashes.get(f"{row['resource']}\0{row['record_key']}")
            != row["payload_hash"]
        ]
        for batch in learning_history_batches(pending):
            encoded = json.dumps(
                {"records": batch}, ensure_ascii=False, allow_nan=False,
                separators=(",", ":"),
            ).encode("utf-8")
            _post_json(history_url, encoded, config)
            for row in batch:
                hashes[f"{row['resource']}\0{row['record_key']}"] = row["payload_hash"]
            _write_news_sync_state(history_state_path, {
                "contract_version": LEARNING_HISTORY_CONTRACT_VERSION,
                "hashes": hashes,
                "last_full_sync": last_full,
            })
        if full_refresh_due:
            last_full = datetime.now(UTC).isoformat()
        _write_news_sync_state(history_state_path, {
            "contract_version": LEARNING_HISTORY_CONTRACT_VERSION,
            "hashes": hashes,
            "last_full_sync": last_full,
            "last_success": datetime.now(UTC).isoformat(),
        })
    learning_state = _read_news_sync_state(learning_state_path)
    learning_payload = learning_snapshot(local_payload)
    learning_hash = hashlib.sha256(learning_payload).hexdigest()
    if learning_state.get("payload_hash") != learning_hash:
        _post_json(learning_url, learning_payload, config)
        _write_news_sync_state(learning_state_path, {
            "payload_hash": learning_hash,
            "last_success": datetime.now(UTC).isoformat(),
        })


def _sync_market(local_payload: dict, config: dict) -> None:
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
            if current and len(encoded) > MARKET_HISTORY_BATCH_LIMIT_BYTES:
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
    state_path = Path(config.get(
        "market_history_state_file", DEFAULT_MARKET_HISTORY_STATE,
    ))
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
    while True:
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
        if pages >= 1_000:
            raise RuntimeError("market history backfill exceeded 1000 pages")
        after = str(next_cursor)
    for summary in decision_overviews.values():
        _post_json(
            remote_url, _market_decision_overview_payload(summary), config,
        )


def _local_news_archive_url(config: dict, after: str | None) -> str:
    status_url = urllib.parse.urlsplit(config["local_status_url"])
    query = {"limit": str(NEWS_WRITE_BATCH_ITEMS)}
    if after:
        query["after"] = after
    return urllib.parse.urlunsplit((
        status_url.scheme, status_url.netloc, "/api/news-archive",
        urllib.parse.urlencode(query), "",
    ))


def _verify_news_mirror_state(
    news_index_url: str,
    config: dict,
    *,
    expected_contract: str | None,
) -> None:
    """Verify persisted D1 relationships after a bounded mirror write."""
    query = {
        "health_check": "1",
        "current_contract": NEWS_MIRROR_CONTRACT_VERSION,
    }
    if expected_contract:
        query["expected_contract"] = expected_contract
    payload = _get_json(
        news_index_url + "?" + urllib.parse.urlencode(query), config,
    )
    if payload.get("status") != "OK":
        raise RemoteInvariantViolation(payload)


def _sync_news(_local_payload: dict, config: dict) -> None:
    """Advance one bounded archive page; never replay the whole archive."""
    state_path = Path(config.get("news_state_file", DEFAULT_NEWS_STATE))
    state = _read_news_sync_state(state_path)
    contract_changed = (
        state.get("mirror_contract_version") != NEWS_MIRROR_CONTRACT_VERSION
    )
    if contract_changed:
        state = {"mirror_contract_version": NEWS_MIRROR_CONTRACT_VERSION}

    if config.get("local_status_url"):
        with urllib.request.urlopen(
            _local_news_archive_url(config, state.get("cursor")),
            timeout=LOCAL_STATUS_TIMEOUT_SECONDS,
        ) as response:
            page = json.loads(response.read())
    else:
        page = {"items": _local_payload.get("recent_news", []), "has_more": False}
    news_index, details = news_mirror_parts(page)
    withdrawal_keys = news_withdrawal_keys(page)
    news_index_url = config.get("remote_news_index_url") or (
        config["remote_ingest_url"].rsplit("/", 1)[0] + "/news-index"
    )
    news_url = config.get("remote_news_ingest_url") or (
        config["remote_ingest_url"].rsplit("/", 1)[0] + "/news-content"
    )

    if contract_changed:
        _post_json(news_index_url, json.dumps({
            "neutralize_operational_state_for_contract": NEWS_MIRROR_CONTRACT_VERSION,
        }, separators=(",", ":")).encode("utf-8"), config)

    # Details are durable before their index records become discoverable.
    for batch in news_detail_batches(details):
        _post_json(news_url, json.dumps(
            {"items": batch}, ensure_ascii=False, allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8"), config)
    for batch in news_index_batches(news_index):
        _post_json(news_index_url, json.dumps(
            {"items": batch}, ensure_ascii=False, allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8"), config)
    for start in range(0, len(withdrawal_keys), NEWS_WRITE_BATCH_ITEMS):
        _post_json(news_index_url, json.dumps(
            {"withdraw_detail_keys": withdrawal_keys[
                start:start + NEWS_WRITE_BATCH_ITEMS
            ]}, separators=(",", ":"),
        ).encode("utf-8"), config)

    next_cursor = page.get("next_cursor")
    if next_cursor:
        state["cursor"] = str(next_cursor)
    now = datetime.now(UTC)
    last_prune = state.get("last_prune")
    try:
        prune_due = (
            not last_prune
            or (now - datetime.fromisoformat(str(last_prune))).total_seconds() >= 86_400
        )
    except ValueError:
        prune_due = True
    maintenance: dict[str, str] = {}
    if prune_due:
        cutoff = (now - timedelta(days=NEWS_READER_WINDOW_DAYS)).isoformat()
        maintenance["prune_before"] = cutoff
        state["last_prune"] = now.isoformat()
    if not page.get("has_more") and state.get("reconciled_contract") != NEWS_MIRROR_CONTRACT_VERSION:
        maintenance["reconcile_contract"] = NEWS_MIRROR_CONTRACT_VERSION
        state["reconciled_contract"] = NEWS_MIRROR_CONTRACT_VERSION
    if maintenance:
        _post_json(news_index_url, json.dumps(
            maintenance, separators=(",", ":"),
        ).encode("utf-8"), config)
    _verify_news_mirror_state(
        news_index_url,
        config,
        expected_contract=(
            NEWS_MIRROR_CONTRACT_VERSION if not page.get("has_more") else None
        ),
    )
    state["has_more"] = bool(page.get("has_more"))
    state["last_success"] = now.isoformat()
    _write_news_sync_state(state_path, state)


def _sync_audit(local_payload: dict, config: dict) -> None:
    audit_url = config.get("remote_audit_url") or (
        config["remote_ingest_url"].rsplit("/", 1)[0] + "/audit"
    )
    _post_json(audit_url, audit_snapshot(local_payload), config)


def _local_news_evidence_url(config: dict, cursor: str | None) -> str:
    status_url = urllib.parse.urlsplit(config["local_status_url"])
    query = {"limit": str(NEWS_EVIDENCE_WRITE_BATCH_ITEMS)}
    if cursor:
        query["cursor"] = cursor
    return urllib.parse.urlunsplit((
        status_url.scheme, status_url.netloc, "/api/news-evidence",
        urllib.parse.urlencode(query), "",
    ))


def _local_critical_status_url(config: dict) -> str:
    status_url = urllib.parse.urlsplit(config["local_status_url"])
    return urllib.parse.urlunsplit((
        status_url.scheme, status_url.netloc, "/api/critical-status", "", "",
    ))


def _sync_news_evidence(_local_payload: dict, config: dict) -> None:
    """Advance a bounded staging window and activate only a complete snapshot."""
    if not config.get("local_status_url"):
        return
    remote_url = config.get("remote_news_evidence_url") or (
        config["remote_ingest_url"].rsplit("/", 1)[0] + "/news-evidence"
    )
    state_path = Path(config.get(
        "news_evidence_state_file", DEFAULT_NEWS_EVIDENCE_STATE,
    ))
    state = _read_news_sync_state(state_path)
    cursor = None
    snapshot_id = None
    total = None
    received = 0
    first_page = None
    with urllib.request.urlopen(
        _local_news_evidence_url(config, None),
        timeout=LOCAL_STATUS_TIMEOUT_SECONDS,
    ) as response:
        first_page = json.loads(response.read())
    first_snapshot = str(first_page.get("snapshot_id") or "")
    if not re.fullmatch(r"[a-f0-9]{64}", first_snapshot):
        raise PayloadContractError("local news evidence snapshot id is invalid")
    if (
        state.get("contract_version") == NEWS_EVIDENCE_CONTRACT_VERSION
        and state.get("active_snapshot_id") == first_snapshot
    ):
        _post_json(remote_url, json.dumps({
            "contract_version": NEWS_EVIDENCE_CONTRACT_VERSION,
            "cleanup_active_snapshot": first_snapshot,
        }, separators=(",", ":")).encode("utf-8"), config)
        return
    if (
        state.get("contract_version") == NEWS_EVIDENCE_CONTRACT_VERSION
        and state.get("staging_snapshot_id") == first_snapshot
    ):
        snapshot_id = first_snapshot
        total = int(state.get("record_count") or 0)
        received = int(state.get("staged_count") or 0)
        cursor = state.get("next_cursor")
        if not isinstance(cursor, str) or not cursor:
            cursor = None
    else:
        snapshot_id = first_snapshot
        total = int(first_page.get("total") or 0)

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
            if len(encoded) > NEWS_INDEX_BATCH_LIMIT_BYTES:
                raise PayloadContractError(
                    f"news evidence batch is {len(encoded)} bytes "
                    f"(limit {NEWS_INDEX_BATCH_LIMIT_BYTES})"
                )
            _post_json(remote_url, encoded, config)
            received += len(items)
        next_cursor = page.get("next_cursor")
        if not page.get("has_more"):
            if total is None or received != total:
                raise PayloadContractError(
                    f"news evidence snapshot expected {total} rows but staged {received}"
                )
            _post_json(remote_url, json.dumps({
                "contract_version": NEWS_EVIDENCE_CONTRACT_VERSION,
                "activate_snapshot": snapshot_id,
                "expected_count": total,
            }, separators=(",", ":")).encode("utf-8"), config)
            _write_news_sync_state(state_path, {
                "contract_version": NEWS_EVIDENCE_CONTRACT_VERSION,
                "active_snapshot_id": snapshot_id,
                "record_count": total,
                "last_success": datetime.now(UTC).isoformat(),
            })
            _post_json(remote_url, json.dumps({
                "contract_version": NEWS_EVIDENCE_CONTRACT_VERSION,
                "cleanup_active_snapshot": snapshot_id,
            }, separators=(",", ":")).encode("utf-8"), config)
            return
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


def sync_once(config: dict) -> SyncResourceResults:
    with urllib.request.urlopen(
        _local_critical_status_url(config), timeout=LOCAL_STATUS_TIMEOUT_SECONDS
    ) as response:
        critical_payload = json.loads(response.read())

    degraded = []
    observations = []
    healthy_targets = 0
    live_payload = remote_snapshot(critical_payload)
    healthy = []
    for target in configured_targets(config):
        target_name = target["name"]
        started = time.perf_counter()
        try:
            # The live heartbeat is the critical path. Optional, growing
            # resources must not make a healthy target appear offline.
            _post_json(target["remote_ingest_url"], live_payload, target)
            healthy_targets += 1
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
            continue
    if healthy_targets == 0:
        error = AllTargetsRejected(degraded)
        error.resource_observations = observations
        raise error

    try:
        with urllib.request.urlopen(
            config["local_status_url"], timeout=LOCAL_STATUS_TIMEOUT_SECONDS
        ) as response:
            local_payload = json.loads(response.read())
    except Exception as error:
        for target in healthy:
            failure = {
                "target": target["name"],
                "resource": "optional_snapshot",
                "error_type": type(error).__name__,
                "error_code": sync_error_code(error),
                "error": str(error)[:500],
                "duration_ms": 0.0,
            }
            degraded.append(failure)
            observations.append({
                "target": target["name"],
                "resource": "optional_snapshot",
                "status": "ERROR",
                "duration_ms": 0.0,
                "completed_at": datetime.now(UTC).isoformat(),
            })
        return SyncResourceResults(degraded, observations)

    for target in healthy:
        target_name = target["name"]
        for resource, operation in (
            ("audit", _sync_audit),
            ("learning", _sync_learning),
            ("market_chart", _sync_market),
            ("market_history", lambda _payload, scoped: _sync_market_history(scoped)),
            ("news", _sync_news),
            ("news_evidence", _sync_news_evidence),
            ("news_questions", _sync_news_questions),
        ):
            started = time.perf_counter()
            try:
                operation(local_payload, target)
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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--status-file", type=Path, default=DEFAULT_STATUS)
    parser.add_argument("--interval-seconds", type=float, default=30.0)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    config = json.loads(args.config.read_text(encoding="utf-8"))
    while True:
        try:
            attempts_used, degraded_resources = sync_with_retry(config)
            write_sync_status(
                args.status_file,
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
            write_sync_status(args.status_file, success=False, error=error)
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
        if args.once:
            break
        time.sleep(max(5.0, args.interval_seconds))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
