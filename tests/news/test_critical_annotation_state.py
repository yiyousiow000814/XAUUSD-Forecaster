from __future__ import annotations

import hashlib
import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

import xauusd_forecaster.news.annotation.product as annotation_contract
import xauusd_forecaster.news.semantics.critical_state as critical_state
import xauusd_forecaster.news.scheduler.state as scheduler
from xauusd_forecaster.news.annotation.product import PROMPT_VERSION
from xauusd_forecaster.news.semantics.critical_state import (
    INSTALL_VERSION,
    annotation_materialization_contract,
    annotation_queue_snapshot,
    install_critical_annotation_state_schema,
    news_current_counts,
    refresh_news_revision_state,
)
from xauusd_forecaster.evidence.ledger import ForwardLedger
from xauusd_forecaster.runtime.operational_health import scheduler_health_snapshot
from xauusd_forecaster.news.scheduler.state import (
    ROUTINE_POOL,
    backoff_job,
    claim_job,
    complete_job,
    enqueue_job,
    reconcile_completed_jobs,
)


NOW = datetime(2026, 8, 19, 12, 0, tzinfo=UTC)


def _snapshot(connection: sqlite3.Connection, now: datetime = NOW) -> dict[str, int]:
    return annotation_queue_snapshot(
        connection, prompt_version=PROMPT_VERSION,
        observed_at=now.isoformat(timespec="microseconds"),
    )


def _append_revision(
    ledger: ForwardLedger, item: str, body: str, *, cluster: str | None = None,
    published: datetime = NOW,
) -> None:
    ledger.append_news_revision({
        "source": "fixture", "source_item_id": item,
        "source_published_time": published, "collector_first_seen_time": NOW,
        "fetched_time": NOW, "headline": f"headline {item}", "body": body,
        "content_hash": hashlib.sha256(body.encode()).hexdigest(),
        "cluster_id": cluster or item,
    })


