"""Bounded runtime freshness inputs for the local Dashboard status payload."""

from __future__ import annotations

import json
import gzip
import zlib
from functools import lru_cache
import math
import sqlite3
from datetime import datetime
from pathlib import Path

from xauusd_forecaster.market_session import expected_weekly_closure


from xauusd_forecaster.market import parse_quote_line

QUOTE_TAIL_BYTES = 65_536
QUOTE_ARCHIVE_BYTES = 128 * 1024 * 1024


@lru_cache(maxsize=8)
def _quote_tail(path: Path, size: int, modified_ns: int, archive_budget: int) -> tuple[tuple[bytes, ...], int]:
    # Archive identity invalidates cached tails; never inflate an unbounded file.
    if path.suffix == ".gz":
        tail = b""
        remaining = archive_budget
        with gzip.open(path, "rb") as handle:
            while remaining:
                chunk = handle.read(min(1024 * 1024, remaining))
                if not chunk:
                    return tuple(tail.splitlines()), archive_budget - remaining
                remaining -= len(chunk)
                tail = (tail + chunk)[-QUOTE_TAIL_BYTES:]
            if handle.read(1):
                return (), archive_budget
        return tuple(tail.splitlines()), archive_budget - remaining
    with path.open("rb") as handle:
        handle.seek(max(0, size - QUOTE_TAIL_BYTES))
        return tuple(handle.read(QUOTE_TAIL_BYTES).splitlines()), 0


def latest_quote(database: Path) -> dict | None:
    root = database.parent / "quotes"
    # Prefer a raw day over its archive and skip an empty rollover day.
    sources = {path.name.removesuffix(".gz"): path for path in root.glob("*.jsonl.gz")}
    sources.update({path.name: path for path in root.glob("*.jsonl")})
    archive_budget = QUOTE_ARCHIVE_BYTES
    for name in sorted(sources, reverse=True)[:8]:
        path = sources[name]
        try:
            if path.suffix == ".gz" and archive_budget <= 0:
                break
            stat = path.stat()
            lines, consumed = _quote_tail(path, stat.st_size, stat.st_mtime_ns, archive_budget)
            archive_budget -= consumed
        except (OSError, EOFError, zlib.error):
            if path.suffix == ".gz":
                break
            continue
        for line in reversed(lines):
            try:
                quote = parse_quote_line(line, path)
                if not math.isfinite(quote.bid) or not math.isfinite(quote.ask):
                    continue
                return {
                    "bid": quote.bid, "ask": quote.ask, "spread": quote.ask - quote.bid,
                    "source_received_time": quote.received_time.isoformat(),
                    "source_event_time": quote.event_time.isoformat(),
                }
            except (KeyError, AttributeError, TypeError, ValueError):
                continue
    return None


def latest_quote_received(database: Path) -> str | None:
    quote = latest_quote(database)
    return quote["source_received_time"] if quote else None



def runtime_heartbeat(path: Path, *, service: str) -> dict[str, object]:
    """Read one supervised loop heartbeat without treating output as liveness."""
    if not path.exists():
        return {}
    try:
        item = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(item, dict):
            return {}
        if item.get("service") != service:
            return {}
        return item
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return {}


def broker_market_session(database: Path, now: datetime) -> dict | None:
    path = database.parent / "quotes" / "market-session.json"
    if not path.exists():
        return None
    try:
        item = json.loads(path.read_text(encoding="utf-8"))
        if item.get("schema") != "xauusd.forward.market-session.v1":
            return None
        if str(item.get("symbol", "")).casefold() != "xauusd":
            return None
        observed_at = datetime.fromisoformat(
            str(item["observed_at"]).replace("Z", "+00:00")
        )
        age = (now - observed_at).total_seconds()
        if age < -5 or age > 20:
            return None
        session = {
            "is_open": bool(item["is_open"]),
            "observed_at": observed_at.isoformat(),
            "next_open_time": item.get("next_open_time"),
            "next_close_time": item.get("next_close_time"),
        }
        for field in ("opened_at", "first_quote_after_open_at"):
            if item.get(field) is not None:
                session[field] = item[field]
        return session
    except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError):
        return None


def market_session_status(
    broker_session: dict | None,
    *,
    online: bool,
    now: datetime,
) -> str:
    """Classify expected weekend silence without weakening open-market gates."""
    if broker_session is not None:
        if not broker_session["is_open"]:
            return "CLOSED"
        return "OPEN" if online else "DATA_UNAVAILABLE"
    if not online and expected_weekly_closure(now):
        return "WEEKLY_CLOSED"
    return "DATA_UNAVAILABLE"


def market_session_observed_at(
    broker_session: dict | None,
    *,
    market_session: str,
    now: datetime,
) -> str | None:
    if broker_session is not None:
        return str(broker_session["observed_at"])
    if market_session == "WEEKLY_CLOSED":
        return now.isoformat()
    return None
