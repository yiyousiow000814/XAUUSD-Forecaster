"""The local date selector skips history only for its exact coherent inputs."""
from __future__ import annotations

import json
import sqlite3
import time
from datetime import UTC, datetime, timedelta

import pytest

import xauusd_forecaster.news.brief.product as daily_brief
from xauusd_forecaster.evidence.ledger import ForwardLedger


NOW = datetime(2026, 8, 13, 4, tzinfo=UTC)
TODAY = "2026-08-13"


def _receipt(connection, item, received):
    timestamp = received.isoformat()
    connection.execute(
        """INSERT INTO news_revisions
           (source,source_item_id,revision_number,source_published_time,
            collector_first_seen_time,item_first_seen_time,fetched_time,
            headline,body,link,content_hash,cluster_id,collector_latency_seconds)
           VALUES (?,?,1,?,?,?,?,?,?,?,?,?,0)""",
        ("test", item, timestamp, timestamp, timestamp, timestamp,
         "Synthetic date fixture", "No provider content", "https://example.invalid/",
         "hash-" + item, "cluster-" + item),
    )


def _finalization(connection, day, *, correction=False, status="EMPTY"):
    values = (day, None, status, 0, 0, 0, NOW.isoformat(), NOW.isoformat())
    if correction:
        connection.execute(
            """INSERT INTO daily_news_brief_finalization_corrections_v1
               (correction_id,recovery_version,brief_date,revision_number,
                final_status,received_items,reviewed_items,terminal_failure_items,
                cutoff_at,finalized_at) VALUES (?,?,?,?,?,?,?,?,?,?)""",
            ("correction-" + day, daily_brief.BRIEF_RECOVERY_VERSION, *values),
        )
    else:
        connection.execute(
            """INSERT INTO daily_news_brief_finalizations_v1
               (brief_date,revision_number,final_status,received_items,reviewed_items,
                terminal_failure_items,cutoff_at,finalized_at) VALUES (?,?,?,?,?,?,?,?)""",
            values,
        )


@pytest.fixture
def ledger(tmp_path):
    result = ForwardLedger(tmp_path / "brief-dates.sqlite3")
    # The real Brief owner, not a cache initializer, creates today's state.
    daily_brief.update_daily_brief(
        result, api_key=None, request_accountant=None, now=NOW,
    )
    _receipt(result.connection, "yesterday", NOW - timedelta(days=1))
    result.connection.commit()
    try:
        yield result
    finally:
        result.close()


def _observe(connection, now=NOW, limit=14):
    statements = []
    before = connection.total_changes
    connection.set_trace_callback(statements.append)
    try:
        dates = daily_brief.brief_dates_to_process(connection, now=now, limit=limit)
    finally:
        connection.set_trace_callback(None)
    return dates, {
        "distinct": sum("WITH receipt_days AS" in sql for sql in statements),
        "writes": connection.total_changes - before,
        "metadata_selects": sum(sql.lstrip().startswith("SELECT") for sql in statements),
        "statements": statements,
    }


def _cache(connection):
    return connection.execute(
        "SELECT date_discovery_cache_json FROM daily_news_brief_refresh_state WHERE brief_date=?",
        (TODAY,),
    ).fetchone()[0]