def _insert_lane_fixture_jobs(
    connection: sqlite3.Connection,
    *,
    live: int,
    backfill: int,
    unclassified: int,
) -> None:
    timestamp = NOW.isoformat(timespec="microseconds")
    jobs: list[tuple[object, ...]] = []
    for lane, classified, count in (
        (scheduler.LIVE_LANE, 1, live),
        (scheduler.CONTRACT_BACKFILL_LANE, 1, backfill),
        (scheduler.LIVE_LANE, 0, unclassified),
    ):
        prefix = "unclassified" if not classified else lane.lower()
        for index in range(count):
            item = f"{prefix}-{index:05d}"
            jobs.append((
                f"job-{item}", "ACTIVE_ANNOTATION", "fixture", item, 1,
                "", PROMPT_VERSION, "NORMAL", "QUEUED", timestamp,
                timestamp, timestamp, lane, classified,
            ))
    with connection:
        connection.executemany(
            """INSERT INTO news_ai_jobs_v1
               (job_id,task_type,source,source_item_id,revision_number,
                annotation_id,prompt_version,priority,state,available_at,
                created_at,updated_at,work_lane,lane_classified)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            jobs,
        )


def _insert_unclassified_revisions(
    connection: sqlite3.Connection, *, historical: int, live: int,
) -> None:
    rows = []
    for index in range(historical + live):
        item = f"unclassified-{index:05d}"
        first_seen = NOW + timedelta(
            days=-1 if index < historical else 1,
            microseconds=index,
        )
        timestamp = first_seen.isoformat(timespec="microseconds")
        rows.append((
            "fixture", item, 1, timestamp, timestamp, timestamp, timestamp,
            item, "complete evidence body", None, f"hash-{index}", item, 0.0,
        ))
    with connection:
        connection.executemany(
            "INSERT INTO news_revisions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            rows,
        )
        connection.execute(
            """INSERT INTO news_annotation_contract_backfill_v1
               (prompt_version,activated_at,state,updated_at)
               VALUES (?,?,'ACTIVE',?)""",
            (PROMPT_VERSION, NOW.isoformat(), NOW.isoformat()),
        )


def test_annotation_queue_summary_tracks_scheduler_transitions_exactly(tmp_path) -> None:
    ledger = ForwardLedger(tmp_path / "forward.sqlite3", now=NOW)
    connection = ledger.connection
    transition_body = "transition evidence body " * 25
    _append_revision(ledger, "transition", transition_body)
    job_id = enqueue_job(
        connection, task_type="ACTIVE_ANNOTATION", source="fixture",
        source_item_id="transition", revision_number=1, annotation_id="",
        prompt_version=PROMPT_VERSION, priority="NORMAL", now=NOW,
    )
    assert _snapshot(connection)["queued"] == 1

    lease = claim_job(connection, worker_id="worker", pool=ROUTINE_POOL, now=NOW)
    assert lease and lease.job_id == job_id
    backoff_job(
        connection, job_id, "worker", available_at=NOW + timedelta(hours=1),
        error="retryable",
    )
    assert _snapshot(connection)["backing_off"] == 1
    assert _snapshot(connection, NOW + timedelta(hours=2))["queued"] == 0
    assert _snapshot(connection, NOW + timedelta(hours=2))["backing_off"] == 1

    lease = claim_job(
        connection, worker_id="worker", pool=ROUTINE_POOL,
        now=NOW + timedelta(hours=2),
    )
    assert lease and lease.job_id == job_id
    complete_job(connection, job_id, "worker", now=NOW + timedelta(hours=2))
    with connection:
        connection.execute(
            "INSERT INTO news_annotations VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "annotation-transition", "fixture", "transition", 1,
                hashlib.sha256(transition_body.encode()).hexdigest(), "MACRO", "[]",
                0, 0, 0, 0, 0, 0.5, 0.8, "gemini-3.5-flash-lite",
                PROMPT_VERSION, NOW.isoformat(), NOW.isoformat(), "{}",
            ),
        )
        refresh_news_revision_state(connection, "fixture", "transition", 1)
    assert _snapshot(connection, NOW + timedelta(hours=2))["ready"] == 1

    _append_revision(ledger, "dead", "dead evidence body " * 25)
    dead_id = enqueue_job(
        connection, task_type="ACTIVE_ANNOTATION", source="fixture",
        source_item_id="dead", revision_number=1, annotation_id="",
        prompt_version=PROMPT_VERSION, priority="NORMAL", now=NOW,
    )
    lease = claim_job(
        connection, worker_id="dead-worker", pool=ROUTINE_POOL,
        now=NOW + timedelta(hours=3),
    )
    assert lease and lease.job_id == dead_id
    backoff_job(
        connection, dead_id, "dead-worker", available_at=NOW + timedelta(hours=3),
        error="terminal", terminal=True,
    )
    assert _snapshot(connection, NOW + timedelta(hours=3))["dead_letter"] == 1
    ledger.close()


def test_annotation_queue_separates_live_backfill_and_unclassified_migration(
    tmp_path, monkeypatch,
) -> None:
    ledger = ForwardLedger(tmp_path / "forward.sqlite3", now=NOW)
    connection = ledger.connection
    _insert_lane_fixture_jobs(
        connection, live=11, backfill=1_988, unclassified=100,
    )
    _insert_unclassified_revisions(connection, historical=50, live=50)

    traced: list[str] = []
    connection.set_trace_callback(traced.append)
    snapshot = _snapshot(connection)
    connection.set_trace_callback(None)
    assert snapshot["queued"] == 11
    assert snapshot["contract_backfill_queued"] == 1_988
    assert snapshot["unclassified_annotation_jobs"] == 100
    assert not any("FROM news_ai_jobs_v1" in sql for sql in traced)

    health = scheduler_health_snapshot(connection, now=NOW)
    annotation = next(
        task for task in health["scheduler"]["tasks"]
        if task["task_type"] == "ACTIVE_ANNOTATION"
    )
    assert health["status"] == "HEALTHY"
    assert annotation["queued"] == 11
    assert annotation["claimable"] == 11
    assert health["scheduler"]["contract_backfill"]["states"]["queued"] == 1_988
    assert health["scheduler"]["unclassified_annotation_jobs"] == 100
    assert not any(
        alert["scope"] == "ACTIVE_ANNOTATION" for alert in health["alerts"]
    )

    monkeypatch.setattr(
        scheduler, "_contract_backfill_has_current_value",
        lambda *_args, **_kwargs: True,
    )
    scheduler._install_annotation_contract_lanes(
        connection, prompt_version=PROMPT_VERSION, now=NOW,
    )
    classified = _snapshot(connection)
    assert classified["queued"] == 61
    assert classified["contract_backfill_queued"] == 2_038
    assert classified["unclassified_annotation_jobs"] == 0
    assert classified["queued"] + classified["contract_backfill_queued"] == 2_099
    assert connection.execute(
        "SELECT count(*) FROM news_ai_jobs_v1"
    ).fetchone()[0] == 2_099
    ledger.close()


def test_only_backfill_and_unclassified_pressure_keeps_live_health_healthy(
    tmp_path,
) -> None:
    ledger = ForwardLedger(tmp_path / "forward.sqlite3", now=NOW)
    _insert_lane_fixture_jobs(
        ledger.connection, live=0, backfill=2_000, unclassified=500,
    )

    snapshot = _snapshot(ledger.connection)
    health = scheduler_health_snapshot(ledger.connection, now=NOW)
    annotation = next(
        task for task in health["scheduler"]["tasks"]
        if task["task_type"] == "ACTIVE_ANNOTATION"
    )
    assert snapshot["queued"] == 0
    assert snapshot["contract_backfill_queued"] == 2_000
    assert snapshot["unclassified_annotation_jobs"] == 500
    assert health["status"] == "HEALTHY"
    assert health["alerts"] == []
    assert annotation["claimable"] == 0
    assert annotation["queued"] == 0
    assert health["scheduler"]["contract_backfill"]["states"]["queued"] == 2_000
    assert health["scheduler"]["unclassified_annotation_jobs"] == 500
    ledger.close()


def test_critical_and_health_reads_avoid_full_job_scan_with_ten_thousand_mixed_jobs(
    tmp_path,
) -> None:
    ledger = ForwardLedger(tmp_path / "forward.sqlite3", now=NOW)
    _insert_lane_fixture_jobs(
        ledger.connection, live=11, backfill=4_989, unclassified=5_000,
    )
    statements: list[str] = []
    ledger.connection.set_trace_callback(statements.append)
    snapshot = _snapshot(ledger.connection)
    health = scheduler_health_snapshot(ledger.connection, now=NOW)
    ledger.connection.set_trace_callback(None)

    assert snapshot["queued"] == 11
    assert snapshot["contract_backfill_queued"] == 4_989
    assert snapshot["unclassified_annotation_jobs"] == 5_000
    assert health["status"] == "HEALTHY"
    critical_reads = [
        sql for sql in statements
        if "dashboard_annotation_job_counts_v1" in sql
    ]
    assert critical_reads
    assert not any("news_ai_jobs_v1" in sql for sql in critical_reads)

    job_queries = [
        sql for sql in statements
        if sql.lstrip().upper().startswith(("SELECT", "WITH"))
        and "news_ai_jobs_v1" in sql
    ]
    assert job_queries
    job_plan_details: list[str] = []
    for sql in job_queries:
        job_plan_details.extend(
            str(row[3]) for row in ledger.connection.execute(
                f"EXPLAIN QUERY PLAN {sql}"
            ).fetchall()
            if "news_ai_jobs_v1" in str(row[3]) or " j " in f" {row[3]} "
        )
    assert job_plan_details
    assert all("SEARCH" in detail for detail in job_plan_details)
    assert any("news_ai_jobs_lane_" in detail for detail in job_plan_details)
    ledger.close()


def test_current_terminal_failures_retire_when_evidence_is_superseded(
    tmp_path,
) -> None:
    ledger = ForwardLedger(tmp_path / "forward.sqlite3", now=NOW)
    connection = ledger.connection
    _append_revision(ledger, "superseded-dead", "first evidence body " * 25)
    old_job = enqueue_job(
        connection, task_type="ACTIVE_ANNOTATION", source="fixture",
        source_item_id="superseded-dead", revision_number=1, annotation_id="",
        prompt_version=PROMPT_VERSION, priority="NORMAL", now=NOW,
    )
    lease = claim_job(connection, worker_id="worker", pool=ROUTINE_POOL, now=NOW)
    assert lease and lease.job_id == old_job
    backoff_job(
        connection, old_job, "worker", available_at=NOW, error="provider failure",
        terminal=True,
    )
    with connection:
        connection.execute(
            """INSERT INTO news_ai_job_attempts_v1 VALUES
               (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                "attempt-old", old_job, 1, "account", "credential",
                "FAILED", "PROVIDER_FAILURE", "RuntimeError", 503,
                "historical provider failure", NOW.isoformat(), None,
            ),
        )
    assert _snapshot(connection)["dead_letter"] == 1

    _append_revision(ledger, "superseded-dead", "replacement evidence body " * 25)
    enqueue_job(
        connection, task_type="ACTIVE_ANNOTATION", source="fixture",
        source_item_id="superseded-dead", revision_number=2, annotation_id="",
        prompt_version=PROMPT_VERSION, priority="NORMAL", now=NOW,
    )
    reconcile_completed_jobs(connection, now=NOW)

    snapshot = _snapshot(connection)
    assert snapshot["dead_letter"] == 0
    assert snapshot["queued"] == 1
    assert tuple(connection.execute(
        "SELECT state,last_error FROM news_ai_jobs_v1 WHERE job_id=?", (old_job,),
    ).fetchone()) == ("DEAD_LETTER", "CURRENT_EVIDENCE_NO_LONGER_ELIGIBLE")
    assert tuple(connection.execute(
        """SELECT outcome,failure_code,error_detail
           FROM news_ai_job_attempts_v1 WHERE attempt_id='attempt-old'"""
    ).fetchone()) == (
        "FAILED", "PROVIDER_FAILURE", "historical provider failure",
    )
    ledger.close()


