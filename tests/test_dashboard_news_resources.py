from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from datetime import datetime, timedelta, timezone
import pytest
from xauusd_forecaster.dashboard import news_resources
from xauusd_forecaster.annotation import ANNOTATION_FAILURE_RECOVERY_VERSION, PROMPT_VERSION
from xauusd_forecaster.forward_ledger import ForwardLedger
from xauusd_forecaster.news_scheduler import authorize_repairable_annotation_failures
from tests.dashboard_news_fixtures import (
    _isolated_dashboard_credentials, _basic_annotation_payload, _append_basic_annotation,
)

UTC = timezone.utc

def test_news_evidence_display_collapses_frozen_versions_to_one_event() -> None:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.executescript(
        """
        CREATE TABLE news_model_visibility_receipts_v1 (
          source_decision_id TEXT, decision_time TEXT, model_identity TEXT,
          model_version TEXT, event_key TEXT, event_source_hash TEXT
        );
        CREATE TABLE news_model_visibility_events_v1 (
          event_source_hash TEXT, event_key TEXT, canonical_headline TEXT,
          canonical_source TEXT, source_published_time TEXT,
          collector_first_seen_time TEXT, topics_json TEXT,
          evidence_grade TEXT
        );
        """
    )
    for version in ("hash-v1", "hash-v2"):
        connection.execute(
            "INSERT INTO news_model_visibility_events_v1 VALUES (?,?,?,?,?,?,?,?)",
            (version, "same-event", "同一个事件", "source", "2026-08-10T01:00:00+00:00",
             "2026-08-10T01:01:00+00:00", "[]", "SINGLE_RELIABLE"),
        )
        connection.execute(
            "INSERT INTO news_model_visibility_receipts_v1 VALUES (?,?,?,?,?,?)",
            (f"decision-{version}", "2026-08-10T02:00:00+00:00", "FULL",
             f"model-{version}", "same-event", version),
        )
    current = [{
        "event_key": "same-event", "source_hash": "hash-v2",
        "canonical_headline": "同一个事件", "canonical_source": "source",
        "source_published_time": "2026-08-10T01:00:00+00:00",
        "collector_first_seen_time": "2026-08-10T01:01:00+00:00",
        "economic_age_minutes": 60, "freshness_status": "ACTIVE", "topics": [],
        "evidence_grade": "SINGLE_RELIABLE", "broad_model_eligible": True,
        "model_permission": "BROAD_MODEL", "member_count": 2,
        "independent_publishers": 1, "source_names": ["source"],
        "publisher_domains": ["example.com"], "source_identity_organizations": ["source"],
        "reason_codes": [], "prompt_version": "news-json-v14-material-event-evidence",
    }]

    rows = news_resources._news_evidence_display_rows(connection, current)

    assert len(rows) == 1
    assert rows[0]["event_key"] == "same-event"
    assert rows[0]["frozen_versions"] == 2
    assert rows[0]["frozen_decisions"] == 2


def test_news_evidence_display_includes_current_event_from_prior_prompt() -> None:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    current = [{
        "event_key": "prior-prompt-current", "source_hash": "hash-current",
        "canonical_headline": "仍然有效的事件", "canonical_source": "source",
        "source_published_time": "2026-08-10T01:00:00+00:00",
        "collector_first_seen_time": "2026-08-10T01:01:00+00:00",
        "economic_age_minutes": 60, "freshness_status": "ACTIVE", "topics": [],
        "evidence_grade": "SINGLE_RELIABLE", "broad_model_eligible": True,
        "model_permission": "BROAD_MODEL", "member_count": 1,
        "independent_publishers": 1, "source_names": ["source"],
        "publisher_domains": ["example.com"],
        "source_identity_organizations": ["source"], "reason_codes": [],
        "prompt_version": "prior-prompt-version",
    }]

    rows = news_resources._news_evidence_display_rows(connection, current)

    assert [row["event_key"] for row in rows] == ["prior-prompt-current"]
    assert rows[0]["model_unseen_reason_codes"] == [
        "ELIGIBLE_AWAITING_FROZEN_PREDICTION",
    ]


