from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

import xauusd_forecaster.news.scheduler.state as scheduler
import xauusd_forecaster.news.semantics.transitions as transition_policy
from tests.test_news_semantic_contract_v15 import _target_annotation
from xauusd_forecaster.evidence.ledger import ForwardLedger
from xauusd_forecaster.news.semantics.contracts import (
    CURRENT_NEWS_PROMPT_VERSION,
    PREVIOUS_NEWS_PROMPT_VERSION,
)
from xauusd_forecaster.runtime.operational_health import scheduler_health_snapshot


NOW = datetime(2026, 8, 12, 2, 0, tzinfo=UTC)
TRANSITION_KEY = (
    PREVIOUS_NEWS_PROMPT_VERSION,
    CURRENT_NEWS_PROMPT_VERSION,
)


def _historical_annotation(
    ledger: ForwardLedger, *, item: str = "historical", offset: int = 0,
) -> dict[str, object]:
    body = (
        "The Bureau of Labor Statistics reported job openings fell in June. "
        + "The official report contains complete macroeconomic evidence. " * 12
    )
    digest = hashlib.sha256(body.encode()).hexdigest()
    received = NOW - timedelta(hours=1) + timedelta(seconds=offset)
    ledger.append_news_revision({
        "source": "transition-fixture",
        "source_item_id": item,
        "source_published_time": received,
        "collector_first_seen_time": received,
        "fetched_time": received,
        "headline": "Job openings report",
        "body": body,
        "content_hash": digest,
        "cluster_id": item,
    })
    annotation = _target_annotation("job openings fell in June")
    ledger.append_annotation({
        "annotation_id": f"source-v16-{item}" if item != "historical" else "source-v16",
        "source": "transition-fixture",
        "source_item_id": item,
        "revision_number": 1,
        "raw_content_hash": digest,
        "llm_model_version": "gemini-3.5-flash-lite",
        "prompt_version": PREVIOUS_NEWS_PROMPT_VERSION,
        "parse_started_at": received + timedelta(minutes=1),
        "parsed_at": received + timedelta(minutes=2),
        "annotation": annotation,
    })
    ledger.connection.execute(
        """INSERT INTO news_annotation_contract_backfill_v1
           VALUES (?,?,NULL,NULL,NULL,NULL,'ACTIVE',?)
           ON CONFLICT(prompt_version) DO NOTHING""",
        (CURRENT_NEWS_PROMPT_VERSION, NOW.isoformat(), NOW.isoformat()),
    )
    ledger.connection.commit()
    return annotation


