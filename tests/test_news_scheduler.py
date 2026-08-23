from __future__ import annotations

import json
import hashlib
import sqlite3
import threading
import time
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

import xauusd_forecaster.news_scheduler as news_scheduler_module
from xauusd_forecaster.news_scheduler import (
    ApiCredential,
    PREEMPTIBLE_POOL,
    ROUTINE_POOL,
    account_quota_snapshot,
    apply_retry_schedule_override,
    authorize_repairable_annotation_failures,
    authorize_repairable_impact_failures,
    backoff_job,
    claim_job,
    complete_job,
    configured_api_credentials,
    enqueue_job,
    forecast_safe_backfill_budget,
    install_scheduler_schema,
    list_retry_schedule_jobs,
    mark_account_request_attempted,
    rank_accounts_for_models,
    record_account_request_outcome,
    record_scheduler_deferral,
    record_provider_dispatch_outcome,
    reconcile_completed_jobs,
    reserve_account_request,
    reserve_provider_dispatch,
    rolling_account_usage,
    RetryScheduleConflict,
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
from xauusd_forecaster.evidence.ledger import ForwardLedger
from xauusd_forecaster.news_semantics import (
    CURRENT_NEWS_PROMPT_VERSION,
    PREVIOUS_NEWS_PROMPT_VERSION,
)
from xauusd_forecaster.semantic_transition import (
    ARCHIVAL_ONLY,
    DETERMINISTIC_MIGRATION,
    MODEL_REVIEW_REQUIRED,
    REUSE_COMPATIBLE,
    TRAINING_REQUIRED,
    SemanticTransition,
    demand_allows_scheduling,
    requires_model_review,
    provider_dispatches_for_transition,
    transition_for,
)


NOW = datetime(2026, 8, 12, 2, 0, tzinfo=UTC)


def _connection() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    install_scheduler_schema(connection)
    return connection


def _seed_complete_live_quota_days(
    connection: sqlite3.Connection, *, remaining_live: int = 1_000,
) -> None:
    pacific_day = datetime(2026, 8, 11, 19)
    for offset in range(1, 8):
        day = (pacific_day.date() - timedelta(days=offset)).isoformat()
        connection.execute(
            """INSERT INTO news_ai_quota_day_workload_v1
               VALUES (?,?,?,?,?,?,?)""",
            (
                day, 19, "account-a", "gemini_quota", "LIVE_OPERATIONAL",
                remaining_live, NOW.isoformat(timespec="microseconds"),
            ),
        )
    connection.commit()


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


def _backing_off_job(
    connection: sqlite3.Connection,
    item: str,
    *,
    retry_at: datetime,
    priority: str = "NORMAL",
) -> str:
    job_id = _enqueue(connection, item, priority=priority)
    claimed = claim_job(
        connection, worker_id=f"worker-{item}", pool=ROUTINE_POOL, now=NOW,
    )
    assert claimed and claimed.job_id == job_id
    backoff_job(
        connection, job_id, f"worker-{item}",
        available_at=retry_at, error="ConnectionResetError",
    )
    return job_id


def test_live_annotation_claim_preempts_aged_contract_backfill() -> None:
    connection = _connection()
    backfill = enqueue_job(
        connection, task_type="ACTIVE_ANNOTATION", source="source",
        source_item_id="backfill", revision_number=1, prompt_version="prompt",
        priority="BACKGROUND",
        work_lane=news_scheduler_module.CONTRACT_BACKFILL_LANE,
        now=NOW - timedelta(hours=2),
    )
    live = enqueue_job(
        connection, task_type="ACTIVE_ANNOTATION", source="source",
        source_item_id="live", revision_number=1, prompt_version="prompt",
        priority="FAST", work_lane=news_scheduler_module.LIVE_LANE, now=NOW,
    )

    claimed = claim_job(
        connection, worker_id="worker", pool=ROUTINE_POOL, now=NOW,
    )

    assert claimed and claimed.job_id == live
    assert claimed.work_lane == news_scheduler_module.LIVE_LANE
    assert connection.execute(
        "SELECT state FROM news_ai_jobs_v1 WHERE job_id=?", (backfill,),
    ).fetchone()[0] == "QUEUED"
    connection.close()


def test_fast_live_annotation_uses_reserved_order_before_aged_impact() -> None:
    connection = _connection()
    impact = enqueue_job(
        connection, task_type="ACTIVE_IMPACT", source="source",
        source_item_id="impact", revision_number=1, annotation_id="impact-id",
        prompt_version="impact-prompt", priority="IMMEDIATE",
        now=NOW - timedelta(hours=2),
    )
    live = enqueue_job(
        connection, task_type="ACTIVE_ANNOTATION", source="source",
        source_item_id="live", revision_number=1, prompt_version="prompt",
        priority="FAST", work_lane=news_scheduler_module.LIVE_LANE, now=NOW,
    )

    claimed = claim_job(
        connection, worker_id="worker", pool=ROUTINE_POOL, now=NOW,
    )

    assert claimed and claimed.job_id == live
    assert connection.execute(
        "SELECT state FROM news_ai_jobs_v1 WHERE job_id=?", (impact,),
    ).fetchone()[0] == "QUEUED"
    connection.close()


def test_contract_backfill_discovery_is_bounded_resumable_and_separate(tmp_path) -> None:
    ledger = ForwardLedger(tmp_path / "forward.sqlite3", now=NOW - timedelta(days=2))

    def append(item: str, received: datetime) -> None:
        body = f"Complete XAUUSD macro evidence {item}. " * 20
        ledger.append_news_revision({
            "source": "fixture", "source_item_id": item,
            "source_published_time": received,
            "collector_first_seen_time": received, "fetched_time": received,
            "headline": f"Report {item}", "body": body,
            "content_hash": hashlib.sha256(body.encode()).hexdigest(),
            "cluster_id": item,
        })

    for index in range(5):
        append(f"historical-{index}", NOW - timedelta(minutes=index + 1))
    append("live", NOW)

    first = sync_pending_jobs(ledger.connection, now=NOW, limit=4)
    first_rows = ledger.connection.execute(
        """SELECT source_item_id,work_lane,priority FROM news_ai_jobs_v1
           WHERE task_type='ACTIVE_ANNOTATION' ORDER BY source_item_id"""
    ).fetchall()
    first_cursor = ledger.connection.execute(
        """SELECT cursor_first_seen FROM news_annotation_contract_backfill_v1
           WHERE prompt_version=?""", (CURRENT_NEWS_PROMPT_VERSION,),
    ).fetchone()[0]

    second = sync_pending_jobs(
        ledger.connection, now=NOW + timedelta(minutes=1), limit=4,
    )
    second_cursor = ledger.connection.execute(
        """SELECT cursor_first_seen FROM news_annotation_contract_backfill_v1
           WHERE prompt_version=?""", (CURRENT_NEWS_PROMPT_VERSION,),
    ).fetchone()[0]
    third = sync_pending_jobs(
        ledger.connection, now=NOW + timedelta(minutes=2), limit=4,
    )

    assert first["ACTIVE_ANNOTATION"] <= 4
    assert second["ACTIVE_ANNOTATION"] <= 4
    assert third["ACTIVE_ANNOTATION"] <= 4
    assert any(row[0] == "live" and row[1:] == ("LIVE", "FAST") for row in first_rows)
    assert sum(row[1] == "CONTRACT_BACKFILL" for row in first_rows) <= 2
    assert second_cursor < first_cursor
    assert ledger.connection.execute(
        "SELECT count(*) FROM news_ai_jobs_v1 WHERE task_type='ACTIVE_ANNOTATION'"
    ).fetchone()[0] == 6
    ledger.close()


def test_ten_thousand_record_backfill_cursor_is_bounded_and_exact(monkeypatch) -> None:
    connection = _connection()
    connection.execute(
        """INSERT INTO news_annotation_contract_backfill_v1
           VALUES (?,?,NULL,NULL,NULL,NULL,'ACTIVE',?)""",
        ("prompt", NOW.isoformat(), NOW.isoformat()),
    )
    rows = [
        {
            "collector_first_seen_time": (
                NOW - timedelta(seconds=index)
            ).isoformat(timespec="microseconds"),
            "source": "fixture",
            "source_item_id": f"item-{index:05d}",
            "revision_number": 1,
        }
        for index in range(10_000)
    ]
    monkeypatch.setattr(
        news_scheduler_module, "_contract_backfill_has_current_value",
        lambda *_args, **_kwargs: True,
    )

    def pending(_connection, *, before_cursor=None, limit, **_kwargs):
        start = 0
        if before_cursor is not None:
            previous_id = before_cursor[2]
            start = int(previous_id.rsplit("-", 1)[1]) + 1
        return rows[start:start + limit]

    seen: list[str] = []
    for offset in range(201):
        page = news_scheduler_module._contract_backfill_page(
            connection, prompt_version="prompt", activated_at=NOW,
            now=NOW + timedelta(seconds=offset),
            pending_annotation_records=pending, page_size=2_000,
        )
        assert len(page) <= news_scheduler_module.CONTRACT_BACKFILL_PAGE_SIZE
        seen.extend(str(row["source_item_id"]) for row in page)

    assert len(seen) == 10_000
    assert len(set(seen)) == 10_000
    state = connection.execute(
        """SELECT state,cursor_source_item_id
           FROM news_annotation_contract_backfill_v1 WHERE prompt_version='prompt'"""
    ).fetchone()
    assert tuple(state) == ("COMPLETE", "item-09999")


@pytest.mark.parametrize(
    ("mode", "custom", "expected"),
    (
        ("IMMEDIATE", None, NOW + timedelta(minutes=2)),
        ("DELAY_15_MIN", None, NOW + timedelta(minutes=17)),
        ("DELAY_1_HOUR", None, NOW + timedelta(hours=1, minutes=2)),
        ("CUSTOM_TIME", NOW + timedelta(hours=3), NOW + timedelta(hours=3)),
        ("IDLE_CAPACITY", None, NOW + timedelta(minutes=2)),
    ),
)
def test_retry_schedule_override_modes_preserve_attempt_and_failure_evidence(
    mode: str, custom: datetime | None, expected: datetime,
) -> None:
    connection = _connection()
    automatic = NOW + timedelta(hours=6)
    job_id = _backing_off_job(connection, mode.lower(), retry_at=automatic)
    before = connection.execute(
        "SELECT state,available_at,attempt_count,last_error FROM news_ai_jobs_v1 WHERE job_id=?",
        (job_id,),
    ).fetchone()

    result = apply_retry_schedule_override(
        connection,
        request_id=f"request-{mode}", job_id=job_id, operator_id="owner-1",
        mode=mode, reason="Bug fix deployed", expected_state="BACKING_OFF",
        expected_available_at=str(before["available_at"]),
        requested_available_at=custom, now=NOW + timedelta(minutes=2),
    )

    assert datetime.fromisoformat(str(result["available_at"])) == expected
    after = connection.execute(
        "SELECT attempt_count,last_error FROM news_ai_jobs_v1 WHERE job_id=?",
        (job_id,),
    ).fetchone()
    assert tuple(after) == (before["attempt_count"], "ConnectionResetError")
    audit = connection.execute(
        "SELECT * FROM news_ai_retry_schedule_overrides_v1 WHERE job_id=?",
        (job_id,),
    ).fetchone()
    assert audit["original_available_at"] == automatic.isoformat(timespec="microseconds")
    assert audit["previous_available_at"] == automatic.isoformat(timespec="microseconds")
    assert audit["operator_id"] == "owner-1"
    assert audit["mode"] == mode


def test_keep_original_restores_first_automatic_schedule_and_appends_audit() -> None:
    connection = _connection()
    automatic = NOW + timedelta(hours=6)
    job_id = _backing_off_job(connection, "restore", retry_at=automatic)
    expected = automatic.isoformat(timespec="microseconds")
    first = apply_retry_schedule_override(
        connection, request_id="early", job_id=job_id, operator_id="owner-1",
        mode="IMMEDIATE", reason="try repaired path", expected_state="BACKING_OFF",
        expected_available_at=expected, now=NOW + timedelta(minutes=1),
    )
    restored = apply_retry_schedule_override(
        connection, request_id="restore", job_id=job_id, operator_id="owner-1",
        mode="KEEP_ORIGINAL", reason="retain automatic plan",
        expected_state="BACKING_OFF",
        expected_available_at=str(first["available_at"]),
        now=NOW + timedelta(minutes=2),
    )

    assert restored["available_at"] == expected
    audits = connection.execute(
        """SELECT mode,original_available_at,previous_available_at
           FROM news_ai_retry_schedule_overrides_v1
           WHERE job_id=? ORDER BY requested_at""",
        (job_id,),
    ).fetchall()
    assert [row["mode"] for row in audits] == ["IMMEDIATE", "KEEP_ORIGINAL"]
    assert {row["original_available_at"] for row in audits} == {expected}
    assert audits[1]["previous_available_at"] == first["available_at"]


def test_keep_original_without_prior_override_is_an_audited_no_op() -> None:
    connection = _connection()
    automatic = NOW + timedelta(hours=6)
    job_id = _backing_off_job(connection, "keep", retry_at=automatic)
    expected = automatic.isoformat(timespec="microseconds")
    updated_at = connection.execute(
        "SELECT updated_at FROM news_ai_jobs_v1 WHERE job_id=?", (job_id,),
    ).fetchone()[0]

    result = apply_retry_schedule_override(
        connection, request_id="keep", job_id=job_id, operator_id="owner-1",
        mode="KEEP_ORIGINAL", reason="no change", expected_state="BACKING_OFF",
        expected_available_at=expected, now=NOW + timedelta(minutes=1),
    )

    assert result["available_at"] == expected
    assert connection.execute(
        "SELECT updated_at FROM news_ai_jobs_v1 WHERE job_id=?", (job_id,),
    ).fetchone()[0] == updated_at
    assert connection.execute(
        "SELECT count(*) FROM news_ai_retry_schedule_overrides_v1 WHERE job_id=?",
        (job_id,),
    ).fetchone()[0] == 1
    assert list_retry_schedule_jobs(connection)[0]["override_mode"] is None


@pytest.mark.parametrize("terminal_state", ("LEASED", "COMPLETED", "DEAD_LETTER"))
def test_retry_schedule_override_rejects_non_mutable_states(terminal_state: str) -> None:
    connection = _connection()
    job_id = _enqueue(connection, terminal_state.lower())
    if terminal_state == "LEASED":
        claim_job(connection, worker_id="worker", pool=ROUTINE_POOL, now=NOW)
    else:
        connection.execute(
            "UPDATE news_ai_jobs_v1 SET state=? WHERE job_id=?",
            (terminal_state, job_id),
        )
        connection.commit()
    row = connection.execute(
        "SELECT state,available_at FROM news_ai_jobs_v1 WHERE job_id=?", (job_id,),
    ).fetchone()

    with pytest.raises(RetryScheduleConflict, match="JOB_NOT_MUTABLE"):
        apply_retry_schedule_override(
            connection, request_id=f"reject-{terminal_state}", job_id=job_id,
            operator_id="owner-1", mode="IMMEDIATE", reason="not allowed",
            expected_state=str(row["state"]),
            expected_available_at=str(row["available_at"]), now=NOW,
        )
    assert connection.execute(
        "SELECT count(*) FROM news_ai_retry_schedule_overrides_v1",
    ).fetchone()[0] == 0


def test_retry_schedule_override_detects_claim_race_and_is_idempotent() -> None:
    connection = _connection()
    job_id = _backing_off_job(connection, "race", retry_at=NOW + timedelta(hours=2))
    observed = connection.execute(
        "SELECT state,available_at FROM news_ai_jobs_v1 WHERE job_id=?", (job_id,),
    ).fetchone()
    connection.execute(
        "UPDATE news_ai_jobs_v1 SET available_at=? WHERE job_id=?",
        (NOW.isoformat(timespec="microseconds"), job_id),
    )
    connection.commit()
    claimed = claim_job(connection, worker_id="racer", pool=ROUTINE_POOL, now=NOW)
    assert claimed and claimed.job_id == job_id

    with pytest.raises(RetryScheduleConflict, match="JOB_NOT_MUTABLE"):
        apply_retry_schedule_override(
            connection, request_id="lost-race", job_id=job_id,
            operator_id="owner-1", mode="IMMEDIATE", reason="race",
            expected_state=str(observed["state"]),
            expected_available_at=str(observed["available_at"]), now=NOW,
        )

    connection = _connection()
    job_id = _backing_off_job(connection, "idempotent", retry_at=NOW + timedelta(hours=2))
    observed = connection.execute(
        "SELECT state,available_at FROM news_ai_jobs_v1 WHERE job_id=?", (job_id,),
    ).fetchone()
    inputs = dict(
        request_id="same-request", job_id=job_id, operator_id="owner-1",
        mode="IMMEDIATE", reason="retry once", expected_state=str(observed["state"]),
        expected_available_at=str(observed["available_at"]), now=NOW,
    )
    first = apply_retry_schedule_override(connection, **inputs)
    second = apply_retry_schedule_override(connection, **inputs)
    assert second["available_at"] == first["available_at"]
    assert connection.execute(
        "SELECT count(*) FROM news_ai_retry_schedule_overrides_v1",
    ).fetchone()[0] == 1


def test_immediate_override_only_becomes_claimable_and_claim_increments_attempt() -> None:
    connection = _connection()
    job_id = _backing_off_job(connection, "claimable", retry_at=NOW + timedelta(hours=2))
    row = connection.execute(
        "SELECT state,available_at,attempt_count FROM news_ai_jobs_v1 WHERE job_id=?",
        (job_id,),
    ).fetchone()
    result = apply_retry_schedule_override(
        connection, request_id="claimable", job_id=job_id,
        operator_id="owner-1", mode="IMMEDIATE", reason="fixed",
        expected_state=str(row["state"]), expected_available_at=str(row["available_at"]),
        now=NOW + timedelta(minutes=1),
    )
    assert result["attempt_count"] == row["attempt_count"]
    claimed = claim_job(
        connection, worker_id="worker-next", pool=ROUTINE_POOL,
        now=NOW + timedelta(minutes=1),
    )
    assert claimed and claimed.job_id == job_id
    assert claimed.attempt_count == row["attempt_count"] + 1


def test_future_override_is_not_claimable_before_effective_time() -> None:
    connection = _connection()
    job_id = _backing_off_job(connection, "future", retry_at=NOW + timedelta(hours=2))
    row = connection.execute(
        "SELECT state,available_at FROM news_ai_jobs_v1 WHERE job_id=?", (job_id,),
    ).fetchone()
    apply_retry_schedule_override(
        connection, request_id="future", job_id=job_id, operator_id="owner-1",
        mode="DELAY_15_MIN", reason="wait", expected_state=str(row["state"]),
        expected_available_at=str(row["available_at"]), now=NOW,
    )
    assert claim_job(
        connection, worker_id="early", pool=ROUTINE_POOL,
        now=NOW + timedelta(minutes=14, seconds=59),
    ) is None


def test_idle_capacity_yields_to_normal_work_but_ages_out() -> None:
    connection = _connection()
    idle_id = _backing_off_job(connection, "idle", retry_at=NOW + timedelta(hours=2))
    idle = connection.execute(
        "SELECT state,available_at FROM news_ai_jobs_v1 WHERE job_id=?", (idle_id,),
    ).fetchone()
    apply_retry_schedule_override(
        connection, request_id="idle", job_id=idle_id, operator_id="owner-1",
        mode="IDLE_CAPACITY", reason="spare capacity", expected_state=str(idle["state"]),
        expected_available_at=str(idle["available_at"]), now=NOW,
    )
    normal_id = _enqueue(connection, "normal", priority="BACKGROUND")
    claimed = claim_job(
        connection, worker_id="normal-first", pool=ROUTINE_POOL, now=NOW,
    )
    assert claimed and claimed.job_id == normal_id
    complete_job(connection, normal_id, "normal-first", now=NOW)

    # Even sustained newer work cannot starve an idle override past 30 minutes.
    newer_id = enqueue_job(
        connection, task_type="ACTIVE_ANNOTATION", source="source",
        source_item_id="newer", revision_number=1, prompt_version="prompt",
        priority="IMMEDIATE", now=NOW + timedelta(minutes=31),
    )
    claimed = claim_job(
        connection, worker_id="aged-idle", pool=ROUTINE_POOL,
        now=NOW + timedelta(minutes=31),
    )
    assert claimed and claimed.job_id == idle_id
    assert newer_id != idle_id


def test_retry_job_listing_exposes_schedule_provenance_without_credentials() -> None:
    connection = _connection()
    job_id = _backing_off_job(connection, "listed", retry_at=NOW + timedelta(hours=2))
    row = connection.execute(
        "SELECT state,available_at FROM news_ai_jobs_v1 WHERE job_id=?", (job_id,),
    ).fetchone()
    apply_retry_schedule_override(
        connection, request_id="listed", job_id=job_id, operator_id="owner-1",
        mode="DELAY_1_HOUR", reason="operator plan", expected_state=str(row["state"]),
        expected_available_at=str(row["available_at"]), now=NOW,
    )
    item = next(item for item in list_retry_schedule_jobs(connection) if item["job_id"] == job_id)
    assert item["override_mode"] == "DELAY_1_HOUR"
    assert item["original_available_at"] == row["available_at"]
    assert "credential_id" not in item


def test_provider_dispatch_staggers_independent_accounts_without_quota_leak() -> None:
    connection = _connection()

    assert reserve_account_request(
        connection, account_id="account-a", model_family="gemma-4",
        daily_limit=1_000, requests_per_minute=100,
        provider_task="ACTIVE_IMPACT", now=NOW,
    )
    assert not reserve_account_request(
        connection, account_id="account-b", model_family="gemma-4",
        daily_limit=1_000, requests_per_minute=100,
        provider_task="ACTIVE_IMPACT", now=NOW,
    )
    rows = connection.execute(
        """SELECT account_id,request_count
           FROM news_ai_account_daily_usage_v1 ORDER BY account_id"""
    ).fetchall()
    assert [tuple(row) for row in rows] == [("account-a", 1)]

    assert reserve_account_request(
        connection, account_id="account-b", model_family="gemma-4",
        daily_limit=1_000, requests_per_minute=100,
        provider_task="ACTIVE_IMPACT", now=NOW + timedelta(milliseconds=250),
    )
    rows = connection.execute(
        """SELECT account_id,request_count
           FROM news_ai_account_daily_usage_v1 ORDER BY account_id"""
    ).fetchall()
    assert [tuple(row) for row in rows] == [
        ("account-a", 1), ("account-b", 1),
    ]


@pytest.mark.parametrize(
    ("dimension", "first", "second"),
    (
        ("RPM", {"input_tokens": 1, "requests_per_minute": 1},
         {"input_tokens": 1, "requests_per_minute": 1}),
        ("TPM", {"input_tokens": 10_000, "requests_per_minute": 20},
         {"input_tokens": 5_001, "requests_per_minute": 20}),
        ("RPD", {"input_tokens": 1, "requests_per_minute": 20},
         {"input_tokens": 1, "requests_per_minute": 20}),
    ),
)
def test_capacity_rejection_reports_exact_dimension(
    dimension: str, first: dict[str, int], second: dict[str, int],
) -> None:
    connection = _connection()
    daily_limit = 1 if dimension == "RPD" else 100
    common = {
        "account_id": "account-a", "model_family": "gemma-4",
        "daily_limit": daily_limit, "input_tokens_per_minute": 15_000,
    }
    assert reserve_account_request(connection, now=NOW, **common, **first)
    decision: dict[str, object] = {}

    assert not reserve_account_request(
        connection, now=NOW + timedelta(seconds=1), decision=decision,
        **common, **second,
    )

    assert decision["failure_code"] == "MODEL_CAPACITY_DEFERRED"
    assert decision["dimension"] == dimension
    assert decision["dimensions"] == [dimension]
    assert decision["current"] >= 1
    assert decision["requested"] >= 1
    assert decision["limit"] >= 1
    assert decision["next_retry_at"] is not None


@pytest.mark.parametrize(
    ("outcome", "actual_tokens", "expected_tokens"),
    (
        ("PROVIDER_SUCCEEDED", 4_800, 4_800),
        ("PROVIDER_SUCCEEDED", 6_200, 6_200),
        ("PROVIDER_SUCCEEDED", None, 5_600),
        ("PROVIDER_THROTTLED", None, 5_600),
        ("PROVIDER_FAILED", None, 5_600),
    ),
)
def test_rolling_tpm_uses_actual_only_for_trustworthy_success(
    outcome: str, actual_tokens: int | None, expected_tokens: int,
) -> None:
    connection = _connection()
    assert reserve_provider_dispatch(
        connection, provider_task="ACTIVE_IMPACT",
        now=NOW - timedelta(seconds=1),
    )[0]
    usage_id = f"usage-{outcome}-{actual_tokens}"
    assert reserve_account_request(
        connection,
        account_id="account-a",
        model_family="gemma-4",
        daily_limit=1_000,
        requests_per_minute=20,
        input_tokens=5_600,
        input_tokens_per_minute=15_000,
        usage_id=usage_id,
        requested_model="gemma-4",
        purpose="news-impact",
        prompt_contract="impact-v1",
        estimator_version="estimator-v1",
        base_estimated_input_tokens=5_600,
        now=NOW,
    )
    mark_account_request_attempted(connection, usage_id, now=NOW)
    record_account_request_outcome(
        connection,
        usage_id,
        outcome=outcome,
        usage_metadata=(
            {"prompt_token_count": actual_tokens}
            if actual_tokens is not None else None
        ),
        provider_model_version=("gemma-exact-v1" if actual_tokens else None),
        now=NOW + timedelta(milliseconds=100),
    )

    assert rolling_account_usage(
        connection,
        account_id="account-a",
        model_families=("gemma-4",),
        now=NOW + timedelta(seconds=1),
    ) == (1, expected_tokens)
    row = connection.execute(
        """SELECT input_token_count,base_estimated_input_tokens,
                  admitted_input_tokens,provider_prompt_token_count
           FROM news_ai_account_request_usage_v1 WHERE usage_id=?""",
        (usage_id,),
    ).fetchone()
    assert tuple(row) == (5_600, 5_600, 5_600, actual_tokens)


def test_rolling_usage_excludes_reservations_after_observation_time() -> None:
    connection = _connection()
    common = {
        "connection": connection,
        "account_id": "account-a",
        "model_family": "gemini-test",
        "daily_limit": 1_000,
        "requests_per_minute": 20,
        "input_tokens_per_minute": 15_000,
    }
    assert reserve_account_request(
        **common, input_tokens=1_000, usage_id="past",
        now=NOW - timedelta(seconds=30),
    )
    assert reserve_account_request(
        **common, input_tokens=2_000, usage_id="future",
        now=NOW + timedelta(seconds=1),
    )

    assert rolling_account_usage(
        connection,
        account_id="account-a",
        model_families=("gemini-test",),
        now=NOW,
    ) == (1, 1_000)


def test_actual_token_correction_changes_admission_and_exact_expiry() -> None:
    def corrected_connection(actual_tokens: int) -> sqlite3.Connection:
        connection = _connection()
        assert reserve_provider_dispatch(
            connection, provider_task="ACTIVE_IMPACT",
            now=NOW - timedelta(seconds=1),
        )[0]
        assert reserve_account_request(
            connection,
            account_id="account-a", model_family="gemma-4",
            daily_limit=1_000, requests_per_minute=20,
            input_tokens=5_600, input_tokens_per_minute=15_000,
            usage_id="first", requested_model="gemma-4",
            purpose="news-impact", prompt_contract="impact-v1",
            estimator_version="estimator-v1",
            base_estimated_input_tokens=5_600, now=NOW,
        )
        mark_account_request_attempted(connection, "first", now=NOW)
        record_account_request_outcome(
            connection, "first", outcome="PROVIDER_SUCCEEDED",
            usage_metadata={"prompt_token_count": actual_tokens},
            provider_model_version="gemma-exact-v1",
            now=NOW + timedelta(milliseconds=100),
        )
        return connection

    refunded = corrected_connection(4_800)
    assert reserve_account_request(
        refunded,
        account_id="account-a", model_family="gemma-4",
        daily_limit=1_000, requests_per_minute=20,
        input_tokens=10_200, input_tokens_per_minute=15_000,
        now=NOW + timedelta(seconds=1),
    )

    charged = corrected_connection(6_200)
    decision: dict[str, object] = {}
    assert not reserve_account_request(
        charged,
        account_id="account-a", model_family="gemma-4",
        daily_limit=1_000, requests_per_minute=20,
        input_tokens=8_801, input_tokens_per_minute=15_000,
        now=NOW + timedelta(seconds=1), decision=decision,
    )
    assert decision["dimension"] == "TPM"
    assert decision["current"] == 6_200
    assert decision["next_retry_at"] == (
        NOW + timedelta(seconds=60)
    ).isoformat(timespec="microseconds")


def test_provider_dispatch_adapts_to_success_and_retry_after() -> None:
    connection = _connection()
    granted, _ = reserve_provider_dispatch(
        connection, provider_task="ACTIVE_IMPACT", now=NOW,
    )
    assert granted
    record_provider_dispatch_outcome(
        connection, outcome="PROVIDER_SUCCEEDED",
        now=NOW + timedelta(milliseconds=10),
    )
    row = connection.execute(
        "SELECT interval_ms FROM news_ai_provider_dispatch_state_v1"
    ).fetchone()
    assert row[0] == 225

    record_provider_dispatch_outcome(
        connection, outcome="PROVIDER_THROTTLED", retry_after_seconds=2,
        now=NOW + timedelta(milliseconds=20),
    )
    row = connection.execute(
        """SELECT interval_ms,cooldown_until
           FROM news_ai_provider_dispatch_state_v1"""
    ).fetchone()
    assert row[0] == 450
    assert datetime.fromisoformat(row[1]) == NOW + timedelta(milliseconds=20, seconds=2)
    assert reserve_provider_dispatch(
        connection, provider_task="ACTIVE_ANNOTATION",
        now=NOW + timedelta(seconds=1),
    )[0] is False
    assert reserve_provider_dispatch(
        connection, provider_task="ACTIVE_ANNOTATION",
        now=NOW + timedelta(seconds=2, milliseconds=20),
    )[0] is True
    record_provider_dispatch_outcome(
        connection, outcome="PROVIDER_SUCCEEDED",
        now=NOW + timedelta(seconds=2, milliseconds=30),
    )
    assert connection.execute(
        "SELECT interval_ms FROM news_ai_provider_dispatch_state_v1"
    ).fetchone()[0] == 405


def test_live_admission_is_not_reduced_by_backfill_reserves() -> None:
    connection = _connection()
    _seed_complete_live_quota_days(connection)
    assert reserve_account_request(
        connection, account_id="account-a", model_family="gemini-model",
        daily_limit=2_500, requests_per_minute=10, now=NOW,
        quota_authority="gemini_quota",
    )


def test_backfill_gets_only_forecast_safe_remaining_daily_budget() -> None:
    connection = _connection()
    _seed_complete_live_quota_days(connection)
    assert reserve_account_request(
        connection, account_id="account-a", model_family="gemini-model",
        daily_limit=2_500, requests_per_minute=1_000, request_count=800,
        now=NOW - timedelta(seconds=122), quota_authority="gemini_quota",
    )
    decision: dict[str, object] = {}
    assert reserve_account_request(
        connection, account_id="account-a", model_family="gemini-model",
        daily_limit=2_500, requests_per_minute=1_000, request_count=300,
        now=NOW, workload_class="CONTRACT_BACKFILL",
        quota_authority="gemini_quota", decision=decision,
    )
    denied: dict[str, object] = {}
    assert not reserve_account_request(
        connection, account_id="account-a", model_family="gemini-model",
        daily_limit=2_500, requests_per_minute=1_000, request_count=1,
        now=NOW + timedelta(seconds=61), workload_class="CONTRACT_BACKFILL",
        quota_authority="gemini_quota", decision=denied,
    )
    assert denied["failure_code"] == "BACKFILL_BUDGET_DEFERRED"
    assert denied["reason"] == "NO_SAFE_DAILY_BUDGET"
    assert denied["safe_backfill_budget"] == 0
    assert reserve_account_request(
        connection, account_id="account-a", model_family="gemini-model",
        daily_limit=2_500, requests_per_minute=2_000, request_count=1_000,
        now=NOW + timedelta(seconds=122), quota_authority="gemini_quota",
    )


def test_backfill_budget_examples_clamp_at_forecast_safe_surplus() -> None:
    assert forecast_safe_backfill_budget(
        remaining_capacity=1_700, predicted_live=1_000,
        operational_retry_reserve=200, safety_buffer=200,
    ) == 300
    assert forecast_safe_backfill_budget(
        remaining_capacity=800, predicted_live=700,
        operational_retry_reserve=100, safety_buffer=100,
    ) == 0


def test_live_claimable_work_preempts_backfill_before_quota_is_reserved() -> None:
    connection = _connection()
    _seed_complete_live_quota_days(connection, remaining_live=0)
    _enqueue(connection, "new-live")
    decision: dict[str, object] = {}
    assert not reserve_account_request(
        connection, account_id="account-a", model_family="gemini-model",
        daily_limit=2_500, requests_per_minute=10, now=NOW,
        workload_class="CONTRACT_BACKFILL", quota_authority="gemini_quota",
        decision=decision,
    )
    assert decision["reason"] == "LIVE_CLAIMABLE"
    assert connection.execute(
        "SELECT count(*) FROM news_ai_account_request_usage_v1"
    ).fetchone()[0] == 0


def test_provider_throttle_halts_backfill_but_not_live_admission() -> None:
    connection = _connection()
    _seed_complete_live_quota_days(connection, remaining_live=0)
    assert reserve_account_request(
        connection, account_id="account-a", model_family="gemini-model",
        daily_limit=2_500, requests_per_minute=10, now=NOW - timedelta(minutes=1),
        usage_id="throttled", quota_authority="gemini_quota",
    )
    mark_account_request_attempted(connection, "throttled", now=NOW)
    record_account_request_outcome(
        connection, "throttled", outcome="PROVIDER_THROTTLED", now=NOW,
    )
    decision: dict[str, object] = {}
    assert not reserve_account_request(
        connection, account_id="account-a", model_family="gemini-model",
        daily_limit=2_500, requests_per_minute=10, now=NOW,
        workload_class="CONTRACT_BACKFILL", quota_authority="gemini_quota",
        decision=decision,
    )
    assert decision["reason"] == "PROVIDER_THROTTLED"
    assert reserve_account_request(
        connection, account_id="account-a", model_family="gemini-model",
        daily_limit=2_500, requests_per_minute=10, now=NOW,
        quota_authority="gemini_quota",
    )


def test_backfill_cold_start_fails_closed_without_consuming_quota() -> None:
    connection = _connection()
    decision: dict[str, object] = {}
    assert not reserve_account_request(
        connection, account_id="account-a", model_family="gemini-model",
        daily_limit=2_500, requests_per_minute=10, now=NOW,
        workload_class="CONTRACT_BACKFILL", quota_authority="gemini_quota",
        decision=decision,
    )
    assert decision["reason"] == "COLD_START_HISTORY"
    assert decision["history_days"] == 0
    assert connection.execute(
        "SELECT count(*) FROM news_ai_account_request_usage_v1"
    ).fetchone()[0] == 0


def test_backfill_budget_recomputes_at_pacific_quota_reset() -> None:
    connection = _connection()
    _seed_complete_live_quota_days(connection, remaining_live=0)
    current_day = news_scheduler_module.quota_day(NOW)
    connection.execute(
        "INSERT INTO news_ai_account_daily_usage_v1 VALUES (?,?,?,?,?)",
        (current_day, "account-a", "gemini-model", 2_500, NOW.isoformat()),
    )
    connection.execute(
        """INSERT INTO news_ai_quota_day_workload_v1 VALUES
           (?,?,?,?,?,?,?)""",
        (
            current_day, 19, "account-a", "gemini_quota",
            "CONTRACT_BACKFILL", 2_500, NOW.isoformat(),
        ),
    )
    connection.commit()
    denied: dict[str, object] = {}
    assert not reserve_account_request(
        connection, account_id="account-a", model_family="gemini-model",
        daily_limit=2_500, requests_per_minute=10, now=NOW,
        workload_class="CONTRACT_BACKFILL", quota_authority="gemini_quota",
        decision=denied,
    )
    assert denied["reason"] == "NO_SAFE_DAILY_BUDGET"

    assert reserve_account_request(
        connection, account_id="account-a", model_family="gemini-model",
        daily_limit=2_500, requests_per_minute=10,
        now=NOW + timedelta(days=1), workload_class="CONTRACT_BACKFILL",
        quota_authority="gemini_quota",
    )


def test_semantic_transition_taxonomy_requires_review_only_when_declared() -> None:
    assert transition_for("same", "same").kind == REUSE_COMPATIBLE
    assert provider_dispatches_for_transition(
        transition_for("same", "same"),
    ) == 0
    assert requires_model_review(
        PREVIOUS_NEWS_PROMPT_VERSION, CURRENT_NEWS_PROMPT_VERSION,
    )
    assert transition_for(
        PREVIOUS_NEWS_PROMPT_VERSION, CURRENT_NEWS_PROMPT_VERSION,
    ).kind == MODEL_REVIEW_REQUIRED
    deterministic = SemanticTransition("a", "b", DETERMINISTIC_MIGRATION, "fixture")
    assert provider_dispatches_for_transition(deterministic) == 0
    assert provider_dispatches_for_transition(transition_for(
        PREVIOUS_NEWS_PROMPT_VERSION, CURRENT_NEWS_PROMPT_VERSION,
    )) == 1
    with pytest.raises(ValueError, match="not declared"):
        transition_for("unknown-v1", "unknown-v2")


def test_training_and_archival_history_are_demand_driven() -> None:
    assert not demand_allows_scheduling(TRAINING_REQUIRED)
    assert demand_allows_scheduling(
        TRAINING_REQUIRED, training_generation_requested=True,
    )
    assert not demand_allows_scheduling(
        ARCHIVAL_ONLY, training_generation_requested=True,
    )


def test_training_promotion_still_cannot_consume_live_reserve() -> None:
    connection = _connection()
    _seed_complete_live_quota_days(connection, remaining_live=0)
    _enqueue(connection, "current-live")
    assert demand_allows_scheduling(
        TRAINING_REQUIRED, training_generation_requested=True,
    )
    decision: dict[str, object] = {}
    assert not reserve_account_request(
        connection, account_id="account-a", model_family="gemini-model",
        daily_limit=2_500, requests_per_minute=10, now=NOW,
        workload_class="CONTRACT_BACKFILL", quota_authority="gemini_quota",
        decision=decision,
    )
    assert decision["reason"] == "LIVE_CLAIMABLE"


def test_embedding_dispatch_rises_when_dependency_fanout_dominates() -> None:
    connection = _connection()
    _enqueue(connection, "live-annotation", task_type="ACTIVE_ANNOTATION")
    for index in range(3):
        job_id = _enqueue(
            connection, f"blocked-impact-{index}", task_type="ACTIVE_IMPACT",
        )
        connection.execute(
            """UPDATE news_ai_jobs_v1 SET last_error=? WHERE job_id=?""",
            ("NEWS_EMBEDDING_BACKFILL_PENDING", job_id),
        )
    connection.commit()

    assert reserve_provider_dispatch(
        connection, provider_task="news-annotation", now=NOW,
    )[0] is True
    assert reserve_provider_dispatch(
        connection, provider_task="NEWS_EMBEDDING_BACKFILL",
        now=NOW + timedelta(milliseconds=100),
    )[0] is False
    assert reserve_provider_dispatch(
        connection, provider_task="news-annotation",
        now=NOW + timedelta(milliseconds=250),
    )[0] is False
    granted, _ = reserve_provider_dispatch(
        connection, provider_task="NEWS_EMBEDDING_BACKFILL",
        now=NOW + timedelta(milliseconds=250),
    )
    assert granted is True
    row = connection.execute(
        """SELECT last_pressure_json FROM news_ai_provider_dispatch_task_state_v1
           WHERE task_class='EMBEDDING'"""
    ).fetchone()
    assert json.loads(row[0])["dependency_fanout"] == 3


def test_annotation_surge_takes_priority_after_embedding_pressure_falls() -> None:
    connection = _connection()
    for index in range(5):
        _enqueue(connection, f"fresh-annotation-{index}")

    assert reserve_provider_dispatch(
        connection, provider_task="NEWS_EMBEDDING_BACKFILL", now=NOW,
    )[0] is True
    assert reserve_provider_dispatch(
        connection, provider_task="news-annotation",
        now=NOW + timedelta(milliseconds=100),
    )[0] is False
    assert reserve_provider_dispatch(
        connection, provider_task="NEWS_EMBEDDING_BACKFILL",
        now=NOW + timedelta(milliseconds=250),
    )[0] is False
    assert reserve_provider_dispatch(
        connection, provider_task="news-annotation",
        now=NOW + timedelta(milliseconds=250),
    )[0] is True


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


@pytest.mark.parametrize(
    ("work_lane", "priority"),
    (
        (news_scheduler_module.LIVE_LANE, "NORMAL"),
        (news_scheduler_module.CONTRACT_BACKFILL_LANE, "BACKGROUND"),
    ),
)
def test_provider_terminal_display_checkpoint_recovery_preserves_job_state(
    tmp_path, work_lane, priority,
) -> None:
    ledger = ForwardLedger(tmp_path / "forward.sqlite3", now=NOW)
    body = "Complete source evidence for durable display repair recovery. " * 12
    digest = hashlib.sha256(body.encode()).hexdigest()
    ledger.append_news_revision({
        "source": "source", "source_item_id": f"checkpoint-{work_lane}",
        "source_published_time": NOW, "collector_first_seen_time": NOW,
        "fetched_time": NOW, "headline": "Report", "body": body,
        "content_hash": digest, "cluster_id": f"checkpoint-{work_lane}",
    })
    job_id = enqueue_job(
        ledger.connection, task_type="ACTIVE_ANNOTATION",
        source="source", source_item_id=f"checkpoint-{work_lane}",
        revision_number=1, prompt_version=CURRENT_NEWS_PROMPT_VERSION,
        priority=priority, work_lane=work_lane, now=NOW,
    )
    ledger.connection.execute(
        """UPDATE news_ai_jobs_v1
           SET state='DEAD_LETTER',attempt_count=1,
               last_error='HTTP Error 503: Service Unavailable',
               completed_at=?,updated_at=? WHERE job_id=?""",
        (NOW.isoformat(), NOW.isoformat(), job_id),
    )
    ledger.connection.execute(
        """INSERT INTO news_annotation_display_checkpoints_v1 VALUES
           (?,?,?,?,?,?,?,?,?,?,?)""",
        (
            f"checkpoint-id-{work_lane}", "source",
            f"checkpoint-{work_lane}", 1, digest,
            "gemini-3.5-flash-lite", CURRENT_NEWS_PROMPT_VERSION,
            "{}", '["primary_story_title_zh"]',
            "UNGROUNDED_LATIN_DISPLAY", NOW.isoformat(),
        ),
    )
    ledger.append_llm_failure({
        "failure_id": f"failure-{work_lane}", "task_type": "ANNOTATION",
        "source": "source", "source_item_id": f"checkpoint-{work_lane}",
        "revision_number": 1, "raw_content_hash": digest,
        "llm_model_version": "gemini-3.5-flash-lite",
        "prompt_version": CURRENT_NEWS_PROMPT_VERSION, "attempt_number": 5,
        "error_type": "HTTPError", "error_signature": "provider-503",
        "error": "HTTP Error 503: Service Unavailable", "failed_at": NOW,
        "is_terminal": True,
    })

    recovery_at = datetime.now(UTC)
    assert authorize_repairable_annotation_failures(
        ledger.connection,
        prompt_version=CURRENT_NEWS_PROMPT_VERSION,
        recovery_version=ANNOTATION_FAILURE_RECOVERY_VERSION,
        now=recovery_at,
    ) == 1
    recovered = ledger.connection.execute(
        """SELECT state,work_lane,priority,attempt_count,last_error
           FROM news_ai_jobs_v1 WHERE job_id=?""",
        (job_id,),
    ).fetchone()
    assert tuple(recovered) == ("QUEUED", work_lane, priority, 1, None)
    assert authorize_repairable_annotation_failures(
        ledger.connection,
        prompt_version=CURRENT_NEWS_PROMPT_VERSION,
        recovery_version=ANNOTATION_FAILURE_RECOVERY_VERSION,
        now=recovery_at + timedelta(minutes=1),
    ) == 0
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


def test_credential_identity_is_stable_unique_and_secret_safe() -> None:
    first = configured_api_credentials(legacy_keys=("secret-a", "secret-b"))
    restarted = configured_api_credentials(legacy_keys=("secret-a", "secret-b"))

    assert first == restarted
    assert len({item.credential_id for item in first}) == 2
    assert all(item.credential_id.startswith("hmac-v1-") for item in first)
    assert all(len(item.credential_id) == 40 for item in first)
    assert all(item.api_key not in item.credential_id for item in first)
    assert all(item.api_key not in repr(item) for item in first)


def test_explicit_credential_ids_preserve_historical_identity() -> None:
    raw_accounts = json.dumps([{
        "account_id": "legacy-existing-id",
        "pool": "routine",
        "api_keys": ["secret-a", "secret-b"],
        "credential_ids": ["existing-id-a", "existing-id-b"],
    }])

    credentials = configured_api_credentials(raw_accounts=raw_accounts)

    assert [(item.account_id, item.credential_id) for item in credentials] == [
        ("legacy-existing-id", "existing-id-a"),
        ("legacy-existing-id", "existing-id-b"),
    ]
    assert "secret-a" not in repr(credentials)
    assert "secret-b" not in repr(credentials)


@pytest.mark.parametrize(
    "credential_ids",
    [
        [], ["only-one"], ["", "second"],
        ["secret-a", "second"], ["same", "same"],
    ],
)
def test_explicit_credential_ids_fail_closed_on_invalid_migration(
    credential_ids: list[str],
) -> None:
    raw_accounts = json.dumps([{
        "account_id": "account",
        "pool": "routine",
        "api_keys": ["secret-a", "secret-b"],
        "credential_ids": credential_ids,
    }])

    with pytest.raises(ValueError, match="credential_id") as error:
        configured_api_credentials(raw_accounts=raw_accounts)

    assert "secret-a" not in str(error.value)
    assert "secret-b" not in str(error.value)


@pytest.mark.parametrize(
    "raw_accounts",
    [
        [{
            "account_id": "account",
            "pool": "routine",
            "api_keys": ["secret-a", "secret-b"],
            "credential_ids": ["secret-b", "safe-id"],
        }],
        [
            {
                "account_id": "account-a",
                "pool": "routine",
                "api_keys": ["secret-a"],
                "credential_ids": ["safe-a"],
            },
            {
                "account_id": "account-b",
                "pool": "routine",
                "api_keys": ["secret-b"],
                "credential_ids": ["contains-secret-a"],
            },
        ],
        [
            {
                "account_id": "account-secret-b",
                "pool": "routine",
                "api_keys": ["secret-a"],
                "credential_ids": ["safe-a"],
            },
            {
                "account_id": "account-b",
                "pool": "routine",
                "api_keys": ["secret-b"],
                "credential_ids": ["safe-b"],
            },
        ],
    ],
)
def test_operational_ids_reject_any_configured_api_key_without_exposure(
    raw_accounts: list[dict[str, object]],
) -> None:
    with pytest.raises(ValueError) as error:
        configured_api_credentials(raw_accounts=json.dumps(raw_accounts))

    assert "secret-a" not in str(error.value)
    assert "secret-b" not in str(error.value)


def test_account_quota_snapshot_uses_scheduler_usage_without_double_counting() -> None:
    connection = _connection()
    credentials = configured_api_credentials(raw_accounts=json.dumps([
        {
            "account_id": "shared", "pool": "routine",
            "api_keys": ["secret-key-one", "secret-key-two"],
        },
        {
            "account_id": "single", "pool": "routine",
            "api_keys": ["secret-key-three"],
        },
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


def test_gemini_quota_snapshot_exposes_bounded_secret_safe_backfill_evidence() -> None:
    connection = _connection()
    credentials = (
        ApiCredential("private-account", ROUTINE_POOL, "secret-key", "safe-ref"),
    )
    snapshot = account_quota_snapshot(
        connection, credentials,
        model_families=("gemini-model",), daily_limit=2_500,
        quota_authority="gemini_quota", now=NOW,
    )

    account = snapshot["keys"][0]
    assert account["fingerprint"] == "safe-ref"
    assert "private-account" not in json.dumps(snapshot)
    assert "secret-key" not in json.dumps(snapshot)
    assert account["contract_backfill"] == {
        "predicted_remaining_live_demand": 0,
        "operational_retry_reserve": 200,
        "safety_buffer": 200,
        "spendable_remaining": 2_100,
        "requests_today": 0,
        "dispatch_allowed": False,
        "deferred_reason": "COLD_START_HISTORY",
        "forecast_window_days": 14,
    }


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
            {
                "account_id": "a", "pool": "routine",
                "api_keys": ["same-secret-key"],
            },
            {
                "account_id": "b", "pool": "routine",
                "api_keys": ["same-secret-key"],
            },
        ]))


def test_legacy_keys_are_independent_routine_accounts() -> None:
    credentials = configured_api_credentials(legacy_keys=(
        "legacy-secret-a", "legacy-secret-b", "legacy-secret-a",
    ))

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
        """SELECT state,last_error,updated_at,completed_at
           FROM news_ai_jobs_v1 WHERE job_id=?""",
        (job_id,),
    ).fetchone()
    assert tuple(row[:2]) == (
        "DEAD_LETTER", "CURRENT_EVIDENCE_NO_LONGER_ELIGIBLE",
    )
    assert reconcile_completed_jobs(
        ledger.connection, now=NOW + timedelta(minutes=3),
    ) == 0
    reconciled_again = ledger.connection.execute(
        """SELECT state,last_error,updated_at,completed_at
           FROM news_ai_jobs_v1 WHERE job_id=?""",
        (job_id,),
    ).fetchone()
    assert tuple(reconciled_again) == tuple(row)
    counts = scheduler_counts(ledger.connection)
    assert counts["obsolete"] == 1
    assert counts["dead_letter"] == 0
    ledger.close()