def test_news_evidence_display_orders_events_by_latest_publication_time() -> None:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.executescript(
        """
        CREATE TABLE news_model_visibility_receipts_v1 (
          source_decision_id TEXT, decision_time TEXT, model_identity TEXT,
          model_version TEXT, event_key TEXT, event_source_hash TEXT
        );
        CREATE TABLE news_model_visibility_events_v1 (
          event_source_hash TEXT, event_key TEXT, canonical_headline TEXT,
          canonical_source TEXT, source_published_time TEXT,
          collector_first_seen_time TEXT, topics_json TEXT,
          evidence_grade TEXT
        );
        """
    )
    connection.execute(
        "INSERT INTO news_model_visibility_events_v1 VALUES (?,?,?,?,?,?,?,?)",
        ("old-hash", "old-used", "较旧且用过", "source",
         "2026-08-10T01:00:00+00:00", "2026-08-10T01:01:00+00:00",
         "[]", "SINGLE_RELIABLE"),
    )
    connection.execute(
        "INSERT INTO news_model_visibility_receipts_v1 VALUES (?,?,?,?,?,?)",
        ("decision-old", "2026-08-10T02:00:00+00:00", "FULL", "model-v1",
         "old-used", "old-hash"),
    )
    current = [{
        "event_key": "new-unseen", "source_hash": "new-hash",
        "canonical_headline": "较新且未用", "canonical_source": "source",
        "source_published_time": "2026-08-11T01:00:00+00:00",
        "collector_first_seen_time": "2026-08-11T01:01:00+00:00",
        "economic_age_minutes": 60, "freshness_status": "ACTIVE", "topics": [],
        "evidence_grade": "SINGLE_RELIABLE", "broad_model_eligible": True,
        "model_permission": "BROAD_MODEL", "member_count": 1,
        "independent_publishers": 1, "source_names": ["source"],
        "publisher_domains": ["example.com"],
        "source_identity_organizations": ["source"], "reason_codes": [],
        "prompt_version": "prior-prompt-version",
    }]

    rows = news_resources._news_evidence_display_rows(connection, current)

    assert [row["event_key"] for row in rows] == ["new-unseen", "old-used"]


@pytest.mark.parametrize("include_auxiliary", [False, True])
def test_news_evidence_display_reconciles_event_identity_handover(include_auxiliary) -> None:

    class MeasuredConnection(sqlite3.Connection):
        reference = False
        visibility_steps = 0

        def execute(self, sql, parameters=(), /):
            if "SELECT canonical_event_key AS event_key" not in sql:
                return super().execute(sql, parameters)
            if self.reference:
                # Prior query is a semantic oracle, never a production fallback.
                sql = sql[sql.index("SELECT canonical_event_key AS event_key"):]
                sql = sql.replace("LEFT JOIN event_aliases AS alias", "LEFT JOIN json_each(?) AS alias")
            self.visibility_steps = 0

            def count_work():
                self.visibility_steps += 100
                return 0

            self.set_progress_handler(count_work, 100)
            return super().execute(sql, parameters)

    connection = sqlite3.connect(":memory:", factory=MeasuredConnection)
    connection.row_factory = sqlite3.Row
    connection.executescript(
        """
        CREATE TABLE news_model_visibility_receipts_v1 (
          source_decision_id TEXT, decision_time TEXT, model_identity TEXT,
          model_version TEXT, event_key TEXT, event_source_hash TEXT
        );
        CREATE TABLE news_model_visibility_events_v1 (
          event_source_hash TEXT, event_key TEXT, canonical_headline TEXT,
          canonical_source TEXT, source_published_time TEXT,
          collector_first_seen_time TEXT, topics_json TEXT,
          evidence_grade TEXT
        );
        """
    )
    article = (
        "同一篇新闻", "google_news_gold_context",
        "2026-08-10T01:00:00+00:00", "2026-08-10T01:01:00+00:00",
    )
    for event_key, source_hash in (("legacy-key", "hash-v1"), ("canonical-key", "hash-v2")):
        connection.execute(
            "INSERT INTO news_model_visibility_events_v1 VALUES (?,?,?,?,?,?,?,?)",
            (source_hash, event_key, article[0], article[1], article[2], article[3],
             "[]", "SINGLE_RELIABLE"),
        )
    connection.execute(
        "INSERT INTO news_model_visibility_events_v1 VALUES (?,?,?,?,?,?,?,?)",
        ("hash-other", "other-key", article[0], article[1], article[2],
         "2026-08-10T01:02:00+00:00", "[]", "SINGLE_RELIABLE"),
    )
    connection.execute(
        "INSERT INTO news_model_visibility_events_v1 VALUES (?,?,?,?,?,?,?,?)",
        ("hash-other-v2", "other-key-v2", article[0], article[1], article[2],
         "2026-08-10T01:02:00+00:00", "[]", "SINGLE_RELIABLE"),
    )
    for decision_id, event_key, source_hash in (
        ("decision-shared", "legacy-key", "hash-v1"),
        ("decision-shared", "canonical-key", "hash-v2"),
        ("decision-new", "canonical-key", "hash-v2"),
        ("decision-other", "other-key", "hash-other"),
        ("decision-other-v2", "other-key-v2", "hash-other-v2"),
    ):
        connection.execute(
            "INSERT INTO news_model_visibility_receipts_v1 VALUES (?,?,?,?,?,?)",
            (decision_id, "2026-08-10T02:00:00+00:00", "FULL", "model-v1",
             event_key, source_hash),
        )
    current = [{
        "event_key": "canonical-key", "source_hash": "hash-v2",
        "canonical_headline": article[0], "canonical_source": article[1],
        "source_published_time": article[2], "collector_first_seen_time": article[3],
        "economic_age_minutes": 60, "freshness_status": "ACTIVE", "topics": [],
        "evidence_grade": "SINGLE_RELIABLE", "broad_model_eligible": True,
        "model_permission": "BROAD_MODEL", "member_count": 1,
        "independent_publishers": 1, "source_names": [article[1]],
        "publisher_domains": ["fxstreet.com"],
        "source_identity_organizations": ["fxstreet"], "reason_codes": [],
        "prompt_version": "news-json-v14-material-event-evidence",
    }]

    rows = news_resources._news_evidence_display_rows(connection, current)

    canonical = next(row for row in rows if row["event_key"] == "canonical-key")
    assert len(rows) == 2
    assert canonical["frozen_model_uses"] == 3
    assert canonical["frozen_decisions"] == 2
    assert canonical["frozen_versions"] == 2
    assert canonical["publisher_domains"] == ["fxstreet.com"]
    assert canonical["source_identity_organizations"] == ["fxstreet"]
    other = next(row for row in rows if row["event_key"] == "other-key")
    assert other["frozen_model_uses"] == 2
    assert other["frozen_decisions"] == 2

    # Preserve handover aggregation (including shared decisions and both receipt
    # owners), while avoiding a scan of all aliases for every historical use.
    connection.set_progress_handler(None, 0)
    if include_auxiliary:
        connection.execute("CREATE TABLE news_only_visibility_receipts_v1 AS SELECT * FROM news_model_visibility_receipts_v1 WHERE 0")
    for alias in range(64):
        connection.execute("INSERT INTO news_model_visibility_events_v1 VALUES (?,?,?,?,?,?,?,?)",
                           (f"hash-{alias}", f"alias-{alias}", *article, "[]", "SINGLE_RELIABLE"))
        table = "news_only_visibility_receipts_v1" if include_auxiliary and alias % 2 else "news_model_visibility_receipts_v1"
        connection.executemany(f"INSERT INTO {table} VALUES (?,?,?,?,?,?)", [
            (f"shared-{decision}", "2026-08-10T02:00:00+00:00", "FULL", "model-v1",
             f"alias-{alias}", f"hash-{alias}") for decision in range(32)
        ])
    optimized = news_resources._news_evidence_display_rows(connection, current)
    optimized_steps = connection.visibility_steps
    connection.set_progress_handler(None, 0)
    connection.reference = True
    reference = news_resources._news_evidence_display_rows(connection, current)
    reference_steps = connection.visibility_steps
    connection.set_progress_handler(None, 0)
    assert optimized == reference
    assert 0 < optimized_steps < reference_steps / 3
    connection.close()


