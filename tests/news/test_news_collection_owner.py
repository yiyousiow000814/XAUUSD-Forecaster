from __future__ import annotations

import json
import threading
from datetime import datetime, timedelta, timezone

from xauusd_forecaster.decision.collector_runtime import append_due_grid_events
from xauusd_forecaster.decision.engine import ForwardEngine
from xauusd_forecaster.evidence.ledger import ForwardLedger
from xauusd_forecaster.market import MarketObservation
from xauusd_forecaster.market_session import BrokerMarketSession
from xauusd_forecaster.news.collection.runtime import NewsCollectionOwner


UTC = timezone.utc


def test_blocked_news_poll_does_not_block_decisions_or_semantic_health(tmp_path) -> None:
    epoch = datetime(2026, 8, 20, 10, 0, tzinfo=UTC)
    ledger_path = tmp_path / "forward.sqlite3"
    ledger = ForwardLedger(ledger_path, now=epoch)
    ledger.connection.execute(
        "INSERT INTO evaluation_epochs VALUES (?,?,?,?,?,?,?)",
        ("epoch", epoch.isoformat(), epoch.isoformat(), epoch.isoformat(),
         epoch.isoformat(), "commit", "contract"),
    )
    ledger.connection.commit()
    collection_started = threading.Event()
    release_collection = threading.Event()
    news_connection_ids: list[int] = []

    def blocking_collection(news_ledger, _observed_at):
        assert not news_ledger.connection.in_transaction
        news_connection_ids.append(id(news_ledger.connection))
        collection_started.set()
        release_collection.wait(timeout=5)
        return [{"source": "TEST", "status": "OK"}]

    owner = NewsCollectionOwner(
        ledger_path,
        poll_seconds=60,
        collector=blocking_collection,
        clock=lambda: epoch,
    )

    class LiveProvider:
        name = "test-ctrader"

        def observations(self, decision_time):
            return [
                MarketObservation(
                    decision_time - timedelta(seconds=2),
                    decision_time - timedelta(seconds=1),
                    4300,
                    4300.1,
                )
            ]

        def market_session(self, observed_at):
            return BrokerMarketSession(
                observed_at=observed_at,
                server_time=observed_at,
                is_open=True,
                time_till_open=timedelta(0),
                time_till_close=timedelta(hours=1),
                next_open_time=None,
                next_close_time=observed_at + timedelta(hours=1),
            )

    provider = LiveProvider()
    engine = ForwardEngine(ledger, provider)
    owner.start()
    try:
        assert collection_started.wait(timeout=2)
        assert len(news_connection_ids) == 1
        assert news_connection_ids[0] != id(ledger.connection)
        last_decision = epoch
        for boundary in (epoch + timedelta(minutes=5), epoch + timedelta(minutes=10)):
            status = owner.snapshot(boundary)
            assert status[0]["reason_code"] == "NEWS_COLLECTION_PENDING"
            last_decision, appended, skipped = append_due_grid_events(
                ledger, engine, provider, last_decision, boundary, boundary, status,
            )
            assert len(appended) == 1
            assert skipped == {}

        decision_times = ledger.connection.execute(
            "SELECT decision_time FROM decision_events ORDER BY decision_time"
        ).fetchall()
        health_times = ledger.connection.execute(
            """SELECT observed_at FROM news_semantic_health_snapshots_v1
               ORDER BY observed_at"""
        ).fetchall()
        recorded_news = ledger.connection.execute(
            "SELECT news_status_json FROM collector_runs ORDER BY decision_time"
        ).fetchall()
        assert [datetime.fromisoformat(row[0]) for row in decision_times] == [
            epoch + timedelta(minutes=5),
            epoch + timedelta(minutes=10),
        ]
        assert [datetime.fromisoformat(row[0]) for row in health_times] == [
            epoch + timedelta(minutes=5),
            epoch + timedelta(minutes=10),
        ]
        assert [json.loads(row[0])[0]["reason_code"] for row in recorded_news] == [
            "NEWS_COLLECTION_PENDING",
            "NEWS_COLLECTION_PENDING",
        ]
    finally:
        release_collection.set()
        assert owner.close(timeout_seconds=2)
        ledger.close()
