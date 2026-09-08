from __future__ import annotations

import hashlib
import sqlite3
from contextlib import closing
from datetime import UTC, datetime, timedelta

import pytest

import xauusd_forecaster.news.annotation.product as annotation_contract
import xauusd_forecaster.news.semantics.critical_state as critical_state
import xauusd_forecaster.news.scheduler.state as scheduler
from xauusd_forecaster.news.annotation.product import PROMPT_VERSION
from xauusd_forecaster.news.semantics.critical_state import INSTALL_VERSION
from xauusd_forecaster.news.semantics.critical_state import annotation_materialization_contract
from xauusd_forecaster.news.semantics.critical_state import annotation_queue_snapshot
from xauusd_forecaster.news.semantics.critical_state import install_critical_annotation_state_schema
from xauusd_forecaster.news.semantics.critical_state import news_current_counts
from xauusd_forecaster.news.semantics.critical_state import refresh_news_revision_state
from xauusd_forecaster.evidence.ledger import ForwardLedger
from xauusd_forecaster.runtime.operational_health import scheduler_health_snapshot
from xauusd_forecaster.news.scheduler.state import ROUTINE_POOL
from xauusd_forecaster.news.scheduler.state import backoff_job
from xauusd_forecaster.news.scheduler.state import claim_job
from xauusd_forecaster.news.scheduler.state import complete_job
from xauusd_forecaster.news.scheduler.state import enqueue_job
from xauusd_forecaster.news.scheduler.state import reconcile_completed_jobs


NOW = datetime(2026, 8, 19, 12, 0, tzinfo=UTC)


@pytest.mark.parametrize("column,value,dirty", [
    ("task_type", "TITLE_TRANSLATION", True),
    ("source", "another-source", True),
    ("source_item_id", "another-item", True),
    ("revision_number", 2, True),
    ("annotation_id", "another-annotation", True),
    ("prompt_version", "another-prompt", True),
    ("state", "BACKING_OFF", True),
    ("last_error", "ANOTHER_ERROR", True),
    ("state", "QUEUED", False),
    ("updated_at", "2026-08-19T12:01:00+00:00", False),
    ("available_at", "2026-08-19T12:01:00+00:00", False),
])
def test_job_input_version_tracks_reconciliation_fields_not_poll_time(
    tmp_path, column, value, dirty,
) -> None:
    with closing(ForwardLedger(tmp_path / "version.sqlite3", now=NOW)) as ledger:
        connection = ledger.connection
        _insert_lane_fixture_jobs(connection, live=1, backfill=0, unclassified=0)
        before = critical_state.news_job_input_revision(connection)
        assert before == 1
        with connection:
            connection.execute(f"UPDATE news_ai_jobs_v1 SET {column}=?", (value,))
        assert critical_state.news_job_input_revision(connection) == before + int(dirty)


def test_job_input_version_survives_equal_counts_old_insert_and_restart(tmp_path) -> None:
    database = tmp_path / "version.sqlite3"
    with closing(ForwardLedger(database, now=NOW)) as ledger:
        connection = ledger.connection
        _insert_lane_fixture_jobs(connection, live=2, backfill=0, unclassified=0)
        with connection:
            connection.execute("UPDATE news_ai_jobs_v1 SET state='BACKING_OFF' WHERE job_id='job-live-00000'")
        before_counts = [tuple(row) for row in connection.execute(
            "SELECT * FROM dashboard_annotation_job_counts_v1 ORDER BY state",
        )]
        before_revision = critical_state.news_job_input_revision(connection)
        with connection:
            connection.execute("""UPDATE news_ai_jobs_v1 SET state=CASE
                WHEN state='QUEUED' THEN 'BACKING_OFF' ELSE 'QUEUED' END""")
            # Original Stable's one-column positional metadata INSERT remains legal.
            connection.execute("INSERT INTO dashboard_job_count_metadata_v1 VALUES ('old-writer-fixture')")
        assert before_counts == [tuple(row) for row in connection.execute(
            "SELECT * FROM dashboard_annotation_job_counts_v1 ORDER BY state",
        )]
        assert critical_state.news_job_input_revision(connection) == before_revision + 2
        expected = before_revision + 2
    with closing(ForwardLedger(database, now=NOW)) as ledger:
        assert critical_state.news_job_input_revision(ledger.connection) == expected