def test_date_discovery_cold_hot_and_old_brief_writer_preserve_public_contract(ledger, record_property):
    expected = [TODAY, "2026-08-12"]
    dates, cold = _observe(ledger.connection)
    assert dates == expected and cold["distinct"] == cold["writes"] == 1
    original = _cache(ledger.connection)
    hot_observations = []
    baseline_observations = []
    for minute in (1, 2, 30):
        dates, hot = _observe(ledger.connection, NOW + timedelta(minutes=minute))
        assert dates == expected and hot["distinct"] == hot["writes"] == 0
        assert hot["metadata_selects"] == 6
        assert not any("body" in sql or "brief_json" in sql for sql in hot["statements"])
        hot_observations.append({key: value for key, value in hot.items() if key != "statements"})
        statements = []
        ledger.connection.set_trace_callback(statements.append)
        try:
            baseline = daily_brief._uncached_brief_dates(
                ledger.connection, NOW + timedelta(minutes=minute), TODAY, 14,
            )
        finally:
            ledger.connection.set_trace_callback(None)
        assert baseline == expected
        baseline_observations.append({
            "historical_distinct": sum("WITH receipt_days AS" in sql for sql in statements),
            "sql_statements": len(statements),
        })
    # The unchanged named-column lifecycle writer also models old-code writes.
    daily_brief.update_daily_brief(
        ledger, api_key=None, request_accountant=None, now=NOW + timedelta(minutes=31),
    )
    assert _cache(ledger.connection) == original
    summary = daily_brief.daily_brief_summary(ledger.connection, now=NOW, total_brief_days=0)
    assert summary["phase"] == "WAITING"
    assert "date_discovery_cache_json" not in summary
    assert _observe(ledger.connection)[1]["distinct"] == 0
    record_property("date_discovery_work", {
        "cold": {key: value for key, value in cold.items() if key != "statements"},
        "unchanged_original": baseline_observations, "unchanged_cached": hot_observations,
        "scope": "DATE_SELECTOR_ONLY; not reconciliation, Brief synthesis or physical IO",
    })


def test_date_discovery_missing_owner_and_old_schema_do_not_invent_lifecycle_state(ledger, record_property):
    with ledger.connection:
        ledger.connection.execute("DELETE FROM daily_news_brief_refresh_state")
    dates, work = _observe(ledger.connection)
    assert dates == [TODAY, "2026-08-12"] and work["distinct"] == 1 and work["writes"] == 0
    assert ledger.connection.execute("SELECT * FROM daily_news_brief_refresh_state").fetchall() == []
    daily_brief.update_daily_brief(ledger, api_key=None, request_accountant=None, now=NOW)
    with ledger.connection:
        ledger.connection.execute(
            "ALTER TABLE daily_news_brief_refresh_state DROP COLUMN date_discovery_cache_json"
        )
    dates, work = _observe(ledger.connection)
    assert dates == [TODAY, "2026-08-12"] and work["distinct"] == 1 and work["writes"] == 0
    before = dict(ledger.connection.execute("SELECT * FROM daily_news_brief_refresh_state").fetchone())
    # The production additive migration preserves the real old row.
    migration = []
    ledger.connection.set_trace_callback(migration.append)
    started = time.perf_counter()
    try:
        ledger._install_daily_brief_lifecycle_schema()
    finally:
        elapsed = time.perf_counter() - started
        ledger.connection.set_trace_callback(None)
    migrated = dict(ledger.connection.execute("SELECT * FROM daily_news_brief_refresh_state").fetchone())
    assert migrated.pop("date_discovery_cache_json") is None
    assert migrated == before
    alterations = [sql for sql in migration if sql.startswith("ALTER TABLE")]
    assert len(alterations) == 1 and "ADD COLUMN date_discovery_cache_json TEXT" in alterations[0]
    record_property("additive_migration", {
        "elapsed_seconds": elapsed, "alter_statements": alterations,
        "preserved_refresh_rows": 1, "environment": "NEW_ISOLATED_FIXTURE_ONLY",
    })
    assert _observe(ledger.connection)[1]["writes"] == 1
    assert _observe(ledger.connection)[1]["distinct"] == 0