@pytest.mark.parametrize(
    ("kind", "migrator_version", "expected_priority", "expected_usd_impulse"),
    (
        (transition_policy.REUSE_COMPATIBLE, None, "FAST", -0.2),
        (
            transition_policy.DETERMINISTIC_MIGRATION,
            "test-local-v1", "BACKGROUND", 0.25,
        ),
    ),
)
def test_zero_provider_transitions_execute_in_sync_and_replay_safely(
    tmp_path, monkeypatch, kind: str, migrator_version: str | None,
    expected_priority: str, expected_usd_impulse: float,
) -> None:
    ledger = ForwardLedger(
        tmp_path / "forward.sqlite3", now=NOW - timedelta(hours=2),
    )
    source_annotation = _historical_annotation(ledger)
    if migrator_version:
        def migrate(annotation: dict[str, object], _source_text: str) -> None:
            annotation["review_priority"] = "BACKGROUND"
            annotation["usd_impulse"] = 0.25

        monkeypatch.setitem(
            transition_policy.DETERMINISTIC_MIGRATORS,
            migrator_version,
            migrate,
        )
    monkeypatch.setitem(
        transition_policy.DECLARED_TRANSITIONS,
        TRANSITION_KEY,
        transition_policy.SemanticTransition(
            *TRANSITION_KEY,
            kind,
            "scheduler integration fixture",
            migrator_version=migrator_version,
        ),
    )
    declared_contract = transition_policy.semantic_transition_contract(
        transition_policy.DECLARED_TRANSITIONS[TRANSITION_KEY]
    )

    database = ledger.path
    first = scheduler.sync_pending_jobs(ledger.connection, now=NOW, limit=10)
    ledger.close()
    ledger = ForwardLedger(database, now=NOW + timedelta(minutes=1))
    scheduler.sync_pending_jobs(
        ledger.connection, now=NOW + timedelta(minutes=1), limit=10,
    )

    projected = ledger.connection.execute(
        """SELECT * FROM news_annotations
           WHERE source='transition-fixture' AND source_item_id='historical'
             AND prompt_version=?""",
        (CURRENT_NEWS_PROMPT_VERSION,),
    ).fetchall()
    assert len(projected) == 1
    projected_json = json.loads(str(projected[0]["annotation_json"]))
    assert projected_json["review_priority"] == expected_priority
    assert projected_json["usd_impulse"] == expected_usd_impulse
    assert projected[0]["usd_impulse"] == expected_usd_impulse
    if kind == transition_policy.REUSE_COMPATIBLE:
        assert projected_json == source_annotation
    assert projected[0]["llm_model_version"] == "gemini-3.5-flash-lite"
    assert ledger.connection.execute(
        """SELECT annotation_state FROM dashboard_news_current_state_v1
           WHERE cluster_id='historical'"""
    ).fetchone()[0] == "READY"
    assert first["ACTIVE_ANNOTATION"] == 0
    assert ledger.connection.execute(
        "SELECT count(*) FROM news_annotation_transition_projections_v1"
    ).fetchone()[0] == 1
    projection = ledger.connection.execute(
        "SELECT * FROM news_annotation_transition_projections_v1"
    ).fetchone()
    assert projection["source_annotation_id"] == "source-v16"
    assert projection["transition_kind"] == kind
    assert projection["migrator_version"] == migrator_version
    assert projection["transition_fingerprint"] == declared_contract.fingerprint
    assert projection["source_annotation_hash"]
    assert projection["projected_annotation_hash"]
    assert ledger.connection.execute(
        "SELECT count(*) FROM news_ai_job_attempts_v1"
    ).fetchone()[0] == 0
    assert ledger.connection.execute(
        "SELECT count(*) FROM news_ai_account_request_usage_v1"
    ).fetchone()[0] == 0
    assert ledger.connection.execute(
        """SELECT count(*) FROM news_ai_jobs_v1
           WHERE task_type='ACTIVE_ANNOTATION' AND prompt_version=?
             AND attempt_count<>0""",
        (CURRENT_NEWS_PROMPT_VERSION,),
    ).fetchone()[0] == 0
    assert ledger.connection.execute(
        """SELECT count(*) FROM news_ai_jobs_v1
           WHERE task_type='ACTIVE_ANNOTATION' AND prompt_version=?
             AND state IN ('QUEUED','LEASED','BACKING_OFF')""",
        (CURRENT_NEWS_PROMPT_VERSION,),
    ).fetchone()[0] == 0
    projected_job = ledger.connection.execute(
        """SELECT work_lane,lane_classified,provenance_resolved,
                  provenance_origin_task,provenance_origin_ref
           FROM news_ai_jobs_v1
           WHERE task_type='ACTIVE_ANNOTATION' AND prompt_version=?""",
        (CURRENT_NEWS_PROMPT_VERSION,),
    ).fetchone()
    assert tuple(projected_job) == (
        "CONTRACT_BACKFILL", 1, 1, "SEMANTIC_TRANSITION",
        f"{declared_contract.fingerprint}:source-v16",
    )
    assert ledger.connection.execute(
        """SELECT count(*) FROM news_ai_jobs_v1
           WHERE task_type IN ('ACTIVE_IMPACT','TITLE_TRANSLATION')
             AND (work_lane<>'CONTRACT_BACKFILL' OR priority<>'BACKGROUND'
                  OR provenance_origin_task NOT IN (
                    'ACTIVE_ANNOTATION','SEMANTIC_TRANSITION'))"""
    ).fetchone()[0] == 0
    state = ledger.connection.execute(
        """SELECT state,processed_count,transition_fingerprint,
                  transition_contract_json
           FROM news_annotation_transition_state_v1"""
    ).fetchone()
    assert tuple(state) == (
        "COMPLETE", 1, declared_contract.fingerprint,
        declared_contract.contract_json,
    )
    ledger.close()