def test_job_input_version_rollback_and_delete_are_transaction_owned(tmp_path) -> None:
    with closing(ForwardLedger(tmp_path / "version.sqlite3", now=NOW)) as ledger:
        connection = ledger.connection
        _insert_lane_fixture_jobs(connection, live=1, backfill=0, unclassified=0)
        before = critical_state.news_job_input_revision(connection)
        connection.execute("SAVEPOINT caller")
        connection.execute("UPDATE news_ai_jobs_v1 SET state='BACKING_OFF'")
        assert critical_state.news_job_input_revision(connection) == before + 1
        connection.execute("ROLLBACK TO caller")
        connection.execute("RELEASE caller")
        assert critical_state.news_job_input_revision(connection) == before
        with connection:
            connection.execute("DELETE FROM news_ai_jobs_v1")
        assert critical_state.news_job_input_revision(connection) == before + 1


@pytest.mark.parametrize("lost_authority", [
    "revision", "insert-trigger", "old-update-trigger", "partial-update-trigger",
])
def test_job_version_reinitialization_invalidates_old_acceptance(tmp_path, lost_authority) -> None:
    with closing(ForwardLedger(tmp_path / "version.sqlite3", now=NOW)) as ledger:
        connection = ledger.connection
        with connection:
            connection.execute("""INSERT INTO runtime_metadata(key,value,created_at)
                VALUES (?,'{"accepted_revision":0}',?)""",
                (critical_state.NEWS_RECONCILIATION_CACHE_KEY, NOW.isoformat()))
            if lost_authority == "revision":
                connection.execute("DELETE FROM runtime_metadata WHERE key=?",
                                   (critical_state.NEWS_JOB_REVISION_KEY,))
            elif lost_authority == "insert-trigger":
                connection.execute("DROP TRIGGER dashboard_job_count_insert_v1")
            else:
                connection.execute("DROP TRIGGER dashboard_job_count_update_v1")
                if lost_authority == "old-update-trigger":
                    connection.execute("""CREATE TRIGGER dashboard_job_count_update_v1
                        AFTER UPDATE ON news_ai_jobs_v1 BEGIN SELECT 1; END""")
                else:
                    connection.execute(f"""CREATE TRIGGER dashboard_job_count_update_v1
                        AFTER UPDATE OF state ON news_ai_jobs_v1 BEGIN
                          UPDATE runtime_metadata SET value=CAST(value AS INTEGER)+1
                          WHERE key='{critical_state.NEWS_JOB_REVISION_KEY}'; END""")
        if lost_authority == "insert-trigger":
            # Real source mutation during the lost-hook interval leaves stale
            # counts; reinstall must repair counts as well as the cache key.
            _insert_lane_fixture_jobs(connection, live=0, backfill=1, unclassified=0)
        critical_state.install_annotation_job_count_schema(connection)
        assert critical_state.news_job_input_revision(connection) == 0
        assert connection.execute("SELECT value FROM runtime_metadata WHERE key=?",
            (critical_state.NEWS_RECONCILIATION_CACHE_KEY,)).fetchone()[0] == "null"
        assert connection.execute("SELECT COALESCE(sum(job_count),0) "
            "FROM dashboard_annotation_job_counts_v1").fetchone()[0] == int(lost_authority == "insert-trigger")
        _insert_lane_fixture_jobs(connection, live=1, backfill=0, unclassified=0)
        assert critical_state.news_job_input_revision(connection) == 1