def test_news_archive_is_60_day_bounded_and_cursor_safe(tmp_path) -> None:
    now = datetime.now(UTC).replace(microsecond=0)
    database = tmp_path / "forward.sqlite3"
    ledger = ForwardLedger(database, now=now)
    for item_id, published_at in (
        ("current-a", now - timedelta(hours=2)),
        ("current-b", now - timedelta(hours=1)),
        ("current-c", now),
        ("expired", now - timedelta(days=61)),
    ):
        body = f"complete reader evidence for {item_id} " * 30
        ledger.append_news_revision({
            "source": "bea_economic_releases",
            "source_item_id": item_id,
            "source_published_time": published_at,
            "collector_first_seen_time": now,
            "fetched_time": now,
            "headline": item_id,
            "body": body,
            "content_hash": hashlib.sha256(body.encode()).hexdigest(),
            "cluster_id": item_id,
        })

    first = news_resources._news_archive_page(ledger.connection, None, 2)
    second = news_resources._news_archive_page(ledger.connection, first["next_cursor"], 2)
    rows = [*first["items"], *second["items"]]

    assert first["has_more"] is True
    assert second["has_more"] is False
    assert first["window_days"] == 60
    assert {row["source_item_id"] for row in rows} == {
        "current-a", "current-b", "current-c",
    }
    assert len({row["detail_key"] if "detail_key" in row else (
        row["source"], row["source_item_id"], row["revision_number"]
    ) for row in rows}) == 3


def test_news_projection_source_freezes_until_exact_snapshot_is_activated(tmp_path) -> None:
    news_resources._NEWS_PROJECTION_CACHE.clear()
    now = datetime.now(UTC).replace(microsecond=0)
    ledger = ForwardLedger(tmp_path / "forward.sqlite3", now=now)

    def append(item_id: str) -> None:
        body = f"complete projection evidence for {item_id} " * 30
        ledger.append_news_revision({
            "source": "bea_economic_releases", "source_item_id": item_id,
            "source_published_time": now, "collector_first_seen_time": now,
            "fetched_time": now, "headline": item_id, "body": body,
            "content_hash": hashlib.sha256(body.encode()).hexdigest(),
            "cluster_id": item_id,
        })

    append("first")
    frozen = news_resources._news_projection_source(ledger.connection, None)
    append("second")
    retry = news_resources._news_projection_source(ledger.connection, None)

    assert retry is frozen
    assert frozen.manifest["expected_index_count"] == 1
    assert sum(map(len, frozen.detail_batches)) == 1
    assert sum(map(len, frozen.index_batches)) == 1

    replacement = news_resources._news_projection_source(
        ledger.connection, frozen.manifest["snapshot_id"],
    )
    assert replacement.manifest["snapshot_id"] != frozen.manifest["snapshot_id"]
    assert replacement.manifest["expected_index_count"] == 2
    assert replacement.manifest["expected_detail_count"] == 2
    assert replacement.manifest["expected_receipt_digest"] != frozen.manifest[
        "expected_receipt_digest"
    ]