@pytest.mark.parametrize("change", ["news", "finalization", "correction", "recovery", "limit"])
def test_date_discovery_invalidates_only_its_actual_inputs(ledger, monkeypatch, change):
    if change == "correction":
        _finalization(ledger.connection, "2026-08-12", status="DEGRADED")
        ledger.connection.commit()
    _observe(ledger.connection)
    limit = 14
    with ledger.connection:
        if change == "news":
            _receipt(ledger.connection, "older-late-arrival", NOW - timedelta(days=2))
        elif change == "finalization":
            _finalization(ledger.connection, "2026-08-12")
        elif change == "correction":
            _finalization(ledger.connection, "2026-08-12", correction=True)
        elif change == "recovery":
            monkeypatch.setattr(daily_brief, "BRIEF_RECOVERY_VERSION", "test-next-recovery")
        else:
            limit = 1
    expected = daily_brief._uncached_brief_dates(ledger.connection, NOW, TODAY, limit)
    dates, work = _observe(ledger.connection, limit=limit)
    assert dates == expected and work["distinct"] == work["writes"] == 1
    assert _observe(ledger.connection, limit=limit)[1]["distinct"] == 0


@pytest.mark.parametrize("limit", [1, 2, 14])
def test_date_discovery_future_receipt_midnight_and_backward_clock(ledger, limit):
    with ledger.connection:
        _receipt(ledger.connection, "later-today", NOW + timedelta(hours=1))
        _receipt(ledger.connection, "tomorrow", NOW.replace(hour=16))
    _observe(ledger.connection, limit=limit)
    for instant in (NOW + timedelta(hours=2), NOW.replace(hour=15, minute=59, second=59, microsecond=999600)):
        expected = daily_brief._uncached_brief_dates(ledger.connection, instant, TODAY, limit)
        dates, work = _observe(ledger.connection, instant, limit)
        assert dates == expected
        sqlite_day = ledger.connection.execute(
            "SELECT date(julianday(?),'+8 hours')", (instant.isoformat(),),
        ).fetchone()[0]
        assert work["distinct"] == (0 if sqlite_day == TODAY else 1)
    tomorrow = NOW.replace(hour=16)
    expected = daily_brief._uncached_brief_dates(ledger.connection, tomorrow, "2026-08-14", limit)
    dates, work = _observe(ledger.connection, tomorrow, limit)
    assert dates == expected and work["distinct"] == 1 and work["writes"] == 0
    # Returning before the actual cached cutoff cannot use later eligibility.
    earlier = NOW - timedelta(hours=1)
    dates, work = _observe(ledger.connection, earlier, limit)
    assert dates == daily_brief._uncached_brief_dates(ledger.connection, earlier, TODAY, limit)
    assert work["distinct"] == work["writes"] == 1


@pytest.mark.parametrize("bad", ["{", "[]", "null", '{"key":[]}', " " * 4097, "[" * 1500 + "]" * 1500])
def test_date_discovery_invalid_cache_is_bounded_and_rebuilt(ledger, bad):
    with ledger.connection:
        ledger.connection.execute(
            "UPDATE daily_news_brief_refresh_state SET date_discovery_cache_json=?", (bad,),
        )
    dates, work = _observe(ledger.connection)
    assert dates == [TODAY, "2026-08-12"] and work["distinct"] == work["writes"] == 1
    assert len(_cache(ledger.connection).encode()) <= 4096
    assert _observe(ledger.connection)[1]["distinct"] == 0


@pytest.mark.parametrize("field,value", [
    ("key", []), ("dates", [TODAY, "2026-08-11", "2026-08-12"]),
    ("dates", [TODAY, "2026-08-12", "2026-08-12"]),
    ("observed_at", "2026-08-13T06:00:00.000000+00:00"),
    ("observed_at", "2026-08-12T04:00:00.000000+00:00"),
    ("observed_at", "2026-08-13T04:00:00"),
])
def test_date_discovery_wrong_key_dates_or_time_cannot_authorize_a_hit(ledger, field, value):
    _observe(ledger.connection)
    cached = json.loads(_cache(ledger.connection))
    cached[field] = value
    with ledger.connection:
        ledger.connection.execute(
            "UPDATE daily_news_brief_refresh_state SET date_discovery_cache_json=?",
            (json.dumps(cached),),
        )
    dates, work = _observe(ledger.connection)
    assert dates == [TODAY, "2026-08-12"] and work["distinct"] == work["writes"] == 1