def test_deterministic_transition_failure_is_bounded_without_model_fallback(
    tmp_path, monkeypatch,
) -> None:
    ledger = ForwardLedger(
        tmp_path / "forward.sqlite3", now=NOW - timedelta(hours=2),
    )
    _historical_annotation(ledger)

    def invalidate(annotation: dict[str, object], _source_text: str) -> None:
        annotation["review_priority"] = "UNCONTROLLED"

    monkeypatch.setitem(
        transition_policy.DETERMINISTIC_MIGRATORS, "invalid-test-v1", invalidate,
    )
    monkeypatch.setitem(
        transition_policy.DECLARED_TRANSITIONS,
        TRANSITION_KEY,
        transition_policy.SemanticTransition(
            *TRANSITION_KEY,
            transition_policy.DETERMINISTIC_MIGRATION,
            "invalid migration fixture",
            migrator_version="invalid-test-v1",
        ),
    )

    scheduler.sync_pending_jobs(ledger.connection, now=NOW, limit=10)
    scheduler.sync_pending_jobs(
        ledger.connection, now=NOW + timedelta(minutes=1), limit=10,
    )

    assert ledger.connection.execute(
        "SELECT count(*) FROM news_annotation_transition_failures_v1"
    ).fetchone()[0] == 1
    assert ledger.connection.execute(
        """SELECT count(*) FROM news_annotations
           WHERE prompt_version=?""",
        (CURRENT_NEWS_PROMPT_VERSION,),
    ).fetchone()[0] == 0
    assert ledger.connection.execute(
        """SELECT count(*) FROM news_ai_jobs_v1
           WHERE task_type='ACTIVE_ANNOTATION' AND prompt_version=?""",
        (CURRENT_NEWS_PROMPT_VERSION,),
    ).fetchone()[0] == 0
    assert ledger.connection.execute(
        "SELECT count(*) FROM news_ai_job_attempts_v1"
    ).fetchone()[0] == 0
    assert ledger.connection.execute(
        "SELECT count(*) FROM news_ai_account_request_usage_v1"
    ).fetchone()[0] == 0
    health = scheduler_health_snapshot(
        ledger.connection, now=NOW + timedelta(minutes=1),
    )
    assert health["scheduler"]["contract_backfill"][
        "semantic_transition_contract_failures_15m"
    ] == 1
    assert any(
        alert["code"] == "OPS_AI_BACKFILL_MIGRATION_FAILED"
        for alert in health["alerts"]
    )
    ledger.close()