def test_news_projection_scans_candidate_universe_once_across_detail_pages(
    monkeypatch,
) -> None:
    frozen_context = ("epoch", set())
    candidate_keys = [
        ("example", f"item-{index}", 1, f"2026-08-24T00:{index:03d}:00+00:00")
        for index in range(1_001)
    ]
    context_calls = 0
    candidate_calls = 0
    detail_page_sizes: list[int] = []

    def context(_connection, _now):
        nonlocal context_calls
        context_calls += 1
        return frozen_context

    def candidates(_connection, *, cutoff, after, limit):
        nonlocal candidate_calls
        candidate_calls += 1
        assert after is None
        assert limit == news_resources.NEWS_PROJECTION_MAX_ITEMS + 1
        return candidate_keys

    def rows(_connection, _now, *, after=None, limit, candidate_keys=None):
        assert after is None
        assert candidate_keys is not None
        assert limit == len(candidate_keys)
        detail_page_sizes.append(limit)
        return [{
            "source": source, "source_item_id": item_id,
            "revision_number": revision, "cluster_id": item_id,
            "collector_first_seen_time": updated,
        } for source, item_id, revision, updated in candidate_keys]

    def serialize(rows, _now, epoch, claimable):
        assert epoch == frozen_context[0]
        assert claimable is frozen_context[1]
        return rows

    monkeypatch.setattr(news_resources, "_news_archive_context", context)
    monkeypatch.setattr(news_resources, "_news_mirror_candidate_keys", candidates)
    monkeypatch.setattr(news_resources, "_news_reader_rows", rows)
    monkeypatch.setattr(news_resources, "_serialize_news_rows", serialize)

    generation = news_resources._build_news_projection_source(object())

    assert context_calls == 1
    assert candidate_calls == 1
    assert detail_page_sizes == [1_000, 1]
    assert generation.manifest["expected_index_count"] == 1_001


def test_news_capture_uses_fixed_input_scoped_identity_and_real_cursor(tmp_path, monkeypatch):
    now = datetime(2026, 9, 7, tzinfo=UTC)

    class FrozenClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return now

    monkeypatch.setattr(news_resources, "datetime", FrozenClock)
    monkeypatch.setattr(news_resources, "NEWS_SOURCE_CAPTURE_PAGE_ITEMS", 2)
    database = tmp_path / "owned-snapshot.sqlite3"
    ledger = ForwardLedger(database, now=now - timedelta(days=1))
    for number in range(5):
        text = f"Exact frozen article {number}. " * 20
        ledger.append_news_revision({
            "source": "bea_economic_releases", "source_item_id": f"item-{number}",
            "source_published_time": now, "collector_first_seen_time": now,
            "fetched_time": now, "headline": f"item-{number}", "body": text,
            "content_hash": hashlib.sha256(text.encode()).hexdigest(),
            "cluster_id": f"item-{number}",
        })
    epoch = str(ledger.connection.execute(
        "SELECT value FROM runtime_metadata WHERE key='FORWARD_EPOCH'"
    ).fetchone()[0])
    original = news_resources._build_news_projection_source(ledger.connection)
    ledger.close()
    stat_before = news_resources._news_projection_snapshot_stat(database)
    capture = news_resources.NewsProjectionSourceCapture(
        tmp_path / "source.capture", binding={"snapshot_stat": stat_before},
        watermark=now.isoformat(), window_start=(now - timedelta(days=60)).isoformat(),
        epoch=epoch,
    )
    pending = news_resources.pending_annotation_records
    source_reader = news_resources._news_reader_rows
    seen_keys = []
    cursors = []

    def scoped(connection, **kwargs):
        assert kwargs["identity_only"] is True
        assert 0 < len(kwargs["source_keys"]) <= 2
        seen_keys.extend(kwargs["source_keys"])
        rows = pending(connection, **kwargs)
        assert all("body" not in row and "annotation_json" not in row for row in rows)
        return rows

    def streamed(*args, **kwargs):
        assert kwargs["stream"] is True
        rows = source_reader(*args, **kwargs)
        assert isinstance(rows, sqlite3.Cursor)
        cursors.append(rows)
        return rows

    monkeypatch.setattr(news_resources, "pending_annotation_records", scoped)
    monkeypatch.setattr(news_resources, "_news_reader_rows", streamed)
    for expected in (2, 4, 5):
        state = news_resources._advance_news_projection_capture(database, capture)
        assert state["source_count"] == expected
        assert state["last_source_step"]["vm_steps"] < 40_000_000
        assert state["last_source_step"]["sampled_rss_max_bytes"] < 512 * 1024 * 1024
        # A fresh process would reconstruct this same small artifact identity.
        capture = news_resources.NewsProjectionSourceCapture(
            capture.directory, binding=capture.identity["binding"],
            watermark=capture.identity["watermark"], window_start=capture.identity["window_start"],
            epoch=epoch,
        )
    assert state["state"] == "SOURCE_COMPLETE"
    assert len(set(seen_keys)) == len(seen_keys) == 5
    for cursor in cursors:
        with pytest.raises(sqlite3.ProgrammingError):
            cursor.fetchone()
    retained = sorted(capture.records(), key=lambda row: row["detail"]["detail_key"])
    assert [row["index"] for row in retained] == list(original.index_rows)
    assert [row["detail"] for row in retained] == list(original.detail_rows)
    assert news_resources._news_projection_snapshot_stat(database) == stat_before
    monkeypatch.setattr(news_resources.sqlite3, "connect", lambda *_args, **_kwargs: pytest.fail("completed input reopened"))
    assert news_resources._advance_news_projection_capture(database, capture) == state


