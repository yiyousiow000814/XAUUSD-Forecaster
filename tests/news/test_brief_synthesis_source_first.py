"""The existing Brief lifecycle owner adds disposable state and access paths."""
from __future__ import annotations

from contextlib import closing
from collections import Counter
from datetime import UTC, datetime, timedelta
import json
import sqlite3

import pytest

from tests.news.test_daily_brief import _fake_generation, _seed_news_item
from tests.fixtures.model_accounting_fakes import CallbackModelAccountant
import xauusd_forecaster.news.semantics.critical_state as critical_annotation_state
import xauusd_forecaster.news.brief.product as daily_brief
import xauusd_forecaster.news.scheduler.state as news_scheduler
from xauusd_forecaster.evidence.schema import install_v2_schema
from xauusd_forecaster.evidence.ledger import ForwardLedger
from xauusd_forecaster.evidence.ledger import SCHEMA


NOW = datetime(2026, 8, 10, 3, tzinfo=UTC)
CACHE_COLUMN = "synthesis_source_cache_json"
SOURCE_INDEXES = {
    "news_revisions_receipt_clock_v1": (
        "news_revisions", (None,), "julianday(collector_first_seen_time)",
    ),
    "news_title_translations_revision_clock_v1": (
        "news_title_translations",
        ("source", "source_item_id", "revision_number", "raw_content_hash", None),
        "julianday(parsed_at)",
    ),
    "news_event_identity_resolutions_assessment_clock_v1": (
        "news_event_identity_resolutions_v1", ("assessment_id", None),
        "julianday(resolved_at)",
    ),
}
EVIDENCE_TABLES = (
    "news_revisions", "news_annotations", "news_title_translations",
    "news_impact_assessments_v1", "news_event_identity_resolutions_v1",
    "daily_news_briefs",
)


def _column_names(connection):
    return {row["name"] for row in connection.execute(
        "PRAGMA table_info(daily_news_brief_refresh_state)",
    )}


def _installed_source_indexes(connection):
    return {row[0] for row in connection.execute(
        "SELECT name FROM sqlite_master WHERE type='index'",
    )} & SOURCE_INDEXES.keys()


def _evidence(connection):
    return {table: [tuple(row) for row in connection.execute(
        f"SELECT rowid,* FROM {table} ORDER BY rowid",
    )] for table in EVIDENCE_TABLES}


def _refresh(connection):
    return dict(connection.execute(
        "SELECT rowid,* FROM daily_news_brief_refresh_state WHERE brief_date='2026-08-10'",
    ).fetchone())


def _assert_optional_source_indexes(connection):
    assert _installed_source_indexes(connection) == SOURCE_INDEXES.keys()
    for name, (table, expected_columns, clock_expression) in SOURCE_INDEXES.items():
        definition = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type='index' AND name=?", (name,),
        ).fetchone()[0]
        index = next(row for row in connection.execute(f"PRAGMA index_list({table})")
                     if row["name"] == name)
        # These access paths add no uniqueness or partial-membership constraint.
        assert index["unique"] == 0 and index["partial"] == 0
        assert tuple(row["name"] for row in connection.execute(
            f"PRAGMA index_info({name})",
        )) == expected_columns
        assert clock_expression in "".join(definition.split())