def test_deterministic_transition_resumes_only_with_same_fingerprint(
    tmp_path, monkeypatch,
) -> None:
    database = tmp_path / "forward.sqlite3"
    ledger = ForwardLedger(database, now=NOW - timedelta(hours=2))
    for index in range(3):
        _historical_annotation(
            ledger, item=f"resume-{index}", offset=index,
        )

    def migrate(annotation: dict[str, object], _source_text: str) -> None:
        annotation["review_priority"] = "BACKGROUND"

    monkeypatch.setitem(
        transition_policy.DETERMINISTIC_MIGRATORS, "resume-v1", migrate,
    )
    transition = transition_policy.SemanticTransition(
        *TRANSITION_KEY,
        transition_policy.DETERMINISTIC_MIGRATION,
        "restart fingerprint fixture",
        migrator_version="resume-v1",
    )
    contract = transition_policy.semantic_transition_contract(transition)

    first = transition_policy.execute_transition_page(
        ledger.connection, transition, activated_at=NOW, now=NOW, page_size=1,
    )
    first_cursor = ledger.connection.execute(
        """SELECT cursor_annotation_id
           FROM news_annotation_transition_state_v1"""
    ).fetchone()[0]
    assert first["processed"] == 1
    assert first["transition_fingerprint"] == contract.fingerprint
    ledger.close()

    ledger = ForwardLedger(database, now=NOW + timedelta(minutes=1))
    second = transition_policy.execute_transition_page(
        ledger.connection, transition, activated_at=NOW,
        now=NOW + timedelta(minutes=1), page_size=1,
    )
    state = ledger.connection.execute(
        """SELECT cursor_annotation_id,processed_count,transition_fingerprint
           FROM news_annotation_transition_state_v1"""
    ).fetchone()
    assert second["processed"] == 1
    assert state["cursor_annotation_id"] != first_cursor
    assert tuple(state)[1:] == (2, contract.fingerprint)
    assert ledger.connection.execute(
        "SELECT count(*) FROM news_annotation_transition_projections_v1"
    ).fetchone()[0] == 2
    assert ledger.connection.execute(
        "SELECT count(*) FROM news_ai_job_attempts_v1"
    ).fetchone()[0] == 0
    assert ledger.connection.execute(
        "SELECT count(*) FROM news_ai_account_request_usage_v1"
    ).fetchone()[0] == 0
    ledger.close()


def test_changed_migrator_fails_closed_before_resuming_cursor(
    tmp_path, monkeypatch,
) -> None:
    database = tmp_path / "forward.sqlite3"
    ledger = ForwardLedger(database, now=NOW - timedelta(hours=2))
    for index in range(3):
        _historical_annotation(
            ledger, item=f"changed-{index}", offset=index,
        )

    def migrate_v1(annotation: dict[str, object], _source_text: str) -> None:
        annotation["review_priority"] = "BACKGROUND"

    monkeypatch.setitem(
        transition_policy.DETERMINISTIC_MIGRATORS, "migrator-v1", migrate_v1,
    )
    transition_v1 = transition_policy.SemanticTransition(
        *TRANSITION_KEY,
        transition_policy.DETERMINISTIC_MIGRATION,
        "first declared migration",
        migrator_version="migrator-v1",
    )
    transition_policy.execute_transition_page(
        ledger.connection, transition_v1,
        activated_at=NOW, now=NOW, page_size=1,
    )
    before = ledger.connection.execute(
        """SELECT cursor_annotation_id,processed_count,transition_fingerprint
           FROM news_annotation_transition_state_v1"""
    ).fetchone()
    projections_before = ledger.connection.execute(
        "SELECT count(*) FROM news_annotation_transition_projections_v1"
    ).fetchone()[0]
    ledger.close()

    ledger = ForwardLedger(database, now=NOW + timedelta(minutes=1))

    def migrate_v2(annotation: dict[str, object], _source_text: str) -> None:
        annotation["review_priority"] = "NORMAL"

    monkeypatch.setitem(
        transition_policy.DETERMINISTIC_MIGRATORS, "migrator-v2", migrate_v2,
    )
    transition_v2 = transition_policy.SemanticTransition(
        *TRANSITION_KEY,
        transition_policy.DETERMINISTIC_MIGRATION,
        "changed declared migration",
        migrator_version="migrator-v2",
    )
    with pytest.raises(
        transition_policy.SemanticTransitionContractChanged,
        match="SEMANTIC_TRANSITION_CONTRACT_CHANGED",
    ):
        transition_policy.execute_transition_page(
            ledger.connection, transition_v2, activated_at=NOW,
            now=NOW + timedelta(minutes=1), page_size=1,
        )

    after = ledger.connection.execute(
        """SELECT cursor_annotation_id,processed_count,transition_fingerprint
           FROM news_annotation_transition_state_v1"""
    ).fetchone()
    assert tuple(after) == tuple(before)
    assert ledger.connection.execute(
        "SELECT count(*) FROM news_annotation_transition_projections_v1"
    ).fetchone()[0] == projections_before == 1
    failure = ledger.connection.execute(
        "SELECT * FROM news_annotation_transition_contract_failures_v1"
    ).fetchone()
    assert failure["failure_code"] == "SEMANTIC_TRANSITION_CONTRACT_CHANGED"
    assert failure["persisted_fingerprint"] == before["transition_fingerprint"]
    assert failure["current_fingerprint"] == (
        transition_policy.semantic_transition_contract(transition_v2).fingerprint
    )
    assert ledger.connection.execute(
        "SELECT count(*) FROM news_ai_job_attempts_v1"
    ).fetchone()[0] == 0
    assert ledger.connection.execute(
        "SELECT count(*) FROM news_ai_account_request_usage_v1"
    ).fetchone()[0] == 0
    ledger.close()