def test_news_projection_request_starts_one_background_build(monkeypatch, tmp_path) -> None:
    news_resources._NEWS_PROJECTION_CACHE.clear()
    generation = __import__(
        "xauusd_forecaster.news_projection", fromlist=["build_news_projection_generation"],
    ).build_news_projection_generation(
        [], [], window_start="2026-06-25T00:00:00+00:00",
        watermark="2026-08-24T00:00:00+00:00",
    )
    pending_threads = []

    class DeferredThread:
        def __init__(self, *, target, args, name, daemon):
            assert name == "news-projection-source"
            assert daemon is True
            self.target = target
            self.args = args
            pending_threads.append(self)

        def start(self):
            return None

    monkeypatch.setattr(news_resources.threading, "Thread", DeferredThread)
    monkeypatch.setattr(
        news_resources, "_build_news_projection_source_from_database",
        lambda _database: generation,
    )

    with pytest.raises(news_resources.NewsProjectionSourcePending):
        news_resources._news_projection_source_for_request(tmp_path / "db.sqlite3", None)
    with pytest.raises(news_resources.NewsProjectionSourcePending):
        news_resources._news_projection_source_for_request(tmp_path / "db.sqlite3", None)
    assert len(pending_threads) == 1

    pending_threads[0].target(*pending_threads[0].args)

    assert news_resources._news_projection_source_for_request(
        tmp_path / "db.sqlite3", None,
    ) is generation

    news_resources._NEWS_PROJECTION_CACHE["built_at"] = (
        time.monotonic() - news_resources.NEWS_PROJECTION_SOURCE_REFRESH_SECONDS - 1
    )
    assert news_resources._news_projection_source_for_request(
        tmp_path / "db.sqlite3", generation.manifest["snapshot_id"],
    ) is generation
    assert len(pending_threads) == 2
    assert news_resources._news_projection_source_for_request(
        tmp_path / "db.sqlite3", generation.manifest["snapshot_id"],
    ) is generation
    assert len(pending_threads) == 2
    news_resources._NEWS_PROJECTION_CACHE.clear()


def test_news_projection_source_rejects_non_batch_offsets(tmp_path) -> None:
    news_resources._NEWS_PROJECTION_CACHE.clear()
    generation = __import__(
        "xauusd_forecaster.news_projection", fromlist=["build_news_projection_generation"],
    ).build_news_projection_generation(
        [{
            "source": "example", "source_item_id": str(index), "revision_number": 1,
            "category": "其他", "cluster_id": str(index),
            "collector_first_seen_time": "2026-08-24T00:00:00+00:00",
        } for index in range(10)], [],
        window_start="2026-06-25T00:00:00+00:00",
        watermark="2026-08-24T00:00:00+00:00",
    )

    first = news_resources._news_projection_batch(generation, "detail", 0)
    assert len(first["items"]) == 8
    with pytest.raises(ValueError, match="batch boundary"):
        news_resources._news_projection_batch(generation, "detail", 1)


def test_news_archive_discovers_a_bounded_changed_key_page(tmp_path) -> None:
    now = datetime.now(UTC).replace(microsecond=0)
    ledger = ForwardLedger(tmp_path / "forward.sqlite3", now=now)
    body = "complete bounded mirror evidence " * 20
    digest = hashlib.sha256(body.encode()).hexdigest()
    ledger.connection.executemany(
        """INSERT INTO news_revisions VALUES
           (?,?,1,NULL,?,?,?,?,?,NULL,?,?,NULL)""",
        [
            (
                "bea_economic_releases", f"item-{index:03d}",
                now.isoformat(), now.isoformat(), now.isoformat(),
                f"headline {index}", body, digest, f"cluster-{index}",
            )
            for index in range(250)
        ],
    )
    ledger.connection.commit()
    cursor = json.dumps([
        now.isoformat(), "bea_economic_releases", "item-099", 1,
    ])

    keys = news_resources._news_mirror_candidate_keys(
        ledger.connection,
        cutoff=(now - timedelta(days=60)).isoformat(),
        after=cursor,
        limit=20,
    )

    assert keys == [
        (
            "bea_economic_releases", f"item-{index:03d}", 1,
            now.isoformat(),
        )
        for index in range(100, 120)
    ]
    ledger.close()


