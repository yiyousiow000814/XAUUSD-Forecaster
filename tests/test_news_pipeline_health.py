from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta

import pytest

from xauusd_forecaster.forward_ledger import ForwardLedger
from xauusd_forecaster import news_pipeline_health
from xauusd_forecaster.annotation import DEFAULT_GEMINI_MODEL, PROMPT_VERSION
from xauusd_forecaster.critical_annotation_state import record_annotation_completion
from xauusd_forecaster.news_impact import IMPACT_MODEL, IMPACT_PROMPT_VERSION
from xauusd_forecaster.news_scheduler import (
    ApiCredential,
    CONTRACT_BACKFILL_LANE,
    LIVE_LANE,
    ROUTINE_POOL,
    WorkProvenance,
    claim_job,
    enqueue_job,
    record_job_attempt,
)


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


def _news(
    ledger: ForwardLedger,
    received_at: datetime,
    *,
    publication_delay: timedelta = timedelta(minutes=1),
    work_lane: str = LIVE_LANE,
) -> None:
    body = "Material macroeconomic report. " * 20
    ledger.append_news_revision({
        "source": "test_semantic_source", "source_item_id": "item-1",
        "source_published_time": received_at - publication_delay,
        "collector_first_seen_time": received_at, "fetched_time": received_at,
        "headline": "Material macroeconomic report", "body": body,
        "link": "https://example.test/report",
        "content_hash": hashlib.sha256(body.encode()).hexdigest(),
        "cluster_id": "cluster-1",
    })
    enqueue_job(
        ledger.connection,
        task_type="ACTIVE_ANNOTATION",
        source="test_semantic_source",
        source_item_id="item-1",
        revision_number=1,
        prompt_version=PROMPT_VERSION,
        priority="FAST" if work_lane == LIVE_LANE else "BACKGROUND",
        work_lane=work_lane,
        now=received_at,
    )


def _impact_candidate(
    ledger: ForwardLedger, *, published_at: datetime, received_at: datetime,
    parsed_at: datetime | None = None,
    annotation_overrides: dict[str, object] | None = None,
    model_version: str = DEFAULT_GEMINI_MODEL,
    work_lane: str = LIVE_LANE,
) -> None:
    body = "The Federal Reserve announced a material policy decision. " * 12
    digest = hashlib.sha256(body.encode()).hexdigest()
    parsed = parsed_at or received_at
    ledger.append_news_revision({
        "source": "impact-health-source", "source_item_id": "impact-item",
        "source_published_time": published_at,
        "collector_first_seen_time": received_at, "fetched_time": received_at,
        "headline": "Federal Reserve announces material policy decision",
        "body": body, "link": "https://example.test/impact",
        "content_hash": digest, "cluster_id": "impact-cluster",
    })
    annotation = {
        "headline_zh": "美联储宣布重大政策决定",
        "summary_zh": "美联储宣布一项具有完整正文依据的重大政策决定。",
        "primary_category": "rates_fed", "secondary_categories": [],
        "emerging_topic_zh": "美联储政策", "event_type": "monetary_policy",
        "entities": ["Federal Reserve"], "hawkishness": 0.2,
        "inflation_impulse": 0.0, "growth_impulse": 0.0,
        "geopolitical_risk": 0.0, "usd_impulse": 0.2,
        "novelty": 0.9, "confidence": 0.9, "record_kind": "FACT_EVENT",
        "actor": "Federal Reserve", "action": "announced",
        "object": "policy decision", "location": "United States",
        "event_time": published_at.isoformat(), "claim_status": "OFFICIAL",
        "materiality": 0.9, "canonical_actor_id": "federal_reserve",
        "action_family": "POLICY_DECISION",
        "canonical_object_id": "policy_decision",
        "canonical_location_id": "us", "episode_key": "fed_policy_decision",
        "primary_story_title_zh": "美联储政策决定",
        "secondary_contexts_zh": [], "relation_to_prior": "NONE",
        "document_kind": "OFFICIAL_STATEMENT",
        "material_event_key": "fed_policy_decision_current",
        "source_organization_id": "federal_reserve",
        "evidence_role": "CORE_CLAIM", "xauusd_relevance": "MACRO_DRIVER",
        "review_priority": "FAST", "material_change": "NEW_EVENT",
        "time_sensitivity": "SAME_DAY",
        "semantic_reason_zh": "完整正文显示美联储已宣布重大政策决定。",
        "supporting_evidence": [
            "Federal Reserve announced a material policy decision"
        ],
    }
    annotation.update(annotation_overrides or {})
    ledger.append_annotation({
        "annotation_id": "impact-annotation",
        "source": "impact-health-source", "source_item_id": "impact-item",
        "revision_number": 1, "raw_content_hash": digest,
        "llm_model_version": model_version,
        "prompt_version": PROMPT_VERSION,
        "parse_started_at": parsed, "parsed_at": parsed,
        "annotation": annotation,
    })
    record_annotation_completion(
        ledger.connection,
        source="impact-health-source",
        source_item_id="impact-item",
        revision_number=1,
        prompt_version=PROMPT_VERSION,
        completed_at=parsed.isoformat(),
        provenance=WorkProvenance(
            work_lane, "ACTIVE_ANNOTATION", "test-annotation-origin",
        ),
    )


