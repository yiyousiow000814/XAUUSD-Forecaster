"""Dashboard-owned local market history and chart read resources."""

from __future__ import annotations

import gzip
import json
import math
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

from xauusd_forecaster.execution_costs import net_shadow_log_return


UTC = timezone.utc

MARKET_DETAIL_CANDLE_LIMIT = 7 * 288

MARKET_OVERVIEW_CANDLE_LIMIT = 480

MARKET_HISTORY_PAGE_LIMIT = 500

_QUOTE_CANDLE_CACHE_LOCK = threading.Lock()

_QUOTE_CANDLE_CACHE: dict[str, dict] = {}

def _quote_history_files(directory: Path) -> list[Path]:
    """Choose one authoritative file for each append-only quote date."""
    by_day: dict[str, Path] = {}
    for path in sorted(directory.glob("xauusd-quotes-*.jsonl*")):
        if path.name.endswith(".receipt.json"):
            continue
        day = path.name.split(".jsonl", 1)[0]
        current = by_day.get(day)
        replace_empty = (
            current is not None
            and current.stat().st_size == 0
            and path.stat().st_size > 0
        )
        prefer_live = (
            path.suffix == ".jsonl"
            and current is not None
            and current.suffix == ".gz"
            and path.stat().st_size > 0
        )
        if current is None or replace_empty or prefer_live:
            by_day[day] = path
    return [by_day[day] for day in sorted(by_day)]

def _append_quote_candle(buckets: dict[datetime, dict], raw: str | bytes) -> None:
    try:
        quote = json.loads(raw)
        observed = datetime.fromisoformat(
            str(quote["received_time"]).replace("Z", "+00:00")
        )
        observed = (
            observed.replace(tzinfo=UTC)
            if observed.tzinfo is None else observed.astimezone(UTC)
        )
        bid = float(quote["bid"])
        ask = float(quote["ask"])
        midpoint = (bid + ask) / 2.0
        minute = observed.replace(second=0, microsecond=0)
        bucket = minute - timedelta(minutes=minute.minute % 5)
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return
    candle = buckets.get(bucket)
    if candle is None:
        buckets[bucket] = {
            "time": bucket.isoformat(), "open": midpoint,
            "high": midpoint, "low": midpoint, "close": midpoint,
            "ticks": 1,
        }
    else:
        candle["high"] = max(candle["high"], midpoint)
        candle["low"] = min(candle["low"], midpoint)
        candle["close"] = midpoint
        candle["ticks"] += 1

def _quote_file_candles(path: Path) -> list[dict]:
    """Aggregate archives once and consume only new bytes from live quote files."""
    try:
        stat = path.stat()
    except OSError:
        return []
    key = str(path)
    with _QUOTE_CANDLE_CACHE_LOCK:
        cached = _QUOTE_CANDLE_CACHE.get(key)
        if path.suffix == ".gz":
            signature = (stat.st_size, stat.st_mtime_ns)
            if cached and cached.get("signature") == signature:
                return cached["candles"]
            buckets: dict[datetime, dict] = {}
            try:
                with gzip.open(path, "rt", encoding="utf-8") as handle:
                    for line in handle:
                        _append_quote_candle(buckets, line)
            except OSError:
                return []
            candles = [buckets[bucket] for bucket in sorted(buckets)]
            _QUOTE_CANDLE_CACHE[key] = {
                "signature": signature, "candles": candles,
            }
            return candles

        if cached and int(cached.get("offset", 0)) <= stat.st_size:
            buckets = cached["buckets"]
            offset = int(cached["offset"])
            remainder = bytes(cached.get("remainder", b""))
        else:
            buckets = {}
            offset = 0
            remainder = b""
        if offset == stat.st_size:
            return [dict(candle) for candle in cached["candles"]] if cached else []
        try:
            with path.open("rb") as handle:
                handle.seek(offset)
                chunk = handle.read()
                next_offset = handle.tell()
        except OSError:
            return []
        lines = (remainder + chunk).split(b"\n")
        remainder = lines.pop()
        for line in lines:
            if line:
                _append_quote_candle(buckets, line)
        candles = [buckets[bucket] for bucket in sorted(buckets)]
        _QUOTE_CANDLE_CACHE[key] = {
            "offset": next_offset,
            "remainder": remainder,
            "buckets": buckets,
            "candles": candles,
        }
        return [dict(candle) for candle in candles]

