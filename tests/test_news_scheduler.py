from __future__ import annotations

import json
import hashlib
import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from xauusd_forecaster.news_scheduler import (
    ApiCredential,
    PREEMPTIBLE_POOL,
    ROUTINE_POOL,
    account_quota_snapshot,
    backoff_job,
    claim_job,
    complete_job,
    configured_api_credentials,
    enqueue_job,
    install_scheduler_schema,
    reserve_account_request,
    scheduler_counts,
    sync_pending_jobs,
)
from xauusd_forecaster.forward_ledger import ForwardLedger
from xauusd_forecaster.news_semantics import (
    CURRENT_NEWS_PROMPT_VERSION,
)


NOW = datetime(2026, 8, 12, 2, 0, tzinfo=UTC)


def _connection() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    install_scheduler_schema(connection)
    return connection


def _enqueue(
    connection: sqlite3.Connection,
    item: str,
    *,
    priority: str = "NORMAL",
    task_type: str = "ACTIVE_ANNOTATION",
) -> str:
    return enqueue_job(
        connection,
        task_type=task_type,
        source="source",
        source_item_id=item,
        revision_number=1,
        prompt_version="prompt",
        priority=priority,
        now=NOW,
    )


def test_account_configuration_groups_keys_without_exposing_secrets() -> None:
    credentials = configured_api_credentials(raw_accounts=json.dumps([
        {"account_id": "routine-a", "pool": "routine", "api_keys": ["key-a", "key-b"]},
        {"account_id": "urgent-a", "pool": "preemptible", "api_keys": ["key-c"]},
    ]))

    assert [(item.account_id, item.pool) for item in credentials] == [
        ("routine-a", ROUTINE_POOL),
        ("routine-a", ROUTINE_POOL),
        ("urgent-a", PREEMPTIBLE_POOL),
    ]
    assert all("key-" not in item.credential_id for item in credentials)


def test_account_quota_snapshot_uses_scheduler_usage_without_double_counting() -> None:
    connection = _connection()
    credentials = configured_api_credentials(raw_accounts=json.dumps([
        {"account_id": "shared", "pool": "routine", "api_keys": ["a", "b"]},
        {"account_id": "single", "pool": "routine", "api_keys": ["c"]},
    ]))
    for family in ("gemma-impact", "gemma-title"):
        assert reserve_account_request(
            connection, account_id="shared", model_family=family,
            daily_limit=15_000, requests_per_minute=10, now=NOW,
        )
    assert reserve_account_request(
        connection, account_id="single", model_family="gemma-impact",
        daily_limit=15_000, requests_per_minute=10, now=NOW,
    )

    snapshot = account_quota_snapshot(
        connection, credentials,
        model_families=("gemma-impact", "gemma-title"),
        daily_limit=15_000, now=NOW,
    )

    assert snapshot["accounting_source"] == "SCHEDULER_DB"
    assert snapshot["quota_day_pacific"] == "2026-08-11"
    assert snapshot["total_sent"] == 3
    assert [row["sent"] for row in snapshot["keys"]] == [2, 1]
    assert len(snapshot["keys"]) == 2
    assert all("account_id" not in row for row in snapshot["keys"])


def test_gemma_minute_budget_is_shared_across_tasks_and_keys() -> None:
    connection = _connection()
    shared = ("gemma-impact", "gemma-title")
    common = {
        "daily_limit": 15_000,
        "requests_per_minute": 20,
        "input_tokens_per_minute": 15_000,
        "shared_model_families": shared,
        "share_minute_across_accounts": True,
        "now": NOW,
    }

    assert reserve_account_request(
        connection, account_id="key-a", model_family="gemma-impact",
        input_tokens=9_000, **common,
    )
    assert not reserve_account_request(
        connection, account_id="key-b", model_family="gemma-title",
        input_tokens=6_001, **common,
    )
    assert reserve_account_request(
        connection, account_id="key-b", model_family="gemma-title",
        input_tokens=6_000, **common,
    )


def test_gemma_budget_keeps_previous_bucket_to_prevent_boundary_burst() -> None:
    connection = _connection()
    common = {
        "account_id": "key-a", "model_family": "gemma-impact",
        "daily_limit": 15_000, "requests_per_minute": 20,
        "input_tokens_per_minute": 15_000,
        "shared_model_families": ("gemma-impact", "gemma-title"),
        "share_minute_across_accounts": True,
    }
    before_boundary = NOW.replace(second=59)
    assert reserve_account_request(
        connection, input_tokens=9_000, now=before_boundary, **common,
    )
    assert not reserve_account_request(
        connection, input_tokens=6_001,
        now=before_boundary + timedelta(seconds=2), **common,
    )


def test_account_configuration_rejects_one_key_in_two_accounts() -> None:
    with pytest.raises(ValueError, match="two accounts"):
        configured_api_credentials(raw_accounts=json.dumps([
            {"account_id": "a", "pool": "routine", "api_keys": ["same"]},
            {"account_id": "b", "pool": "routine", "api_keys": ["same"]},
        ]))


