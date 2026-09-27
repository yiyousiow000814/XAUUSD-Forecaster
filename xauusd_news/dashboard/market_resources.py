"""Dashboard-owned local market history and chart read resources."""

from __future__ import annotations

import gzip
import json
import math
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path



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


def _quote_file_day(path: Path):
    value = path.name.split(".jsonl", 1)[0].removeprefix("xauusd-quotes-")
    try:
        return datetime.strptime(value, "%Y%m%d").date()
    except ValueError:
        return None


def _market_history_endpoint(sources: list[Path], *, newest: bool) -> str | None:
    ordered = reversed(sources) if newest else iter(sources)
    for source in ordered:
        candles = _quote_file_candles(source)
        if candles:
            return candles[-1 if newest else 0]["time"]
    return None


def _market_history_candles(
    database: Path, after: str | None, limit: int,
) -> tuple[list[dict], str | None, str | None, str | None]:
    """Read at most one bounded cursor page plus exact history endpoints."""
    sources = _quote_history_files(database.parent / "quotes")
    after_day = None
    if after:
        try:
            after_day = datetime.fromisoformat(
                after.replace("Z", "+00:00")
            ).astimezone(UTC).date()
        except ValueError:
            pass
    candidates = []
    for source in sources:
        source_day = _quote_file_day(source)
        if after_day is None or source_day is None or source_day >= after_day:
            candidates.append(source)
    rows_by_time = {}
    for source in candidates:
        for candle in _quote_file_candles(source):
            if not after or candle["time"] > after:
                rows_by_time[candle["time"]] = candle
        if len(rows_by_time) > limit:
            break
    rows = [rows_by_time[key] for key in sorted(rows_by_time)]
    return (
        rows[:limit], rows[limit]["time"] if len(rows) > limit else None,
        _market_history_endpoint(sources, newest=False),
        _market_history_endpoint(sources, newest=True),
    )


def _market_history_page(
    database: Path, connection: sqlite3.Connection, after: str | None, limit: int,
) -> dict:
    """Return an ordered, replay-safe page for incremental remote ingestion."""
    candles, next_time, history_start, history_end = _market_history_candles(
        database, after, limit,
    )
    if not candles:
        return {"candles": [], "next_cursor": after, "has_more": False}
    return {
        "candles": candles,
        "next_cursor": candles[-1]["time"],
        "has_more": next_time is not None,
        "history_start": history_start,
        "history_end": history_end,
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
        "history_resource": "/api/market-history",
    }