@pytest.mark.parametrize("invalid", [
    "invalid", "01", "+1", " 1", "1 ", "1e0", "9" * 20, "-1", "9223372036854775808",
])
def test_corrupt_job_version_is_unavailable_not_reset_by_mutation(tmp_path, invalid) -> None:
    with closing(ForwardLedger(tmp_path / "version.sqlite3", now=NOW)) as ledger:
        connection = ledger.connection
        with connection:
            connection.execute("UPDATE runtime_metadata SET value=? WHERE key=?",
                               (invalid, critical_state.NEWS_JOB_REVISION_KEY))
        assert critical_state.news_job_input_revision(connection) is None
        _insert_lane_fixture_jobs(connection, live=1, backfill=0, unclassified=0)
        assert critical_state.news_job_input_revision(connection) is None
        critical_state.install_annotation_job_count_schema(connection)
        assert critical_state.news_job_input_revision(connection) is None


def test_job_input_version_overflow_cannot_be_reused(tmp_path) -> None:
    with closing(ForwardLedger(tmp_path / "version.sqlite3", now=NOW)) as ledger:
        with ledger.connection:
            ledger.connection.execute("UPDATE runtime_metadata SET value=? WHERE key=?",
                (str(2**63 - 1), critical_state.NEWS_JOB_REVISION_KEY))
        assert critical_state.news_job_input_revision(ledger.connection) == 2**63 - 1
        _insert_lane_fixture_jobs(ledger.connection, live=1, backfill=0, unclassified=0)
        assert critical_state.news_job_input_revision(ledger.connection) is None


def test_job_input_version_reader_old_schema_and_readonly(tmp_path) -> None:
    with closing(sqlite3.connect(":memory:")) as old:
        assert critical_state.news_job_input_revision(old) is None
    database = tmp_path / "readonly.sqlite3"
    with closing(ForwardLedger(database, now=NOW)) as ledger:
        _insert_lane_fixture_jobs(ledger.connection, live=1, backfill=0, unclassified=0)
    with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as readonly:
        assert critical_state.news_job_input_revision(readonly) == 1
        assert readonly.total_changes == 0


@pytest.mark.parametrize("key", [
    None, "FORWARD_EPOCH", "ANOTHER_IMMUTABLE_FIXTURE",
    critical_state.NEWS_JOB_REVISION_KEY, critical_state.NEWS_RECONCILIATION_CACHE_KEY,
])
def test_job_input_version_metadata_exception_does_not_unlock_evidence(tmp_path, key) -> None:
    with closing(ForwardLedger(tmp_path / "guards.sqlite3", now=NOW)) as ledger:
        connection = ledger.connection
        mutable = key in {critical_state.NEWS_JOB_REVISION_KEY,
                          critical_state.NEWS_RECONCILIATION_CACHE_KEY}
        with connection:
            connection.execute("INSERT INTO runtime_metadata(key,value,created_at) "
                "SELECT ?,'initial',? WHERE NOT EXISTS (SELECT 1 FROM runtime_metadata WHERE key IS ?)",
                (key, NOW.isoformat(), key))
        before = tuple(connection.execute("SELECT * FROM runtime_metadata WHERE key IS ?", (key,)).fetchone())
        invalid_updates = [
            ("key", "RENAMED_KEY"), ("created_at", "changed"),
            ("key", critical_state.NEWS_RECONCILIATION_CACHE_KEY),
        ]
        if not mutable:
            invalid_updates.append(("value", "changed"))
        for column, value in invalid_updates:
            if column == "key" and value == key:
                continue
            with pytest.raises(sqlite3.IntegrityError, match="append-only"):
                with connection:
                    connection.execute(f"UPDATE runtime_metadata SET {column}=? WHERE key IS ?", (value, key))
            assert tuple(connection.execute("SELECT * FROM runtime_metadata WHERE key IS ?", (key,)).fetchone()) == before
        if mutable:
            with pytest.raises(sqlite3.IntegrityError, match="append-only"):
                with connection:
                    connection.execute("INSERT OR REPLACE INTO runtime_metadata VALUES (?,'replacement','changed')", (key,))
            with connection:
                connection.execute("UPDATE runtime_metadata SET value='changed' WHERE key=?", (key,))
            assert connection.execute("SELECT created_at FROM runtime_metadata WHERE key=?", (key,)).fetchone()[0] == before[2]
        else:
            with pytest.raises(sqlite3.IntegrityError, match="append-only"):
                with connection:
                    connection.execute("DELETE FROM runtime_metadata WHERE key IS ?", (key,))


