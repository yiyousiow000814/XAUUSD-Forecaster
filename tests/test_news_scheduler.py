from __future__ import annotations

import json
import hashlib
import sqlite3
import threading
import time
from datetime import UTC, datetime, timedelta

import pytest

import xauusd_forecaster.news_scheduler as news_scheduler_module
from xauusd_forecaster.news_scheduler import (
    ApiCredential,
    PREEMPTIBLE_POOL,
    ROUTINE_POOL,
    account_quota_snapshot,
    authorize_repairable_annotation_failures,
    authorize_repairable_impact_failures,
    backoff_job,
    claim_job,
    complete_job,
    configured_api_credentials,
    enqueue_job,
    install_scheduler_schema,
    rank_accounts_for_models,
    reconcile_completed_jobs,
    reserve_account_request,
    scheduler_counts,
    sync_pending_jobs,
)
from xauusd_forecaster.annotation import (
    ANNOTATION_FAILURE_RECOVERY_VERSION,
    IMPACT_FAILURE_RECOVERY_VERSION,
    IMPACT_MODEL,
    IMPACT_PROMPT_VERSION,
    _append_impact_failure,
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


def test_identity_contract_recovery_requeues_each_impact_only_once(tmp_path) -> None:
    ledger = ForwardLedger(tmp_path / "forward.sqlite3", now=NOW)
    ledger.connection.execute("PRAGMA foreign_keys=OFF")
    job_id = enqueue_job(
        ledger.connection, task_type="ACTIVE_IMPACT",
        source="source", source_item_id="item", revision_number=1,
        annotation_id="annotation", prompt_version=IMPACT_PROMPT_VERSION,
        priority="NORMAL", now=NOW,
    )
    row = {
        "source": "source", "source_item_id": "item", "revision_number": 1,
        "content_hash": "hash", "annotation_id": "annotation",
    }
    error = ValueError("New-episode identity requires an anchor difference")
    _append_impact_failure(ledger, row, error, model_version=IMPACT_MODEL)
    _append_impact_failure(ledger, row, error, model_version=IMPACT_MODEL)
    assert claim_job(
        ledger.connection, worker_id="worker", pool=ROUTINE_POOL, now=NOW,
    ) is not None
    backoff_job(
        ledger.connection, job_id, "worker", available_at=NOW,
        error=str(error), terminal=True,
    )

    recovery_at = datetime.now(UTC)
    assert authorize_repairable_impact_failures(
        ledger.connection,
        prompt_version=IMPACT_PROMPT_VERSION,
        recovery_version=IMPACT_FAILURE_RECOVERY_VERSION,
        now=recovery_at,
    ) == 1
    assert ledger.connection.execute(
        "SELECT state FROM news_ai_jobs_v1 WHERE job_id=?", (job_id,),
    ).fetchone()[0] == "QUEUED"

    assert claim_job(
        ledger.connection, worker_id="worker", pool=ROUTINE_POOL,
        now=recovery_at,
    ) is not None
    backoff_job(
        ledger.connection, job_id, "worker", available_at=NOW,
        error=str(error), terminal=True,
    )
    assert authorize_repairable_impact_failures(
        ledger.connection,
        prompt_version=IMPACT_PROMPT_VERSION,
        recovery_version=IMPACT_FAILURE_RECOVERY_VERSION,
        now=recovery_at + timedelta(minutes=1),
    ) == 0
    assert ledger.connection.execute(
        "SELECT state FROM news_ai_jobs_v1 WHERE job_id=?", (job_id,),
    ).fetchone()[0] == "DEAD_LETTER"
    ledger.close()


def test_contract_recovery_requeues_each_annotation_only_once(tmp_path) -> None:
    ledger = ForwardLedger(tmp_path / "forward.sqlite3", now=NOW)
    body = "Complete source evidence for one bounded recovery attempt. " * 12
    digest = hashlib.sha256(body.encode()).hexdigest()
    ledger.append_news_revision({
        "source": "source", "source_item_id": "item",
        "source_published_time": NOW, "collector_first_seen_time": NOW,
        "fetched_time": NOW, "headline": "Report", "body": body,
        "content_hash": digest, "cluster_id": "item",
    })
    job_id = enqueue_job(
        ledger.connection, task_type="ACTIVE_ANNOTATION",
        source="source", source_item_id="item", revision_number=1,
        prompt_version=CURRENT_NEWS_PROMPT_VERSION,
        priority="NORMAL", now=NOW,
    )
    cause = "annotation supporting evidence is absent from source"
    ledger.append_llm_failure({
        "failure_id": "failure", "task_type": "ANNOTATION",
        "source": "source", "source_item_id": "item", "revision_number": 1,
        "raw_content_hash": digest, "llm_model_version": "model",
        "prompt_version": CURRENT_NEWS_PROMPT_VERSION, "attempt_number": 1,
        "error_type": "ValueError",
        "error_signature": hashlib.sha256(cause.encode()).hexdigest(),
        "error": cause, "failed_at": NOW, "is_terminal": True,
        "failure_evidence": {
            "failure_code": "MODEL_OUTPUT_CONTRACT_FAILED",
            "failure_stage": "SEMANTIC_CONTRACT", "response_hash": "a" * 64,
            "selected_output": {}, "cause_type": "ValueError", "cause": cause,
        },
    })
    assert claim_job(
        ledger.connection, worker_id="worker", pool=ROUTINE_POOL, now=NOW,
    ) is not None
    backoff_job(
        ledger.connection, job_id, "worker", available_at=NOW,
        error=cause, terminal=True,
    )

    recovery_at = datetime.now(UTC)
    assert authorize_repairable_annotation_failures(
        ledger.connection,
        prompt_version=CURRENT_NEWS_PROMPT_VERSION,
        recovery_version=ANNOTATION_FAILURE_RECOVERY_VERSION,
        now=recovery_at,
    ) == 1
    assert claim_job(
        ledger.connection, worker_id="worker", pool=ROUTINE_POOL,
        now=recovery_at,
    ) is not None
    backoff_job(
        ledger.connection, job_id, "worker", available_at=NOW,
        error=cause, terminal=True,
    )

    assert authorize_repairable_annotation_failures(
        ledger.connection,
        prompt_version=CURRENT_NEWS_PROMPT_VERSION,
        recovery_version=ANNOTATION_FAILURE_RECOVERY_VERSION,
        now=recovery_at + timedelta(minutes=1),
    ) == 0
    assert ledger.connection.execute(
        "SELECT state FROM news_ai_jobs_v1 WHERE job_id=?", (job_id,),
    ).fetchone()[0] == "DEAD_LETTER"
    ledger.close()


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


def test_gemma_budget_uses_an_exact_trailing_sixty_second_window() -> None:
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
    assert reserve_account_request(
        connection, input_tokens=6_001,
        now=before_boundary + timedelta(seconds=61), **common,
    )


def test_exact_window_migration_preserves_recent_legacy_usage() -> None:
    connection = _connection()
    now = datetime.now(UTC)
    with connection:
        connection.execute(
            "DELETE FROM news_ai_scheduler_migrations_v1"
        )
        connection.execute(
            """INSERT INTO news_ai_account_minute_usage_v1
               VALUES (?,?,?,?,?,?)""",
            (
                news_scheduler_module.minute_bucket(now), "account-a",
                "gemma-impact", 3, 14_000, now.isoformat(),
            ),
        )

    install_scheduler_schema(connection)

    assert not reserve_account_request(
        connection, account_id="account-a", model_family="gemma-impact",
        daily_limit=15_000, requests_per_minute=20,
        input_tokens=1_001, input_tokens_per_minute=15_000, now=now,
    )


def test_gemini_model_tpm_is_shared_across_keys_in_one_project() -> None:
    connection = _connection()
    common = {
        "model_family": "gemini-3.5-flash-lite",
        "daily_limit": 500, "requests_per_minute": 12,
        "input_tokens_per_minute": 225_000,
        "share_minute_across_accounts": True, "now": NOW,
    }
    assert reserve_account_request(
        connection, account_id="key-a", input_tokens=200_000, **common,
    )
    assert reserve_account_request(
        connection, account_id="key-b", input_tokens=25_000, **common,
    )
    assert not reserve_account_request(
        connection, account_id="key-c", input_tokens=1, **common,
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


def test_runtime_credentials_hot_reload_user_configuration(monkeypatch) -> None:
    configured = {
        "GEMINI_API_ACCOUNTS": "",
        "GEMINI_API_KEYS": "key-a",
        "GEMINI_API_KEY": "",
    }
    monkeypatch.setattr(
        news_scheduler_module, "_runtime_environment_value",
        lambda name: configured.get(name, ""),
    )

    first = configured_api_credentials()
    configured["GEMINI_API_KEYS"] = "key-a;key-b"
    second = configured_api_credentials()

    assert len(first) == 1
    assert len(second) == 2
    assert {item.credential_id for item in first} < {
        item.credential_id for item in second
    }


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


def test_claim_job_can_preserve_capacity_by_selecting_task_family() -> None:
    connection = _connection()
    impact_id = _enqueue(
        connection, "impact", priority="IMMEDIATE", task_type="ACTIVE_IMPACT",
    )
    annotation_id = _enqueue(
        connection, "annotation", priority="NORMAL", task_type="ACTIVE_ANNOTATION",
    )

    claimed = claim_job(
        connection, worker_id="annotation-only", pool=ROUTINE_POOL, now=NOW,
        task_types=("ACTIVE_ANNOTATION",),
    )

    assert claimed and claimed.job_id == annotation_id
    queued = connection.execute(
        "SELECT state FROM news_ai_jobs_v1 WHERE job_id=?", (impact_id,),
    ).fetchone()
    assert queued["state"] == "QUEUED"

    assert claim_job(
        connection, worker_id="none", pool=ROUTINE_POOL, now=NOW,
        task_types=(),
    ) is None


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


@pytest.mark.parametrize(("stage", "cause"), (
    ("DISPLAY_REPAIR", "number mismatch"),
    (
        "SEMANTIC_CONTRACT",
        "annotation supporting evidence is absent from source",
    ),
))
def test_repair_version_reopens_matching_annotation_failure_only_once(
    tmp_path, stage, cause,
) -> None:
    ledger = ForwardLedger(tmp_path / "forward.sqlite3", now=NOW)
    body = "Complete source evidence for one bounded recovery attempt. " * 12
    digest = hashlib.sha256(body.encode()).hexdigest()
    ledger.append_news_revision({
        "source": "google_news_fed_rates", "source_item_id": "repair-once",
        "source_published_time": NOW, "collector_first_seen_time": NOW,
        "fetched_time": NOW, "headline": "Fed policy report", "body": body,
        "content_hash": digest, "cluster_id": "repair-once",
    })
    job_id = enqueue_job(
        ledger.connection, task_type="ACTIVE_ANNOTATION",
        source="google_news_fed_rates", source_item_id="repair-once",
        revision_number=1, prompt_version=CURRENT_NEWS_PROMPT_VERSION,
        priority="NORMAL", now=NOW,
    )
    first_claim = claim_job(
        ledger.connection, worker_id="old-worker", pool=ROUTINE_POOL, now=NOW,
    )
    assert first_claim and first_claim.job_id == job_id
    backoff_job(
        ledger.connection, job_id, "old-worker", available_at=NOW,
        error="display repair failed", terminal=True,
    )

    def append_failure(attempt: int, failure_id: str) -> None:
        ledger.append_llm_failure({
            "failure_id": failure_id, "task_type": "ANNOTATION",
            "source": "google_news_fed_rates", "source_item_id": "repair-once",
            "revision_number": 1, "raw_content_hash": digest,
            "llm_model_version": "gemini-3.5-flash-lite",
            "prompt_version": CURRENT_NEWS_PROMPT_VERSION,
            "attempt_number": attempt, "error_type": "ValueError",
            "error_signature": f"signature-{attempt}",
            "error": "display repair failed", "failed_at": NOW,
            "is_terminal": True,
            "failure_evidence": {
                "failure_code": "MODEL_OUTPUT_CONTRACT_FAILED",
                "failure_stage": stage,
                "response_hash": str(attempt) * 64,
                "selected_output": {"headline_zh": "bounded"},
                "cause_type": "ValueError", "cause": cause,
            },
        })

    append_failure(2, "old-display-failure")
    recovery_at = datetime.now(UTC)
    sync_pending_jobs(ledger.connection, now=recovery_at)
    recovered = ledger.connection.execute(
        "SELECT state FROM news_ai_jobs_v1 WHERE job_id=?", (job_id,),
    ).fetchone()
    assert recovered["state"] == "QUEUED"

    second_claim = claim_job(
        ledger.connection, worker_id="new-worker", pool=ROUTINE_POOL,
        now=recovery_at,
    )
    assert second_claim and second_claim.job_id == job_id
    backoff_job(
        ledger.connection, job_id, "new-worker", available_at=NOW,
        error="display repair still failed", terminal=True,
    )
    append_failure(3, "new-display-failure")
    sync_pending_jobs(ledger.connection, now=recovery_at + timedelta(minutes=1))
    stopped = ledger.connection.execute(
        "SELECT state FROM news_ai_jobs_v1 WHERE job_id=?", (job_id,),
    ).fetchone()
    receipts = ledger.connection.execute(
        "SELECT count(*) FROM news_ai_failure_recoveries_v1",
    ).fetchone()[0]
    assert stopped["state"] == "DEAD_LETTER"
    assert receipts == 1
    ledger.close()


def test_superseded_jobs_are_reconciled_without_another_model_attempt(tmp_path) -> None:
    ledger = ForwardLedger(tmp_path / "forward.sqlite3", now=NOW)
    first_body = "First complete article body. " * 20
    second_body = "Corrected complete article body. " * 20
    common = {
        "source": "revision-source",
        "source_item_id": "item",
        "source_published_time": NOW,
        "collector_first_seen_time": NOW,
        "headline": "Report",
        "link": "https://example.test/report",
        "cluster_id": "revision-cluster",
    }
    ledger.append_news_revision({
        **common, "fetched_time": NOW, "body": first_body,
        "content_hash": hashlib.sha256(first_body.encode()).hexdigest(),
    })
    job_id = enqueue_job(
        ledger.connection, task_type="ACTIVE_ANNOTATION",
        source="revision-source", source_item_id="item", revision_number=1,
        prompt_version="prompt", priority="NORMAL", now=NOW,
    )
    ledger.append_news_revision({
        **common, "fetched_time": NOW + timedelta(minutes=1), "body": second_body,
        "content_hash": hashlib.sha256(second_body.encode()).hexdigest(),
    })

    assert reconcile_completed_jobs(
        ledger.connection, now=NOW + timedelta(minutes=2),
    ) == 1
    row = ledger.connection.execute(
        "SELECT state,last_error FROM news_ai_jobs_v1 WHERE job_id=?", (job_id,),
    ).fetchone()
    assert tuple(row) == ("DEAD_LETTER", "CURRENT_EVIDENCE_NO_LONGER_ELIGIBLE")
    counts = scheduler_counts(ledger.connection)
    assert counts["obsolete"] == 1
    assert counts["dead_letter"] == 0
    ledger.close()


def test_pending_contract_reopens_jobs_completed_by_invalid_legacy_annotations(
    tmp_path,
) -> None:
    ledger = ForwardLedger(tmp_path / "forward.sqlite3", now=NOW)
    body = "Complete source evidence for semantic recovery. " * 20
    digest = hashlib.sha256(body.encode()).hexdigest()
    ledger.append_news_revision({
        "source": "semantic-recovery", "source_item_id": "legacy-invalid",
        "source_published_time": NOW, "collector_first_seen_time": NOW,
        "fetched_time": NOW, "headline": "Current macroeconomic report",
        "body": body, "content_hash": digest, "cluster_id": "legacy-invalid",
    })
    job_id = enqueue_job(
        ledger.connection, task_type="ACTIVE_ANNOTATION",
        source="semantic-recovery", source_item_id="legacy-invalid",
        revision_number=1, prompt_version=CURRENT_NEWS_PROMPT_VERSION,
        priority="NORMAL", now=NOW,
    )
    claimed = claim_job(
        ledger.connection, worker_id="old-worker", pool=ROUTINE_POOL, now=NOW,
    )
    assert claimed and claimed.job_id == job_id
    complete_job(ledger.connection, job_id, "old-worker", now=NOW)
    legacy_invalid = json.dumps({
        "xauusd_relevance": "IRRELEVANT",
        "semantic_reason_zh": "语言或结构一致性检查未通过，禁止进入当前模型。",
    }, ensure_ascii=False)
    ledger.connection.execute(
        """INSERT INTO news_annotations(
          annotation_id,source,source_item_id,revision_number,raw_content_hash,
          event_type,entities_json,hawkishness,inflation_impulse,growth_impulse,
          geopolitical_risk,usd_impulse,novelty,confidence,llm_model_version,
          prompt_version,parse_started_at,parsed_at,annotation_json)
          VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            "legacy-invalid", "semantic-recovery", "legacy-invalid", 1, digest,
            "other", "[]", 0, 0, 0, 0, 0, 0, 0,
            "gemini-3.5-flash-lite", CURRENT_NEWS_PROMPT_VERSION,
            NOW.isoformat(), NOW.isoformat(), legacy_invalid,
        ),
    )
    ledger.connection.commit()

    discovered = sync_pending_jobs(ledger.connection, now=NOW + timedelta(minutes=1))
    row = ledger.connection.execute(
        "SELECT state,attempt_count,completed_at FROM news_ai_jobs_v1 WHERE job_id=?",
        (job_id,),
    ).fetchone()

    assert discovered["ACTIVE_ANNOTATION"] == 1
    assert tuple(row) == ("QUEUED", 1, None)
    recovered = claim_job(
        ledger.connection, worker_id="recovery", pool=ROUTINE_POOL,
        now=NOW + timedelta(minutes=1),
    )
    assert recovered and recovered.job_id == job_id
    ledger.close()


def test_protected_daily_brief_job_resolves_after_cross_date_dedup(
    tmp_path,
) -> None:
    ledger = ForwardLedger(tmp_path / "forward.sqlite3", now=NOW)
    old_received = datetime(2026, 8, 10, 2, tzinfo=UTC)
    new_received = datetime(2026, 8, 11, 2, tzinfo=UTC)
    for item_id, received, body_length in (
        ("old-day", old_received, 300),
        ("new-day", new_received, 400),
    ):
        body = "x" * body_length
        ledger.append_news_revision({
            "source": "Reuters", "source_item_id": item_id,
            "source_published_time": received,
            "collector_first_seen_time": received, "fetched_time": received,
            "headline": f"Report {item_id}", "body": body,
            "content_hash": hashlib.sha256(body.encode()).hexdigest(),
            "cluster_id": "cross-date-cluster",
        })
    with ledger.connection:
        ledger.connection.execute(
            """INSERT INTO daily_news_briefs
               (brief_date,revision_number,source_hash,cutoff_at,generated_at,
                model_version,prompt_version,brief_json)
               VALUES ('2026-08-10',1,'old-hash',?,?,
                       'system-degraded-fallback','old-prompt',?)""",
            (NOW.isoformat(), NOW.isoformat(), '{"title":"old","items":[]}'),
        )
        ledger.connection.execute(
            """INSERT INTO daily_news_brief_finalizations_v1
               (brief_date,revision_number,final_status,received_items,
                reviewed_items,terminal_failure_items,cutoff_at,finalized_at)
               VALUES ('2026-08-10',1,'DEGRADED',1,0,1,?,?)""",
            (NOW.isoformat(), NOW.isoformat()),
        )

    obsolete_job_id = enqueue_job(
        ledger.connection, task_type="ACTIVE_ANNOTATION", source="Reuters",
        source_item_id="old-day", revision_number=1,
        prompt_version=CURRENT_NEWS_PROMPT_VERSION, priority="NORMAL", now=NOW,
    )
    obsolete = claim_job(
        ledger.connection, worker_id="old-worker", pool=ROUTINE_POOL, now=NOW,
    )
    assert obsolete and obsolete.job_id == obsolete_job_id
    backoff_job(
        ledger.connection, obsolete_job_id, "old-worker", available_at=NOW,
        error="CURRENT_EVIDENCE_NO_LONGER_ELIGIBLE", terminal=True,
    )

    discovered = sync_pending_jobs(
        ledger.connection, now=NOW + timedelta(minutes=1), limit=20,
    )
    row = ledger.connection.execute(
        """SELECT * FROM news_ai_jobs_v1
           WHERE task_type='ACTIVE_ANNOTATION' AND source_item_id='old-day'"""
    ).fetchone()
    job = news_scheduler_module._job_from_row(row)
    resolved = news_scheduler_module.pending_record_for_job(
        ledger.connection, job, now=NOW + timedelta(minutes=1),
    )

    assert discovered["ACTIVE_ANNOTATION"] >= 1
    assert row["state"] == "QUEUED"
    assert row["last_error"] is None
    assert resolved is not None
    assert resolved["source_item_id"] == "old-day"
    ledger.close()


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


def test_annotation_discovery_reserves_capacity_for_unfinished_brief_dates(
    tmp_path,
) -> None:
    ledger = ForwardLedger(tmp_path / "forward.sqlite3", now=NOW)

    def append(item: str, received_at: datetime) -> None:
        body = f"Complete macroeconomic evidence for {item}. " * 20
        ledger.append_news_revision({
            "source": "federal_reserve_monetary",
            "source_item_id": item,
            "source_published_time": received_at,
            "collector_first_seen_time": received_at,
            "fetched_time": received_at,
            "headline": f"Federal Reserve report {item}",
            "body": body,
            "content_hash": hashlib.sha256(body.encode()).hexdigest(),
            "cluster_id": item,
        })

    append("unfinished-brief", NOW - timedelta(days=2))
    for index in range(4):
        append(f"current-{index}", NOW - timedelta(minutes=index))

    discovered = sync_pending_jobs(ledger.connection, now=NOW, limit=2)
    queued = {
        row[0] for row in ledger.connection.execute(
            "SELECT source_item_id FROM news_ai_jobs_v1 "
            "WHERE task_type='ACTIVE_ANNOTATION'"
        ).fetchall()
    }

    assert discovered["ACTIVE_ANNOTATION"] == 2
    assert "unfinished-brief" in queued
    assert len(queued) == 2
    ledger.close()


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

    assert [status["pool"] for status in statuses] == [ROUTINE_POOL]
    assert statuses[0]["attempted_accounts"] == 2
    assert progress == [1]
    assert ledger.connection.execute(
        "SELECT state FROM news_ai_jobs_v1 WHERE job_id=?", (job_id,),
    ).fetchone()["state"] == "COMPLETED"
    attempts = ledger.connection.execute(
        """SELECT account_id,outcome,error_detail
        FROM news_ai_job_attempts_v1 ORDER BY attempted_at,account_id"""
    ).fetchall()
    assert [tuple(row) for row in attempts] == [
        ("urgent-account", "DEFERRED", "quota"),
        ("routine-account", "OK", None),
    ]
    ledger.close()


def test_aged_fifo_work_cannot_be_starved_by_fresh_priority_work() -> None:
    connection = _connection()
    oldest = _enqueue(connection, "oldest", priority="BACKGROUND")
    enqueue_job(
        connection, task_type="ACTIVE_IMPACT", source="source",
        source_item_id="fresh-urgent", revision_number=1,
        annotation_id="annotation", prompt_version="prompt",
        priority="IMMEDIATE", now=NOW + timedelta(seconds=61),
    )

    claimed = claim_job(
        connection, worker_id="routine", pool=ROUTINE_POOL,
        now=NOW + timedelta(seconds=62),
    )

    assert claimed and claimed.job_id == oldest


def test_claim_can_skip_a_capacity_blocked_task_route() -> None:
    connection = _connection()
    _enqueue(connection, "impact", task_type="ACTIVE_IMPACT")
    annotation = _enqueue(connection, "annotation")

    claimed = claim_job(
        connection, worker_id="routine", pool=ROUTINE_POOL,
        task_types=("ACTIVE_ANNOTATION", "ACTIVE_IMPACT"),
        excluded_task_types=frozenset({"ACTIVE_IMPACT"}), now=NOW,
    )

    assert claimed and claimed.job_id == annotation


def test_accounts_are_ranked_by_shared_live_model_headroom() -> None:
    from xauusd_forecaster.news_impact import IMPACT_MODEL

    connection = _connection()
    credentials = (
        ApiCredential("busy", ROUTINE_POOL, "key-a", "a"),
        ApiCredential("free", ROUTINE_POOL, "key-b", "b"),
        ApiCredential("busy", ROUTINE_POOL, "key-c", "c"),
    )
    assert reserve_account_request(
        connection, account_id="busy", model_family=IMPACT_MODEL,
        daily_limit=15_000, requests_per_minute=20,
        input_tokens=14_000, input_tokens_per_minute=15_000,
        shared_model_families=(IMPACT_MODEL, "gemma-impact", "gemma-title"),
        now=NOW,
    )

    ranked = rank_accounts_for_models(
        connection, credentials, models=(IMPACT_MODEL,), urgent=False, now=NOW,
    )

    assert ranked == ("free", "busy")


def test_scheduler_tries_every_independent_account_before_waiting(
    tmp_path, monkeypatch,
) -> None:
    from scripts import run_news_annotator as runner

    ledger = ForwardLedger(tmp_path / "forward.sqlite3", now=NOW)
    enqueue_job(
        ledger.connection, task_type="ACTIVE_IMPACT", source="source",
        source_item_id="chain", revision_number=1, annotation_id="annotation",
        prompt_version="prompt", priority="NORMAL",
        now=datetime.now(UTC) - timedelta(seconds=1),
    )
    credentials = tuple(
        ApiCredential(f"account-{name}", ROUTINE_POOL, f"key-{name}", name)
        for name in ("a", "b", "c")
    )
    monkeypatch.setattr(runner, "configured_api_credentials", lambda: credentials)
    monkeypatch.setattr(runner, "sync_pending_jobs", lambda *_args, **_kwargs: {})
    calls = []

    def execute(_ledger, credential, _job, **_kwargs):
        calls.append(credential.account_id)
        if credential.account_id == "account-a":
            return {"status": "DEFERRED", "reason": "quota"}
        if credential.account_id == "account-b":
            return {"status": "ERROR", "provider_http_status": 503,
                    "error": "temporarily unavailable"}
        return {"status": "OK"}

    monkeypatch.setattr(runner, "_execute_job", execute)

    statuses = runner.run_scheduled_batch(ledger, batch_size=1)

    assert calls == ["account-a", "account-b", "account-c"]
    assert statuses[0]["status"] == "OK"
    assert statuses[0]["attempted_accounts"] == 3
    assert ledger.connection.execute(
        "SELECT state FROM news_ai_jobs_v1"
    ).fetchone()["state"] == "COMPLETED"
    ledger.close()


def test_embedding_catchup_is_deferred_without_trying_another_account(
    monkeypatch,
) -> None:
    from scripts import run_news_annotator as runner
    from xauusd_forecaster.news_retrieval import NewsEmbeddingBackfillPending

    def pending(*_args, **_kwargs):
        raise NewsEmbeddingBackfillPending(
            "news identity embedding backfill is incomplete: 300 missing"
        )

    monkeypatch.setattr(runner, "_execute_job", pending)

    status = runner._execute_job_safely(
        object(), object(), object(), now=NOW,
    )

    assert status["status"] == "DEFERRED"
    assert status["failure_code"] == "NEWS_EMBEDDING_BACKFILL_PENDING"
    assert runner._may_try_another_credential(status) is False


def test_embedding_maintenance_does_not_consume_job_attempts_and_resumes(
    tmp_path, monkeypatch,
) -> None:
    from scripts import run_news_annotator as runner
    from xauusd_forecaster.news_retrieval import NewsEmbeddingBackfillPending

    ledger = ForwardLedger(tmp_path / "forward.sqlite3", now=NOW)
    job_id = enqueue_job(
        ledger.connection, task_type="ACTIVE_IMPACT", source="source",
        source_item_id="maintenance", revision_number=1,
        annotation_id="annotation", prompt_version="prompt", priority="FAST",
        now=datetime.now(UTC) - timedelta(minutes=1),
    )
    credential = ApiCredential(
        "account-a", ROUTINE_POOL, "not-a-real-key", "credential-a",
    )
    monkeypatch.setattr(runner, "configured_api_credentials", lambda: (credential,))
    monkeypatch.setattr(runner, "sync_pending_jobs", lambda *_args, **_kwargs: {})
    ready = False
    gemma_requests = 0

    def execute(*_args, **_kwargs):
        nonlocal gemma_requests
        if not ready:
            raise NewsEmbeddingBackfillPending("embedding generation incomplete")
        gemma_requests += 1
        return {"status": "OK"}

    monkeypatch.setattr(runner, "_execute_job", execute)

    for _ in range(2):
        statuses = runner.run_scheduled_batch(ledger, batch_size=3)
        assert statuses[0]["failure_code"] == "NEWS_EMBEDDING_BACKFILL_PENDING"
        row = ledger.connection.execute(
            "SELECT state,attempt_count FROM news_ai_jobs_v1 WHERE job_id=?",
            (job_id,),
        ).fetchone()
        assert dict(row) == {"state": "QUEUED", "attempt_count": 0}
        assert ledger.connection.execute(
            "SELECT count(*) FROM news_ai_job_attempts_v1 WHERE job_id=?",
            (job_id,),
        ).fetchone()[0] == 0
        assert gemma_requests == 0
        ledger.connection.execute(
            "UPDATE news_ai_jobs_v1 SET available_at=? WHERE job_id=?",
            ((datetime.now(UTC) - timedelta(seconds=1)).isoformat(), job_id),
        )
        ledger.connection.commit()

    ready = True
    statuses = runner.run_scheduled_batch(ledger, batch_size=1)

    assert statuses[0]["status"] == "OK"
    assert gemma_requests == 1
    row = ledger.connection.execute(
        "SELECT state,attempt_count FROM news_ai_jobs_v1 WHERE job_id=?",
        (job_id,),
    ).fetchone()
    assert dict(row) == {"state": "COMPLETED", "attempt_count": 1}
    assert ledger.connection.execute(
        "SELECT count(*) FROM news_ai_job_attempts_v1 WHERE job_id=?",
        (job_id,),
    ).fetchone()[0] == 1
    ledger.close()


def test_display_repair_tries_another_independent_account_before_waiting(
    tmp_path, monkeypatch,
) -> None:
    from scripts import run_news_annotator as runner

    ledger = ForwardLedger(tmp_path / "forward.sqlite3", now=NOW)
    enqueue_job(
        ledger.connection, task_type="ACTIVE_ANNOTATION", source="source",
        source_item_id="display-repair", revision_number=1,
        prompt_version="prompt", priority="NORMAL",
        now=datetime.now(UTC) - timedelta(seconds=1),
    )
    credentials = (
        ApiCredential("account-a", ROUTINE_POOL, "key-a", "a"),
        ApiCredential("account-b", ROUTINE_POOL, "key-b", "b"),
    )
    monkeypatch.setattr(runner, "configured_api_credentials", lambda: credentials)
    monkeypatch.setattr(runner, "sync_pending_jobs", lambda *_args, **_kwargs: {})
    calls = []

    def execute(_ledger, credential, _job, **_kwargs):
        calls.append(credential.account_id)
        if credential.account_id == "account-a":
            return {
                "status": "ERROR", "error": "display remains invalid",
                "retry_with_another_account": True,
            }
        return {"status": "OK"}

    monkeypatch.setattr(runner, "_execute_job", execute)

    statuses = runner.run_scheduled_batch(ledger, batch_size=1)

    assert calls == ["account-a", "account-b"]
    assert statuses[0]["status"] == "OK"
    assert statuses[0]["attempted_accounts"] == 2
    ledger.close()


def test_capacity_blocked_route_is_skipped_for_the_rest_of_the_lane(
    tmp_path, monkeypatch,
) -> None:
    from scripts import run_news_annotator as runner

    ledger = ForwardLedger(tmp_path / "forward.sqlite3", now=NOW)
    created = datetime.now(UTC) - timedelta(minutes=2)
    for item, task in (("blocked-1", "ACTIVE_IMPACT"),
                       ("blocked-2", "ACTIVE_IMPACT"),
                       ("ready", "ACTIVE_ANNOTATION")):
        enqueue_job(
            ledger.connection, task_type=task, source="source",
            source_item_id=item, revision_number=1,
            annotation_id="annotation", prompt_version="prompt",
            priority="NORMAL", now=created,
        )
        created += timedelta(seconds=1)
    credentials = (
        ApiCredential("account-a", ROUTINE_POOL, "key-a", "a"),
        ApiCredential("account-b", ROUTINE_POOL, "key-b", "b"),
    )
    monkeypatch.setattr(runner, "configured_api_credentials", lambda: credentials)
    monkeypatch.setattr(runner, "sync_pending_jobs", lambda *_args, **_kwargs: {})

    def execute(_ledger, _credential, job, **_kwargs):
        if job.task_type == "ACTIVE_IMPACT":
            return {"status": "DEFERRED", "reason": "quota"}
        return {"status": "OK"}

    monkeypatch.setattr(runner, "_execute_job", execute)

    statuses = runner.run_scheduled_batch(ledger, batch_size=3)

    assert [(row["task_type"], row["status"]) for row in statuses] == [
        ("ACTIVE_IMPACT", "DEFERRED"),
        ("ACTIVE_ANNOTATION", "OK"),
    ]
    rows = ledger.connection.execute(
        "SELECT source_item_id,state,available_at,attempt_count "
        "FROM news_ai_jobs_v1 "
        "ORDER BY created_at"
    ).fetchall()
    assert rows[0]["state"] == "QUEUED"
    assert datetime.fromisoformat(rows[0]["available_at"]) > datetime.now(UTC)
    assert rows[1]["state"] == "QUEUED"
    assert rows[1]["attempt_count"] == 0
    assert rows[2]["state"] == "COMPLETED"
    ledger.close()


def test_every_scheduler_task_has_one_declared_semantic_route() -> None:
    from xauusd_forecaster.ai_task_registry import AI_TASK_ROUTE_BY_TYPE
    from xauusd_forecaster.news_scheduler import TASKS

    assert set(TASKS).issubset(AI_TASK_ROUTE_BY_TYPE)
    assert AI_TASK_ROUTE_BY_TYPE["DAILY_BRIEF"].semantic_owner == "DISPLAY_ONLY"
    assert AI_TASK_ROUTE_BY_TYPE["ACTIVE_IMPACT"].semantic_owner == (
        "NEWS_EVENT_IDENTITY"
    )


def test_extra_key_in_one_account_does_not_inflate_batch_capacity(
    tmp_path, monkeypatch,
) -> None:
    from scripts import run_news_annotator as runner

    ledger = ForwardLedger(tmp_path / "forward.sqlite3", now=NOW)
    for index in range(12):
        enqueue_job(
            ledger.connection, task_type="ACTIVE_ANNOTATION", source="source",
            source_item_id=f"item-{index}", revision_number=1,
            prompt_version="prompt", priority="NORMAL",
            now=datetime.now(UTC) - timedelta(minutes=2),
        )
    credentials = (
        ApiCredential("one-account", ROUTINE_POOL, "key-a", "a"),
        ApiCredential("one-account", ROUTINE_POOL, "key-b", "b"),
    )
    monkeypatch.setattr(runner, "configured_api_credentials", lambda: credentials)
    monkeypatch.setattr(runner, "sync_pending_jobs", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(
        runner, "_execute_job", lambda *_args, **_kwargs: {"status": "OK"},
    )

    statuses = runner.run_scheduled_batch(ledger, batch_size=None)

    assert len(statuses) == 10
    assert scheduler_counts(ledger.connection)["queued"] == 2
    ledger.close()


def test_default_batch_runs_independent_accounts_concurrently(
    tmp_path, monkeypatch,
) -> None:
    from scripts import run_news_annotator as runner

    ledger = ForwardLedger(tmp_path / "forward.sqlite3", now=NOW)
    for index in range(20):
        enqueue_job(
            ledger.connection, task_type="ACTIVE_ANNOTATION", source="source",
            source_item_id=f"concurrent-{index}", revision_number=1,
            prompt_version="prompt", priority="NORMAL",
            now=datetime.now(UTC) - timedelta(minutes=2),
        )
    credentials = (
        ApiCredential("account-a", ROUTINE_POOL, "key-a", "a"),
        ApiCredential("account-b", ROUTINE_POOL, "key-b", "b"),
    )
    monkeypatch.setattr(runner, "configured_api_credentials", lambda: credentials)
    monkeypatch.setattr(runner, "sync_pending_jobs", lambda *_args, **_kwargs: {})
    first_calls = threading.Barrier(4)
    lock = threading.Lock()
    active = 0
    maximum_active = 0
    started_calls = 0
    started_accounts: set[str] = set()

    def execute(_ledger, credential, _job, **_kwargs):
        nonlocal active, maximum_active, started_calls
        with lock:
            started_accounts.add(credential.account_id)
            started_calls += 1
            synchronize = started_calls <= 4
            active += 1
            maximum_active = max(maximum_active, active)
        if synchronize:
            first_calls.wait(timeout=5)
        time.sleep(0.01)
        with lock:
            active -= 1
        return {"status": "OK"}

    monkeypatch.setattr(runner, "_execute_job", execute)
    progress: list[int] = []

    statuses = runner.run_scheduled_batch(
        ledger, batch_size=None, progress_callback=progress.append,
    )

    assert len(statuses) == 20
    assert maximum_active == 4
    assert started_accounts == {"account-a", "account-b"}
    assert progress == list(range(1, 21))
    assert scheduler_counts(ledger.connection)["completed"] == 20
    ledger.close()


def test_concurrent_account_lanes_bound_capacity_probes_per_lane(
    tmp_path, monkeypatch,
) -> None:
    from scripts import run_news_annotator as runner

    ledger = ForwardLedger(tmp_path / "forward.sqlite3", now=NOW)
    for index in range(4):
        enqueue_job(
            ledger.connection, task_type="ACTIVE_IMPACT", source="source",
            source_item_id=f"blocked-{index}", revision_number=1,
            annotation_id=f"annotation-{index}", prompt_version="prompt",
            priority="NORMAL", now=datetime.now(UTC) - timedelta(minutes=2),
        )
    credentials = (
        ApiCredential("account-a", ROUTINE_POOL, "key-a", "a"),
        ApiCredential("account-b", ROUTINE_POOL, "key-b", "b"),
    )
    monkeypatch.setattr(runner, "configured_api_credentials", lambda: credentials)
    monkeypatch.setattr(runner, "sync_pending_jobs", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(
        runner, "_execute_job",
        lambda *_args, **_kwargs: {"status": "DEFERRED", "reason": "quota"},
    )

    statuses = runner.run_scheduled_batch(ledger, batch_size=None)

    assert 2 <= len(statuses) <= 4
    attempts = ledger.connection.execute(
        "SELECT job_id,account_id,failure_code FROM news_ai_job_attempts_v1"
    ).fetchall()
    assert len(attempts) == len(statuses)
    assert {row["account_id"] for row in attempts} == {"account-a", "account-b"}
    per_account = {
        account_id: sum(row["account_id"] == account_id for row in attempts)
        for account_id in {"account-a", "account-b"}
    }
    assert max(per_account.values()) <= runner.PRODUCTION_LANES_PER_ACCOUNT
    assert {row["failure_code"] for row in attempts} == {
        "MODEL_CAPACITY_DEFERRED",
    }
    assert ledger.connection.execute(
        "SELECT max(attempt_count) FROM news_ai_jobs_v1"
    ).fetchone()[0] == 1
    ledger.close()


def test_daily_brief_capacity_reserves_only_its_own_account(
    tmp_path, monkeypatch,
) -> None:
    from scripts import run_news_annotator as runner

    ledger = ForwardLedger(tmp_path / "forward.sqlite3", now=NOW)
    for index in range(4):
        enqueue_job(
            ledger.connection, task_type="ACTIVE_IMPACT", source="source",
            source_item_id=f"impact-{index}", revision_number=1,
            annotation_id=f"annotation-{index}", prompt_version="prompt",
            priority="NORMAL", now=datetime.now(UTC) - timedelta(minutes=2),
        )
    credentials = (
        ApiCredential("brief-account", ROUTINE_POOL, "key-a", "a"),
        ApiCredential("impact-account", ROUTINE_POOL, "key-b", "b"),
    )
    monkeypatch.setattr(runner, "configured_api_credentials", lambda: credentials)
    monkeypatch.setattr(runner, "sync_pending_jobs", lambda *_args, **_kwargs: {})
    attempted_accounts: list[str] = []

    def execute(_ledger, credential, _job, **_kwargs):
        attempted_accounts.append(credential.account_id)
        return {"status": "OK"}

    monkeypatch.setattr(runner, "_execute_job", execute)

    statuses = runner.run_scheduled_batch(
        ledger,
        batch_size=None,
        gemma_reserved_accounts=frozenset({"brief-account"}),
    )

    assert len(statuses) == 4
    assert attempted_accounts == ["impact-account"] * 4
    assert scheduler_counts(ledger.connection)["completed"] == 4
    ledger.close()


def test_display_route_uses_declared_fallback_when_gemma_capacity_is_full(
    monkeypatch,
) -> None:
    import xauusd_forecaster.annotation as annotation
    from xauusd_forecaster.model_gateway import (
        ModelGatewayCapacityExhausted,
        ModelRequestAccountant,
    )

    class Accountant(ModelRequestAccountant):
        def reserve(self, _usage) -> bool:
            return True

    pool = annotation._GeminiRequestPool(
        ("offline-key",), request_accountant=Accountant(),
    )
    monkeypatch.setattr(pool.gateway, "count_input_tokens", lambda *_args: 10)
    calls = []

    def generate(_index, *, model, **_kwargs):
        calls.append(model)
        if model == annotation.DEFAULT_GEMMA_MODEL:
            raise ModelGatewayCapacityExhausted("full")
        return "中文标题", model

    monkeypatch.setattr(pool.gateway, "generate", generate)

    title, model = pool.call_title(0, annotation.DEFAULT_GEMMA_MODEL, "Headline")

    assert title == "中文标题"
    assert model == annotation.DEFAULT_GEMINI_MODEL
    assert calls == [
        annotation.DEFAULT_GEMMA_MODEL, annotation.DEFAULT_GEMINI_MODEL,
    ]


def test_scheduler_persists_structured_model_failure_without_credentials(
    tmp_path, monkeypatch,
) -> None:
    from scripts import run_news_annotator as runner

    available_now = datetime.now(UTC) - timedelta(seconds=1)
    ledger = ForwardLedger(tmp_path / "forward.sqlite3", now=NOW)
    job_id = enqueue_job(
        ledger.connection, task_type="ACTIVE_IMPACT", source="source",
        source_item_id="failed", revision_number=1, annotation_id="annotation",
        prompt_version="prompt", priority="NORMAL", now=available_now,
    )
    credential = ApiCredential(
        "account-a", ROUTINE_POOL, "secret-api-key", "safe-fingerprint",
    )
    monkeypatch.setattr(runner, "configured_api_credentials", lambda: (credential,))
    monkeypatch.setattr(runner, "sync_pending_jobs", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(
        runner, "_execute_job",
        lambda *_args, **_kwargs: {
            "status": "ERROR", "failure_code": "PROVIDER_HTTP_ERROR",
            "provider_http_status": 503, "error_type": "HTTPError",
            "error": "Service Unavailable",
            "next_retry_at": (datetime.now(UTC) + timedelta(minutes=15)).isoformat(),
        },
    )

    runner.run_scheduled_batch(ledger, batch_size=1)

    attempt = ledger.connection.execute(
        "SELECT * FROM news_ai_job_attempts_v1 WHERE job_id=?", (job_id,),
    ).fetchone()
    assert attempt["failure_code"] == "PROVIDER_HTTP_ERROR"
    assert attempt["provider_http_status"] == 503
    assert attempt["account_id"] == "account-a"
    assert attempt["credential_id"] == "safe-fingerprint"
    assert "secret-api-key" not in json.dumps(dict(attempt))
    ledger.close()


def test_provider_http_failure_uses_an_independent_account_failover(
    tmp_path, monkeypatch,
) -> None:
    from scripts import run_news_annotator as runner

    available_now = datetime.now(UTC) - timedelta(seconds=1)
    ledger = ForwardLedger(tmp_path / "forward.sqlite3", now=NOW)
    job_id = enqueue_job(
        ledger.connection, task_type="ACTIVE_IMPACT", source="source",
        source_item_id="recover", revision_number=1, annotation_id="annotation",
        prompt_version="prompt", priority="NORMAL", now=available_now,
    )
    credentials = (
        ApiCredential("account-a", ROUTINE_POOL, "key-a", "fingerprint-a"),
        ApiCredential("account-b", ROUTINE_POOL, "key-b", "fingerprint-b"),
    )
    monkeypatch.setattr(runner, "configured_api_credentials", lambda: credentials)
    monkeypatch.setattr(runner, "sync_pending_jobs", lambda *_args, **_kwargs: {})
    calls = []

    def execute(_ledger, credential, _job, **_kwargs):
        calls.append(credential.account_id)
        if credential.account_id == "account-a":
            return {
                "status": "ERROR", "failure_code": "PROVIDER_HTTP_ERROR",
                "provider_http_status": 503, "error_type": "HTTPError",
                "error": "Service Unavailable",
            }
        return {"status": "OK"}

    monkeypatch.setattr(runner, "_execute_job", execute)

    statuses = runner.run_scheduled_batch(ledger, batch_size=1)

    assert calls == ["account-a", "account-b"]
    assert statuses[0]["status"] == "OK"
    assert statuses[0]["account_id"] == "account-b"
    assert statuses[0]["attempted_accounts"] == 2
    assert ledger.connection.execute(
        "SELECT state FROM news_ai_jobs_v1 WHERE job_id=?", (job_id,),
    ).fetchone()["state"] == "COMPLETED"
    attempts = ledger.connection.execute(
        """SELECT account_id,outcome FROM news_ai_job_attempts_v1
        WHERE job_id=? ORDER BY account_id""", (job_id,),
    ).fetchall()
    assert [tuple(row) for row in attempts] == [
        ("account-a", "ERROR"), ("account-b", "OK"),
    ]
    ledger.close()


def test_failover_deferral_is_owned_by_the_account_that_deferred(
    tmp_path, monkeypatch,
) -> None:
    from scripts import run_news_annotator as runner

    available_now = datetime.now(UTC) - timedelta(seconds=1)
    ledger = ForwardLedger(tmp_path / "forward.sqlite3", now=NOW)
    enqueue_job(
        ledger.connection, task_type="ACTIVE_IMPACT", source="source",
        source_item_id="deferred-failover", revision_number=1,
        annotation_id="annotation", prompt_version="prompt", priority="FAST",
        now=available_now,
    )
    credentials = (
        ApiCredential("account-a", PREEMPTIBLE_POOL, "key-a", "fingerprint-a"),
        ApiCredential("account-b", ROUTINE_POOL, "key-b", "fingerprint-b"),
    )
    monkeypatch.setattr(runner, "configured_api_credentials", lambda: credentials)
    monkeypatch.setattr(runner, "sync_pending_jobs", lambda *_args, **_kwargs: {})
    calls = []

    def execute(_ledger, credential, _job, **_kwargs):
        calls.append(credential.account_id)
        if credential.account_id == "account-a":
            return {
                "status": "ERROR", "failure_code": "PROVIDER_HTTP_ERROR",
                "provider_http_status": 503, "error_type": "HTTPError",
                "error": "Service Unavailable",
            }
        return {"status": "DEFERRED", "reason": "quota"}

    monkeypatch.setattr(runner, "_execute_job", execute)

    statuses = runner.run_scheduled_batch(ledger, batch_size=2)

    assert calls == ["account-a", "account-b"]
    assert len(statuses) == 1
    assert statuses[0]["account_id"] == "account-b"
    row = ledger.connection.execute(
        "SELECT state,available_at FROM news_ai_jobs_v1",
    ).fetchone()
    assert row["state"] == "QUEUED"
    assert datetime.fromisoformat(row["available_at"]) > datetime.now(UTC)
    ledger.close()


def test_job_safety_boundary_does_not_misclassify_database_failures(
    monkeypatch,
) -> None:
    from scripts import run_news_annotator as runner

    monkeypatch.setattr(
        runner, "_execute_job",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            sqlite3.OperationalError("database is locked")
        ),
    )

    with pytest.raises(sqlite3.OperationalError, match="database is locked"):
        runner._execute_job_safely(None, None, None, now=NOW)


def test_annotator_retries_transient_writer_contention_without_exiting(
    tmp_path, monkeypatch,
) -> None:
    from scripts import run_news_annotator as runner

    ledger = ForwardLedger(tmp_path / "forward.sqlite3", now=NOW)
    calls = []
    sleeps = []

    def scheduled_batch(*_args, **_kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise sqlite3.OperationalError("database is locked")
        return [{"status": "OK"}]

    monkeypatch.setattr(runner, "run_scheduled_batch", scheduled_batch)
    statuses = runner.run_scheduled_batch_with_lock_retry(
        ledger,
        batch_size=1,
        progress_callback=lambda _count: None,
        sleep=sleeps.append,
    )

    assert statuses == [{"status": "OK"}]
    assert len(calls) == 2
    assert sleeps == [5.0]
    ledger.close()


def test_scheduler_lifecycle_survives_restart_without_duplicate_terminal_work(
    tmp_path,
) -> None:
    database = tmp_path / "scheduler.sqlite3"

    def open_connection() -> sqlite3.Connection:
        connection = sqlite3.connect(database)
        connection.row_factory = sqlite3.Row
        install_scheduler_schema(connection)
        return connection

    first = open_connection()
    job_id = enqueue_job(
        first, task_type="ACTIVE_ANNOTATION", source="source",
        source_item_id="restart-safe", revision_number=1,
        prompt_version="prompt", priority="NORMAL", now=NOW,
    )
    lease = claim_job(
        first, worker_id="worker-before-restart", pool=ROUTINE_POOL, now=NOW,
    )
    assert lease is not None and lease.attempt_count == 1
    retry_at = NOW + timedelta(minutes=5)
    backoff_job(
        first, job_id, "worker-before-restart", available_at=retry_at,
        error="PROVIDER_HTTP_429",
    )
    first.close()

    restarted = open_connection()
    assert claim_job(
        restarted, worker_id="too-early", pool=ROUTINE_POOL,
        now=retry_at - timedelta(seconds=1),
    ) is None
    retry_lease = claim_job(
        restarted, worker_id="worker-after-restart", pool=ROUTINE_POOL,
        now=retry_at,
    )
    assert retry_lease is not None
    assert retry_lease.job_id == job_id
    assert retry_lease.attempt_count == 2
    complete_job(
        restarted, job_id, "worker-after-restart",
        now=retry_at + timedelta(seconds=1),
    )
    restarted.close()

    verified = open_connection()
    assert enqueue_job(
        verified, task_type="ACTIVE_ANNOTATION", source="source",
        source_item_id="restart-safe", revision_number=1,
        prompt_version="prompt", priority="NORMAL",
        now=retry_at + timedelta(minutes=1),
    ) == job_id
    assert claim_job(
        verified, worker_id="must-not-requeue", pool=ROUTINE_POOL,
        now=retry_at + timedelta(minutes=1),
    ) is None
    row = verified.execute(
        "SELECT state,attempt_count,count(*) AS total FROM news_ai_jobs_v1",
    ).fetchone()
    assert dict(row) == {"state": "COMPLETED", "attempt_count": 2, "total": 1}
    verified.close()
