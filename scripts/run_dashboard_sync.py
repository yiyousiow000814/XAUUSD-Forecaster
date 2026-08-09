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
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from pathlib import Path


MODULE_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = MODULE_ROOT / ".local" / "forward" / "dashboard-sync.json"
DEFAULT_STATUS = MODULE_ROOT / ".local" / "forward" / "dashboard-sync-status.json"
DEFAULT_NEWS_STATE = (
    MODULE_ROOT / ".local" / "forward" / "dashboard-news-sync-state.json"
)
DEFAULT_LEARNING_STATE = (
    MODULE_ROOT / ".local" / "forward" / "dashboard-learning-sync-state.json"
)
DEFAULT_MARKET_HISTORY_STATE = (
    MODULE_ROOT / ".local" / "forward" / "dashboard-market-history-sync-state.json"
)
REMOTE_PAYLOAD_LIMIT_BYTES = 750_000
LOCAL_STATUS_TIMEOUT_SECONDS = 20
REMOTE_POST_TIMEOUT_SECONDS = 30
REMOTE_NEWS_LIMIT = 200
REMOTE_DECISION_LIMIT = 20
REMOTE_EVIDENCE_LIMIT = 60
NEWS_DETAIL_BATCH_LIMIT_BYTES = 400_000
NEWS_INDEX_BATCH_LIMIT_BYTES = 400_000
NEWS_DETAIL_FULL_REFRESH_SECONDS = 86_400
NEWS_INDEX_FULL_REFRESH_SECONDS = 86_400
NEWS_MIRROR_CONTRACT_VERSION = "news-readable-authoritative-v1"
MARKET_HISTORY_CONTRACT_VERSION = "market-history-d1-v1"
MARKET_HISTORY_BATCH_LIMIT_BYTES = 350_000
MARKET_HISTORY_OVERLAP_SECONDS = 2 * 3_600
REMOTE_CURVE_POINTS_PER_IDENTITY = 480
REMOTE_CURVE_POINT_LIMITS = (480, 360, 240, 160, 120, 80, 40, 20)
REMOTE_DETAIL_LIMITS = (240, 160, 120, 80, 40, 20)
REMOTE_EXECUTION_RESULT_LIMIT = 100
REMOTE_MARKET_DECISION_LIMIT = 288 * 5
REMOTE_MARKET_DENSE_LIMITS = (1440, 1152, 864, 576, 288, 0)
REMOTE_MARKET_OVERVIEW_LIMITS = (480, 240, 120, 80, 40)

NEWS_INDEX_FIELDS = (
    "category", "source", "source_item_id", "revision_number",
    "source_published_time", "collector_first_seen_time", "headline",
    "content_characters", "content_status", "content_fetch_status",
    "content_error_type", "annotation_status", "annotation_reason_code",
    "annotation_reason",
    "model_visibility", "parsed_at", "emerging_topic_zh",
    "impact_status", "impact_class", "impact_event_state",
    "impact_update_type", "impact_assessed_at", "impact_expires_at",
    "impact_event_at", "impact_clock_source", "impact_reason_zh",
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
    for row in payload.get("recent_news", [])[:REMOTE_NEWS_LIMIT]:
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
        })
        detail_rows.append({
            "detail_key": detail_key,
            "detail_hash": detail_hash,
            "payload": detail_payload,
        })
    return index_rows, detail_rows


def news_detail_batches(rows: list[dict]) -> list[list[dict]]:
    return _bounded_item_batches(rows, NEWS_DETAIL_BATCH_LIMIT_BYTES)


def news_index_batches(rows: list[dict]) -> list[list[dict]]:
    return _bounded_item_batches(rows, NEWS_INDEX_BATCH_LIMIT_BYTES)