def test_date_discovery_clock_day_disagreement_uses_original_selector(ledger, monkeypatch):
    _observe(ledger.connection)
    # A declared test clock changes the Python day only. SQLite retains its
    # real +8-hour receipt semantics; an ambiguous runtime cannot use the cache.
    monkeypatch.setattr(daily_brief, "KUALA_LUMPUR", UTC)
    early = NOW.replace(hour=17)
    expected = daily_brief._uncached_brief_dates(ledger.connection, early, TODAY, 14)
    dates, work = _observe(ledger.connection, early)
    assert dates == expected and work["distinct"] == 1 and work["writes"] == 0


def test_date_discovery_cache_write_failure_does_not_hide_database_failure(ledger):
    ledger.connection.execute(
        """CREATE TEMP TRIGGER reject_cache BEFORE UPDATE OF date_discovery_cache_json
           ON daily_news_brief_refresh_state BEGIN SELECT RAISE(ABORT,'test cache failure'); END"""
    )
    with pytest.raises(sqlite3.IntegrityError, match="test cache failure"):
        _observe(ledger.connection)
    assert _cache(ledger.connection) is None and not ledger.connection.in_transaction
    ledger.connection.execute("DROP TRIGGER reject_cache")
    assert _observe(ledger.connection)[1]["writes"] == 1


@pytest.mark.parametrize("code", [sqlite3.SQLITE_BUSY_SNAPSHOT, sqlite3.SQLITE_CORRUPT,
                                  sqlite3.SQLITE_AUTH, sqlite3.SQLITE_READONLY_DBMOVED])
def test_date_discovery_never_converts_other_write_failures_to_readonly_success(ledger, code):
    class FailingPublication(sqlite3.Connection):
        def execute(self, sql, *args):
            if sql.lstrip().startswith("UPDATE daily_news_brief_refresh_state SET date_discovery_cache_json="):
                error = sqlite3.OperationalError("declared test publication failure")
                error.sqlite_errorcode = code
                raise error
            return super().execute(sql, *args)

    database = ledger.connection.execute("PRAGMA database_list").fetchone()[2]
    connection = sqlite3.connect(database, factory=FailingPublication)
    try:
        with pytest.raises(sqlite3.OperationalError, match="declared test publication failure"):
            _observe(connection)
        assert _cache(connection) is None and not connection.in_transaction
    finally:
        connection.close()


def test_date_discovery_yields_to_real_business_writer_without_wait_or_retry(ledger, record_property):
    database = ledger.connection.execute("PRAGMA database_list").fetchone()[2]
    writer = sqlite3.connect(database)
    original_timeout = ledger.connection.execute("PRAGMA busy_timeout").fetchone()[0]
    try:
        writer.execute("BEGIN IMMEDIATE")
        # This is a real independent WAL write lock. Reads must still work, and
        # optional publication must not consume the normal 60-second budget.
        for _ in range(2):
            dates, work = _observe(ledger.connection)
            assert dates == [TODAY, "2026-08-12"]
            assert work["distinct"] == 1 and work["writes"] == 0
            assert _cache(ledger.connection) is None
            assert sum("UPDATE daily_news_brief_refresh_state SET date_discovery_cache_json=" in sql
                       for sql in work["statements"]) == 1
            assert "PRAGMA busy_timeout=0" in work["statements"]
            assert ledger.connection.execute("PRAGMA busy_timeout").fetchone()[0] == original_timeout
            assert not ledger.connection.in_transaction and writer.in_transaction
        record_property("contended_publication", {
            "real_writer_lock": True, "attempts_per_call": 1,
            "publication_busy_timeout_ms": 0, "restored_busy_timeout_ms": original_timeout,
            "cache_writes": 0, "result": dates,
        })
    finally:
        writer.rollback()
        writer.close()
    assert _observe(ledger.connection)[1]["writes"] == 1
    assert _observe(ledger.connection)[1]["distinct"] == 0