def test_job_input_version_installer_preserves_transaction_and_rolls_back_failure(tmp_path) -> None:
    with closing(ForwardLedger(tmp_path / "installer.sqlite3", now=NOW)) as ledger:
        connection = ledger.connection
        connection.execute("SAVEPOINT caller")
        with pytest.raises(ValueError, match="NEWS_JOB_SCHEMA_TRANSACTION_ALREADY_ACTIVE"):
            critical_state.install_annotation_job_count_schema(connection)
        assert connection.in_transaction
        connection.execute("ROLLBACK TO caller")
        connection.execute("RELEASE caller")
        with connection:
            connection.execute("DROP TRIGGER runtime_metadata_no_update")
            connection.execute("""CREATE TRIGGER runtime_metadata_no_update
                BEFORE UPDATE ON runtime_metadata BEGIN
                SELECT RAISE(ABORT,'runtime_metadata is append-only'); END""")
        old_guard = connection.execute("SELECT sql FROM sqlite_master WHERE name='runtime_metadata_no_update'").fetchone()[0]

        def refuse_metadata_update(action, table, *_):
            return sqlite3.SQLITE_DENY if action == sqlite3.SQLITE_UPDATE and table == "runtime_metadata" else sqlite3.SQLITE_OK

        connection.set_authorizer(refuse_metadata_update)
        try:
            with pytest.raises(sqlite3.DatabaseError, match="not authorized"):
                critical_state.install_annotation_job_count_schema(connection)
        finally:
            connection.set_authorizer(None)
        assert not connection.in_transaction
        assert connection.execute("SELECT sql FROM sqlite_master WHERE name='runtime_metadata_no_update'").fetchone()[0] == old_guard
        critical_state.install_annotation_job_count_schema(connection)
        _insert_lane_fixture_jobs(connection, live=1, backfill=0, unclassified=0)
        assert critical_state.news_job_input_revision(connection) == 1
        # An old installer uses CREATE IF NOT EXISTS, so it cannot replace the
        # new scoped evidence guard or remove the job hook's extended fields.
        connection.execute("""CREATE TRIGGER IF NOT EXISTS runtime_metadata_no_update
            BEFORE UPDATE ON runtime_metadata BEGIN SELECT RAISE(ABORT,'old'); END""")
        before = connection.total_changes
        critical_state.install_annotation_job_count_schema(connection)
        assert connection.total_changes == before
        with connection:
            connection.execute("UPDATE news_ai_jobs_v1 SET annotation_id='changed'")
        assert critical_state.news_job_input_revision(connection) == 2