def test_news_archive_reemits_legacy_invalid_annotation_for_recovery(tmp_path) -> None:
    now = datetime.now(UTC).replace(microsecond=0)
    ledger = ForwardLedger(tmp_path / "forward.sqlite3", now=now)
    body = "Complete source evidence awaiting semantic recovery. " * 20
    digest = hashlib.sha256(body.encode()).hexdigest()
    ledger.append_news_revision({
        "source": "gdelt_gold_geopolitics", "source_item_id": "recover-me",
        "source_published_time": now, "collector_first_seen_time": now,
        "fetched_time": now, "headline": "Current market report",
        "body": body, "content_hash": digest, "cluster_id": "recover-me",
    })
    first = news_resources._news_archive_page(ledger.connection, None, 20)
    invalid = json.dumps({
        "xauusd_relevance": "IRRELEVANT",
        "semantic_reason_zh": "语言或结构一致性检查未通过，禁止进入当前模型。",
    }, ensure_ascii=False)
    parsed_at = now + timedelta(seconds=1)
    ledger.connection.execute(
        """INSERT INTO news_annotations(
          annotation_id,source,source_item_id,revision_number,raw_content_hash,
          event_type,entities_json,hawkishness,inflation_impulse,growth_impulse,
          geopolitical_risk,usd_impulse,novelty,confidence,llm_model_version,
          prompt_version,parse_started_at,parsed_at,annotation_json)
          VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            "legacy-invalid", "gdelt_gold_geopolitics", "recover-me", 1, digest,
            "other", "[]", 0, 0, 0, 0, 0, 0, 0,
            "gemini-3.5-flash-lite", PROMPT_VERSION,
            parsed_at.isoformat(), parsed_at.isoformat(), invalid,
        ),
    )
    ledger.connection.commit()

    changed = news_resources._news_archive_page(
        ledger.connection, first["next_cursor"], 20,
    )

    assert [row["source_item_id"] for row in changed["items"]] == ["recover-me"]
    assert changed["items"][0]["annotation_status"] == "QUEUED"
    assert changed["items"][0]["mirror_updated_at"] == parsed_at.isoformat()
    assert changed["withdrawals"] == []
    ledger.close()


def test_news_archive_reemits_failure_when_recovery_is_authorized(tmp_path) -> None:
    now = datetime.now(UTC).replace(microsecond=0)
    ledger = ForwardLedger(tmp_path / "forward.sqlite3", now=now)
    body = "Complete source body with one exact evidence sentence. " * 20
    digest = hashlib.sha256(body.encode()).hexdigest()
    ledger.append_news_revision({
        "source": "google_news_fed_rates", "source_item_id": "recover-failure",
        "source_published_time": now, "collector_first_seen_time": now,
        "fetched_time": now, "headline": "Fed policy report", "body": body,
        "content_hash": digest, "cluster_id": "recover-failure",
    })
    cause = "annotation supporting evidence is absent from source"
    ledger.append_llm_failure({
        "failure_id": "recoverable-failure", "task_type": "ANNOTATION",
        "source": "google_news_fed_rates", "source_item_id": "recover-failure",
        "revision_number": 1, "raw_content_hash": digest,
        "llm_model_version": "gemini-3.5-flash-lite",
        "prompt_version": PROMPT_VERSION, "attempt_number": 2,
        "error_type": "ValueError",
        "error_signature": hashlib.sha256(cause.encode()).hexdigest(),
        "error": cause, "failed_at": now, "is_terminal": True,
        "failure_evidence": {
            "failure_code": "MODEL_OUTPUT_CONTRACT_FAILED",
            "failure_stage": "SEMANTIC_CONTRACT", "response_hash": "a" * 64,
            "selected_output": {"supporting_evidence": ["bounded excerpt"]},
            "cause_type": "ValueError", "cause": cause,
        },
    })
    before = news_resources._news_archive_page(ledger.connection, None, 20)
    authorized_at = now + timedelta(seconds=1)

    recovered = authorize_repairable_annotation_failures(
        ledger.connection,
        prompt_version=PROMPT_VERSION,
        recovery_version=ANNOTATION_FAILURE_RECOVERY_VERSION,
        now=authorized_at,
    )
    changed = news_resources._news_archive_page(
        ledger.connection, before["next_cursor"], 20,
    )

    assert recovered == 1
    assert [row["source_item_id"] for row in changed["items"]] == [
        "recover-failure",
    ]
    assert changed["items"][0]["annotation_status"] == "QUEUED"
    assert changed["items"][0]["model_visibility"] == "NOT_YET_PARSED"
    assert changed["items"][0]["mirror_updated_at"] == authorized_at.isoformat(
        timespec="microseconds",
    )
    ledger.close()


def test_news_archive_does_not_mark_nonclaimable_news_as_waiting(tmp_path) -> None:
    now = datetime.now(UTC).replace(microsecond=0)
    ledger = ForwardLedger(tmp_path / "forward.sqlite3", now=now)
    body = "Complete but stale source evidence. " * 30
    ledger.append_news_revision({
        "source": "google_news_fed_rates", "source_item_id": "stale-at-intake",
        "source_published_time": now - timedelta(days=4),
        "collector_first_seen_time": now, "fetched_time": now,
        "headline": "Old market report", "body": body,
        "content_hash": hashlib.sha256(body.encode()).hexdigest(),
        "cluster_id": "stale-at-intake",
    })

    item = news_resources._news_archive_page(ledger.connection, None, 20)["items"][0]

    assert item["annotation_status"] == "NOT_REQUIRED"
    assert item["model_visibility"] == "MODEL_INELIGIBLE"
    ledger.close()


def test_news_archive_exposes_display_checkpoint_as_active_repair(tmp_path) -> None:
    now = datetime.now(UTC).replace(microsecond=0)
    ledger = ForwardLedger(tmp_path / "forward.sqlite3", now=now)
    body = "Complete source body with one exact evidence sentence. " * 20
    digest = hashlib.sha256(body.encode()).hexdigest()
    source = "google_news_fed_rates"
    item_id = "repair-display"
    ledger.append_news_revision({
        "source": source, "source_item_id": item_id,
        "source_published_time": now, "collector_first_seen_time": now,
        "fetched_time": now, "headline": "Fed policy report", "body": body,
        "content_hash": digest, "cluster_id": item_id,
    })
    semantic_result = _basic_annotation_payload(
        ledger, source=source, item_id=item_id, parsed_at=now,
    )
    semantic_result["headline_zh"] = "Untranslated headline"
    semantic_result["semantic_reason_zh"] = "Untranslated semantic reason"
    ledger.append_annotation_display_checkpoint({
        "checkpoint_id": "display-checkpoint",
        "source": source, "source_item_id": item_id, "revision_number": 1,
        "raw_content_hash": digest,
        "llm_model_version": "gemini-3.5-flash-lite",
        "prompt_version": PROMPT_VERSION,
        "semantic_result": semantic_result,
        "invalid_fields": ["headline_zh", "semantic_reason_zh"],
        "rejection_reason": "headline_zh must be Chinese-primary",
        "captured_at": now,
    })

    item = news_resources._news_archive_page(ledger.connection, None, 20)["items"][0]

    assert item["annotation_status"] == "REPAIRING_DISPLAY"
    assert item["annotation_reason_code"] == "DISPLAY_REPAIR_IN_PROGRESS"
    assert item["model_visibility"] == "REPAIRING_DISPLAY"
    assert "修复中文显示" in item["annotation_reason"]
    ledger.close()


@pytest.mark.parametrize("peer_kind,expected", (
    ("self", False), ("other-source-item", True), ("same-source-hash", True),
    ("short", False), ("newer-short", False), ("newer-unmatched", False),
))
def test_news_reader_global_peer_lookup_preserves_out_of_page_latest_semantics(tmp_path, peer_kind, expected):
    now = datetime(2026, 9, 7, tzinfo=UTC)
    ledger = ForwardLedger(tmp_path / "forward.sqlite3", now=now - timedelta(days=90))
    source, item = "google_news_fed_rates", "candidate"
    body = "Complete candidate evidence for the reader. " * 20
    digest = hashlib.sha256(body.encode()).hexdigest()

    def append(owner, identifier, text, stamp):
        return ledger.append_news_revision({
            "source": owner, "source_item_id": identifier,
            "source_published_time": stamp, "collector_first_seen_time": stamp,
            "fetched_time": stamp, "headline": identifier, "body": text,
            "content_hash": hashlib.sha256(text.encode()).hexdigest(),
            "cluster_id": owner + ":" + identifier,
        })

    append(source, item, body, now)
    if peer_kind != "self":
        owner = source if peer_kind == "same-source-hash" else "google_news_gold_context"
        identifier = item if peer_kind in {"other-source-item", "short", "newer-short"} else "peer"
        text = ("short" if peer_kind == "short" else "Different complete source body. " * 20
                if peer_kind in {"other-source-item", "newer-short"} else body)
        append(owner, identifier, text, now - timedelta(days=70))
        if peer_kind in {"newer-short", "newer-unmatched"}:
            append(owner, identifier, "short" if peer_kind == "newer-short" else "Replacement different body. " * 20,
                   now - timedelta(days=69))
    # Unrelated global identities never become peers, while the real peer above
    # remains relevant despite being outside the page and its sixty-day window.
    for number in range(12):
        append("example", str(number), ("Unrelated evidence " + str(number)) * 30, now - timedelta(days=80))
    keys = [(source, item, 1, now.isoformat())]
    try:
        rows = news_resources._news_reader_rows(ledger.connection, now, candidate_keys=keys, limit=1)
        assert [(row["source"], row["source_item_id"], row["revision_number"]) for row in rows] == [(source, item, 1)]
        assert bool(rows[0]["has_canonical_content_peer"]) is expected
        assert rows[0]["body"] == body
        assert rows[0]["content_hash"] == digest
    finally:
        ledger.close()


def test_news_archive_materializes_late_discovery_canonical_annotation(
    tmp_path,
) -> None:
    epoch = datetime(2026, 8, 5, tzinfo=UTC)
    published_at = datetime(2026, 8, 15, 6, 13, 28, tzinfo=UTC)
    first_seen = datetime(2026, 8, 17, 4, 9, 1, tzinfo=UTC)
    ledger = ForwardLedger(tmp_path / "forward.sqlite3", now=epoch)
    item_id = "late-discovery-cpi"
    cluster_id = "late-discovery-cpi-cluster"
    bodies = {
        "google_news_fed_rates": "Complete CPI and US dollar analysis. " * 210,
        "google_news_gold_context": "Complete CPI and US dollar analysis. " * 210,
    }
    for source, body in bodies.items():
        ledger.append_news_revision({
            "source": source, "source_item_id": item_id,
            "source_published_time": published_at,
            "collector_first_seen_time": first_seen, "fetched_time": first_seen,
            "headline": "CPI in Focus: Can the Dollar Turn Lower Again?",
            "body": body,
            "content_hash": hashlib.sha256(body.encode()).hexdigest(),
            "cluster_id": cluster_id,
        })
    canonical_body = bodies["google_news_fed_rates"]
    _append_basic_annotation(
        ledger,
        source="google_news_fed_rates",
        item_id=item_id,
        digest=hashlib.sha256(canonical_body.encode()).hexdigest(),
        parsed_at=first_seen + timedelta(seconds=1),
    )

    archive = news_resources._news_archive_page(ledger.connection, None, 20)

    assert len(archive["items"]) == 1
    item = archive["items"][0]
    assert item["source"] == "google_news_fed_rates"
    assert item["source_published_time"] == published_at.isoformat(
        timespec="microseconds"
    )
    assert item["collector_first_seen_time"] == first_seen.isoformat(
        timespec="microseconds"
    )
    assert item["annotation_status"] == "READY"
    assert item["model_visibility"] == "IMPACT_PENDING"
    assert item["impact_status"] == "PENDING_IMPACT"
    assert item.get("annotation_reason_code") != "QUEUE_INVARIANT_MISMATCH"
    ledger.close()


def test_news_evidence_generation_freezes_until_activation_then_tracks_current_state(
    tmp_path,
) -> None:
    manifest = tmp_path / "news-evidence-generation.json"
    base = {
        "event_key": "a" * 64,
        "collector_first_seen_time": "2026-08-19T10:00:00+00:00",
        "source_published_time": "2026-08-19T09:00:00+00:00",
        "broad_model_eligible": True,
        "model_seen": False,
        "source_hash": "b" * 64,
        "economic_age_minutes": 60.0,
        "freshness_status": "FRESH",
        "model_permission": "BROAD_MODEL",
        "reason_codes": ["EVIDENCE_PRIMARY"],
    }
    first_id, first_rows = news_resources._materialize_news_evidence_generation(
        [base], manifest,
    )
    later_id, later_rows = news_resources._materialize_news_evidence_generation([{
        **base,
        "economic_age_minutes": 181.5,
        "freshness_status": "EVENT_LIFETIME_EXPIRED",
        "broad_model_eligible": False,
        "model_permission": "DISPLAY_ONLY",
        "reason_codes": ["EVIDENCE_PRIMARY", "EVENT_LIFETIME_EXPIRED"],
    }], manifest)

    assert later_id == first_id
    assert later_rows == first_rows
    assert "economic_age_minutes" not in later_rows[0]

    age_only_id, age_only_rows = news_resources._materialize_news_evidence_generation(
        [{**base, "economic_age_minutes": 240.0}],
        manifest,
        activated_snapshot_id=first_id,
    )
    assert age_only_id == first_id
    assert age_only_rows == first_rows

    expired_id, expired_rows = news_resources._materialize_news_evidence_generation(
        [{
            **base,
            "economic_age_minutes": 241.0,
            "freshness_status": "EVENT_LIFETIME_EXPIRED",
            "broad_model_eligible": False,
            "model_permission": "DISPLAY_ONLY",
            "reason_codes": ["EVIDENCE_PRIMARY", "EVENT_LIFETIME_EXPIRED"],
        }],
        manifest,
        activated_snapshot_id=first_id,
    )
    assert expired_id != first_id
    assert expired_rows[0]["broad_model_eligible"] is False
    assert expired_rows[0]["freshness_status"] == "EVENT_LIFETIME_EXPIRED"

