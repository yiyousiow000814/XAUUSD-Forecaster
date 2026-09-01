"""Bounded runtime freshness inputs for the local Dashboard status payload."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path

from xauusd_forecaster.market_session import expected_weekly_closure


def latest_quote_received(database: Path) -> str | None:
    sources = sorted((database.parent / "quotes").glob("*.jsonl"))
    if not sources:
        return None
    with sources[-1].open("rb") as handle:
        handle.seek(0, 2)
        size = handle.tell()
        handle.seek(max(0, size - 65_536))
        lines = handle.read().splitlines()
    for line in reversed(lines):
        try:
            return str(json.loads(line)["received_time"]).replace("Z", "+00:00")
        except (KeyError, ValueError, json.JSONDecodeError):
            continue
    return None


def latest_decision_created_at(
    database: Path, snapshot_connection: sqlite3.Connection | None = None,
) -> str | None:
    """Read cadence from the caller's snapshot when one owns the build."""
    owns_connection = snapshot_connection is None
    connection = snapshot_connection or sqlite3.connect(
        f"file:{database}?mode=ro", uri=True, timeout=5,
    )
    try:
        return connection.execute(
            """SELECT activity_time FROM dashboard_latest_activity_v1
               WHERE activity_name='decision_events'"""
        ).fetchone()[0]
    finally:
        if owns_connection:
            connection.close()


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