def test_date_discovery_source_read_busy_is_not_an_optional_publication(ledger):
    class BusySource(sqlite3.Connection):
        def execute(self, sql, *args):
            if "WITH receipt_days AS" in sql:
                error = sqlite3.OperationalError("declared source busy")
                error.sqlite_errorcode = sqlite3.SQLITE_BUSY
                raise error
            return super().execute(sql, *args)

    database = ledger.connection.execute("PRAGMA database_list").fetchone()[2]
    connection = sqlite3.connect(database, factory=BusySource)
    try:
        with pytest.raises(sqlite3.OperationalError, match="declared source busy"):
            _observe(connection)
        assert _cache(connection) is None and not connection.in_transaction
    finally:
        connection.close()


def test_date_discovery_source_and_cache_rollback_together(ledger):
    _observe(ledger.connection)
    original = _cache(ledger.connection)
    ledger.connection.execute("BEGIN")
    _receipt(ledger.connection, "uncommitted", NOW - timedelta(days=2))
    dates, work = _observe(ledger.connection)
    assert dates == [TODAY, "2026-08-12", "2026-08-11"] and work["writes"] == 0
    assert ledger.connection.in_transaction
    assert _cache(ledger.connection) == original
    ledger.connection.rollback()
    assert _cache(ledger.connection) == original
    assert _observe(ledger.connection)[0] == [TODAY, "2026-08-12"]
    assert _observe(ledger.connection)[1]["distinct"] == 0


@pytest.mark.parametrize("read_boundary", ["uri-readonly", "query-only", "caller-savepoint"])
def test_date_discovery_read_consumers_keep_their_original_capabilities(ledger, read_boundary):
    database = ledger.connection.execute("PRAGMA database_list").fetchone()[2]
    if read_boundary == "uri-readonly":
        from pathlib import Path
        connection = sqlite3.connect(Path(database).as_uri() + "?mode=ro", uri=True)
    else:
        connection = sqlite3.connect(database)
    try:
        if read_boundary == "query-only":
            connection.execute("PRAGMA query_only=ON")
        if read_boundary == "caller-savepoint":
            connection.execute("SAVEPOINT actual_caller")
            _receipt(connection, "caller-uncommitted", NOW - timedelta(days=2))
        dates, work = _observe(connection)
        assert dates[:2] == [TODAY, "2026-08-12"]
        assert work["distinct"] == 1 and work["writes"] == 0
        assert _cache(connection) is None
        if read_boundary == "caller-savepoint":
            assert dates[-1] == "2026-08-11" and connection.in_transaction
            connection.execute("ROLLBACK TO SAVEPOINT actual_caller")
            connection.execute("RELEASE SAVEPOINT actual_caller")
            assert _observe(connection)[0] == [TODAY, "2026-08-12"]
        else:
            assert not connection.in_transaction
    finally:
        connection.close()


def test_date_discovery_concurrent_append_rejects_late_cache_without_wal_upgrade(ledger, monkeypatch):
    database = ledger.connection.execute("PRAGMA database_list").fetchone()[2]
    writer = sqlite3.connect(database)
    original = daily_brief._uncached_brief_dates

    def append_after_read(*args):
        dates = original(*args)
        with writer:
            _receipt(writer, "concurrent", NOW - timedelta(days=2))
        return dates

    monkeypatch.setattr(daily_brief, "_uncached_brief_dates", append_after_read)
    try:
        # This call's coherent read precedes the concurrent append. A late
        # publication may store its token, but cannot claim the new row.
        dates, work = _observe(ledger.connection)
        assert dates == [TODAY, "2026-08-12"] and work["writes"] == 1
        assert not ledger.connection.in_transaction
        assert json.loads(_cache(ledger.connection))["key"][-1][0][0] == 1
    finally:
        writer.close()
    monkeypatch.setattr(daily_brief, "_uncached_brief_dates", original)
    assert _observe(ledger.connection)[0] == [TODAY, "2026-08-12", "2026-08-11"]
    assert _observe(ledger.connection)[1]["distinct"] == 0


