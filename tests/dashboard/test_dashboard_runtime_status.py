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
        + json.dumps({"received_time": "2026-08-11T20:59:59Z", "event_time": "2026-08-11T20:59:59Z", "bid": 2600, "ask": 2601})
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


def test_live_quote_uses_source_file_without_any_decision_database(tmp_path):
    database = tmp_path / "never-created.sqlite3"
    quotes = tmp_path / "quotes"
    quotes.mkdir()
    path = quotes / "xauusd-quotes-2026-09-26.jsonl"
    path.write_text(json.dumps({"bid": 2600, "ask": 2600.5,
        "received_time": "2026-09-26T00:00:00Z", "event_time": "2026-09-26T00:00:00Z"})
         + '\n' + json.dumps({"bid":2601,"ask":2602,"received_time":"2026-09-26T00:01:00Z","event_time":"invalid"}) + '\n' + '{"bid": NaN, "ask": 2600.5}\n' + '{"partial":', encoding="utf-8")
    latest = module.latest_quote(database)
    assert latest["bid"] == 2600
    assert latest["ask"] == 2600.5
    assert latest["source_received_time"] == "2026-09-26T00:00:00+00:00"
    assert not database.exists()


def test_quote_survives_empty_rollover_archive_and_resumes(tmp_path):
    import gzip
    database = tmp_path / "evidence.sqlite3"
    root = tmp_path / "quotes"
    root.mkdir()
    previous = root / "xauusd-quotes-20260925.jsonl"
    row = {"bid": 2600, "ask": 2601, "received_time": "2026-09-25T20:59:59Z", "event_time": "2026-09-25T20:59:59Z"}
    previous.write_text(json.dumps(row) + "\n")
    current = root / "xauusd-quotes-20260926.jsonl"
    current.touch()
    # An archived empty weekend day must not hide the last trading day either.
    with gzip.open(root / "xauusd-quotes-20260925z.jsonl.gz", "wb") as handle:
        handle.write(b"")
    for archived in (False, True):
        if archived:
            with gzip.open(str(previous) + ".gz", "wt") as handle:
                handle.write(previous.read_text())
            previous.unlink()
        assert module.latest_quote(database)["bid"] == 2600
        assert module.latest_quote_received(database) == "2026-09-25T20:59:59+00:00"
    current.write_text('{"partial":')
    assert module.latest_quote(database)["bid"] == 2600
    row.update(bid=2602, ask=2603, received_time="2026-09-26T01:00:00Z", event_time="2026-09-26T01:00:00Z")
    current.write_text(json.dumps(row) + "\n")
    assert module.latest_quote(database)["bid"] == 2602
    assert module.latest_quote_received(database) == "2026-09-26T01:00:00+00:00"


def test_quote_archive_inflation_is_bounded(tmp_path, monkeypatch):
    import gzip
    root = tmp_path / "quotes"
    root.mkdir()
    with gzip.open(root / "xauusd-quotes-20260925.jsonl.gz", "wb") as handle:
        handle.write(b"x" * 200)
    monkeypatch.setattr(module, "QUOTE_ARCHIVE_BYTES", 100)
    assert module.latest_quote(tmp_path / "evidence.sqlite3") is None
