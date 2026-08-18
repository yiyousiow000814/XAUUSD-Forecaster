import json
from datetime import UTC, datetime, timedelta

import xauusd_forecaster.news_pipeline_health as news_pipeline_health_module

from xauusd_forecaster.forward_engine import ForwardEngine
from xauusd_forecaster.forward_ledger import ForwardLedger
from xauusd_forecaster.news_pipeline_health import news_semantic_pipeline_health_at
from xauusd_forecaster.news_scheduler import enqueue_job
from xauusd_forecaster.news_source_registry import NEWS_SOURCE_REGISTRY


class _EmptyProvider:
    name = "empty"

    def observations(self, now: datetime) -> list:
        return []


def _prepare_news_coverage_decision(
    ledger: ForwardLedger, decision: datetime,
) -> None:
    ledger.connection.execute(
        "INSERT INTO evaluation_epochs VALUES (?,?,?,?,?,?,?)",
        (
            f"epoch-{decision.isoformat()}", decision.isoformat(),
            decision.isoformat(), decision.isoformat(), decision.isoformat(),
            "commit", "contract",
        ),
    )
    for index, spec in enumerate(NEWS_SOURCE_REGISTRY):
        ledger.append_source_poll({
            "poll_id": f"decision-poll-{index}", "source": spec.source,
            "fetched_time": decision, "status": "OK",
        })


def test_catch_up_live_decision_ignores_later_mutable_semantic_failure(
    tmp_path,
) -> None:
    decision = datetime(2026, 8, 10, 20, 5, tzinfo=UTC)
    collected = decision + timedelta(minutes=25)
    ledger = ForwardLedger(tmp_path / "forward.sqlite3", now=decision - timedelta(hours=1))
    _prepare_news_coverage_decision(ledger, decision)
    pending_job_id = enqueue_job(
        ledger.connection, task_type="ACTIVE_ANNOTATION", source="pending-source",
        source_item_id="pending-item", revision_number=1,
        prompt_version="pending-prompt", priority="NORMAL",
        now=decision - timedelta(minutes=2),
    )
    before = news_semantic_pipeline_health_at(ledger, observed_at=decision)

    (ledger.path.parent / "news-annotator-status.json").write_text(
        json.dumps({
            "service": "annotator", "state": "STOPPED",
            "last_success": (decision - timedelta(hours=1)).isoformat(),
        }),
        encoding="utf-8",
    )
    enqueue_job(
        ledger.connection, task_type="ACTIVE_ANNOTATION", source="later-source",
        source_item_id="later-item", revision_number=1,
        prompt_version="later-prompt", priority="NORMAL",
        now=decision + timedelta(minutes=15),
    )
    ledger.append_source_poll({
        "poll_id": "later-source-failure",
        "source": NEWS_SOURCE_REGISTRY[0].source,
        "fetched_time": decision + timedelta(minutes=20), "status": "ERROR",
    })
    ledger.connection.execute(
        """INSERT INTO news_ai_scheduler_deferrals_v1 VALUES
           (?,?,?,?,?,?,?,?)""",
        (
            "later-deferral", pending_job_id, "ACTIVE_ANNOTATION", "account",
            "MODEL_CAPACITY_DEFERRED", None,
            (decision + timedelta(minutes=15)).isoformat(),
            (decision + timedelta(minutes=20)).isoformat(),
        ),
    )
    after = news_semantic_pipeline_health_at(ledger, observed_at=decision)
    assert after["snapshot_hash"] == before["snapshot_hash"]

    _, decision_id = ForwardEngine(ledger, _EmptyProvider()).append_clock_event(
        decision, collected,
    )
    semantic = ledger.connection.execute(
        """SELECT observed_at,reason_codes_json,snapshot_hash
           FROM news_semantic_health_snapshots_v1 WHERE source_decision_id=?""",
        (decision_id,),
    ).fetchone()
    coverage = ledger.connection.execute(
        """SELECT observed_at,state,coverage_reason_codes_json,
                  source_observability_json
           FROM news_input_coverage_snapshots_v1 WHERE source_decision_id=?""",
        (decision_id,),
    ).fetchone()
    assert semantic["observed_at"] == decision.isoformat()
    assert semantic["snapshot_hash"] == before["snapshot_hash"]
    assert "ANNOTATOR_HEARTBEAT_STALE" not in json.loads(
        semantic["reason_codes_json"]
    )
    assert coverage["observed_at"] == decision.isoformat()
    source_evidence = json.loads(coverage["source_observability_json"])
    coverage_reasons = json.loads(coverage["coverage_reason_codes_json"])
    assert coverage["state"] == "DEGRADED", (
        coverage_reasons, source_evidence,
    )
    assert "ACTIONABLE_NEWS_SEMANTICS_RECOVERING" not in coverage_reasons
    assert source_evidence["evidence_cutoff"] == decision.isoformat()