def test_date_discovery_restart_and_coherent_backup_restore(ledger, tmp_path):
    _observe(ledger.connection)
    original = _cache(ledger.connection)
    backup = sqlite3.connect(tmp_path / "coherent.sqlite3")
    restored = sqlite3.connect(tmp_path / "restored.sqlite3")
    try:
        ledger.connection.backup(backup)
        with ledger.connection:
            _receipt(ledger.connection, "after-backup", NOW - timedelta(days=2))
        assert len(_observe(ledger.connection)[0]) == 3
        backup.backup(restored)
        assert _cache(restored) == original
        assert _observe(restored)[1]["distinct"] == 0
    finally:
        backup.close()
        restored.close()
    resumed = ForwardLedger(tmp_path / "restored.sqlite3")
    try:
        dates, work = _observe(resumed.connection)
        assert dates == [TODAY, "2026-08-12"] and work["distinct"] == work["writes"] == 0
    finally:
        resumed.close()


def test_date_discovery_tail_reads_use_constant_bounded_vm_work(ledger, record_property):
    counts = []
    for size in (8, 256):
        start = 0 if size == 8 else 8
        with ledger.connection:
            for index in range(start, size):
                _receipt(ledger.connection, f"tail-{index}", NOW)
                day = (NOW - timedelta(days=index + 2)).date().isoformat()
                _finalization(ledger.connection, day, status="DEGRADED")
                _finalization(ledger.connection, day, correction=True)
        steps = []
        ledger.connection.set_progress_handler(lambda: steps.append(1) or 0, 1)
        try:
            token = daily_brief._brief_date_source_token(ledger.connection)
        finally:
            ledger.connection.set_progress_handler(None, 0)
        assert token is not None and len(token) == 3
        counts.append(len(steps))
    assert counts[0] == counts[1] and counts[1] < 250
    record_property("bounded_tail_work", {"row_counts": [8, 256], "three_tail_vm_steps": counts})


def test_date_discovery_large_tail_does_not_read_payload_or_publish_partial_identity(ledger):
    with ledger.connection:
        _receipt(ledger.connection, "x" * 513, NOW)
    for _ in range(2):
        dates, work = _observe(ledger.connection)
        assert dates == [TODAY, "2026-08-12"] and work["distinct"] == 1 and work["writes"] == 0
        assert not any("body" in sql for sql in work["statements"])
    assert _cache(ledger.connection) is None


def test_actual_brief_batch_and_scheduler_share_dates_without_skipping_due_owners(ledger):
    from scripts.runtime.run_news_annotator import run_daily_brief_batch
    from xauusd_forecaster.news.scheduler.state import sync_pending_jobs

    # Explicit empty credentials preserve actual scheduler/Brief behavior and
    # prohibit reading a user's credential source. Finalization is real SQLite.
    first = run_daily_brief_batch(ledger, now=NOW, credentials=())
    assert {item["brief_date"] for item in first} == {TODAY, "2026-08-12"}
    assert ledger.connection.execute(
        "SELECT final_status FROM daily_news_brief_finalizations_v1 WHERE brief_date='2026-08-12'"
    ).fetchone()[0] == "EMPTY"
    sync_pending_jobs(ledger.connection, now=NOW)
    assert _observe(ledger.connection)[0] == [TODAY]
    statements = []
    ledger.connection.set_trace_callback(statements.append)
    try:
        second = run_daily_brief_batch(ledger, now=NOW + timedelta(minutes=1), credentials=())
        sync_pending_jobs(ledger.connection, now=NOW + timedelta(minutes=1))
    finally:
        ledger.connection.set_trace_callback(None)
    assert [item["brief_date"] for item in second] == [TODAY]
    assert second[0]["phase"] == "WAITING"
    assert not any("WITH receipt_days AS" in statement for statement in statements)
    # Other owners are NOT bypassed or counted as zero work by this cache.
    assert any("UPDATE" in statement or "INSERT" in statement for statement in statements)