@pytest.mark.parametrize("change", [
    "none", "source", "annotation", "title", "impact", "job", "protected", "clock",
    "corrupt-cache", "deep-cache",
])
def test_reconciliation_source_first_preserves_rules_without_unchanged_body_reads(
    tmp_path, change, record_property,
) -> None:
    from tests.test_daily_brief import _seed_news, _seed_news_item

    with closing(ForwardLedger(tmp_path / "reconciliation.sqlite3", now=NOW - timedelta(days=30))) as ledger:
        connection = ledger.connection
        _seed_news(ledger)
        job_id = enqueue_job(connection, task_type="ACTIVE_ANNOTATION", source="Reuters",
            source_item_id="item-1", revision_number=1, annotation_id="",
            prompt_version=PROMPT_VERSION, priority="NORMAL", now=NOW)
        assert reconcile_completed_jobs(connection, now=NOW) == 1
        body_reads = []
        statements = []

        def measured_length(value):
            if value == "x" * 300:
                body_reads.append(300)
            return len(value) if value is not None else None

        connection.create_function("length", 1, measured_length, deterministic=True)
        connection.set_trace_callback(statements.append)
        before = connection.total_changes
        assert reconcile_completed_jobs(connection, now=NOW + timedelta(minutes=1)) == 0
        assert body_reads == []
        assert connection.total_changes == before
        assert not any("UPDATE news_ai_jobs_v1 AS j" in sql for sql in statements)
        record_property("unchanged_reconciliation", {
            "body_evaluations": 0, "body_bytes_evaluated": 0,
            "sqlite_changes": 0, "metadata_selects": sum(sql.lstrip().startswith("SELECT") for sql in statements),
            "statement_count": len(statements), "http_business_post": 0,
            "measurement": "SQLite expression evaluation, not physical disk or D1 rows",
        })
        connection.set_trace_callback(None)
        if change == "source":
            _seed_news_item(ledger, "new-item", minute=2)
        elif change == "annotation":
            columns = [str(row[1]) for row in connection.execute("PRAGMA table_info(news_annotations)")]
            values = list(connection.execute("SELECT * FROM news_annotations LIMIT 1").fetchone())
            values[columns.index("annotation_id")] = "new-annotation"
            values[columns.index("llm_model_version")] = "declared-other-model"
            with connection:
                connection.execute(f"INSERT INTO news_annotations VALUES ({','.join('?' for _ in values)})", values)
        elif change == "title":
            with connection:
                connection.execute("INSERT INTO news_title_translations VALUES (?,?,?,?,?,?,?,?,?,?)", (
                    "new-title", "Reuters", "item-1", 1, "hash-item-1", "新的标题",
                    "declared-model", PROMPT_VERSION, NOW.isoformat(), NOW.isoformat(),
                ))
        elif change == "impact":
            with connection:
                connection.execute("INSERT INTO news_impact_assessments_v1 VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
                    "new-impact", "Reuters", "item-1", 1, "hash-item-1", "annotation-item-1",
                    "declared-model", "declared-impact-prompt", NOW.isoformat(), NOW.isoformat(),
                    "SAME_DAY", "ACTIVE", "NEW_EVENT", 0.8, "隔离测试影响",
                ))
        elif change == "job":
            with connection:
                connection.execute("UPDATE news_ai_jobs_v1 SET last_error='PRIOR_FAILURE' WHERE job_id=?", (job_id,))
        elif change in {"corrupt-cache", "deep-cache"}:
            with connection:
                connection.execute("UPDATE runtime_metadata SET value=? WHERE key=?",
                    ("malformed" if change == "corrupt-cache" else "[" * 1500 + "]" * 1500,
                     critical_state.NEWS_RECONCILIATION_CACHE_KEY))
        statements.clear()
        body_reads.clear()
        connection.set_trace_callback(statements.append)
        try:
            assert reconcile_completed_jobs(connection,
                now=NOW - timedelta(seconds=1) if change == "clock" else NOW + timedelta(minutes=2),
                protected_receipt_days=("2026-08-10",) if change == "protected" else (),
            ) == 0
        finally:
            connection.set_trace_callback(None)
        assert bool(body_reads) == (change != "none")
        assert any("UPDATE news_ai_jobs_v1 AS j" in sql for sql in statements) == (change != "none")
        assert connection.execute("SELECT state FROM news_ai_jobs_v1 WHERE job_id=?", (job_id,)).fetchone()[0] == "COMPLETED"
        if change == "job":
            assert connection.execute("SELECT last_error FROM news_ai_jobs_v1 WHERE job_id=?", (job_id,)).fetchone()[0] == "PRIOR_FAILURE"


