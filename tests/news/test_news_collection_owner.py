from __future__ import annotations

import json
import threading
from datetime import datetime, timedelta, timezone

from xauusd_forecaster.evidence.ledger import ForwardLedger
from xauusd_forecaster.market import MarketObservation
from xauusd_forecaster.market_session import BrokerMarketSession
from xauusd_forecaster.news.collection.runtime import NewsCollectionOwner


UTC = timezone.utc


def test_blocked_news_poll_does_not_block_other_evidence_writers(tmp_path) -> None:
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

    owner.start()
    try:
        assert collection_started.wait(timeout=2)
        assert len(news_connection_ids) == 1
        assert news_connection_ids[0] != id(ledger.connection)
        status = owner.snapshot(epoch + timedelta(minutes=5))
        assert status[0]["reason_code"] == "NEWS_COLLECTION_PENDING"
        ledger.append_source_poll({
            "poll_id": "independent", "source": "TEST", "fetched_time": epoch,
            "status": "OK",
        })
        assert ledger.connection.execute("SELECT count(*) FROM source_polls").fetchone()[0] == 1
    finally:
        release_collection.set()
        assert owner.close(timeout_seconds=2)
        ledger.close()
