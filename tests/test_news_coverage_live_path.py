import json
from datetime import UTC, datetime, timedelta

import pytest

import xauusd_forecaster.news.scheduler.health as news_pipeline_health_module

from xauusd_forecaster.news.semantics.critical_state import RETIRED_ERROR
from xauusd_forecaster.decision.engine import ForwardEngine
from xauusd_forecaster.evidence.ledger import ForwardLedger
from xauusd_forecaster.news.scheduler.health import news_semantic_pipeline_health_at
from xauusd_forecaster.news.scheduler.state import (
    CONTRACT_BACKFILL_LANE,
    enqueue_job,
)
from xauusd_forecaster.news.collection.source_registry import NEWS_SOURCE_REGISTRY


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


def test_current_grid_live_decision_ignores_later_operational_evidence(
    tmp_path, monkeypatch,
) -> None:
    decision = datetime(2026, 8, 10, 20, 5, tzinfo=UTC)
    collected = decision + timedelta(minutes=4)
    ledger = ForwardLedger(tmp_path / "forward.sqlite3", now=decision - timedelta(hours=1))
    _prepare_news_coverage_decision(ledger, decision)
    pending_job_id = enqueue_job(
        ledger.connection, task_type="ACTIVE_ANNOTATION", source="pending-source",
        source_item_id="pending-item", revision_number=1,
        prompt_version="pending-prompt", priority="NORMAL",
        now=decision - timedelta(minutes=2),
    )
    before = news_semantic_pipeline_health_at(ledger, observed_at=decision)
    assert before["evidence_mode"] == "DURABLE_POINT_IN_TIME"

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
        now=decision + timedelta(minutes=2),
    )
    ledger.append_source_poll({
        "poll_id": "later-source-failure",
        "source": NEWS_SOURCE_REGISTRY[0].source,
        "fetched_time": decision + timedelta(minutes=2), "status": "ERROR",
    })
    ledger.connection.execute(
        """INSERT INTO news_ai_scheduler_deferrals_v1 VALUES
           (?,?,?,?,?,?,?,?)""",
        (
            "later-deferral", pending_job_id, "ACTIVE_ANNOTATION", "account",
            "MODEL_CAPACITY_DEFERRED", None,
            (decision + timedelta(minutes=3)).isoformat(),
            (decision + timedelta(minutes=4)).isoformat(),
        ),
    )
    monkeypatch.setattr(
        news_pipeline_health_module, "configured_api_credentials", lambda: (),
    )
    after = news_semantic_pipeline_health_at(ledger, observed_at=decision)
    assert after["snapshot_hash"] == before["snapshot_hash"]
    operator_health = news_pipeline_health_module.news_semantic_pipeline_health(
        ledger, observed_at=collected,
    )
    assert "ANNOTATOR_NOT_RUNNING" in operator_health["reason_codes"]
    assert "ANNOTATOR_HEARTBEAT_STALE" in operator_health["reason_codes"]
    assert "MODEL_CREDENTIALS_UNAVAILABLE" in operator_health["reason_codes"]

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


def test_current_grid_live_decision_keeps_prior_unavailable_after_later_recovery(
    tmp_path,
) -> None:
    decision = datetime(2026, 8, 10, 20, 5, tzinfo=UTC)
    collected = decision + timedelta(minutes=4)
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
            (decision + timedelta(minutes=3)).isoformat(),
        ),
    )
    ledger.connection.execute(
        """UPDATE news_ai_jobs_v1 SET state='COMPLETED',updated_at=?,completed_at=?
           WHERE job_id=?""",
        (
            (decision + timedelta(minutes=3)).isoformat(),
            (decision + timedelta(minutes=3)).isoformat(), job_id,
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


@pytest.mark.parametrize(
    ("task_type", "pending_reason"),
    (
        ("ACTIVE_ANNOTATION", "ACTIONABLE_NEWS_SEMANTICS_PENDING"),
        ("ACTIVE_IMPACT", "ACTIONABLE_NEWS_IMPACT_PENDING"),
    ),
)
def test_point_in_time_health_counts_only_authoritative_live_provenance(
    tmp_path, task_type: str, pending_reason: str,
) -> None:
    decision = datetime(2026, 8, 10, 20, 5, tzinfo=UTC)
    ledger = ForwardLedger(
        tmp_path / "forward.sqlite3", now=decision - timedelta(hours=1),
    )
    _prepare_news_coverage_decision(ledger, decision)

    annotation_id = f"{task_type.lower()}-annotation"
    enqueue_job(
        ledger.connection, task_type=task_type,
        source=f"backfill-{task_type.lower()}", source_item_id="item",
        revision_number=1, annotation_id=annotation_id,
        prompt_version="historical-prompt", priority="BACKGROUND",
        work_lane=CONTRACT_BACKFILL_LANE,
        now=decision - timedelta(minutes=10),
    )
    unresolved_job_id = enqueue_job(
        ledger.connection, task_type=task_type,
        source=f"unresolved-{task_type.lower()}", source_item_id="item",
        revision_number=1, annotation_id=f"unresolved-{annotation_id}",
        prompt_version="unknown-prompt", priority="NORMAL",
        now=decision - timedelta(minutes=10),
    )
    ledger.connection.execute(
        """UPDATE news_ai_jobs_v1
           SET lane_classified=0,provenance_resolved=0,
               provenance_version=NULL,provenance_origin_task=NULL,
               provenance_origin_ref=NULL
           WHERE job_id=?""",
        (unresolved_job_id,),
    )

    health = news_semantic_pipeline_health_at(ledger, observed_at=decision)

    assert health["status"] == "HEALTHY"
    assert health["reason_codes"] == ()
    assert health["unresolved_items"] == 0

    live_job_id = enqueue_job(
        ledger.connection, task_type=task_type,
        source=f"live-{task_type.lower()}", source_item_id="item",
        revision_number=1, annotation_id=f"live-{annotation_id}",
        prompt_version="live-prompt", priority="NORMAL",
        now=decision - timedelta(minutes=10),
    )
    live_health = news_semantic_pipeline_health_at(
        ledger, observed_at=decision,
    )

    assert live_health["status"] == "UNHEALTHY"
    assert pending_reason in live_health["reason_codes"]
    assert live_health["unresolved_items"] == 1

    retired_at = decision + timedelta(seconds=30)
    ledger.connection.execute(
        """UPDATE news_ai_jobs_v1
           SET state='DEAD_LETTER',last_error=?,completed_at=?,updated_at=?
           WHERE job_id=?""",
        (
            RETIRED_ERROR, retired_at.isoformat(), retired_at.isoformat(),
            live_job_id,
        ),
    )

    replayed_health = news_semantic_pipeline_health_at(
        ledger, observed_at=decision,
    )
    recovered_health = news_semantic_pipeline_health_at(
        ledger, observed_at=decision + timedelta(minutes=1),
    )

    assert replayed_health["snapshot_hash"] == live_health["snapshot_hash"]
    assert recovered_health["status"] == "HEALTHY"
    assert recovered_health["unresolved_items"] == 0