def test_late_discovery_is_admitted_to_annotation_scheduler(tmp_path) -> None:
    ledger = ForwardLedger(
        tmp_path / "forward.sqlite3", now=NOW - timedelta(hours=3),
    )
    body = "Complete but late economic report. " * 20
    ledger.append_news_revision({
        "source": "google_news_us_inflation", "source_item_id": "late",
        "source_published_time": NOW - timedelta(hours=2),
        "collector_first_seen_time": NOW, "fetched_time": NOW,
        "headline": "Old CPI report collected recently", "body": body,
        "content_hash": hashlib.sha256(body.encode()).hexdigest(),
        "cluster_id": "late-cluster",
    })

    discovered = sync_pending_jobs(ledger.connection, now=NOW)

    assert discovered["ACTIVE_ANNOTATION"] == 1
    assert ledger.connection.execute(
        "SELECT count(*) FROM news_ai_jobs_v1 "
        "WHERE task_type='ACTIVE_ANNOTATION'"
    ).fetchone()[0] == 1
    ledger.close()


def test_small_positive_publication_skew_enters_annotation_scheduler(tmp_path) -> None:
    ledger = ForwardLedger(tmp_path / "forward.sqlite3", now=NOW - timedelta(days=1))
    body = "Complete production-shaped Federal Reserve report. " * 20
    ledger.append_news_revision({
        "source": "google_news_fed_rates", "source_item_id": "skew-2.3s",
        "source_published_time": NOW + timedelta(seconds=2.3),
        "collector_first_seen_time": NOW, "fetched_time": NOW,
        "headline": "Federal Reserve report", "body": body,
        "content_hash": hashlib.sha256(body.encode()).hexdigest(),
        "cluster_id": "skew-2.3s-cluster",
    })

    discovered = sync_pending_jobs(ledger.connection, now=NOW)

    assert discovered["ACTIVE_ANNOTATION"] == 1
    assert ledger.connection.execute(
        "SELECT count(*) FROM news_ai_jobs_v1 "
        "WHERE task_type='ACTIVE_ANNOTATION'"
    ).fetchone()[0] == 1
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
    ledger = ForwardLedger(
        tmp_path / "forward.sqlite3", now=NOW - timedelta(days=3),
    )
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
    for item in ("oldest", "newest"):
        digest = hashlib.sha256(item.encode()).hexdigest()
        ledger.append_news_revision({
            "source": "scheduler-fairness", "source_item_id": item,
            "source_published_time": NOW, "collector_first_seen_time": NOW,
            "fetched_time": NOW, "headline": item, "body": "body",
            "content_hash": digest, "cluster_id": item,
        })
        ledger.connection.execute(
            """INSERT INTO news_annotations VALUES (
               ?,?, ?,1,?,'EVENT','[]',0,0,0,0,0,0,1,
               'gemini-3.5-flash-lite',?,?,?,?)""",
            (
                f"annotation-{item}", "scheduler-fairness", item,
                digest, CURRENT_NEWS_PROMPT_VERSION,
                NOW.isoformat(), NOW.isoformat(), "{}",
            ),
        )
        parent = enqueue_job(
            ledger.connection, task_type="ACTIVE_ANNOTATION",
            source="scheduler-fairness", source_item_id=item,
            revision_number=1, prompt_version=CURRENT_NEWS_PROMPT_VERSION,
            priority="FAST", now=NOW,
        )
        ledger.connection.execute(
            """UPDATE news_ai_jobs_v1 SET state='COMPLETED',completed_at=?
               WHERE job_id=?""",
            (NOW.isoformat(), parent),
        )
    ledger.connection.commit()

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
    ledger = ForwardLedger(
        tmp_path / "forward.sqlite3", now=NOW - timedelta(days=3),
    )

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
    monkeypatch.setattr(runner.scheduler_runtime, "configured_api_credentials", lambda: credentials)
    monkeypatch.setattr(runner.scheduler_runtime, "sync_pending_jobs", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(
        runner.scheduler_runtime,
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
    monkeypatch.setattr(runner.scheduler_runtime, "configured_api_credentials", lambda: credentials)
    monkeypatch.setattr(runner.scheduler_runtime, "sync_pending_jobs", lambda *_args, **_kwargs: {})
    calls = []

    def execute(_ledger, credential, _job, **_kwargs):
        calls.append(credential.account_id)
        if credential.account_id == "account-a":
            return {"status": "DEFERRED", "reason": "quota"}
        if credential.account_id == "account-b":
            return {"status": "ERROR", "provider_http_status": 503,
                    "error": "temporarily unavailable"}
        return {"status": "OK"}

    monkeypatch.setattr(runner.scheduler_runtime, "_execute_job", execute)

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

    monkeypatch.setattr(runner.scheduler_runtime, "_execute_job", pending)

    status = runner._execute_job_safely(
        object(), object(), object(), now=NOW,
    )

    assert status["status"] == "DEFERRED"
    assert status["failure_code"] == "NEWS_EMBEDDING_BACKFILL_PENDING"
    assert runner._may_try_another_credential(status) is False


def test_embedding_provider_throttle_wait_does_not_consume_impact_attempt(
    tmp_path, monkeypatch,
) -> None:
    from scripts import run_news_annotator as runner
    from xauusd_forecaster.gemini_embeddings import GeminiEmbeddingFailure

    ledger = ForwardLedger(tmp_path / "forward.sqlite3", now=NOW)
    job_id = enqueue_job(
        ledger.connection, task_type="ACTIVE_IMPACT", source="source",
        source_item_id="throttled", revision_number=1,
        annotation_id="annotation", prompt_version="prompt", priority="FAST",
        now=datetime.now(UTC) - timedelta(minutes=1),
    )
    credential = ApiCredential(
        "account-a", ROUTINE_POOL, "not-a-real-key", "credential-a",
    )
    monkeypatch.setattr(runner.scheduler_runtime, "configured_api_credentials", lambda: (credential,))
    monkeypatch.setattr(runner.scheduler_runtime, "sync_pending_jobs", lambda *_args, **_kwargs: {})
    retry_at = datetime.now(UTC) + timedelta(minutes=5)

    def throttled(*_args, **_kwargs):
        error = GeminiEmbeddingFailure(
            "provider throttled",
            failure_code="NEWS_EMBEDDING_PROVIDER_THROTTLED",
            provider_http_status=429,
            retry_after_seconds=300,
            diagnostic={"batch_item_count": 20, "estimated_input_tokens": 25000},
        )
        error.next_retry_at = retry_at.isoformat()
        raise error

    monkeypatch.setattr(runner.scheduler_runtime, "_execute_job", throttled)

    statuses = runner.run_scheduled_batch(ledger, batch_size=3)

    assert len(statuses) == 1
    assert statuses[0]["failure_code"] == "NEWS_EMBEDDING_PROVIDER_THROTTLED"
    assert statuses[0]["provider_http_status"] == 429
    row = ledger.connection.execute(
        "SELECT state,attempt_count,last_error,available_at "
        "FROM news_ai_jobs_v1 WHERE job_id=?", (job_id,),
    ).fetchone()
    assert row["state"] == "QUEUED"
    assert row["attempt_count"] == 0
    assert row["last_error"] == "NEWS_EMBEDDING_PROVIDER_THROTTLED"
    assert datetime.fromisoformat(row["available_at"]) == retry_at
    assert ledger.connection.execute(
        "SELECT count(*) FROM news_ai_job_attempts_v1 WHERE job_id=?", (job_id,),
    ).fetchone()[0] == 0
    ledger.close()


def test_provider_dispatch_deferral_does_not_probe_accounts_or_consume_attempt(
    tmp_path, monkeypatch,
) -> None:
    from scripts import run_news_annotator as runner

    ledger = ForwardLedger(tmp_path / "forward.sqlite3", now=NOW)
    job_id = enqueue_job(
        ledger.connection, task_type="ACTIVE_IMPACT", source="source",
        source_item_id="paced", revision_number=1,
        annotation_id="annotation", prompt_version="prompt", priority="FAST",
        now=datetime.now(UTC) - timedelta(minutes=1),
    )
    credentials = tuple(
        ApiCredential(f"account-{name}", ROUTINE_POOL, f"key-{name}", name)
        for name in ("a", "b", "c")
    )
    retry_at = datetime.now(UTC) + timedelta(milliseconds=250)
    calls = []
    monkeypatch.setattr(runner.scheduler_runtime, "configured_api_credentials", lambda: credentials)
    monkeypatch.setattr(runner.scheduler_runtime, "sync_pending_jobs", lambda *_args, **_kwargs: {})

    def deferred(_ledger, credential, _job, **_kwargs):
        calls.append(credential.account_id)
        return {
            "status": "DEFERRED",
            "failure_code": "PROVIDER_DISPATCH_DEFERRED",
            "next_retry_at": retry_at.isoformat(),
        }

    monkeypatch.setattr(runner.scheduler_runtime, "_execute_job", deferred)
    statuses = runner.run_scheduled_batch(ledger, batch_size=3)

    assert calls == ["account-a"]
    assert statuses[0]["attempted_credentials"] == 0
    row = ledger.connection.execute(
        """SELECT state,attempt_count,available_at FROM news_ai_jobs_v1
           WHERE job_id=?""", (job_id,),
    ).fetchone()
    assert tuple(row) == ("QUEUED", 0, retry_at.isoformat())
    assert ledger.connection.execute(
        "SELECT count(*) FROM news_ai_job_attempts_v1 WHERE job_id=?", (job_id,),
    ).fetchone()[0] == 0
    deferral = ledger.connection.execute(
        """SELECT failure_code,next_retry_at
           FROM news_ai_scheduler_deferrals_v1 WHERE job_id=?""", (job_id,),
    ).fetchone()
    assert tuple(deferral) == (
        "PROVIDER_DISPATCH_DEFERRED", retry_at.isoformat(),
    )
    ledger.close()


@pytest.mark.parametrize(
    ("failure_code", "expected_models", "expected_status"),
    (
        (
            "PROVIDER_DISPATCH_DEFERRED",
            ("gemini-3.5-flash-lite",),
            "DEFERRED",
        ),
        (
            "BACKFILL_BUDGET_DEFERRED",
            ("gemini-3.5-flash-lite",),
            "DEFERRED",
        ),
        (
            "MODEL_CAPACITY_DEFERRED",
            ("gemini-3.5-flash-lite", "gemini-3.1-flash-lite"),
            "OK",
        ),
    ),
)
def test_annotation_fallback_never_crosses_maintenance_deferral(
    tmp_path, monkeypatch, failure_code: str,
    expected_models: tuple[str, ...], expected_status: str,
) -> None:
    from scripts import run_news_annotator as runner

    ledger = ForwardLedger(tmp_path / "forward.sqlite3", now=NOW)
    job = SimpleNamespace(
        task_type="ACTIVE_ANNOTATION",
        priority="FAST",
        work_lane="LIVE",
    )
    credential = ApiCredential(
        "account-a", ROUTINE_POOL, "key-a", "credential-a",
    )
    calls = []
    monkeypatch.setattr(
        runner.scheduler_runtime, "pending_record_for_job",
        lambda *_args, **_kwargs: {"source_item_id": "current"},
    )

    def annotate(*_args, model, **_kwargs):
        calls.append(model)
        if len(calls) == 1:
            return [{
                "status": "DEFERRED",
                "failure_code": failure_code,
            }]
        return [{"status": "OK"}]

    monkeypatch.setattr(runner.scheduler_runtime, "annotate_pending_news", annotate)

    status = runner._execute_job(
        ledger, credential, job, now=NOW,
    )

    assert tuple(calls) == expected_models
    assert status["status"] == expected_status
    ledger.close()


def test_backfill_budget_deferral_is_non_attempt_healthy_pacing(
    tmp_path, monkeypatch,
) -> None:
    from scripts import run_news_annotator as runner

    ledger = ForwardLedger(tmp_path / "forward.sqlite3", now=NOW)
    job_id = enqueue_job(
        ledger.connection, task_type="ACTIVE_ANNOTATION", source="source",
        source_item_id="historical", revision_number=1,
        prompt_version="prompt", priority="BACKGROUND",
        work_lane="CONTRACT_BACKFILL",
        now=datetime.now(UTC) - timedelta(days=1),
    )
    credential = ApiCredential("account-a", ROUTINE_POOL, "key-a", "credential-a")
    retry_at = datetime.now(UTC) + timedelta(minutes=1)
    monkeypatch.setattr(runner.scheduler_runtime, "configured_api_credentials", lambda: (credential,))
    monkeypatch.setattr(runner.scheduler_runtime, "sync_pending_jobs", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(
        runner.scheduler_runtime, "_execute_job",
        lambda *_args, **_kwargs: {
            "status": "DEFERRED",
            "failure_code": "BACKFILL_BUDGET_DEFERRED",
            "reason": "COLD_START_HISTORY",
            "next_retry_at": retry_at.isoformat(),
        },
    )

    statuses = runner.run_scheduled_batch(ledger, batch_size=1)

    assert statuses[0]["attempted_credentials"] == 0
    row = ledger.connection.execute(
        """SELECT state,attempt_count,available_at,last_error
           FROM news_ai_jobs_v1 WHERE job_id=?""", (job_id,),
    ).fetchone()
    assert tuple(row) == (
        "QUEUED", 0, retry_at.isoformat(), "BACKFILL_BUDGET_DEFERRED",
    )
    assert ledger.connection.execute(
        "SELECT count(*) FROM news_ai_job_attempts_v1 WHERE job_id=?", (job_id,),
    ).fetchone()[0] == 0
    ledger.close()


def test_scheduler_deferral_retention_is_bounded_to_24_hours() -> None:
    connection = _connection()
    _enqueue(connection, "retention", task_type="ACTIVE_IMPACT")
    job = claim_job(
        connection, worker_id="retention-worker", pool=ROUTINE_POOL, now=NOW,
    )
    assert job is not None
    credential = ApiCredential("account", ROUTINE_POOL, "key", "credential")
    with connection:
        connection.executemany(
            """INSERT INTO news_ai_scheduler_deferrals_v1
               VALUES (?,?,?,?,?,?,?,?)""",
            (
                (
                    "expired", job.job_id, job.task_type, credential.account_id,
                    "PROVIDER_DISPATCH_DEFERRED", None,
                    (NOW - timedelta(hours=24)).isoformat(), None,
                ),
                (
                    "retained", job.job_id, job.task_type, credential.account_id,
                    "PROVIDER_DISPATCH_DEFERRED", None,
                    (NOW - timedelta(hours=24) + timedelta(microseconds=1)).isoformat(),
                    None,
                ),
            ),
        )

    record_scheduler_deferral(
        connection,
        job=job,
        credential=credential,
        status={
            "failure_code": "PROVIDER_DISPATCH_DEFERRED",
            "next_retry_at": (NOW + timedelta(milliseconds=250)).isoformat(),
        },
        deferred_at=NOW,
    )

    rows = connection.execute(
        """SELECT deferral_id,deferred_at
           FROM news_ai_scheduler_deferrals_v1 ORDER BY deferred_at"""
    ).fetchall()
    identifiers = [str(row["deferral_id"]) for row in rows]
    assert len(identifiers) == 2
    assert identifiers[0] == "retained"
    assert "expired" not in identifiers


def test_scheduler_wakes_for_short_capacity_retry_without_busy_spin() -> None:
    from scripts import run_news_annotator as runner

    assert runner._scheduler_sleep_seconds(
        [{"next_retry_at": (NOW + timedelta(seconds=8)).isoformat()}],
        interval_seconds=60, now=NOW,
    ) == 8
    assert runner._scheduler_sleep_seconds(
        [{"next_retry_at": (NOW + timedelta(milliseconds=250)).isoformat()}],
        interval_seconds=60, now=NOW,
    ) == pytest.approx(0.25)
    assert runner._scheduler_sleep_seconds(
        [{"next_retry_at": (NOW - timedelta(seconds=2)).isoformat()}],
        interval_seconds=60, now=NOW,
    ) == pytest.approx(0.05)
    assert runner._scheduler_sleep_seconds(
        [], interval_seconds=60, now=NOW,
    ) == 60


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
    monkeypatch.setattr(runner.scheduler_runtime, "configured_api_credentials", lambda: (credential,))
    monkeypatch.setattr(runner.scheduler_runtime, "sync_pending_jobs", lambda *_args, **_kwargs: {})
    ready = False
    gemma_requests = 0

    def execute(*_args, **_kwargs):
        nonlocal gemma_requests
        if not ready:
            raise NewsEmbeddingBackfillPending("embedding generation incomplete")
        gemma_requests += 1
        return {"status": "OK"}

    monkeypatch.setattr(runner.scheduler_runtime, "_execute_job", execute)

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
    monkeypatch.setattr(runner.scheduler_runtime, "configured_api_credentials", lambda: credentials)
    monkeypatch.setattr(runner.scheduler_runtime, "sync_pending_jobs", lambda *_args, **_kwargs: {})
    calls = []

    def execute(_ledger, credential, _job, **_kwargs):
        calls.append(credential.account_id)
        if credential.account_id == "account-a":
            return {
                "status": "ERROR", "error": "display remains invalid",
                "retry_with_another_account": True,
            }
        return {"status": "OK"}

    monkeypatch.setattr(runner.scheduler_runtime, "_execute_job", execute)

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
    monkeypatch.setattr(runner.scheduler_runtime, "configured_api_credentials", lambda: credentials)
    monkeypatch.setattr(runner.scheduler_runtime, "sync_pending_jobs", lambda *_args, **_kwargs: {})

    def execute(_ledger, _credential, job, **_kwargs):
        if job.task_type == "ACTIVE_IMPACT":
            return {"status": "DEFERRED", "reason": "quota"}
        return {"status": "OK"}

    monkeypatch.setattr(runner.scheduler_runtime, "_execute_job", execute)

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
    monkeypatch.setattr(runner.scheduler_runtime, "configured_api_credentials", lambda: credentials)
    monkeypatch.setattr(runner.scheduler_runtime, "sync_pending_jobs", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(
        runner.scheduler_runtime, "_execute_job",
        lambda *_args, **_kwargs: {"status": "OK"},
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
    monkeypatch.setattr(runner.scheduler_runtime, "configured_api_credentials", lambda: credentials)
    monkeypatch.setattr(runner.scheduler_runtime, "sync_pending_jobs", lambda *_args, **_kwargs: {})
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

    monkeypatch.setattr(runner.scheduler_runtime, "_execute_job", execute)
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
    monkeypatch.setattr(runner.scheduler_runtime, "configured_api_credentials", lambda: credentials)
    monkeypatch.setattr(runner.scheduler_runtime, "sync_pending_jobs", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(
        runner.scheduler_runtime, "_execute_job",
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
    monkeypatch.setattr(runner.scheduler_runtime, "configured_api_credentials", lambda: credentials)
    monkeypatch.setattr(runner.scheduler_runtime, "sync_pending_jobs", lambda *_args, **_kwargs: {})
    attempted_accounts: list[str] = []

    def execute(_ledger, credential, _job, **_kwargs):
        attempted_accounts.append(credential.account_id)
        return {"status": "OK"}

    monkeypatch.setattr(runner.scheduler_runtime, "_execute_job", execute)

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
    monkeypatch.setattr(runner.scheduler_runtime, "configured_api_credentials", lambda: (credential,))
    monkeypatch.setattr(runner.scheduler_runtime, "sync_pending_jobs", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(
        runner.scheduler_runtime, "_execute_job",
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
    monkeypatch.setattr(runner.scheduler_runtime, "configured_api_credentials", lambda: credentials)
    monkeypatch.setattr(runner.scheduler_runtime, "sync_pending_jobs", lambda *_args, **_kwargs: {})
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

    monkeypatch.setattr(runner.scheduler_runtime, "_execute_job", execute)

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
    monkeypatch.setattr(runner.scheduler_runtime, "configured_api_credentials", lambda: credentials)
    monkeypatch.setattr(runner.scheduler_runtime, "sync_pending_jobs", lambda *_args, **_kwargs: {})
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

    monkeypatch.setattr(runner.scheduler_runtime, "_execute_job", execute)

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
        runner.scheduler_runtime, "_execute_job",
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