def _downsample_candles(candles: list[dict], limit: int) -> list[dict]:
    """Preserve OHLC extremes while bounding the all-history overview."""
    if len(candles) <= limit:
        return candles
    chunk_size = math.ceil(len(candles) / limit)
    compacted = []
    for start in range(0, len(candles), chunk_size):
        rows = candles[start:start + chunk_size]
        compacted.append({
            "time": rows[0]["time"],
            "open": rows[0]["open"],
            "high": max(row["high"] for row in rows),
            "low": min(row["low"] for row in rows),
            "close": rows[-1]["close"],
            "ticks": sum(int(row.get("ticks") or 0) for row in rows),
            "source_candles": len(rows),
        })
    return compacted

def _all_market_candles(database: Path) -> list[dict]:
    history_by_time: dict[str, dict] = {}
    for path in _quote_history_files(database.parent / "quotes"):
        for candle in _quote_file_candles(path):
            history_by_time[candle["time"]] = candle
    return [history_by_time[key] for key in sorted(history_by_time)]

def _market_decisions(
    connection: sqlite3.Connection, start_time: str, end_time: str | None = None,
) -> list[dict]:
    end_clause = " AND p.decision_time<?" if end_time else ""
    parameters: tuple[str, ...] = (
        (start_time, end_time) if end_time else (start_time,)
    )
    decision_rows = connection.execute(
        f"""WITH ranked AS (
             SELECT p.source_decision_id,p.decision_time,p.model_identity,
                    p.model_version,p.recommended_action,p.effective_action,
                    p.prediction_status,p.predicted_direction_u5,
                    p.ev_long_u5,p.ev_short_u5,p.lcb_long_u5,p.lcb_short_u5,
                    s.value_quote_return,
                    o.long_quote_return,o.short_quote_return,o.outcome_status,
                    o.reason_codes_json AS outcome_reason_codes_json,
                    row_number() OVER (
                      PARTITION BY p.source_decision_id,p.model_identity
                      ORDER BY u.created_at DESC,u.model_version DESC
                    ) AS version_rank
             FROM predictions_v2 p
             JOIN model_updates_v2 u USING(model_version)
             LEFT JOIN prediction_scores_v2 s
               USING(source_decision_id,model_version)
             LEFT JOIN derived_outcomes o
               ON o.source_decision_id=p.source_decision_id
             WHERE p.decision_time>=?{end_clause} AND p.decision_time>u.created_at
           )
           SELECT * FROM ranked WHERE version_rank=1
           ORDER BY decision_time,model_identity""",
        parameters,
    ).fetchall()
    decisions = []
    for row in decision_rows:
        recorded = row["recommended_action"]
        row_payload = {
            key: value for key, value in dict(row).items()
            if key != "outcome_reason_codes_json"
        }
        for key in ("long_quote_return", "short_quote_return"):
            gross = row_payload.get(key)
            row_payload[f"gross_{key}"] = gross
            if gross is not None:
                row_payload[key] = net_shadow_log_return(gross)
        gross_score = row_payload.get("value_quote_return")
        row_payload["gross_value_quote_return"] = gross_score
        if gross_score is not None:
            row_payload["value_quote_return"] = (
                0.0 if recorded == "WAIT" else net_shadow_log_return(gross_score)
            )
        ev_long = row["ev_long_u5"]
        ev_short = row["ev_short_u5"]
        lcb_long = row["lcb_long_u5"]
        lcb_short = row["lcb_short_u5"]
        expected = "WAIT"
        legacy_lcb_policy = row["prediction_status"] == "PROVISIONAL_LCB_GATED"
        if legacy_lcb_policy and lcb_long is not None and lcb_short is not None:
            if lcb_long > lcb_short and lcb_long > 0:
                expected = "LONG"
            elif lcb_short > lcb_long and lcb_short > 0:
                expected = "SHORT"
        elif not legacy_lcb_policy and ev_long is not None and ev_short is not None:
            if ev_long > ev_short and ev_long > 0:
                expected = "LONG"
            elif ev_short > ev_long and ev_short > 0:
                expected = "SHORT"
        decisions.append({
            **row_payload,
            "outcome_reason_codes": json.loads(row["outcome_reason_codes_json"] or "[]"),
            "exit_time": (
                datetime.fromisoformat(row["decision_time"]) + timedelta(minutes=30)
            ).isoformat(),
            "outcome_status": row["outcome_status"] or "PENDING",
            "policy_expected_action": expected,
            "policy_consistent": recorded == expected,
            "action_policy": (
                "POSITIVE_LCB_V1" if legacy_lcb_policy else "POSITIVE_POST_COST_EV_V2"
            ),
            "frozen_record": True,
        })
    return decisions