def test_duplicate_terminal_failure_retires_for_preferred_cluster_peer(
    tmp_path,
) -> None:
    ledger = ForwardLedger(tmp_path / "forward.sqlite3", now=NOW)
    connection = ledger.connection
    _append_revision(
        ledger, "duplicate-dead", "short duplicate body " * 20,
        cluster="terminal-duplicate",
    )
    old_job = enqueue_job(
        connection, task_type="ACTIVE_ANNOTATION", source="fixture",
        source_item_id="duplicate-dead", revision_number=1, annotation_id="",
        prompt_version=PROMPT_VERSION, priority="NORMAL", now=NOW,
    )
    lease = claim_job(connection, worker_id="worker", pool=ROUTINE_POOL, now=NOW)
    assert lease and lease.job_id == old_job
    backoff_job(
        connection, old_job, "worker", available_at=NOW, error="terminal",
        terminal=True,
    )
    _append_revision(
        ledger, "duplicate-preferred", "long preferred duplicate body " * 30,
        cluster="terminal-duplicate",
    )
    enqueue_job(
        connection, task_type="ACTIVE_ANNOTATION", source="fixture",
        source_item_id="duplicate-preferred", revision_number=1, annotation_id="",
        prompt_version=PROMPT_VERSION, priority="NORMAL", now=NOW,
    )

    reconcile_completed_jobs(connection, now=NOW)

    assert _snapshot(connection)["dead_letter"] == 0
    assert _snapshot(connection)["queued"] == 1
    assert connection.execute(
        "SELECT last_error FROM news_ai_jobs_v1 WHERE job_id=?", (old_job,),
    ).fetchone()[0] == "CURRENT_EVIDENCE_NO_LONGER_ELIGIBLE"
    ledger.close()