def _bounded_item_batches(rows: list[dict], limit_bytes: int) -> list[list[dict]]:
    batches: list[list[dict]] = []
    current: list[dict] = []
    for row in rows:
        candidate = [*current, row]
        size = len(json.dumps(
            {"items": candidate}, ensure_ascii=False, allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8"))
        if current and size > limit_bytes:
            batches.append(current)
            current = [row]
        else:
            current = candidate
    if current:
        batches.append(current)
    return batches


def compact_curve_points(
    points: list[dict],
    *,
    limit: int = REMOTE_CURVE_POINTS_PER_IDENTITY,
    value_keys: tuple[str, ...] = ("cumulative_quote_return",),
) -> list[dict]:
    """Preserve curve shape and version boundaries within a visual-size budget."""
    if len(points) <= limit:
        return points
    bucket_count = max(1, limit // max(2, 2 * len(value_keys)))
    bucket_size = max(1, (len(points) + bucket_count - 1) // bucket_count)
    keep = {0, len(points) - 1}
    # Every point carries model_version.  Preserve only actual transition
    # boundaries; preserving every non-empty value defeats compaction.
    for index in range(1, len(points)):
        if points[index].get("model_version") != points[index - 1].get("model_version"):
            keep.update((index - 1, index))
    for start in range(0, len(points), bucket_size):
        indices = list(range(start, min(len(points), start + bucket_size)))
        for key in value_keys:
            candidates = [index for index in indices if points[index].get(key) is not None]
            if candidates:
                keep.add(min(candidates, key=lambda index: float(points[index][key])))
                keep.add(max(candidates, key=lambda index: float(points[index][key])))
    ordered = sorted(keep)
    if len(ordered) > limit:
        mandatory = {0, len(points) - 1}
        for key in value_keys:
            candidates = [
                index for index, point in enumerate(points)
                if point.get(key) is not None
            ]
            if candidates:
                mandatory.add(min(candidates, key=lambda index: float(points[index][key])))
                mandatory.add(max(candidates, key=lambda index: float(points[index][key])))
        remaining = [index for index in ordered if index not in mandatory]
        available = max(0, limit - len(mandatory))
        if available and remaining:
            sampled = {
                remaining[round(position * (len(remaining) - 1) / max(1, available - 1))]
                for position in range(available)
            }
        else:
            sampled = set()
        ordered = sorted(mandatory | sampled)[:limit]
    return [points[index] for index in ordered]


def _compact_learning_payload(
    payload: dict, point_limit: int, detail_limit: int
) -> dict:
    learning = copy.deepcopy(payload.get("learning_curves") or {})
    models = learning.get("models")
    if isinstance(models, list):
        learning["archived_model_count"] = sum(
            row.get("lifecycle_status") not in {"LATEST", "PREVIOUS"}
            for row in models
        )
        learning["model_detail_total"] = len(models)
        learning["models"] = models[-detail_limit:]
    version_groups = learning.get("version_groups")
    if isinstance(version_groups, list):
        learning["version_group_total"] = len(version_groups)
        learning["version_groups"] = version_groups[-detail_limit:]
    curves = learning.get("identity_curves")
    if isinstance(curves, list):
        for curve in curves:
            if not isinstance(curve, dict):
                continue
            for field in ("points", "points_30m"):
                if isinstance(curve.get(field), list):
                    curve[field] = compact_curve_points(
                        curve[field], limit=point_limit
                    )
    for field in ("full_minus_market", "broad_full_minus_official_full"):
        if isinstance(learning.get(field), list):
            learning[field] = compact_curve_points(
                learning[field], limit=point_limit,
                value_keys=("cumulative_delta",),
            )

    execution = copy.deepcopy(payload.get("execution_learning") or {})
    for model in execution.get("models", []) if isinstance(execution, dict) else []:
        evaluation = model.get("evaluation") if isinstance(model, dict) else None
        if isinstance(evaluation, dict) and isinstance(evaluation.get("points"), list):
            evaluation["points"] = compact_curve_points(
                evaluation["points"], limit=point_limit,
                value_keys=("selected_cumulative_return", "baseline_cumulative_return"),
            )
        if isinstance(evaluation, dict) and isinstance(evaluation.get("results"), list):
            evaluation["result_total"] = len(evaluation["results"])
            evaluation["results"] = evaluation["results"][-REMOTE_EXECUTION_RESULT_LIMIT:]
    return {
        "learning_curves": learning,
        "execution_learning": execution,
        "mirror_compaction": {
            "display_only": True,
            "sqlite_history_complete": True,
            "curve_point_limit": point_limit,
            "detail_row_limit": detail_limit,
        },
    }


def _is_half_hour_decision(row: dict) -> bool:
    try:
        clock = str(row.get("decision_time") or "").split("T", 1)[1]
        return int(clock.split(":", 2)[1]) in (0, 30)
    except (IndexError, TypeError, ValueError):
        return False


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
    """Keep all half-hour evidence plus a bounded recent five-minute window."""
    market = copy.deepcopy(payload.get("market_chart") or {})
    for candle_key in ("candles", "overview_candles"):
        compact_candles = []
        source_rows = market.get(candle_key, [])
        if candle_key == "overview_candles":
            source_rows = _downsample_market_overview(source_rows, overview_limit)
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
    half_hour = [row for row in compact_decisions if _is_half_hour_decision(row)]
    retained = {_decision_key(row): row for row in half_hour}
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
    raise ValueError(
        f"half-hour market chart payload is {last_size} bytes "
        f"(limit {REMOTE_PAYLOAD_LIMIT_BYTES})"
    )


def learning_snapshot(payload: dict) -> bytes:
    """Keep growing learning surfaces outside the live status heartbeat."""
    last_size = 0
    for detail_limit in REMOTE_DETAIL_LIMITS:
        for point_limit in REMOTE_CURVE_POINT_LIMITS:
            encoded = json.dumps(
                _compact_learning_payload(payload, point_limit, detail_limit),
                ensure_ascii=False, allow_nan=False, separators=(",", ":"),
            ).encode("utf-8")
            last_size = len(encoded)
            if last_size <= REMOTE_PAYLOAD_LIMIT_BYTES:
                return encoded
    raise ValueError(
        f"learning payload is {last_size} bytes after adaptive compaction "
        f"(limit {REMOTE_PAYLOAD_LIMIT_BYTES})"
    )


def remote_snapshot(payload: dict) -> bytes:
    """Build a bounded Sites mirror without truncating retained news content."""
    snapshot = copy.deepcopy(payload)
    training = snapshot.get("training")
    if isinstance(training, dict):
        training.pop("models", None)  # Duplicated by learning_curves.models.

    snapshot.pop("learning_curves", None)
    snapshot.pop("execution_learning", None)
    snapshot["learning_resource"] = "/api/learning"

    snapshot["recent_news"] = []
    snapshot["news_index_resource"] = "/api/news-index"

    market = snapshot.get("market_chart")
    if isinstance(market, dict):
        # The full chart is synchronized separately.  Keeping it in the status
        # snapshot made five model families compete for one global row limit.
        market["decisions"] = []
        market["decision_resource"] = "/api/market-chart"
        market["history_resource"] = "/api/market-history"

    for name, limit in (
        ("recent_news", REMOTE_NEWS_LIMIT),
        ("recent_decisions", REMOTE_DECISION_LIMIT),
        ("news_evidence", REMOTE_EVIDENCE_LIMIT),
    ):
        rows = snapshot.get(name)
        if isinstance(rows, list):
            snapshot[name] = rows[:limit]

    snapshot["mirror_window"] = {
        "bounded": True,
        "recent_news": len(snapshot.get("recent_news", [])),
        "recent_decisions": len(snapshot.get("recent_decisions", [])),
        "news_evidence": len(snapshot.get("news_evidence", [])),
    }
    encoded = json.dumps(
        snapshot, ensure_ascii=False, allow_nan=False, separators=(",", ":")
    ).encode("utf-8")
    if len(encoded) > REMOTE_PAYLOAD_LIMIT_BYTES:
        raise ValueError(
            f"bounded dashboard payload is still {len(encoded)} bytes "
            f"(limit {REMOTE_PAYLOAD_LIMIT_BYTES}); split another large surface "
            "instead of dropping news index rows"
        )
    return encoded


def write_sync_status(
    path: Path,
    *,
    success: bool,
    attempts_used: int | None = None,
    error: Exception | None = None,
    degraded_resources: list[dict] | None = None,
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
                "attempts_used": attempts_used,
                "status": "DEGRADED" if degraded_resources else "OK",
                "degraded_resources": degraded_resources,
            }
        )
    else:
        existing.update(
            {
                "last_attempt": now,
                "last_error": str(error)[:500] if error else "Unknown sync error",
                "last_error_type": type(error).__name__ if error else "UnknownError",
                "status": "ERROR",
            }
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(existing, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)


def _post_json(url: str, payload: bytes, config: dict) -> None:
    headers = {
        "Authorization": f"Bearer {config['token']}",
        "Content-Type": "application/json",
        "User-Agent": "AurumSignalRoomMirror/1.0",
    }
    sites_bypass_token = os.environ.get("SITES_BYPASS_TOKEN", "").strip()
    remote_host = (urllib.parse.urlsplit(url).hostname or "").lower()
    if sites_bypass_token and remote_host.endswith(".chatgpt.site"):
        headers["OAI-Sites-Authorization"] = f"Bearer {sites_bypass_token}"
    request = urllib.request.Request(url, data=payload, headers=headers, method="POST")
    with urllib.request.urlopen(
        request, timeout=REMOTE_POST_TIMEOUT_SECONDS
    ) as response:
        if response.status != 200:
            raise RuntimeError(f"dashboard sync returned HTTP {response.status}")


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
    _post_json(market_url, market_chart_snapshot(local_payload), config)


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
        for payload in _market_history_payloads(candles, decisions):
            _post_json(remote_url, payload, config)
        next_cursor = page.get("next_cursor")
        if next_cursor:
            cursor = str(next_cursor)
            _write_news_sync_state(state_path, {
                "contract_version": MARKET_HISTORY_CONTRACT_VERSION,
                "cursor": cursor,
                "last_success": datetime.now(UTC).isoformat(),
            })
        pages += 1
        if not page.get("has_more") or not next_cursor or next_cursor == after:
            break
        if pages >= 1_000:
            raise RuntimeError("market history backfill exceeded 1000 pages")
        after = str(next_cursor)


def _sync_news(local_payload: dict, config: dict) -> None:
    news_index, details = news_mirror_parts(local_payload)
    state_path = Path(config.get("news_state_file", DEFAULT_NEWS_STATE))
    state = _read_news_sync_state(state_path)
    synced_index_hashes = state.get("index_hashes", {})
    if not isinstance(synced_index_hashes, dict):
        synced_index_hashes = {}
    current_index_hashes = {
        row["detail_key"]: _json_hash(row) for row in news_index
    }
    news_index_url = config.get("remote_news_index_url") or (
        config["remote_ingest_url"].rsplit("/", 1)[0] + "/news-index"
    )
    news_url = config.get("remote_news_ingest_url") or (
        config["remote_ingest_url"].rsplit("/", 1)[0] + "/news-content"
    )
    removed_keys = set(synced_index_hashes) - set(current_index_hashes)
    reset_required = (
        state.get("mirror_contract_version") != NEWS_MIRROR_CONTRACT_VERSION
        or bool(removed_keys)
    )
    if reset_required:
        reset_payload = json.dumps(
            {"reset": True}, separators=(",", ":"),
        ).encode("utf-8")
        _post_json(news_index_url, reset_payload, config)
        _post_json(news_url, reset_payload, config)
        synced_index_hashes = {}
        state = {"mirror_contract_version": NEWS_MIRROR_CONTRACT_VERSION}
    last_index_full = state.get("last_index_full_sync")
    try:
        index_full_refresh_due = (
            not last_index_full
            or (
                datetime.now(UTC) - datetime.fromisoformat(last_index_full)
            ).total_seconds() >= NEWS_INDEX_FULL_REFRESH_SECONDS
        )
    except (TypeError, ValueError):
        index_full_refresh_due = True
    pending_index = [
        row for row in news_index
        if index_full_refresh_due
        or synced_index_hashes.get(row["detail_key"])
        != current_index_hashes[row["detail_key"]]
    ]
    for batch in news_index_batches(pending_index):
        encoded = json.dumps(
            {"items": batch}, ensure_ascii=False, allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")
        _post_json(news_index_url, encoded, config)
        for row in batch:
            synced_index_hashes[row["detail_key"]] = current_index_hashes[
                row["detail_key"]
            ]
        _write_news_sync_state(state_path, {
            **state,
            "index_hashes": synced_index_hashes,
        })
    if index_full_refresh_due:
        state["last_index_full_sync"] = datetime.now(UTC).isoformat()
    synced_hashes = state.get("hashes", {})
    if not isinstance(synced_hashes, dict):
        synced_hashes = {}
    last_full = state.get("last_full_sync")
    try:
        full_refresh_due = (
            not last_full
            or (datetime.now(UTC) - datetime.fromisoformat(last_full)).total_seconds()
            >= NEWS_DETAIL_FULL_REFRESH_SECONDS
        )
    except (TypeError, ValueError):
        full_refresh_due = True
    pending = [
        row for row in details
        if full_refresh_due or synced_hashes.get(row["detail_key"]) != row["detail_hash"]
    ]
    for batch in news_detail_batches(pending):
        encoded = json.dumps(
            {"items": batch}, ensure_ascii=False, allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")
        _post_json(news_url, encoded, config)
        for row in batch:
            synced_hashes[row["detail_key"]] = row["detail_hash"]
        _write_news_sync_state(state_path, {
            "hashes": synced_hashes,
            "last_full_sync": state.get("last_full_sync"),
        })
    if full_refresh_due:
        state["last_full_sync"] = datetime.now(UTC).isoformat()
    current_keys = {row["detail_key"] for row in details}
    state["hashes"] = {
        key: value for key, value in synced_hashes.items() if key in current_keys
    }
    state["index_hashes"] = {
        key: current_index_hashes[key] for key in current_index_hashes
    }
    state["mirror_contract_version"] = NEWS_MIRROR_CONTRACT_VERSION
    _write_news_sync_state(state_path, state)


def sync_once(config: dict) -> list[dict]:
    with urllib.request.urlopen(
        config["local_status_url"], timeout=LOCAL_STATUS_TIMEOUT_SECONDS
    ) as response:
        local_payload = json.loads(response.read())

    degraded = []
    healthy_targets = 0
    live_payload = remote_snapshot(local_payload)
    for target in configured_targets(config):
        target_name = target["name"]
        try:
            # The live heartbeat is the critical path. Optional, growing
            # resources must not make a healthy target appear offline.
            _post_json(target["remote_ingest_url"], live_payload, target)
            healthy_targets += 1
        except Exception as error:
            degraded.append({
                "target": target_name,
                "resource": "heartbeat",
                "error_type": type(error).__name__,
                "error": str(error)[:500],
            })
            continue
        for resource, operation in (
            ("learning", _sync_learning),
            ("market_chart", _sync_market),
            ("market_history", lambda _payload, scoped: _sync_market_history(scoped)),
            ("news", _sync_news),
        ):
            try:
                operation(local_payload, target)
            except Exception as error:
                degraded.append({
                    "target": target_name,
                    "resource": resource,
                    "error_type": type(error).__name__,
                    "error": str(error)[:500],
                })
    if healthy_targets == 0:
        raise RuntimeError("all dashboard mirror targets rejected the heartbeat")
    return degraded


def sync_with_retry(config: dict, *, attempts: int = 3) -> tuple[int, list[dict]]:
    """Retry transient transport failures without waiting for the next sync cycle."""
    for attempt in range(1, attempts + 1):
        try:
            degraded = sync_once(config) or []
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