@pytest.fixture
def legacy_lifecycle_owner(tmp_path):
    # Build the old persisted shape before any constructor can auto-upgrade it.
    # Bind the real owner method to that connection; constructor installation is
    # covered independently by the fresh-schema/reopen case below.
    path = tmp_path / "old-brief.sqlite3"
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    owner = object.__new__(ForwardLedger)
    owner.path = path
    owner.connection = connection
    try:
        connection.executescript(SCHEMA)
        connection.execute(
            f"ALTER TABLE daily_news_brief_refresh_state DROP COLUMN {CACHE_COLUMN}",
        )
        install_v2_schema(connection)
        owner._install_append_only_triggers()
        _seed_news_item(owner, "migration", minute=0)
        timestamp = NOW.isoformat()
        with connection:
            connection.execute(
                """INSERT INTO daily_news_brief_refresh_state
                   (brief_date,last_observed_at,phase,received_items,reviewed_items,
                    pending_source_hash,generation_failure_count,last_failure_code,
                    date_discovery_cache_json)
                   VALUES ('2026-08-10',?,'DEFERRED',1,1,'accepted-source',2,
                           'DECLARED_FAILURE','{"dates":["2026-08-10"]}')""",
                (timestamp,),
            )
            connection.execute(
                "INSERT INTO news_title_translations VALUES (?,?,?,?,?,?,?,?,?,?)",
                ("title-migration", "Reuters", "migration", 1, "hash-migration",
                 "既有标题", "fixture-model", "fixture-prompt", timestamp, timestamp),
            )
            connection.execute(
                "INSERT INTO news_impact_assessments_v1 VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                ("impact-migration", "Reuters", "migration", 1, "hash-migration",
                 "annotation-migration", "fixture-model", "fixture-prompt",
                 timestamp, timestamp, "SAME_DAY", "ACTIVE", "NEW_EVENT", 0.8,
                 "既有影响证据"),
            )
            connection.execute(
                """INSERT INTO news_event_identity_resolutions_v1
                   (resolution_id,annotation_id,assessment_id,llm_model_version,
                    prompt_version,resolved_at,identity_relation,matched_annotation_id,
                    identity_comparison_json,canonical_episode_id,canonical_event_id)
                   VALUES (?,?,?,?,?,?,'NEW_EPISODE',NULL,'{}',?,?)""",
                ("resolution-migration", "annotation-migration", "impact-migration",
                 "fixture-model", "fixture-prompt", timestamp, "episode-migration",
                 "event-migration"),
            )
            connection.execute(
                """INSERT INTO daily_news_briefs VALUES
                   ('2026-08-10',1,'accepted-source',?,?,'fixture-model',
                    'fixture-prompt','{"title":"既有简报"}')""",
                (timestamp, timestamp),
            )
        assert CACHE_COLUMN not in _column_names(connection)
        assert _installed_source_indexes(connection) == set()
        yield owner
    finally:
        owner.close()


def test_synthesis_source_schema_fresh_defaults_and_hot_reinstall(tmp_path) -> None:
    path = tmp_path / "fresh-brief.sqlite3"
    with closing(ForwardLedger(path, now=NOW)) as owner:
        connection = owner.connection
        column = next(row for row in connection.execute(
            "PRAGMA table_info(daily_news_brief_refresh_state)",
        ) if row["name"] == CACHE_COLUMN)
        assert column["type"] == "TEXT" and column["notnull"] == 0
        assert column["dflt_value"] is None
        with connection:
            connection.execute(
                "INSERT INTO daily_news_brief_refresh_state(brief_date,last_observed_at) VALUES (?,?)",
                ("2026-08-10", NOW.isoformat()),
            )
        state = _refresh(connection)
        assert state[CACHE_COLUMN] is None
        assert state["phase"] == "WAITING"
        assert state["received_items"] == state["generation_failure_count"] == 0
        _assert_optional_source_indexes(connection)
        before_schema = connection.execute("PRAGMA schema_version").fetchone()[0]
        before_changes = connection.total_changes
        owner._install_daily_brief_lifecycle_schema()
        assert connection.execute("PRAGMA schema_version").fetchone()[0] == before_schema
        assert connection.total_changes == before_changes
        assert _refresh(connection) == state
    with closing(ForwardLedger(path, now=NOW)) as reopened:
        assert _refresh(reopened.connection) == state
        _assert_optional_source_indexes(reopened.connection)


def test_synthesis_source_schema_additive_migration_preserves_old_rows(
    legacy_lifecycle_owner,
) -> None:
    owner = legacy_lifecycle_owner
    connection = owner.connection
    old_state = _refresh(connection)
    old_evidence = _evidence(connection)
    old_triggers = [tuple(row) for row in connection.execute(
        "SELECT name,sql FROM sqlite_master WHERE type='trigger' ORDER BY name",
    )]
    changes = connection.total_changes
    owner._install_daily_brief_lifecycle_schema()
    migrated = _refresh(connection)
    assert migrated.pop(CACHE_COLUMN) is None
    assert migrated == old_state
    assert _evidence(connection) == old_evidence
    assert connection.total_changes == changes
    assert [tuple(row) for row in connection.execute(
        "SELECT name,sql FROM sqlite_master WHERE type='trigger' ORDER BY name",
    )] == old_triggers
    _assert_optional_source_indexes(connection)
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        with connection:
            connection.execute("UPDATE news_revisions SET body='replacement'")
    assert _evidence(connection) == old_evidence