def test_legacy_keys_are_independent_routine_accounts() -> None:
    credentials = configured_api_credentials(legacy_keys=("a", "b", "a"))

    assert len(credentials) == 2
    assert len({item.account_id for item in credentials}) == 2
    assert {item.pool for item in credentials} == {ROUTINE_POOL}


def test_enqueue_is_idempotent_and_lease_is_exclusive() -> None:
    connection = _connection()
    first = _enqueue(connection, "one")
    second = _enqueue(connection, "one")

    assert first == second
    job = claim_job(connection, worker_id="worker-a", pool=ROUTINE_POOL, now=NOW)
    assert job and job.job_id == first and job.attempt_count == 1
    assert claim_job(connection, worker_id="worker-b", pool=ROUTINE_POOL, now=NOW) is None
    with pytest.raises(ValueError, match="not owned"):
        complete_job(connection, first, "worker-b", now=NOW)
    complete_job(connection, first, "worker-a", now=NOW)
    assert scheduler_counts(connection)["completed"] == 1


def test_expired_lease_recovers_and_backoff_respects_available_time() -> None:
    connection = _connection()
    job_id = _enqueue(connection, "one")
    first = claim_job(
        connection, worker_id="worker-a", pool=ROUTINE_POOL,
        now=NOW, lease_seconds=30,
    )
    assert first
    recovered = claim_job(
        connection, worker_id="worker-b", pool=ROUTINE_POOL,
        now=NOW + timedelta(seconds=31),
    )
    assert recovered and recovered.attempt_count == 2
    available = NOW + timedelta(minutes=5)
    backoff_job(
        connection, job_id, "worker-b", available_at=available, error="retry",
    )
    assert claim_job(
        connection, worker_id="worker-c", pool=ROUTINE_POOL,
        now=available - timedelta(seconds=1),
    ) is None
    assert claim_job(
        connection, worker_id="worker-c", pool=ROUTINE_POOL, now=available,
    )


def test_preemptible_claims_ai_urgent_only_and_routine_accepts_overflow() -> None:
    connection = _connection()
    _enqueue(connection, "normal", priority="NORMAL")
    urgent_id = _enqueue(
        connection, "urgent", priority="IMMEDIATE", task_type="ACTIVE_IMPACT",
    )

    urgent = claim_job(
        connection, worker_id="preemptible", pool=PREEMPTIBLE_POOL, now=NOW,
    )
    assert urgent and urgent.job_id == urgent_id
    complete_job(connection, urgent.job_id, "preemptible", now=NOW)
    normal = claim_job(connection, worker_id="routine", pool=ROUTINE_POOL, now=NOW)
    assert normal and normal.priority == "NORMAL"

    overflow_id = _enqueue(connection, "overflow", priority="FAST")
    complete_job(connection, normal.job_id, "routine", now=NOW)
    overflow = claim_job(connection, worker_id="routine", pool=ROUTINE_POOL, now=NOW)
    assert overflow and overflow.job_id == overflow_id


def test_account_quota_is_shared_by_keys_and_reserve_is_urgent_only() -> None:
    connection = _connection()
    common = {
        "connection": connection,
        "account_id": "shared-account",
        "model_family": "gemini",
        "daily_limit": 3,
        "requests_per_minute": 10,
        "reserve_total": 1,
        "now": NOW,
    }

    assert reserve_account_request(**common)
    assert reserve_account_request(**common)
    assert not reserve_account_request(**common)
    assert reserve_account_request(**common, urgent=True)
    assert not reserve_account_request(**common, urgent=True)


def test_dead_letter_is_terminal() -> None:
    connection = _connection()
    job_id = _enqueue(connection, "one")
    assert claim_job(connection, worker_id="worker", pool=ROUTINE_POOL, now=NOW)
    backoff_job(
        connection, job_id, "worker", available_at=NOW,
        error="terminal", terminal=True,
    )

    assert claim_job(connection, worker_id="next", pool=ROUTINE_POOL, now=NOW) is None
    assert scheduler_counts(connection)["dead_letter"] == 1


