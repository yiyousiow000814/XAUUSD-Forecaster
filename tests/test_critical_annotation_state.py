from __future__ import annotations

import hashlib
import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

import xauusd_forecaster.annotation as annotation_contract
import xauusd_forecaster.critical_annotation_state as critical_state
from xauusd_forecaster.annotation import PROMPT_VERSION
from xauusd_forecaster.critical_annotation_state import (
    INSTALL_VERSION,
    annotation_materialization_contract,
    annotation_queue_snapshot,
    install_critical_annotation_state_schema,
    news_current_counts,
    refresh_news_revision_state,
)
from xauusd_forecaster.forward_ledger import ForwardLedger
from xauusd_forecaster.news_scheduler import (
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
    assert _snapshot(connection, NOW + timedelta(hours=2))["queued"] == 1

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
    assert annotation_queue_snapshot(
        connection, prompt_version=prompt_b, observed_at=NOW.isoformat(),
    )["queued"] == 1
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
