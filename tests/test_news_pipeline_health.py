from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta

import pytest

from xauusd_forecaster.forward_ledger import ForwardLedger
from xauusd_forecaster import news_pipeline_health
from xauusd_forecaster.annotation import PROMPT_VERSION
from xauusd_forecaster.news_scheduler import enqueue_job


def _heartbeat(ledger: ForwardLedger, at: datetime) -> None:
    (ledger.path.parent / "news-annotator-status.json").write_text(
        json.dumps({
            "service": "annotator", "state": "RUNNING",
            "last_success": at.isoformat(),
        }),
        encoding="utf-8",
    )
    ledger.append_source_poll({
        "poll_id": f"poll-{at.isoformat()}", "source": "test_source",
        "fetched_time": at, "status": "OK",
    })


def _news(ledger: ForwardLedger, received_at: datetime) -> None:
    body = "Material macroeconomic report. " * 20
    ledger.append_news_revision({
        "source": "test_semantic_source", "source_item_id": "item-1",
        "source_published_time": received_at - timedelta(minutes=1),
        "collector_first_seen_time": received_at, "fetched_time": received_at,
        "headline": "Material macroeconomic report", "body": body,
        "link": "https://example.test/report",
        "content_hash": hashlib.sha256(body.encode()).hexdigest(),
        "cluster_id": "cluster-1",
    })


@pytest.fixture
def credentials(monkeypatch):
    monkeypatch.setattr(
        news_pipeline_health, "configured_api_credentials", lambda: (object(),),
    )


def test_idle_pipeline_is_healthy_without_synthetic_provider_probe(
    tmp_path, credentials,
) -> None:
    now = datetime(2026, 8, 14, 7, 0, tzinfo=UTC)
    ledger = ForwardLedger(tmp_path / "forward.sqlite3", now=now)
    _heartbeat(ledger, now)

    health = news_pipeline_health.news_semantic_pipeline_health(
        ledger, observed_at=now,
    )

    assert health["status"] == "HEALTHY"
    assert health["unresolved_items"] == 0


def test_recent_arrival_gets_one_decision_interval_before_fail_closed_gate(
    tmp_path, credentials,
) -> None:
    now = datetime(2026, 8, 14, 7, 0, tzinfo=UTC)
    ledger = ForwardLedger(tmp_path / "forward.sqlite3", now=now)
    _heartbeat(ledger, now)
    _news(ledger, now - timedelta(minutes=2))

    health = news_pipeline_health.news_semantic_pipeline_health(
        ledger, observed_at=now,
    )

    assert health["status"] == "HEALTHY"


def test_unresolved_actionable_news_after_one_interval_fails_closed(
    tmp_path, credentials,
) -> None:
    now = datetime(2026, 8, 14, 7, 0, tzinfo=UTC)
    ledger = ForwardLedger(tmp_path / "forward.sqlite3", now=now)
    _heartbeat(ledger, now)
    _news(ledger, now - timedelta(minutes=6))

    health = news_pipeline_health.news_semantic_pipeline_health(
        ledger, observed_at=now,
    )

    assert health["status"] == "UNHEALTHY"
    assert health["reason_codes"] == ("ACTIONABLE_NEWS_SEMANTICS_PENDING",)
    assert health["actionable_failure_counts"] == {}


def test_expired_failed_candidate_does_not_hold_the_gate_closed(
    tmp_path, credentials,
) -> None:
    now = datetime(2026, 8, 14, 7, 0, tzinfo=UTC)
    ledger = ForwardLedger(tmp_path / "forward.sqlite3", now=now)
    _heartbeat(ledger, now)
    body = "Complete gold report. " * 20
    received = now - timedelta(minutes=6)
    ledger.append_news_revision({
        "source": "google_news_gold_context", "source_item_id": "expired",
        "source_published_time": now - timedelta(hours=73),
        "collector_first_seen_time": received, "fetched_time": received,
        "headline": "Gold market report", "body": body,
        "link": "https://example.test/expired",
        "content_hash": hashlib.sha256(body.encode()).hexdigest(),
        "cluster_id": "expired-cluster",
    })
    job_id = enqueue_job(
        ledger.connection, task_type="ACTIVE_ANNOTATION",
        source="google_news_gold_context", source_item_id="expired",
        revision_number=1, prompt_version=PROMPT_VERSION,
        priority="NORMAL", now=received,
    )
    with ledger.connection:
        ledger.connection.execute(
            "UPDATE news_ai_jobs_v1 SET state='DEAD_LETTER' WHERE job_id=?",
            (job_id,),
        )

    health = news_pipeline_health.news_semantic_pipeline_health(
        ledger, observed_at=now,
    )

    assert health["status"] == "HEALTHY"


def test_known_current_model_failure_fails_closed_without_waiting_for_grace(
    tmp_path, credentials,
) -> None:
    now = datetime(2026, 8, 14, 7, 0, tzinfo=UTC)
    ledger = ForwardLedger(tmp_path / "forward.sqlite3", now=now)
    _heartbeat(ledger, now)
    received = now - timedelta(minutes=2)
    _news(ledger, received)
    job_id = enqueue_job(
        ledger.connection, task_type="ACTIVE_ANNOTATION",
        source="test_semantic_source", source_item_id="item-1",
        revision_number=1, prompt_version=PROMPT_VERSION,
        priority="NORMAL", now=received,
    )
    with ledger.connection:
        ledger.connection.execute(
            "UPDATE news_ai_jobs_v1 SET state='BACKING_OFF' WHERE job_id=?",
            (job_id,),
        )

    health = news_pipeline_health.news_semantic_pipeline_health(
        ledger, observed_at=now,
    )

    assert health["status"] == "UNHEALTHY"
    assert health["reason_codes"] == ("ACTIONABLE_NEWS_SEMANTICS_PENDING",)
    assert health["actionable_failure_counts"] == {"UNCLASSIFIED": 1}


@pytest.mark.parametrize("failure", ["missing", "stale", "credentials"])
def test_runtime_dependencies_fail_closed(tmp_path, monkeypatch, failure: str) -> None:
    now = datetime(2026, 8, 14, 7, 0, tzinfo=UTC)
    ledger = ForwardLedger(tmp_path / "forward.sqlite3", now=now)
    if failure != "missing":
        _heartbeat(
            ledger,
            now - timedelta(minutes=6) if failure == "stale" else now,
        )
    monkeypatch.setattr(
        news_pipeline_health, "configured_api_credentials",
        lambda: () if failure == "credentials" else (object(),),
    )

    health = news_pipeline_health.news_semantic_pipeline_health(
        ledger, observed_at=now,
    )

    assert health["status"] == "UNHEALTHY"