def test_transition_kind_change_fails_closed_instead_of_model_fallback(
    tmp_path,
) -> None:
    ledger = ForwardLedger(
        tmp_path / "forward.sqlite3", now=NOW - timedelta(hours=2),
    )
    for index in range(2):
        _historical_annotation(ledger, item=f"kind-{index}", offset=index)
    reuse = transition_policy.SemanticTransition(
        *TRANSITION_KEY, transition_policy.REUSE_COMPATIBLE,
        "compatible fixture",
    )
    transition_policy.execute_transition_page(
        ledger.connection, reuse, activated_at=NOW, now=NOW, page_size=1,
    )
    before = tuple(ledger.connection.execute(
        """SELECT cursor_annotation_id,processed_count,transition_fingerprint
           FROM news_annotation_transition_state_v1"""
    ).fetchone())
    review = transition_policy.SemanticTransition(
        *TRANSITION_KEY, transition_policy.MODEL_REVIEW_REQUIRED,
        "changed to model review",
    )

    with pytest.raises(transition_policy.SemanticTransitionContractChanged):
        transition_policy.execute_transition_page(
            ledger.connection, review, activated_at=NOW,
            now=NOW + timedelta(minutes=1), page_size=1,
        )

    assert tuple(ledger.connection.execute(
        """SELECT cursor_annotation_id,processed_count,transition_fingerprint
           FROM news_annotation_transition_state_v1"""
    ).fetchone()) == before
    assert ledger.connection.execute(
        "SELECT count(*) FROM news_annotation_transition_projections_v1"
    ).fetchone()[0] == 1
    assert ledger.connection.execute(
        "SELECT count(*) FROM news_ai_job_attempts_v1"
    ).fetchone()[0] == 0
    assert ledger.connection.execute(
        "SELECT count(*) FROM news_ai_account_request_usage_v1"
    ).fetchone()[0] == 0
    ledger.close()


def test_undeclared_transition_fails_before_scheduler_mutation(
    tmp_path, monkeypatch,
) -> None:
    ledger = ForwardLedger(tmp_path / "forward.sqlite3", now=NOW)
    monkeypatch.delitem(transition_policy.DECLARED_TRANSITIONS, TRANSITION_KEY)

    with pytest.raises(ValueError, match="transition is not declared"):
        scheduler.sync_pending_jobs(ledger.connection, now=NOW)

    assert ledger.connection.execute(
        "SELECT count(*) FROM news_ai_jobs_v1"
    ).fetchone()[0] == 0
    assert ledger.connection.execute(
        "SELECT count(*) FROM news_annotation_transition_state_v1"
    ).fetchone()[0] == 0
    ledger.close()