def test_sync_uses_v15_semantic_priority_not_headline_keywords(tmp_path) -> None:
    body = "The agency reported that its ordinary administrative update took effect. " * 8
    digest = hashlib.sha256(body.encode()).hexdigest()
    ledger = ForwardLedger(tmp_path / "forward.sqlite3", now=NOW)
    ledger.append_news_revision({
        "source": "semantic-scheduler-test",
        "source_item_id": "ordinary-headline",
        "source_published_time": NOW,
        "collector_first_seen_time": NOW,
        "fetched_time": NOW,
        "headline": "Agency publishes an administrative update",
        "body": body,
        "content_hash": digest,
        "cluster_id": "ordinary-headline",
    })
    target = {
        "headline_zh": "机构发布行政更新",
        "summary_zh": "该机构报告一项行政更新已经生效，并提供完整正文依据。",
        "primary_category": "regulation_other",
        "secondary_categories": [],
        "emerging_topic_zh": "行政更新",
        "record_kind": "FACT_EVENT",
        "actor": "agency", "action": "reported", "object": "administrative update",
        "location": "", "event_time": "2026-08-12", "claim_status": "OFFICIAL",
        "materiality": 0.6, "canonical_actor_id": "agency",
        "action_family": "ECONOMIC_RELEASE",
        "canonical_object_id": "administrative_update",
        "canonical_location_id": "", "episode_key": "administrative_update",
        "primary_story_title_zh": "行政更新", "secondary_contexts_zh": [],
        "relation_to_prior": "NONE", "document_kind": "REPORT",
        "material_event_key": "administrative_update_2026_08_12",
        "source_organization_id": "agency", "evidence_role": "CORE_CLAIM",
        "event_type": "economic_release", "entities": ["agency"],
        "hawkishness": 0.0, "inflation_impulse": 0.0, "growth_impulse": 0.0,
        "geopolitical_risk": 0.0, "usd_impulse": 0.0, "novelty": 0.7,
        "confidence": 0.8, "xauusd_relevance": "MACRO_DRIVER",
        "review_priority": "FAST", "material_change": "NEW_EVENT",
        "time_sensitivity": "SAME_DAY",
        "semantic_reason_zh": "完整正文显示行政更新已经生效。",
        "supporting_evidence": ["administrative update took effect"],
    }
    common = {
        "source": "semantic-scheduler-test",
        "source_item_id": "ordinary-headline",
        "revision_number": 1,
        "raw_content_hash": digest,
        "llm_model_version": "gemini-3.5-flash-lite",
        "parse_started_at": NOW,
        "parsed_at": NOW,
    }
    ledger.append_annotation({
        **common, "annotation_id": "active", "annotation": target,
        "prompt_version": CURRENT_NEWS_PROMPT_VERSION,
    })

    sync_pending_jobs(ledger.connection, now=NOW)
    job = claim_job(
        ledger.connection, worker_id="urgent", pool=PREEMPTIBLE_POOL, now=NOW,
    )

    assert job and job.task_type == "ACTIVE_IMPACT"
    assert job.priority == "FAST"
    assert job.annotation_id == "active"
    ledger.close()


def test_impact_discovery_advances_old_backfill_and_new_arrivals(
    tmp_path, monkeypatch,
) -> None:
    import xauusd_forecaster.annotation as annotation

    ledger = ForwardLedger(tmp_path / "forward.sqlite3", now=NOW)
    calls = []

    def impact_rows(_connection, *, limit, selection_order, **_kwargs):
        calls.append((selection_order, limit))
        item = "oldest" if selection_order == "oldest" else "newest"
        return [{
            "source": "scheduler-fairness",
            "source_item_id": item,
            "revision_number": 1,
            "annotation_id": f"annotation-{item}",
            "annotation": {"review_priority": "NORMAL"},
        }]

    monkeypatch.setattr(annotation, "pending_annotation_records", lambda *_a, **_k: [])
    monkeypatch.setattr(annotation, "pending_title_translation_records", lambda *_a, **_k: [])
    monkeypatch.setattr(annotation, "pending_impact_records", impact_rows)

    discovered = sync_pending_jobs(ledger.connection, now=NOW, limit=4)
    queued = {
        row[0] for row in ledger.connection.execute(
            "SELECT source_item_id FROM news_ai_jobs_v1 WHERE task_type='ACTIVE_IMPACT'"
        ).fetchall()
    }

    assert calls == [("oldest", 2), ("newest", 2)]
    assert discovered["ACTIVE_IMPACT"] == 2
    assert queued == {"oldest", "newest"}


def test_preemptible_quota_deferral_flows_to_routine_account(
    tmp_path, monkeypatch,
) -> None:
    from scripts import run_news_annotator as runner

    available_now = datetime.now(UTC) - timedelta(seconds=1)
    ledger = ForwardLedger(tmp_path / "forward.sqlite3", now=NOW)
    job_id = enqueue_job(
        ledger.connection,
        task_type="ACTIVE_IMPACT",
        source="source",
        source_item_id="urgent",
        revision_number=1,
        annotation_id="annotation",
        prompt_version="prompt",
        priority="FAST",
        now=available_now,
    )
    credentials = (
        ApiCredential("urgent-account", PREEMPTIBLE_POOL, "urgent-key", "urgent"),
        ApiCredential("routine-account", ROUTINE_POOL, "routine-key", "routine"),
    )
    monkeypatch.setattr(runner, "configured_api_credentials", lambda: credentials)
    monkeypatch.setattr(runner, "sync_pending_jobs", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(
        runner,
        "_execute_job",
        lambda _ledger, credential, _job, **_kwargs: (
            {"status": "DEFERRED", "reason": "quota"}
            if credential.pool == PREEMPTIBLE_POOL else {"status": "OK"}
        ),
    )

    progress = []
    statuses = runner.run_scheduled_batch(
        ledger, batch_size=2, progress_callback=progress.append,
    )

    assert [status["pool"] for status in statuses] == [
        PREEMPTIBLE_POOL, ROUTINE_POOL,
    ]
    assert progress == [1, 2]
    assert ledger.connection.execute(
        "SELECT state FROM news_ai_jobs_v1 WHERE job_id=?", (job_id,),
    ).fetchone()["state"] == "COMPLETED"
    ledger.close()
