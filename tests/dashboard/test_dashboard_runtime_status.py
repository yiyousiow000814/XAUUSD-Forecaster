from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone

from xauusd_forecaster.dashboard import runtime_status as module


UTC = timezone.utc


def test_runtime_inputs_are_bounded_and_service_scoped(tmp_path) -> None:
    database = tmp_path / "forward-evidence.sqlite3"
    quotes = tmp_path / "quotes"
    quotes.mkdir()
    quote_path = quotes / "xauusd-20260811.jsonl"
    quote_path.write_text(
        "not-json\n"
        + json.dumps({"received_time": "2026-08-11T20:59:59Z"})
        + "\n",
        encoding="utf-8",
    )
    heartbeat_path = tmp_path / "collector-heartbeat.json"
    heartbeat_path.write_text(
        json.dumps({"service": "collector", "sequence": 7}),
        encoding="utf-8-sig",
    )

    assert module.latest_quote_received(database) == "2026-08-11T20:59:59+00:00"
    assert module.runtime_heartbeat(
        heartbeat_path, service="collector",
    )["sequence"] == 7
    assert module.runtime_heartbeat(heartbeat_path, service="annotator") == {}


def test_latest_decision_uses_the_callers_snapshot(tmp_path) -> None:
    database = tmp_path / "forward-evidence.sqlite3"
    connection = sqlite3.connect(database)
    connection.execute(
        "CREATE TABLE dashboard_latest_activity_v1 "
        "(activity_name TEXT PRIMARY KEY, activity_time TEXT NOT NULL)"
    )
    connection.execute(
        "INSERT INTO dashboard_latest_activity_v1 VALUES (?, ?)",
        ("decision_events", "2026-08-11T20:55:00+00:00"),
    )
    connection.commit()

    assert module.latest_decision_created_at(
        database, snapshot_connection=connection,
    ) == "2026-08-11T20:55:00+00:00"
    assert connection.execute("SELECT 1").fetchone()[0] == 1
    connection.close()


def test_dashboard_reads_only_fresh_ctrader_market_session(tmp_path) -> None:
    now = datetime(2026, 8, 11, 21, 0, tzinfo=UTC)
    database = tmp_path / "forward-evidence.sqlite3"
    quotes = tmp_path / "quotes"
    quotes.mkdir()
    session_path = quotes / "market-session.json"
    session_path.write_text(json.dumps({
        "schema": "xauusd.forward.market-session.v1",
        "symbol": "XAUUSD",
        "observed_at": now.isoformat(),
        "is_open": False,
        "next_open_time": (now + timedelta(hours=1)).isoformat(),
        "next_close_time": None,
    }), encoding="utf-8")

    session = module.broker_market_session(database, now)

    assert session == {
        "is_open": False,
        "observed_at": now.isoformat(),
        "next_open_time": (now + timedelta(hours=1)).isoformat(),
        "next_close_time": None,
    }
    assert module.broker_market_session(
        database, now + timedelta(seconds=21),
    ) is None


def test_dashboard_distinguishes_weekly_close_from_missing_open_market_data() -> None:
    saturday = datetime(2026, 8, 15, 12, 0, tzinfo=UTC)
    monday = datetime(2026, 8, 17, 12, 0, tzinfo=UTC)

    assert module.market_session_status(
        None, online=False, now=saturday,
    ) == "WEEKLY_CLOSED"
    assert module.market_session_status(
        None, online=False, now=monday,
    ) == "DATA_UNAVAILABLE"
    assert module.market_session_status(
        {"is_open": False}, online=False, now=monday,
    ) == "CLOSED"
    assert module.market_session_status(
        {"is_open": True}, online=True, now=monday,
    ) == "OPEN"
    assert module.market_session_status(
        {"is_open": True}, online=False, now=saturday,
    ) == "DATA_UNAVAILABLE"
    assert module.market_session_observed_at(
        None, market_session="WEEKLY_CLOSED", now=saturday,
    ) == saturday.isoformat()
    assert module.market_session_observed_at(
        None, market_session="DATA_UNAVAILABLE", now=monday,
    ) is None
    assert module.market_session_observed_at(
        {"observed_at": monday.isoformat()}, market_session="OPEN", now=monday,
    ) == monday.isoformat()