def _bulk_existing_jobs(
    connection: sqlite3.Connection, *, count: int, activation: datetime,
) -> None:
    revisions = []
    jobs = []
    for index in range(count):
        historical = index < count // 2
        received = activation + timedelta(
            days=-1 if historical else 1,
            microseconds=index,
        )
        item = f"item-{index:05d}"
        timestamp = received.isoformat(timespec="microseconds")
        revisions.append((
            "lane-fixture", item, 1, timestamp, timestamp, timestamp,
            timestamp, item, "complete body", None, f"hash-{index}", item, 0.0,
        ))
        jobs.append((
            f"job-{index:05d}", "ACTIVE_ANNOTATION", "lane-fixture", item, 1,
            "", CURRENT_NEWS_PROMPT_VERSION, "NORMAL", "QUEUED", timestamp,
            None, None, index % 4, f"error-{index}", timestamp, timestamp, None,
            scheduler.LIVE_LANE, 0,
        ))
    with connection:
        connection.executemany(
            "INSERT INTO news_revisions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            revisions,
        )
        connection.executemany(
            """INSERT INTO news_ai_jobs_v1
               (job_id,task_type,source,source_item_id,revision_number,
                annotation_id,prompt_version,priority,state,available_at,
                lease_owner,lease_expires_at,attempt_count,last_error,created_at,
                updated_at,completed_at,work_lane,lane_classified)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            jobs,
        )
        connection.execute(
            """INSERT INTO news_annotation_contract_backfill_v1
               VALUES (?,?,NULL,NULL,NULL,NULL,'ACTIVE',?)""",
            (
                CURRENT_NEWS_PROMPT_VERSION,
                activation.isoformat(timespec="microseconds"),
                activation.isoformat(timespec="microseconds"),
            ),
        )


def test_existing_job_lane_migration_pages_ten_thousand_and_restarts(
    tmp_path, monkeypatch,
) -> None:
    database = tmp_path / "forward.sqlite3"
    ledger = ForwardLedger(database, now=NOW)
    _bulk_existing_jobs(ledger.connection, count=10_000, activation=NOW)
    with ledger.connection:
        ledger.connection.execute(
            """UPDATE news_ai_jobs_v1 SET state='BACKING_OFF',available_at=?
               WHERE job_id='job-00000'""",
            ((NOW + timedelta(hours=2)).isoformat(timespec="microseconds"),),
        )
        ledger.connection.execute(
            """UPDATE news_ai_jobs_v1 SET state='LEASED',lease_owner='worker',
                   lease_expires_at=? WHERE job_id='job-00001'""",
            ((NOW + timedelta(minutes=5)).isoformat(timespec="microseconds"),),
        )
    monkeypatch.setattr(
        scheduler, "_contract_backfill_has_current_value",
        lambda *_args, **_kwargs: True,
    )
    original_attempt_total = ledger.connection.execute(
        "SELECT sum(attempt_count) FROM news_ai_jobs_v1"
    ).fetchone()[0]
    protected_state = [tuple(row) for row in ledger.connection.execute(
        """SELECT job_id,state,available_at,lease_owner,lease_expires_at,
                  attempt_count,last_error
           FROM news_ai_jobs_v1 WHERE job_id IN ('job-00000','job-00001')
           ORDER BY job_id"""
    ).fetchall()]
    plan = ledger.connection.execute(
        """EXPLAIN QUERY PLAN SELECT job_id FROM news_ai_jobs_v1
           WHERE prompt_version=? AND task_type='ACTIVE_ANNOTATION'
             AND lane_classified=0 ORDER BY created_at,job_id LIMIT 100""",
        (CURRENT_NEWS_PROMPT_VERSION,),
    ).fetchall()
    assert any(
        "news_ai_jobs_lane_migration_v1" in str(row[3]) for row in plan
    )

    scheduler._install_annotation_contract_lanes(
        ledger.connection,
        prompt_version=CURRENT_NEWS_PROMPT_VERSION,
        now=NOW,
    )
    assert ledger.connection.execute(
        "SELECT count(*) FROM news_ai_jobs_v1 WHERE lane_classified=1"
    ).fetchone()[0] == scheduler.EXISTING_JOB_LANE_PAGE_SIZE
    for offset in range(1, 3):
        scheduler._install_annotation_contract_lanes(
            ledger.connection,
            prompt_version=CURRENT_NEWS_PROMPT_VERSION,
            now=NOW + timedelta(seconds=offset),
        )
    assert ledger.connection.execute(
        "SELECT count(*) FROM news_ai_jobs_v1 WHERE lane_classified=1"
    ).fetchone()[0] == 300
    first_cursor = ledger.connection.execute(
        """SELECT cursor_job_id FROM news_annotation_work_lane_migrations_v1
           WHERE prompt_version=?""",
        (CURRENT_NEWS_PROMPT_VERSION,),
    ).fetchone()[0]
    ledger.close()

    ledger = ForwardLedger(database, now=NOW + timedelta(minutes=1))
    for offset in range(98):
        scheduler._install_annotation_contract_lanes(
            ledger.connection,
            prompt_version=CURRENT_NEWS_PROMPT_VERSION,
            now=NOW + timedelta(minutes=1, seconds=offset),
        )
    state = ledger.connection.execute(
        """SELECT state,cursor_job_id,processed_count
           FROM news_annotation_work_lane_migrations_v1
           WHERE prompt_version=?""",
        (CURRENT_NEWS_PROMPT_VERSION,),
    ).fetchone()
    assert tuple(state) == ("COMPLETE", "job-09999", 10_000)
    assert str(state["cursor_job_id"]) > str(first_cursor)
    assert ledger.connection.execute(
        """SELECT count(*) FROM news_ai_jobs_v1
           WHERE work_lane='CONTRACT_BACKFILL'"""
    ).fetchone()[0] == 5_000
    assert ledger.connection.execute(
        """SELECT count(*) FROM news_ai_jobs_v1
           WHERE work_lane='LIVE'"""
    ).fetchone()[0] == 5_000
    assert ledger.connection.execute(
        "SELECT sum(attempt_count) FROM news_ai_jobs_v1"
    ).fetchone()[0] == original_attempt_total
    assert [tuple(row) for row in ledger.connection.execute(
        """SELECT job_id,state,available_at,lease_owner,lease_expires_at,
                  attempt_count,last_error
           FROM news_ai_jobs_v1 WHERE job_id IN ('job-00000','job-00001')
           ORDER BY job_id"""
    ).fetchall()] == protected_state
    assert ledger.connection.execute(
        "SELECT count(*) FROM news_ai_jobs_v1"
    ).fetchone()[0] == 10_000
    assert ledger.connection.execute(
        "SELECT count(*) FROM news_ai_job_attempts_v1"
    ).fetchone()[0] == 0
    assert ledger.connection.execute(
        "SELECT count(*) FROM news_ai_account_request_usage_v1"
    ).fetchone()[0] == 0
    traced: list[str] = []
    ledger.connection.set_trace_callback(traced.append)
    scheduler._install_annotation_contract_lanes(
        ledger.connection,
        prompt_version=CURRENT_NEWS_PROMPT_VERSION,
        now=NOW + timedelta(hours=1),
    )
    ledger.connection.set_trace_callback(None)
    assert not any("lane_classified=0" in sql for sql in traced)
    ledger.close()


def test_existing_job_lane_page_rolls_back_and_resumes_after_failure(
    tmp_path, monkeypatch,
) -> None:
    database = tmp_path / "forward.sqlite3"
    ledger = ForwardLedger(database, now=NOW)
    _bulk_existing_jobs(ledger.connection, count=300, activation=NOW)

    def fail_on_second_page(_connection, row, **_kwargs) -> bool:
        if row["source_item_id"] == "item-00125":
            raise RuntimeError("injected migration interruption")
        return True

    monkeypatch.setattr(
        scheduler, "_contract_backfill_has_current_value", fail_on_second_page,
    )
    scheduler._install_annotation_contract_lanes(
        ledger.connection,
        prompt_version=CURRENT_NEWS_PROMPT_VERSION,
        now=NOW,
    )
    with pytest.raises(RuntimeError, match="injected migration interruption"):
        scheduler._install_annotation_contract_lanes(
            ledger.connection,
            prompt_version=CURRENT_NEWS_PROMPT_VERSION,
            now=NOW + timedelta(seconds=1),
        )
    assert ledger.connection.execute(
        "SELECT count(*) FROM news_ai_jobs_v1 WHERE lane_classified=1"
    ).fetchone()[0] == 100
    state = ledger.connection.execute(
        """SELECT cursor_job_id,processed_count
           FROM news_annotation_work_lane_migrations_v1"""
    ).fetchone()
    assert tuple(state) == ("job-00099", 100)
    ledger.close()

    ledger = ForwardLedger(database, now=NOW + timedelta(minutes=1))
    monkeypatch.setattr(
        scheduler, "_contract_backfill_has_current_value",
        lambda *_args, **_kwargs: True,
    )
    for offset in range(3):
        scheduler._install_annotation_contract_lanes(
            ledger.connection,
            prompt_version=CURRENT_NEWS_PROMPT_VERSION,
            now=NOW + timedelta(minutes=1, seconds=offset),
        )
    assert ledger.connection.execute(
        "SELECT count(*) FROM news_ai_jobs_v1 WHERE lane_classified=1"
    ).fetchone()[0] == 300
    final_state = ledger.connection.execute(
        """SELECT state,processed_count
           FROM news_annotation_work_lane_migrations_v1"""
    ).fetchone()
    assert tuple(final_state) == ("COMPLETE", 300)
    ledger.close()


def test_backfill_budget_deferrals_coalesce_without_row_amplification() -> None:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    scheduler.install_scheduler_schema(connection)
    job_id = scheduler.enqueue_job(
        connection,
        task_type="ACTIVE_ANNOTATION",
        source="fixture",
        source_item_id="backfill",
        revision_number=1,
        prompt_version=CURRENT_NEWS_PROMPT_VERSION,
        priority="BACKGROUND",
        work_lane=scheduler.CONTRACT_BACKFILL_LANE,
        now=NOW,
    )
    row = connection.execute(
        "SELECT * FROM news_ai_jobs_v1 WHERE job_id=?", (job_id,),
    ).fetchone()
    job = scheduler._job_from_row(row)
    credential = scheduler.ApiCredential(
        "account", scheduler.ROUTINE_POOL, "secret", "credential",
    )
    scheduler.record_scheduler_deferral(
        connection,
        job=job,
        credential=credential,
        status={
            "failure_code": "BACKFILL_BUDGET_DEFERRED",
            "failure_evidence": {"reason": "NO_SAFE_DAILY_BUDGET"},
            "next_retry_at": (NOW + timedelta(minutes=10)).isoformat(),
        },
        deferred_at=NOW,
    )
    changes_after_first = connection.total_changes
    for offset in range(1, 100):
        scheduler.record_scheduler_deferral(
            connection,
            job=job,
            credential=credential,
            status={
                "failure_code": "BACKFILL_BUDGET_DEFERRED",
                "failure_evidence": {"reason": "NO_SAFE_DAILY_BUDGET"},
                "next_retry_at": (NOW + timedelta(minutes=10)).isoformat(),
            },
            deferred_at=NOW + timedelta(seconds=offset),
        )
    assert connection.total_changes == changes_after_first
    assert connection.execute(
        "SELECT count(*) FROM news_ai_scheduler_deferrals_v1"
    ).fetchone()[0] == 1
    assert connection.execute(
        "SELECT deferred_at FROM news_ai_scheduler_deferrals_v1"
    ).fetchone()[0] == NOW.isoformat(timespec="microseconds")
    scheduler.record_scheduler_deferral(
        connection,
        job=job,
        credential=credential,
        status={"failure_code": "BACKFILL_BUDGET_DEFERRED"},
        deferred_at=NOW + timedelta(minutes=6),
    )
    assert connection.execute(
        "SELECT count(*) FROM news_ai_scheduler_deferrals_v1"
    ).fetchone()[0] == 1
    assert connection.execute(
        "SELECT deferred_at FROM news_ai_scheduler_deferrals_v1"
    ).fetchone()[0] == (NOW + timedelta(minutes=6)).isoformat(timespec="microseconds")
    connection.close()