@pytest.mark.parametrize("caller_transaction", [False, True])
def test_synthesis_source_schema_failure_retries_and_respects_actual_transaction(
    legacy_lifecycle_owner, caller_transaction,
) -> None:
    owner = legacy_lifecycle_owner
    connection = owner.connection
    before_state = _refresh(connection)
    before_evidence = _evidence(connection)
    rejected_index = "news_title_translations_revision_clock_v1"

    def reject_second_index(action, name, *_):
        return (sqlite3.SQLITE_DENY
                if action == sqlite3.SQLITE_CREATE_INDEX and name == rejected_index
                else sqlite3.SQLITE_OK)

    if caller_transaction:
        connection.execute("BEGIN")
    connection.set_authorizer(reject_second_index)
    try:
        with pytest.raises(sqlite3.DatabaseError, match="not authorized"):
            owner._install_daily_brief_lifecycle_schema()
    finally:
        connection.set_authorizer(None)
    assert not connection.in_transaction
    assert _evidence(connection) == before_evidence
    after_failure = _refresh(connection)
    if caller_transaction:
        assert CACHE_COLUMN not in _column_names(connection)
        assert _installed_source_indexes(connection) == set()
    else:
        # The existing connection context does not begin a transaction for DDL.
        # Completed additive work can persist, and the same owner resumes it.
        assert after_failure.pop(CACHE_COLUMN) is None
        assert _installed_source_indexes(connection) == {"news_revisions_receipt_clock_v1"}
    assert after_failure == before_state
    owner._install_daily_brief_lifecycle_schema()
    final_state = _refresh(connection)
    assert final_state.pop(CACHE_COLUMN) is None
    assert final_state == before_state
    assert _evidence(connection) == before_evidence
    _assert_optional_source_indexes(connection)


def _publish(ledger, monkeypatch, *, instant=NOW):
    calls = []
    monkeypatch.setattr(daily_brief, "generate_metered_json", _fake_generation(calls))
    result = daily_brief.update_daily_brief(
        ledger, api_key="fixture-only", request_accountant=CallbackModelAccountant(lambda _: True),
        now=instant,
    )
    assert result["status"] == "OK" and len(calls) == 1
    return calls