def test_current_news_summary_moves_waiting_to_available_without_history_scan(tmp_path) -> None:
    ledger = ForwardLedger(tmp_path / "forward.sqlite3", now=NOW)
    _append_revision(ledger, "item", "short")
    assert news_current_counts(ledger.connection)["waiting_content"] == 1

    _append_revision(ledger, "item", "complete official evidence " * 20)
    assert news_current_counts(ledger.connection) == {
        "waiting_content": 0, "unavailable_content": 0, "invalid_display": 0,
    }
    ledger.close()


def test_ready_summary_retires_superseded_duplicate_and_ineligible_work(tmp_path) -> None:
    ledger = ForwardLedger(tmp_path / "forward.sqlite3", now=NOW)
    _append_revision(
        ledger, "ineligible", "old evidence " * 30,
        published=NOW - timedelta(minutes=1),
    )
    _append_revision(ledger, "duplicate-short", "short evidence " * 25,
                     cluster="duplicate")
    _append_revision(ledger, "duplicate-long", "longer evidence body " * 30,
                     cluster="duplicate")
    _append_revision(ledger, "superseded", "first revision body " * 25)
    with ledger.connection:
        ledger.connection.executemany(
            "INSERT INTO news_annotations VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [(
                f"annotation-{item}", "fixture", item, 1, "digest", "MACRO",
                "[]", 0, 0, 0, 0, 0, 0.5, 0.8,
                "gemini-3.5-flash-lite", PROMPT_VERSION, NOW.isoformat(),
                NOW.isoformat(), "{}",
            ) for item in (
                "ineligible", "duplicate-short", "duplicate-long", "superseded",
            )],
        )
        for item in (
            "ineligible", "duplicate-short", "duplicate-long", "superseded",
        ):
            refresh_news_revision_state(ledger.connection, "fixture", item, 1)
    jobs = []
    for item in ("ineligible", "duplicate-short", "duplicate-long", "superseded"):
        jobs.append(enqueue_job(
            ledger.connection, task_type="ACTIVE_ANNOTATION", source="fixture",
            source_item_id=item, revision_number=1, annotation_id="",
            prompt_version=PROMPT_VERSION, priority="NORMAL", now=NOW,
        ))
    with ledger.connection:
        ledger.connection.executemany(
            """UPDATE news_ai_jobs_v1 SET state='COMPLETED',completed_at=?
               WHERE job_id=?""",
            [(NOW.isoformat(), job_id) for job_id in jobs],
        )
    _append_revision(ledger, "superseded", "second revision body " * 25)

    reconcile_completed_jobs(ledger.connection, now=NOW)

    assert _snapshot(ledger.connection)["ready"] == 1
    assert _snapshot(ledger.connection)["dead_letter"] == 0
    retired = ledger.connection.execute(
        """SELECT count(*) FROM news_ai_jobs_v1
           WHERE last_error='CURRENT_EVIDENCE_NO_LONGER_ELIGIBLE'"""
    ).fetchone()[0]
    assert retired == 3
    ledger.close()