def _complete_impact(ledger: ForwardLedger, at: datetime) -> None:
    revision = ledger.connection.execute(
        """SELECT content_hash FROM news_revisions
           WHERE source='impact-health-source'
             AND source_item_id='impact-item' AND revision_number=1"""
    ).fetchone()
    ledger.append_news_impact_assessment({
        "assessment_id": "impact-complete", "resolution_id": "resolution-complete",
        "source": "impact-health-source", "source_item_id": "impact-item",
        "revision_number": 1, "raw_content_hash": revision["content_hash"],
        "annotation_id": "impact-annotation", "llm_model_version": IMPACT_MODEL,
        "prompt_version": IMPACT_PROMPT_VERSION,
        "parse_started_at": at, "assessed_at": at,
        "source_context_mode": "COMPLETE_BODY",
        "source_body_character_count": 720, "impact_class": "SAME_DAY",
        "event_state": "ACTIVE", "update_type": "NEW_EVENT",
        "identity_relation": "NEW_EPISODE", "matched_candidate_id": "",
        "identity_anchor_zh": "美联储当前政策决定",
        "core_fact_changes_zh": [],
        "identity_differences_zh": ["这是新的政策决定批次。"],
        "context_differences_zh": [], "confidence": 0.9,
        "reason_zh": "完整正文确认新的政策决定。",
        "canonical_episode_id": "episode-current",
        "canonical_event_id": "event-current",
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
    ledger = ForwardLedger(tmp_path / "forward.sqlite3", now=now - timedelta(days=1))
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
    ledger = ForwardLedger(tmp_path / "forward.sqlite3", now=now - timedelta(days=1))
    _heartbeat(ledger, now)
    _news(ledger, now - timedelta(minutes=2))

    health = news_pipeline_health.news_semantic_pipeline_health(
        ledger, observed_at=now,
    )

    assert health["status"] == "HEALTHY"


@pytest.mark.parametrize(
    "publication_delay", (
        timedelta(seconds=-2.3), timedelta(minutes=1), timedelta(hours=2),
    ),
)
def test_unresolved_semantic_news_after_one_interval_fails_closed(
    tmp_path, credentials, publication_delay: timedelta,
) -> None:
    now = datetime(2026, 8, 14, 7, 0, tzinfo=UTC)
    ledger = ForwardLedger(tmp_path / "forward.sqlite3", now=now - timedelta(days=1))
    _heartbeat(ledger, now)
    _news(
        ledger,
        now - timedelta(minutes=6),
        publication_delay=publication_delay,
    )

    health = news_pipeline_health.news_semantic_pipeline_health(
        ledger, observed_at=now,
    )

    assert health["status"] == "UNHEALTHY"
    assert health["reason_codes"] == ("ACTIONABLE_NEWS_SEMANTICS_PENDING",)
    assert health["actionable_failure_counts"] == {}


@pytest.mark.parametrize(
    ("work_lane", "lane_classified"),
    ((CONTRACT_BACKFILL_LANE, 1), (LIVE_LANE, 0)),
)
def test_non_live_annotation_origin_does_not_close_current_gate(
    tmp_path, credentials, work_lane: str, lane_classified: int,
) -> None:
    now = datetime(2026, 8, 14, 7, 0, tzinfo=UTC)
    ledger = ForwardLedger(tmp_path / "forward.sqlite3", now=now - timedelta(days=1))
    _heartbeat(ledger, now)
    _news(
        ledger, now - timedelta(minutes=6), work_lane=work_lane,
    )
    if not lane_classified:
        with ledger.connection:
            ledger.connection.execute(
                """UPDATE news_ai_jobs_v1 SET lane_classified=0
                   WHERE task_type='ACTIVE_ANNOTATION'"""
            )

    health = news_pipeline_health.news_semantic_pipeline_health(
        ledger, observed_at=now,
    )

    assert health["status"] == "HEALTHY"
    assert health["reason_codes"] == ()
    assert health["unresolved_items"] == 0


def test_expired_failed_candidate_does_not_hold_the_gate_closed(
    tmp_path, credentials,
) -> None:
    now = datetime(2026, 8, 14, 7, 0, tzinfo=UTC)
    ledger = ForwardLedger(tmp_path / "forward.sqlite3", now=now - timedelta(days=1))
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
    ledger = ForwardLedger(tmp_path / "forward.sqlite3", now=now - timedelta(days=1))
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
    assert health["actionable_failure_counts"] == {
        "ACTIVE_ANNOTATION": {"UNCLASSIFIED": 1},
    }


def test_late_discovery_model_failure_still_closes_semantic_gate(
    tmp_path, credentials,
) -> None:
    now = datetime(2026, 8, 14, 7, 0, tzinfo=UTC)
    ledger = ForwardLedger(
        tmp_path / "forward.sqlite3", now=now - timedelta(hours=3),
    )
    body = "Complete but late macroeconomic report. " * 20
    received = now - timedelta(minutes=10)
    ledger.append_news_revision({
        "source": "google_news_us_inflation", "source_item_id": "late",
        "source_published_time": received - timedelta(hours=2),
        "collector_first_seen_time": received, "fetched_time": received,
        "headline": "Old CPI report collected recently", "body": body,
        "link": "https://example.test/late",
        "content_hash": hashlib.sha256(body.encode()).hexdigest(),
        "cluster_id": "late-cluster",
    })
    job_id = enqueue_job(
        ledger.connection, task_type="ACTIVE_ANNOTATION",
        source="google_news_us_inflation", source_item_id="late",
        revision_number=1, prompt_version=PROMPT_VERSION,
        priority="NORMAL", now=received,
    )
    with ledger.connection:
        ledger.connection.execute(
            "UPDATE news_ai_jobs_v1 SET state='BACKING_OFF' WHERE job_id=?",
            (job_id,),
        )
    _heartbeat(ledger, now)

    health = news_pipeline_health.news_semantic_pipeline_health(
        ledger, observed_at=now,
    )

    assert health["status"] == "UNHEALTHY"
    assert health["reason_codes"] == ("ACTIONABLE_NEWS_SEMANTICS_PENDING",)
    assert health["actionable_failure_counts"] == {
        "ACTIVE_ANNOTATION": {"UNCLASSIFIED": 1},
    }


def test_superseded_annotation_failure_does_not_close_semantic_gate(
    tmp_path, credentials,
) -> None:
    now = datetime(2026, 8, 14, 7, 0, tzinfo=UTC)
    ledger = ForwardLedger(
        tmp_path / "forward.sqlite3", now=now - timedelta(days=1),
    )
    received = now - timedelta(minutes=10)
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
    newer_body = "Corrected material macroeconomic report. " * 20
    ledger.append_news_revision({
        "source": "test_semantic_source", "source_item_id": "item-1",
        "source_published_time": now - timedelta(minutes=3),
        "collector_first_seen_time": now - timedelta(minutes=2),
        "fetched_time": now - timedelta(minutes=2),
        "headline": "Corrected macroeconomic report", "body": newer_body,
        "link": "https://example.test/report",
        "content_hash": hashlib.sha256(newer_body.encode()).hexdigest(),
        "cluster_id": "cluster-1",
    })
    _heartbeat(ledger, now)

    health = news_pipeline_health.news_semantic_pipeline_health(
        ledger, observed_at=now,
    )

    assert health["status"] == "HEALTHY"
    assert health["unresolved_items"] == 0


def test_recent_actionable_impact_pending_past_grace_fails_closed(
    tmp_path, credentials,
) -> None:
    now = datetime(2026, 8, 14, 7, 0, tzinfo=UTC)
    ledger = ForwardLedger(tmp_path / "forward.sqlite3", now=now - timedelta(days=1))
    _heartbeat(ledger, now)
    _impact_candidate(
        ledger, published_at=now - timedelta(minutes=7),
        received_at=now - timedelta(minutes=7),
        parsed_at=now - timedelta(minutes=6),
    )

    health = news_pipeline_health.news_semantic_pipeline_health(
        ledger, observed_at=now,
    )

    assert health["status"] == "UNHEALTHY"
    assert health["reason_codes"] == ("ACTIONABLE_NEWS_IMPACT_PENDING",)
    assert health["actionable_failure_counts"] == {}


def test_impact_gate_derives_supported_models_from_registry(
    tmp_path, credentials, monkeypatch,
) -> None:
    now = datetime(2026, 8, 14, 7, 0, tzinfo=UTC)
    ledger = ForwardLedger(tmp_path / "forward.sqlite3", now=now - timedelta(days=1))
    _heartbeat(ledger, now)
    future_model = "gemini-future-compatible"
    monkeypatch.setattr(
        news_pipeline_health, "SUPPORTED_GEMINI_MODELS",
        (*news_pipeline_health.SUPPORTED_GEMINI_MODELS, future_model),
    )
    monkeypatch.setattr(
        news_pipeline_health, "pending_annotation_records", lambda *_a, **_k: [],
    )
    _impact_candidate(
        ledger, published_at=now - timedelta(minutes=7),
        received_at=now - timedelta(minutes=7),
        parsed_at=now - timedelta(minutes=6), model_version=future_model,
    )

    health = news_pipeline_health.news_semantic_pipeline_health(
        ledger, observed_at=now,
    )

    assert health["status"] == "UNHEALTHY"
    assert health["reason_codes"] == ("ACTIONABLE_NEWS_IMPACT_PENDING",)


def test_recent_actionable_impact_within_grace_keeps_gate_healthy(
    tmp_path, credentials,
) -> None:
    now = datetime(2026, 8, 14, 7, 0, tzinfo=UTC)
    ledger = ForwardLedger(tmp_path / "forward.sqlite3", now=now - timedelta(days=1))
    _heartbeat(ledger, now)
    _impact_candidate(
        ledger, published_at=now - timedelta(minutes=7),
        received_at=now - timedelta(minutes=7),
        parsed_at=now - timedelta(minutes=2),
    )

    health = news_pipeline_health.news_semantic_pipeline_health(
        ledger, observed_at=now,
    )

    assert health["status"] == "HEALTHY"


def test_recent_non_actionable_impact_backlog_does_not_close_gate(
    tmp_path, credentials,
) -> None:
    now = datetime(2026, 8, 14, 7, 0, tzinfo=UTC)
    ledger = ForwardLedger(tmp_path / "forward.sqlite3", now=now - timedelta(days=1))
    _heartbeat(ledger, now)
    _impact_candidate(
        ledger, published_at=now - timedelta(minutes=7),
        received_at=now - timedelta(minutes=7),
        parsed_at=now - timedelta(minutes=6),
        annotation_overrides={
            "primary_category": "regulation_other",
            "xauusd_relevance": "IRRELEVANT",
            "review_priority": "BACKGROUND",
            "materiality": 0.1,
        },
    )

    health = news_pipeline_health.news_semantic_pipeline_health(
        ledger, observed_at=now,
    )

    assert health["status"] == "HEALTHY"


def test_recent_actionable_impact_backoff_fails_closed_immediately(
    tmp_path, credentials,
) -> None:
    now = datetime(2026, 8, 14, 7, 0, tzinfo=UTC)
    ledger = ForwardLedger(tmp_path / "forward.sqlite3", now=now - timedelta(days=1))
    _heartbeat(ledger, now)
    _impact_candidate(
        ledger, published_at=now - timedelta(minutes=2),
        received_at=now - timedelta(minutes=2), parsed_at=now - timedelta(minutes=1),
    )
    job_id = enqueue_job(
        ledger.connection, task_type="ACTIVE_IMPACT",
        source="impact-health-source", source_item_id="impact-item",
        revision_number=1, annotation_id="impact-annotation",
        prompt_version=IMPACT_PROMPT_VERSION, priority="FAST", now=now,
    )
    job = claim_job(
        ledger.connection, worker_id="health-test", pool=ROUTINE_POOL, now=now,
    )
    assert job is not None and job.job_id == job_id
    credential = ApiCredential(
        "test-account", ROUTINE_POOL, "not-a-real-key", "test-credential",
    )
    record_job_attempt(
        ledger.connection, job=job, credential=credential,
        status={
            "status": "ERROR", "failure_code": "PROVIDER_UNAVAILABLE",
            "provider_http_status": 503,
        },
        attempted_at=now,
    )
    with ledger.connection:
        ledger.connection.execute(
            """UPDATE news_ai_jobs_v1
               SET state='BACKING_OFF',available_at=?,
                   lease_owner=NULL,lease_expires_at=NULL
               WHERE job_id=?""",
            ((now + timedelta(minutes=1)).isoformat(), job_id),
        )

    health = news_pipeline_health.news_semantic_pipeline_health(
        ledger, observed_at=now,
    )

    assert health["status"] == "UNHEALTHY"
    assert health["reason_codes"] == (
        "ACTIONABLE_NEWS_IMPACT_PENDING",
        "ACTIONABLE_NEWS_IMPACT_RECOVERING",
    )
    assert health["actionable_failure_counts"] == {
        "ACTIVE_IMPACT": {"PROVIDER_UNAVAILABLE": 1},
    }
    assert health["unresolved_annotation_count"] == 0
    assert health["unresolved_impact_count"] == 1
    assert health["recovering_count"] == 1
    assert health["terminal_or_overdue_count"] == 0


def test_terminal_actionable_impact_remains_operator_error(
    tmp_path, credentials,
) -> None:
    now = datetime(2026, 8, 14, 7, 0, tzinfo=UTC)
    ledger = ForwardLedger(tmp_path / "forward.sqlite3", now=now - timedelta(days=1))
    _heartbeat(ledger, now)
    _impact_candidate(
        ledger, published_at=now - timedelta(minutes=7),
        received_at=now - timedelta(minutes=7),
        parsed_at=now - timedelta(minutes=6),
    )
    job_id = enqueue_job(
        ledger.connection, task_type="ACTIVE_IMPACT",
        source="impact-health-source", source_item_id="impact-item",
        revision_number=1, annotation_id="impact-annotation",
        prompt_version=IMPACT_PROMPT_VERSION, priority="FAST", now=now,
    )
    with ledger.connection:
        ledger.connection.execute(
            "UPDATE news_ai_jobs_v1 SET state='DEAD_LETTER' WHERE job_id=?",
            (job_id,),
        )

    health = news_pipeline_health.news_semantic_pipeline_health(
        ledger, observed_at=now,
    )

    assert health["status"] == "UNHEALTHY"
    assert health["reason_codes"] == (
        "ACTIONABLE_NEWS_IMPACT_PENDING",
        "ACTIONABLE_NEWS_IMPACT_TERMINAL",
    )
    assert health["recovering_count"] == 0
    assert health["terminal_or_overdue_count"] == 1


def test_current_actionable_impact_backfill_does_not_close_current_gate(
    tmp_path, credentials,
) -> None:
    now = datetime(2026, 8, 14, 7, 0, tzinfo=UTC)
    ledger = ForwardLedger(tmp_path / "forward.sqlite3", now=now - timedelta(days=1))
    _heartbeat(ledger, now)
    _impact_candidate(
        ledger, published_at=now - timedelta(minutes=7),
        received_at=now - timedelta(minutes=7),
        parsed_at=now - timedelta(minutes=6),
        work_lane=CONTRACT_BACKFILL_LANE,
    )
    enqueue_job(
        ledger.connection,
        task_type="ACTIVE_IMPACT",
        source="impact-health-source",
        source_item_id="impact-item",
        revision_number=1,
        annotation_id="impact-annotation",
        prompt_version=IMPACT_PROMPT_VERSION,
        priority="BACKGROUND",
        work_lane=CONTRACT_BACKFILL_LANE,
        provenance=WorkProvenance(
            CONTRACT_BACKFILL_LANE, "ACTIVE_ANNOTATION", "backfill-parent",
        ),
        now=now - timedelta(minutes=6),
    )

    health = news_pipeline_health.news_semantic_pipeline_health(
        ledger, observed_at=now,
    )

    assert health["status"] == "HEALTHY"
    assert health["unresolved_items"] == 0


def test_completed_recent_impact_keeps_current_gate_healthy(
    tmp_path, credentials,
) -> None:
    now = datetime(2026, 8, 14, 7, 0, tzinfo=UTC)
    ledger = ForwardLedger(tmp_path / "forward.sqlite3", now=now - timedelta(days=1))
    _heartbeat(ledger, now)
    _impact_candidate(
        ledger, published_at=now - timedelta(minutes=7),
        received_at=now - timedelta(minutes=7),
        parsed_at=now - timedelta(minutes=6),
    )
    _complete_impact(ledger, now - timedelta(minutes=5))

    health = news_pipeline_health.news_semantic_pipeline_health(
        ledger, observed_at=now,
    )

    assert health["status"] == "HEALTHY"
    assert health["unresolved_items"] == 0


@pytest.mark.parametrize("failure", ["missing", "stale", "credentials"])
def test_runtime_dependencies_fail_closed(tmp_path, monkeypatch, failure: str) -> None:
    now = datetime(2026, 8, 14, 7, 0, tzinfo=UTC)
    ledger = ForwardLedger(tmp_path / "forward.sqlite3", now=now - timedelta(days=1))
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