def _no_payload(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("unchanged/no-due source must not build a payload")
    monkeypatch.setattr(daily_brief, "_population_rows", forbidden)
    monkeypatch.setattr(daily_brief, "_budgeted_evidence_packet", forbidden)
    monkeypatch.setattr(daily_brief, "generate_metered_json", forbidden)


def _advance(ledger, instant=NOW):
    return daily_brief.update_daily_brief(
        ledger, api_key="fixture-only", request_accountant=CallbackModelAccountant(lambda _: True),
        now=instant,
    )


def test_synthesis_source_unchanged_reuses_committed_result_without_observation_write(
    tmp_path, monkeypatch, record_property,
) -> None:
    with closing(ForwardLedger(tmp_path / "unchanged.sqlite3", now=NOW)) as ledger:
        _seed_news_item(ledger, "known", minute=0)
        _publish(ledger, monkeypatch)
        before = _refresh(ledger.connection)
        assert before[CACHE_COLUMN]
        changes = ledger.connection.total_changes
        statements = Counter()
        forbidden_reads = []
        def authorize(action, table, column, *_):
            if action == sqlite3.SQLITE_READ and (table, column) in {
                ("news_revisions", "body"), ("news_annotations", "annotation_json"),
                ("daily_news_briefs", "brief_json"),
            }:
                forbidden_reads.append((table, column))
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK
        def trace(sql):
            statements[sql.lstrip().split()[0].upper()] += 1
        ledger.connection.set_authorizer(authorize)
        ledger.connection.set_trace_callback(trace)
        try:
            with monkeypatch.context() as scoped:
                _no_payload(scoped)
                result = _advance(ledger, NOW + timedelta(minutes=1))
        finally:
            ledger.connection.set_authorizer(None)
            ledger.connection.set_trace_callback(None)
        assert result["status"] == "UNCHANGED"
        assert result["reason"] == "CANDIDATES_UNCHANGED"
        assert result["received_items"] == result["reviewed_items"] == 1
        assert _refresh(ledger.connection) == before
        assert ledger.connection.total_changes == changes
        assert forbidden_reads == []
        assert CACHE_COLUMN not in daily_brief.daily_brief_summary(ledger.connection, now=NOW)
        record_property("synthesis_unchanged", {
            "population_builder": 0, "packet_builder": 0, "model_requests": 0,
            "sqlite_row_changes": 0, "observation_time_changed": False,
            "remote_ack": "NOT_APPLICABLE", "production": False,
            "actual_sql_statement_classes": dict(statements),
            "forbidden_payload_reads": forbidden_reads,
        })


def test_synthesis_source_other_day_append_skips_payload_and_same_day_due_resumes(
    tmp_path, monkeypatch,
) -> None:
    with closing(ForwardLedger(tmp_path / "dated.sqlite3", now=NOW)) as ledger:
        _seed_news_item(ledger, "known", minute=0, category="rates_fed")
        _publish(ledger, monkeypatch)
        observed = _refresh(ledger.connection)["last_observed_at"]
        _seed_news_item(ledger, "other-day", minute=0,
                        received_at=NOW - timedelta(days=2))
        with monkeypatch.context() as scoped:
            _no_payload(scoped)
            assert _advance(ledger, NOW + timedelta(minutes=1))["status"] == "UNCHANGED"
        assert _refresh(ledger.connection)["last_observed_at"] == observed
        _seed_news_item(ledger, "new-today", minute=121, category="rates_fed",
                        parsed_at=NOW + timedelta(seconds=1))
        waiting = _advance(ledger, NOW + timedelta(minutes=1))
        assert waiting["status"] == "DEFERRED"
        assert waiting["phase"] == "UPDATING"
        before = _refresh(ledger.connection)
        with monkeypatch.context() as scoped:
            _no_payload(scoped)
            repeated = _advance(ledger, NOW + timedelta(minutes=2))
        assert repeated["status"] == "DEFERRED"
        assert _refresh(ledger.connection) == before
        due = datetime.fromisoformat(waiting["next_retry_at"])
        assert _advance(ledger, due)["status"] == "OK"
        assert _refresh(ledger.connection)["latest_revision"] == 2


def test_synthesis_source_failed_retry_preserves_failure_and_runs_at_deadline(
    tmp_path, monkeypatch,
) -> None:
    with closing(ForwardLedger(tmp_path / "retry.sqlite3", now=NOW)) as ledger:
        _seed_news_item(ledger, "known", minute=0)
        def failed(*args, **kwargs):
            raise ValueError("declared model output failure")
        monkeypatch.setattr(daily_brief, "generate_metered_json", failed)
        result = _advance(ledger)
        assert result["reason"] == "MODEL_OUTPUT_INVALID"
        before = _refresh(ledger.connection)
        changes = ledger.connection.total_changes
        with monkeypatch.context() as scoped:
            _no_payload(scoped)
            again = _advance(ledger, NOW + timedelta(seconds=59))
        assert again["reason"] == result["reason"]
        assert again["next_retry_at"] == result["next_retry_at"]
        assert _refresh(ledger.connection) == before
        assert ledger.connection.total_changes == changes
        _publish(ledger, monkeypatch, instant=datetime.fromisoformat(result["next_retry_at"]))
        assert _refresh(ledger.connection)["last_failure_code"] is None


def test_synthesis_source_future_visibility_uses_existing_gate_not_last_tail_clock(
    tmp_path, monkeypatch,
) -> None:
    with closing(ForwardLedger(tmp_path / "future.sqlite3", now=NOW)) as ledger:
        _seed_news_item(ledger, "future-annotation", minute=0,
                        parsed_at=NOW + timedelta(seconds=3))
        _seed_news_item(ledger, "backdated-tail", minute=1,
                        parsed_at=NOW - timedelta(seconds=1))
        _publish(ledger, monkeypatch)
        cached = json.loads(_refresh(ledger.connection)[CACHE_COLUMN])
        assert cached["next_visible_jd"] == ledger.connection.execute(
            "SELECT julianday(?)", ((NOW + timedelta(seconds=3)).isoformat(),),
        ).fetchone()[0]
        with monkeypatch.context() as scoped:
            _no_payload(scoped)
            assert _advance(ledger, NOW + timedelta(seconds=2))["reviewed_items"] == 1
        due_result = _advance(ledger, NOW + timedelta(seconds=3))
        assert due_result["reviewed_items"] == 2


def test_synthesis_source_job_invalidation_is_date_scoped_and_transactional(
    tmp_path, monkeypatch,
) -> None:
    with closing(ForwardLedger(tmp_path / "jobs.sqlite3", now=NOW)) as ledger:
        _seed_news_item(ledger, "known", minute=0)
        _seed_news_item(ledger, "unrelated", minute=0,
                        received_at=NOW - timedelta(days=2))
        _publish(ledger, monkeypatch)
        accepted = _refresh(ledger.connection)[CACHE_COLUMN]
        news_scheduler.enqueue_job(
            ledger.connection, task_type="ACTIVE_ANNOTATION", source="Reuters",
            source_item_id="unrelated", revision_number=1, annotation_id="",
            prompt_version=daily_brief.PROMPT_VERSION, priority="NORMAL", now=NOW,
        )
        assert _refresh(ledger.connection)[CACHE_COLUMN] == accepted
        job = news_scheduler.enqueue_job(
            ledger.connection, task_type="ACTIVE_ANNOTATION", source="Reuters",
            source_item_id="known", revision_number=1, annotation_id="",
            prompt_version=daily_brief.PROMPT_VERSION, priority="NORMAL", now=NOW,
        )
        assert _refresh(ledger.connection)[CACHE_COLUMN] == accepted
        ledger.connection.execute("SAVEPOINT caller")
        ledger.connection.execute("UPDATE news_ai_jobs_v1 SET state='DEAD_LETTER' WHERE job_id=?", (job,))
        assert _refresh(ledger.connection)[CACHE_COLUMN] is None
        ledger.connection.execute("ROLLBACK TO caller")
        ledger.connection.execute("RELEASE caller")
        assert _refresh(ledger.connection)[CACHE_COLUMN] == accepted


@pytest.mark.parametrize("unavailable", ["missing_index", "overflow"])
def test_synthesis_source_optional_initialization_never_accepts_partial_facts(
    tmp_path, monkeypatch, unavailable,
) -> None:
    with closing(ForwardLedger(tmp_path / "unavailable.sqlite3", now=NOW)) as ledger:
        _seed_news_item(ledger, "known", minute=0)
        if unavailable == "missing_index":
            ledger.connection.execute("DROP INDEX news_revisions_receipt_clock_v1")
        else:
            monkeypatch.setattr(daily_brief, "BRIEF_SOURCE_GATE_LIMIT", 1)
        _publish(ledger, monkeypatch)
        assert _refresh(ledger.connection)[CACHE_COLUMN] is None


@pytest.mark.parametrize("reason", ["malformed", "future_cache", "backward_clock", "budget", "prompt"])
def test_synthesis_source_applicability_changes_run_real_preparation(
    tmp_path, monkeypatch, reason,
) -> None:
    with closing(ForwardLedger(tmp_path / "identity.sqlite3", now=NOW)) as ledger:
        _seed_news_item(ledger, "known", minute=0)
        _publish(ledger, monkeypatch)
        instant = NOW + timedelta(seconds=2)
        if reason in {"malformed", "future_cache"}:
            cache = json.loads(_refresh(ledger.connection)[CACHE_COLUMN])
            cache["observed_at"] = (NOW + timedelta(days=1)).isoformat()
            value = "{" if reason == "malformed" else json.dumps(cache)
            with ledger.connection:
                ledger.connection.execute(
                    f"UPDATE daily_news_brief_refresh_state SET {CACHE_COLUMN}=?", (value,),
                )
        elif reason == "backward_clock":
            instant = NOW - timedelta(seconds=1)
        elif reason == "budget":
            monkeypatch.setattr(daily_brief, "BRIEF_INPUT_TOKEN_BUDGET",
                                daily_brief._brief_input_budget(None) - 1)
        elif reason == "prompt":
            monkeypatch.setattr(daily_brief, "BRIEF_PROMPT_VERSION", "fixture-changed-contract")
        calls = []
        actual = daily_brief._population_rows
        def observed(*args, **kwargs):
            calls.append(True)
            return actual(*args, **kwargs)
        monkeypatch.setattr(daily_brief, "_population_rows", observed)
        _advance(ledger, instant)
        assert calls == [True]


@pytest.mark.parametrize("holder", ["writer", "readonly", "query_only"])
def test_synthesis_source_optional_publication_yields_without_losing_accepted_result(
    tmp_path, monkeypatch, holder,
) -> None:
    path = tmp_path / "publication.sqlite3"
    with closing(ForwardLedger(path, now=NOW)) as ledger:
        _seed_news_item(ledger, "known", minute=0)
        _publish(ledger, monkeypatch)
        before = _refresh(ledger.connection)
        _seed_news_item(ledger, "other-day", minute=0,
                        received_at=NOW - timedelta(days=2))
        owner_connection = ledger.connection
        extra = None
        try:
            if holder == "writer":
                extra = sqlite3.connect(path)
                extra.execute("BEGIN IMMEDIATE")
            elif holder == "readonly":
                extra = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
                extra.row_factory = sqlite3.Row
                ledger.connection = extra
            else:
                ledger.connection.execute("PRAGMA query_only=ON")
            budget = ledger.connection.execute("PRAGMA busy_timeout").fetchone()[0]
            with monkeypatch.context() as scoped:
                _no_payload(scoped)
                assert _advance(ledger, NOW + timedelta(seconds=1))["status"] == "UNCHANGED"
            assert ledger.connection.execute("PRAGMA busy_timeout").fetchone()[0] == budget
            assert not ledger.connection.in_transaction
            assert _refresh(ledger.connection) == before
            if holder == "writer":
                assert extra.in_transaction
            if holder == "query_only":
                assert ledger.connection.execute("PRAGMA query_only").fetchone()[0] == 1
        finally:
            if extra is not None:
                extra.close()
            ledger.connection = owner_connection
            owner_connection.execute("PRAGMA query_only=OFF")
        with monkeypatch.context() as scoped:
            _no_payload(scoped)
            assert _advance(ledger, NOW + timedelta(seconds=2))["status"] == "UNCHANGED"
        assert _refresh(ledger.connection)["last_observed_at"] == before["last_observed_at"]
        assert _refresh(ledger.connection)[CACHE_COLUMN] != before[CACHE_COLUMN]


def test_synthesis_source_prepared_cache_cannot_erase_later_job_invalidation(
    tmp_path, monkeypatch,
) -> None:
    with closing(ForwardLedger(tmp_path / "race.sqlite3", now=NOW)) as ledger:
        _seed_news_item(ledger, "known", minute=0)
        _publish(ledger, monkeypatch)
        budget = daily_brief._brief_input_budget(None)
        prepared = daily_brief._synthesis_context(ledger.connection, "2026-08-10", NOW, budget)
        value = json.loads(prepared["cached"])
        job = news_scheduler.enqueue_job(
            ledger.connection, task_type="ACTIVE_ANNOTATION", source="Reuters",
            source_item_id="known", revision_number=1, annotation_id="",
            prompt_version=daily_brief.PROMPT_VERSION, priority="NORMAL", now=NOW,
        )
        with ledger.connection:
            ledger.connection.execute("UPDATE news_ai_jobs_v1 SET state='DEAD_LETTER' WHERE job_id=?", (job,))
        assert _refresh(ledger.connection)[CACHE_COLUMN] is None
        daily_brief._publish_synthesis_cache(
            ledger.connection, "2026-08-10", NOW, budget, prepared, value,
        )
        assert _refresh(ledger.connection)[CACHE_COLUMN] is None


@pytest.mark.parametrize("replacement", ["old_writer", "missing", "oversized"])
def test_synthesis_source_live_reader_rejects_replaced_job_hooks(
    tmp_path, monkeypatch, replacement,
) -> None:
    with closing(ForwardLedger(tmp_path / "mixed-writer.sqlite3", now=NOW)) as ledger:
        connection = ledger.connection
        _seed_news_item(ledger, "ready", minute=0)
        _seed_news_item(ledger, "pending", minute=1, parsed_at=NOW + timedelta(hours=1))
        _publish(ledger, monkeypatch)
        job = news_scheduler.enqueue_job(
            connection, task_type="ACTIVE_ANNOTATION", source="Reuters",
            source_item_id="pending", revision_number=1, annotation_id="",
            prompt_version=daily_brief.PROMPT_VERSION, priority="NORMAL", now=NOW,
        )
        budget = daily_brief._brief_input_budget(None)
        prepared = daily_brief._synthesis_context(connection, "2026-08-10", NOW, budget)
        accepted = prepared["cached"]
        # Install the real old-schema variant of the existing count owner's
        # hooks. It maintains job counts/revision, but has no new day-cache DML.
        with connection:
            for sql in critical_annotation_state._annotation_job_count_statements(
                has_runtime_metadata=True, has_brief_cache=False,
            ):
                if not sql.startswith("CREATE TRIGGER"):
                    continue
                name = sql.split()[5]
                if replacement != "old_writer" and name != "dashboard_job_count_update_v1":
                    continue
                connection.execute(f"DROP TRIGGER {name}")
                if replacement == "missing":
                    continue
                if replacement == "oversized":
                    sql = sql.removesuffix("END;") + "/*" + "x" * 8193 + "*/ END;"
                connection.execute(sql)
        assert not critical_annotation_state.news_job_source_hooks_are_current(connection)
        # Publication must recheck authority even before any job version changes.
        changes = connection.total_changes
        daily_brief._publish_synthesis_cache(
            connection, "2026-08-10", NOW, budget, prepared,
            {**json.loads(accepted), "observed_at": (NOW + timedelta(seconds=1)).isoformat()},
        )
        assert connection.total_changes == changes
        assert _refresh(connection)[CACHE_COLUMN] == accepted
        with connection:
            connection.execute("UPDATE news_ai_jobs_v1 SET state='DEAD_LETTER' WHERE job_id=?", (job,))
        assert _refresh(connection)[CACHE_COLUMN] == accepted
        calls = []
        actual = daily_brief._population_rows
        def observed(*args, **kwargs):
            calls.append(True)
            return actual(*args, **kwargs)
        monkeypatch.setattr(daily_brief, "_population_rows", observed)
        def deny_reader_ddl(action, *_):
            return (sqlite3.SQLITE_DENY if action in {
                sqlite3.SQLITE_CREATE_TRIGGER, sqlite3.SQLITE_DROP_TRIGGER,
                sqlite3.SQLITE_CREATE_TABLE, sqlite3.SQLITE_ALTER_TABLE,
            } else sqlite3.SQLITE_OK)
        connection.set_authorizer(deny_reader_ddl)
        try:
            result = _advance(ledger, NOW + timedelta(seconds=2))
        finally:
            connection.set_authorizer(None)
        assert calls == [True]
        assert (result["pending_items"], result["terminal_failure_items"]) == (0, 1)
        assert not critical_annotation_state.news_job_source_hooks_are_current(connection)
        # Only the real installer repairs hooks and invalidates old acceptance.
        critical_annotation_state.install_annotation_job_count_schema(connection)
        assert critical_annotation_state.news_job_source_hooks_are_current(connection)
        assert _refresh(connection)[CACHE_COLUMN] is None
        _advance(ledger, NOW + timedelta(seconds=3))
        assert calls == [True, True]
        with monkeypatch.context() as scoped:
            _no_payload(scoped)
            assert _advance(ledger, NOW + timedelta(seconds=4))["terminal_failure_items"] == 1


def test_synthesis_source_finalization_retires_only_disposable_cache(tmp_path, monkeypatch) -> None:
    with closing(ForwardLedger(tmp_path / "finalized.sqlite3", now=NOW)) as ledger:
        _seed_news_item(ledger, "known", minute=0)
        _publish(ledger, monkeypatch)
        assert _refresh(ledger.connection)[CACHE_COLUMN]
        evidence = _evidence(ledger.connection)
        result = daily_brief.update_daily_brief(
            ledger, api_key="fixture-only", request_accountant=CallbackModelAccountant(lambda _: True),
            now=NOW + timedelta(days=1), brief_date="2026-08-10",
        )
        assert result["phase"] == "FINAL"
        assert _refresh(ledger.connection)[CACHE_COLUMN] is None
        assert _evidence(ledger.connection) == evidence
        assert ledger.connection.execute(
            "SELECT final_status FROM daily_news_brief_finalizations_v1 WHERE brief_date='2026-08-10'",
        ).fetchone()[0] == "FINAL"
