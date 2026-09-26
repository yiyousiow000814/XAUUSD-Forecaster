from datetime import UTC, datetime
import sqlite3

import pytest
from tests.fixtures.dashboard_news_fixtures import _append_basic_annotation

from scripts.maintenance.purge_live_oos import purge
from xauusd_forecaster.evidence.ledger import ForwardLedger
from xauusd_forecaster.news.semantics.evidence import event_evidence_rows_from_connection
from xauusd_forecaster.dashboard.status_resources import _dashboard_payload


def test_cleanup_preserves_news_and_is_restartable(tmp_path):
    now = datetime(2026, 9, 26, tzinfo=UTC)
    database = tmp_path / "forward-evidence.sqlite3"
    ledger = ForwardLedger(database, now=now)
    ledger.append_news_revision({
        "source": "test", "source_item_id": "one", "content_hash": "news-hash",
        "cluster_id": "event-one", "headline": "Retained source",
        "body": "Official economic release with complete source evidence. " * 10,
        "source_published_time": now,
        "collector_first_seen_time": now, "fetched_time": now,
    })
    _append_basic_annotation(ledger, source="test", item_id="one", digest="news-hash", parsed_at=now)
    ledger.connection.execute(
        "INSERT INTO model_updates_v2 VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("retired", "MARKET_ONLY", "SHADOW", now.isoformat(), now.isoformat(),
         0, 0, 0, 0, 0, 0, "hash", "features", "eligibility", "artifact", "digest", "CHALLENGER"),
    )
    ledger.connection.commit()
    before = [tuple(row) for row in ledger.connection.execute("SELECT * FROM news_revisions")]
    events = event_evidence_rows_from_connection(ledger.connection, now)
    assert events, "fixture must contain a retained semantic event"
    assert purge(ledger.connection)["model_updates_v2"] == 1
    assert ledger.connection.execute("SELECT count(*) FROM model_updates_v2").fetchone()[0] == 1
    assert purge(ledger.connection, apply=True)["model_updates_v2"] == 1
    assert purge(ledger.connection, apply=True)["model_updates_v2"] == 0
    assert before == [tuple(row) for row in ledger.connection.execute("SELECT * FROM news_revisions")]
    assert event_evidence_rows_from_connection(ledger.connection, now) == events
    ledger.close()
    ForwardLedger(database, now=now).close()
    payload = _dashboard_payload(database, clock=lambda: now)
    assert payload["counts"]["news_revisions"] == 1
    assert not {"learning_curves", "training", "recent_decisions", "research_forecast"} & payload.keys()
    assert "news_collector" in payload["system"]["components"]
    assert not {"decision_collector", "outcome_settler"} & payload["system"]["components"].keys()


def test_cleanup_rejects_retained_foreign_key_and_rolls_back():
    connection = sqlite3.connect(":memory:")
    connection.executescript("""
        CREATE TABLE predictions_v2(id INTEGER PRIMARY KEY);
        INSERT INTO predictions_v2 VALUES (1);
        CREATE TABLE retained_news(id INTEGER REFERENCES predictions_v2(id));
        INSERT INTO retained_news VALUES (1);
    """)
    with pytest.raises(ValueError, match="retained dependency"):
        purge(connection, apply=True)
    assert connection.execute("SELECT * FROM predictions_v2").fetchall() == [(1,)]
    assert connection.execute("SELECT * FROM retained_news").fetchall() == [(1,)]
    connection.close()