def _market_history_page(
    database: Path, connection: sqlite3.Connection, after: str | None, limit: int,
) -> dict:
    """Return an ordered, replay-safe page for incremental remote ingestion."""
    history = _all_market_candles(database)
    start_index = 0
    if after:
        while start_index < len(history) and history[start_index]["time"] <= after:
            start_index += 1
    candles = history[start_index:start_index + limit]
    if not candles:
        return {"candles": [], "decisions": [], "next_cursor": after, "has_more": False}
    end_index = start_index + len(candles)
    end_time = history[end_index]["time"] if end_index < len(history) else None
    return {
        "candles": candles,
        "decisions": _market_decisions(
            connection, candles[0]["time"], end_time,
        ),
        "next_cursor": candles[-1]["time"],
        "has_more": end_index < len(history),
        "history_start": history[0]["time"],
        "history_end": history[-1]["time"],
    }

def _recent_market_chart(
    database: Path, connection: sqlite3.Connection, now: datetime
) -> dict:
    """Build recorded quote history; weekends must not erase the last session."""
    history = _all_market_candles(database)
    candles = history[-MARKET_DETAIL_CANDLE_LIMIT:]
    overview_candles = (
        _downsample_candles(history, MARKET_OVERVIEW_CANDLE_LIMIT)
        if len(history) > len(candles) else []
    )
    first_time = candles[0]["time"] if candles else now.isoformat()
    decisions = _market_decisions(connection, first_time)
    marker_rows = connection.execute(
        """WITH grouped AS (
             SELECT model_identity,training_dataset_hash,min(created_at) created_at,
                    min(training_rows) training_rows,min(training_cutoff) training_cutoff,
                    count(*) artifact_count
             FROM model_updates_v2
             GROUP BY model_identity,training_dataset_hash
           )
           SELECT * FROM grouped WHERE created_at>=?
           ORDER BY created_at,model_identity""",
        (first_time,),
    ).fetchall()
    prediction_history_start: dict[str, str] = {}
    for row in decisions:
        identity = str(row.get("model_identity") or "")
        decision_time = str(row.get("decision_time") or "")
        if identity and decision_time and (
            identity not in prediction_history_start
            or decision_time < prediction_history_start[identity]
        ):
            prediction_history_start[identity] = decision_time
    return {
        "window_hours": None,
        "candle_minutes": 5,
        "candles": candles,
        "overview_candles": overview_candles,
        "history_start": history[0]["time"] if history else None,
        "history_end": history[-1]["time"] if history else None,
        "detail_start": candles[0]["time"] if candles else None,
        "source_candle_count": len(history),
        "overview_downsampled": bool(overview_candles),
        "prediction_history_start": prediction_history_start,
        "history_resource": "/api/market-history",
        "decisions": [dict(row) for row in decisions],
        "training_markers": [dict(row) for row in marker_rows],
    }