def test_reconciliation_source_first_live_old_hook_cannot_preserve_false_acceptance(tmp_path) -> None:
    from tests.test_daily_brief import _seed_news

    with closing(ForwardLedger(tmp_path / "old-hook.sqlite3", now=NOW - timedelta(days=30))) as ledger:
        connection = ledger.connection
        _seed_news(ledger)
        job = enqueue_job(connection, task_type="ACTIVE_ANNOTATION", source="Reuters",
            source_item_id="item-1", revision_number=1, annotation_id="",
            prompt_version=PROMPT_VERSION, priority="NORMAL", now=NOW)
        assert reconcile_completed_jobs(connection, now=NOW) == 1
        revision = critical_state.news_job_input_revision(connection)
        cache = connection.execute("SELECT value FROM runtime_metadata WHERE key=?",
            (critical_state.NEWS_RECONCILIATION_CACHE_KEY,)).fetchone()[0]
        assert cache
        # The pre-revision schema variant really maintains the same job counts,
        # but cannot advance a counter introduced by a newer running reader.
        old_update = next(sql for sql in critical_state._annotation_job_count_statements(
            has_runtime_metadata=False, has_brief_cache=False,
        ) if sql.startswith("CREATE TRIGGER IF NOT EXISTS dashboard_job_count_update_v1"))
        with connection:
            connection.execute("DROP TRIGGER dashboard_job_count_update_v1")
            connection.execute(old_update)
            connection.execute("UPDATE news_ai_jobs_v1 SET state='QUEUED' WHERE job_id=?", (job,))
        assert int(connection.execute("SELECT value FROM runtime_metadata WHERE key=?",
            (critical_state.NEWS_JOB_REVISION_KEY,)).fetchone()[0]) == revision
        assert critical_state.news_job_input_revision(connection) is None
        assert reconcile_completed_jobs(connection, now=NOW + timedelta(seconds=1)) == 1
        assert connection.execute("SELECT state FROM news_ai_jobs_v1 WHERE job_id=?", (job,)).fetchone()[0] == "COMPLETED"
        assert connection.execute("SELECT value FROM runtime_metadata WHERE key=?",
            (critical_state.NEWS_RECONCILIATION_CACHE_KEY,)).fetchone()[0] == cache
        assert not critical_state.news_job_source_hooks_are_current(connection)
        critical_state.install_annotation_job_count_schema(connection)
        assert critical_state.news_job_input_revision(connection) == revision
        assert connection.execute("SELECT value FROM runtime_metadata WHERE key=?",
            (critical_state.NEWS_RECONCILIATION_CACHE_KEY,)).fetchone()[0] == "null"
        assert reconcile_completed_jobs(connection, now=NOW + timedelta(seconds=2)) == 0
        changes = connection.total_changes
        assert reconcile_completed_jobs(connection, now=NOW + timedelta(seconds=3)) == 0
        assert connection.total_changes == changes


def test_reconciliation_source_first_caller_rollback_does_not_publish_acceptance(tmp_path) -> None:
    from tests.test_daily_brief import _seed_news

    with closing(ForwardLedger(tmp_path / "rollback.sqlite3", now=NOW - timedelta(days=30))) as ledger:
        connection = ledger.connection
        _seed_news(ledger)
        job_id = enqueue_job(connection, task_type="ACTIVE_ANNOTATION", source="Reuters",
            source_item_id="item-1", revision_number=1, annotation_id="",
            prompt_version=PROMPT_VERSION, priority="NORMAL", now=NOW)
        assert reconcile_completed_jobs(connection, now=NOW) == 1
        accepted = connection.execute("SELECT value FROM runtime_metadata WHERE key=?",
                                     (critical_state.NEWS_RECONCILIATION_CACHE_KEY,)).fetchone()[0]
        version = critical_state.news_job_input_revision(connection)
        connection.execute("SAVEPOINT caller")
        connection.execute("UPDATE news_ai_jobs_v1 SET state='QUEUED' WHERE job_id=?", (job_id,))
        assert reconcile_completed_jobs(connection, now=NOW, manage_transaction=False) == 1
        assert connection.in_transaction
        assert connection.execute("SELECT value FROM runtime_metadata WHERE key=?",
            (critical_state.NEWS_RECONCILIATION_CACHE_KEY,)).fetchone()[0] == accepted
        connection.execute("ROLLBACK TO caller")
        connection.execute("RELEASE caller")
        assert critical_state.news_job_input_revision(connection) == version
        before = connection.total_changes
        assert reconcile_completed_jobs(connection, now=NOW) == 0
        assert connection.total_changes == before