def test_catch_up_live_decision_keeps_prior_unavailable_after_later_recovery(
    tmp_path,
) -> None:
    decision = datetime(2026, 8, 10, 20, 5, tzinfo=UTC)
    collected = decision + timedelta(minutes=25)
    ledger = ForwardLedger(tmp_path / "forward.sqlite3", now=decision - timedelta(hours=1))
    _prepare_news_coverage_decision(ledger, decision)
    job_id = enqueue_job(
        ledger.connection, task_type="ACTIVE_ANNOTATION", source="known-source",
        source_item_id="known-item", revision_number=1,
        prompt_version="known-prompt", priority="NORMAL",
        now=decision - timedelta(minutes=10),
    )
    ledger.connection.execute(
        """INSERT INTO news_ai_job_attempts_v1 VALUES
           (?,?,?,?,?,'DISABLED','GEMINI_API_KEY_MISSING',NULL,NULL,NULL,?,NULL)""",
        (
            "attempt-before", job_id, 1, "account", "credential",
            (decision - timedelta(minutes=1)).isoformat(),
        ),
    )
    before = news_semantic_pipeline_health_at(ledger, observed_at=decision)
    assert "MODEL_CREDENTIALS_UNAVAILABLE" in before["reason_codes"]

    ledger.connection.execute(
        """INSERT INTO news_ai_job_attempts_v1 VALUES
           (?,?,?,?,?,'OK',NULL,NULL,NULL,NULL,?,NULL)""",
        (
            "attempt-after", job_id, 2, "account", "credential-2",
            (decision + timedelta(minutes=15)).isoformat(),
        ),
    )
    ledger.connection.execute(
        """UPDATE news_ai_jobs_v1 SET state='COMPLETED',updated_at=?,completed_at=?
           WHERE job_id=?""",
        (
            (decision + timedelta(minutes=15)).isoformat(),
            (decision + timedelta(minutes=15)).isoformat(), job_id,
        ),
    )
    after = news_semantic_pipeline_health_at(ledger, observed_at=decision)
    assert after["snapshot_hash"] == before["snapshot_hash"]

    _, decision_id = ForwardEngine(ledger, _EmptyProvider()).append_clock_event(
        decision, collected,
    )
    coverage = ledger.connection.execute(
        """SELECT state,coverage_reason_codes_json
           FROM news_input_coverage_snapshots_v1 WHERE source_decision_id=?""",
        (decision_id,),
    ).fetchone()
    assert coverage["state"] == "UNAVAILABLE"
    assert "MODEL_CREDENTIALS_UNAVAILABLE" in json.loads(
        coverage["coverage_reason_codes_json"]
    )


def test_current_live_decision_keeps_current_operational_evidence(
    tmp_path, monkeypatch,
) -> None:
    decision = datetime(2026, 8, 10, 20, 5, tzinfo=UTC)
    ledger = ForwardLedger(tmp_path / "forward.sqlite3", now=decision - timedelta(hours=1))
    _prepare_news_coverage_decision(ledger, decision)
    (ledger.path.parent / "news-annotator-status.json").write_text(
        json.dumps({
            "service": "annotator", "state": "RUNNING",
            "last_success": (decision - timedelta(minutes=10)).isoformat(),
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        news_pipeline_health_module, "configured_api_credentials", lambda: (object(),),
    )

    _, decision_id = ForwardEngine(ledger, _EmptyProvider()).append_clock_event(
        decision, decision,
    )
    semantic = ledger.connection.execute(
        """SELECT observed_at,reason_codes_json
           FROM news_semantic_health_snapshots_v1 WHERE source_decision_id=?""",
        (decision_id,),
    ).fetchone()
    coverage = ledger.connection.execute(
        """SELECT state FROM news_input_coverage_snapshots_v1
           WHERE source_decision_id=?""",
        (decision_id,),
    ).fetchone()
    assert semantic["observed_at"] == decision.isoformat()
    assert "ANNOTATOR_HEARTBEAT_STALE" in json.loads(semantic["reason_codes_json"])
    assert coverage["state"] == "UNAVAILABLE"