def test_job_summary_install_is_idempotent_and_never_rebackfills_history(tmp_path) -> None:
    ledger = ForwardLedger(tmp_path / "forward.sqlite3", now=NOW)
    connection = ledger.connection
    rows = [(
        f"history-{index}", "ACTIVE_ANNOTATION", "fixture", f"item-{index}",
        1, "", "historical-prompt", "NORMAL", "COMPLETED", NOW.isoformat(),
        None, None, 1, None, NOW.isoformat(), NOW.isoformat(), NOW.isoformat(),
    ) for index in range(4_000)]
    with connection:
        connection.executemany(
            """INSERT INTO news_ai_jobs_v1
               (job_id,task_type,source,source_item_id,revision_number,annotation_id,
                prompt_version,priority,state,available_at,lease_owner,
                lease_expires_at,attempt_count,last_error,created_at,updated_at,
                completed_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            rows,
        )
    statements: list[str] = []
    connection.set_trace_callback(statements.append)
    install_critical_annotation_state_schema(connection)
    connection.set_trace_callback(None)

    normalized = [" ".join(statement.lower().split()) for statement in statements]
    assert not any(
        "from news_ai_jobs_v1" in statement
        and ("count(" in statement or "group by" in statement)
        for statement in normalized
    )
    assert not any("select distinct cluster_id from news_revisions" in statement
                   for statement in normalized)
    assert _snapshot(connection)["ready"] == 0
    ledger.close()


def test_one_time_install_backfills_pre_scheduler_current_completion(tmp_path) -> None:
    ledger = ForwardLedger(tmp_path / "forward.sqlite3", now=NOW)
    body = "existing current annotation evidence " * 20
    _append_revision(ledger, "existing", body)
    with ledger.connection:
        ledger.connection.execute(
            "INSERT INTO news_annotations VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "annotation-existing", "fixture", "existing", 1,
                hashlib.sha256(body.encode()).hexdigest(), "MACRO", "[]",
                0, 0, 0, 0, 0, 0.5, 0.8, "gemini-3.5-flash-lite",
                PROMPT_VERSION, NOW.isoformat(), NOW.isoformat(), "{}",
            ),
        )
        ledger.connection.execute(
            "DELETE FROM dashboard_critical_state_metadata_v1 WHERE version=?",
            (INSTALL_VERSION,),
        )

    install_critical_annotation_state_schema(ledger.connection)
    assert _snapshot(ledger.connection)["ready"] == 1

    statements: list[str] = []
    ledger.connection.set_trace_callback(statements.append)
    install_critical_annotation_state_schema(ledger.connection)
    ledger.connection.set_trace_callback(None)
    assert not any(
        "from news_revisions" in statement.lower()
        or "from news_annotations" in statement.lower()
        or "from news_ai_jobs_v1" in statement.lower()
        for statement in statements
        if statement.lstrip().upper().startswith("SELECT")
    )
    ledger.close()


def test_materialization_contract_handover_rebuilds_once_and_is_atomic(
    tmp_path, monkeypatch,
) -> None:
    ledger = ForwardLedger(tmp_path / "forward.sqlite3", now=NOW)
    connection = ledger.connection
    body = "contract-bound current annotation evidence " * 20
    _append_revision(ledger, "contract", body)
    with connection:
        connection.execute(
            "INSERT INTO news_annotations VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "annotation-contract", "fixture", "contract", 1,
                hashlib.sha256(body.encode()).hexdigest(), "MACRO", "[]",
                0, 0, 0, 0, 0, 0.5, 0.8, "gemini-3.5-flash-lite",
                PROMPT_VERSION, NOW.isoformat(), NOW.isoformat(), "{}",
            ),
        )
        refresh_news_revision_state(connection, "fixture", "contract", 1)
    old_job = enqueue_job(
        connection, task_type="ACTIVE_ANNOTATION", source="fixture",
        source_item_id="contract", revision_number=1, annotation_id="",
        prompt_version=PROMPT_VERSION, priority="NORMAL", now=NOW,
    )
    with connection:
        connection.execute(
            "UPDATE news_ai_jobs_v1 SET state='COMPLETED' WHERE job_id=?",
            (old_job,),
        )
    contract_a = annotation_materialization_contract()

    same_contract_statements: list[str] = []
    connection.set_trace_callback(same_contract_statements.append)
    install_critical_annotation_state_schema(connection)
    connection.set_trace_callback(None)
    assert not any(
        "select distinct cluster_id from news_revisions" in statement.lower()
        for statement in same_contract_statements
    )

    prompt_b = f"{PROMPT_VERSION}-contract-b"
    monkeypatch.setattr(annotation_contract, "PROMPT_VERSION", prompt_b)
    contract_b = annotation_materialization_contract()
    assert contract_b.fingerprint != contract_a.fingerprint
    handover_statements: list[str] = []
    connection.set_trace_callback(handover_statements.append)
    install_critical_annotation_state_schema(connection)
    connection.set_trace_callback(None)
    assert sum(
        "select distinct cluster_id from news_revisions" in statement.lower()
        for statement in handover_statements
    ) == 1
    handover_queue = annotation_queue_snapshot(
        connection, prompt_version=prompt_b, observed_at=NOW.isoformat(),
    )
    assert handover_queue["queued"] == 0
    assert handover_queue["semantic_pending"] == 1
    assert connection.execute(
        "SELECT last_error FROM news_ai_jobs_v1 WHERE job_id=?", (old_job,),
    ).fetchone()[0] == "CURRENT_EVIDENCE_NO_LONGER_ELIGIBLE"
    marker = connection.execute(
        """SELECT contract_fingerprint,contract_json
           FROM dashboard_critical_state_metadata_v1 WHERE version=?""",
        (INSTALL_VERSION,),
    ).fetchone()
    assert tuple(marker) == (contract_b.fingerprint, contract_b.components_json)

    second_b_statements: list[str] = []
    connection.set_trace_callback(second_b_statements.append)
    install_critical_annotation_state_schema(connection)
    connection.set_trace_callback(None)
    assert not any(
        "from news_revisions" in statement.lower()
        or "from news_annotations" in statement.lower()
        or "from news_ai_jobs_v1" in statement.lower()
        for statement in second_b_statements
        if statement.lstrip().upper().startswith("SELECT")
    )

    monkeypatch.setattr(annotation_contract, "PROMPT_VERSION", f"{prompt_b}-failed")
    failed_contract = annotation_materialization_contract()

    def fail_refresh(*_args, **_kwargs) -> None:
        raise RuntimeError("rebuild failed")

    monkeypatch.setattr(critical_state, "refresh_news_cluster_state", fail_refresh)
    with pytest.raises(RuntimeError, match="rebuild failed"):
        install_critical_annotation_state_schema(connection)
    persisted = connection.execute(
        """SELECT contract_fingerprint FROM dashboard_critical_state_metadata_v1
           WHERE version=?""",
        (INSTALL_VERSION,),
    ).fetchone()[0]
    assert persisted == contract_b.fingerprint
    assert persisted != failed_contract.fingerprint
    ledger.close()