@pytest.mark.parametrize("error_code,cache_only,skips", [
    (sqlite3.SQLITE_BUSY, True, True), (sqlite3.SQLITE_READONLY, True, True),
    (sqlite3.SQLITE_BUSY_SNAPSHOT, True, False), (sqlite3.SQLITE_AUTH, True, False),
    (sqlite3.SQLITE_CORRUPT, True, False), (sqlite3.SQLITE_IOERR, True, False),
    (sqlite3.SQLITE_BUSY, False, False), (sqlite3.SQLITE_IOERR, "source-read", False),
])
def test_reconciliation_source_first_optional_cache_error_is_not_source_success(
    tmp_path, error_code, cache_only, skips,
) -> None:
    from tests.test_daily_brief import _seed_news

    with closing(ForwardLedger(tmp_path / "failure.sqlite3", now=NOW - timedelta(days=30))) as ledger:
        connection = ledger.connection
        _seed_news(ledger)
        job_id = enqueue_job(connection, task_type="ACTIVE_ANNOTATION", source="Reuters",
            source_item_id="item-1", revision_number=1, annotation_id="",
            prompt_version=PROMPT_VERSION, priority="NORMAL", now=NOW)

        class DeclaredSqlFailure:
            def __getattr__(self, name):
                return getattr(connection, name)

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return connection.__exit__(*args)

            def execute(self, sql, parameters=()):
                target = (sql.startswith("SELECT rowid,") if cache_only == "source-read"
                          else sql.startswith("INSERT INTO runtime_metadata") if cache_only
                          else "UPDATE news_ai_jobs_v1 AS j" in sql)
                if target:
                    if error_code == sqlite3.SQLITE_IOERR:
                        # Explicitly inject SQLite's whole-transaction-aborted
                        # error shape; cleanup must retain the original error.
                        connection.rollback()
                    error = sqlite3.OperationalError("DECLARED_SQLITE_FAILURE")
                    error.sqlite_errorcode = error_code
                    raise error
                return connection.execute(sql, parameters)

        if skips:
            assert reconcile_completed_jobs(DeclaredSqlFailure(), now=NOW) == 1
        else:
            with pytest.raises(sqlite3.OperationalError, match="DECLARED_SQLITE_FAILURE"):
                reconcile_completed_jobs(DeclaredSqlFailure(), now=NOW)
        assert not connection.in_transaction
        assert connection.execute("SELECT state FROM news_ai_jobs_v1 WHERE job_id=?", (job_id,)).fetchone()[0] == (
            "COMPLETED" if skips else "QUEUED")
        assert connection.execute("SELECT value FROM runtime_metadata WHERE key=?",
            (critical_state.NEWS_RECONCILIATION_CACHE_KEY,)).fetchone() is None
        # Genuine work can establish acceptance on the next normal owner call;
        # a skipped cache write never invents completion or causes an inner retry.
        assert reconcile_completed_jobs(connection, now=NOW) == (0 if skips else 1)


def test_reconciliation_source_first_survives_restart_and_coherent_restore(tmp_path) -> None:
    from tests.test_daily_brief import _seed_news

    original = tmp_path / "original.sqlite3"
    restored = tmp_path / "restored.sqlite3"
    with closing(ForwardLedger(original, now=NOW - timedelta(days=30))) as ledger:
        _seed_news(ledger)
        enqueue_job(ledger.connection, task_type="ACTIVE_ANNOTATION", source="Reuters",
            source_item_id="item-1", revision_number=1, annotation_id="",
            prompt_version=PROMPT_VERSION, priority="NORMAL", now=NOW)
        assert reconcile_completed_jobs(ledger.connection, now=NOW) == 1
        with closing(sqlite3.connect(restored)) as target:
            ledger.connection.backup(target)
    for database in (original, restored):
        with closing(ForwardLedger(database, now=NOW)) as ledger:
            statements = []
            ledger.connection.set_trace_callback(statements.append)
            before = ledger.connection.total_changes
            assert reconcile_completed_jobs(ledger.connection, now=NOW) == 0
            assert ledger.connection.total_changes == before
            assert not any("UPDATE news_ai_jobs_v1 AS j" in sql for sql in statements)
            ledger.connection.set_trace_callback(None)


@pytest.mark.parametrize("source_rows", [8, 256])
def test_reconciliation_source_first_hot_work_is_bounded_by_tails_not_history(
    tmp_path, source_rows, record_property,
) -> None:
    with closing(ForwardLedger(tmp_path / "tails.sqlite3", now=NOW)) as ledger:
        connection = ledger.connection
        _insert_unclassified_revisions(connection, historical=source_rows, live=0)
        assert reconcile_completed_jobs(connection, now=NOW) == 0
        # SQLite's schema catalog has no name index. Canonical hook verification
        # scans this fixed metadata, not the historical source tables. Keep its
        # measured allowance separate from the original source-read budget.
        schema_objects = connection.execute("SELECT count(*) FROM sqlite_master").fetchone()[0]
        limits = {"source": 1024, "schema": min(8192, 8 * schema_objects + 128)}
        steps = {"source": 0, "schema": 0}
        current = ["source"]

        def classify_statement(sql):
            current[0] = "schema" if "FROM sqlite_master" in sql else "source"

        def bounded_steps():
            kind = current[0]
            steps[kind] += 1
            return int(steps[kind] > limits[kind])

        before = connection.total_changes
        connection.set_trace_callback(classify_statement)
        connection.set_progress_handler(bounded_steps, 1)
        try:
            assert reconcile_completed_jobs(connection, now=NOW) == 0
        finally:
            connection.set_progress_handler(None, 0)
            connection.set_trace_callback(None)
        assert connection.total_changes == before
        record_property("reconciliation_tail_budget", {"source_rows": source_rows,
                        "schema_objects": schema_objects,
                        "sqlite_vm_steps": steps, "max_vm_steps": limits})


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
    job_roots = {int(row[0]) for row in ledger.connection.execute(
        "SELECT rootpage FROM sqlite_master WHERE tbl_name='news_ai_jobs_v1' AND rootpage>0"
    )}
    def historical_full_scans(sql):
        bytecode = ledger.connection.execute(f"EXPLAIN {sql}").fetchall()
        historical_cursors = {int(row[2]) for row in bytecode
            if row[1] == "OpenRead" and int(row[3]) in job_roots and int(row[4]) == 0}
        return [tuple(row) for row in bytecode
            if row[1] in {"Rewind", "Last", "Count"} and int(row[2]) in historical_cursors]

    # Negative controls protect aliased scans, including SQLite's special
    # count(*) operation, which need not iterate using Rewind/Last.
    for sql in ("SELECT job_id FROM news_ai_jobs_v1 AS j NOT INDEXED",
                "SELECT count(*) FROM news_ai_jobs_v1 AS j NOT INDEXED"):
        assert historical_full_scans(sql), sql
    job_plan_details: list[str] = []
    for sql in job_queries:
        # A materialized frontier can reuse the base-table alias in an outer
        # SCAN. Inspect actual database cursors so that this is neither mistaken
        # for a full historical-table scan nor allowed to hide a real one.
        full_scans = historical_full_scans(sql)
        assert not full_scans, full_scans
        job_plan_details.extend(
            str(row[3]) for row in ledger.connection.execute(
                f"EXPLAIN QUERY PLAN {sql}"
            ).fetchall()
            if "news_ai_jobs_" in str(row[3])
        )
    assert job_plan_details
    unindexed = [detail for detail in job_plan_details if "SEARCH" not in detail]
    assert not unindexed, "\n".join(unindexed)
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
