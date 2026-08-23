from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timedelta, timezone

from xauusd_forecaster.news.annotation.product import (
    ANNOTATION_FAILURE_RECOVERY_VERSION,
    PROMPT_VERSION,
)
from xauusd_forecaster.dashboard import news_resources as module
from xauusd_forecaster.evidence.ledger import ForwardLedger
from xauusd_forecaster.news.scheduler.state import authorize_repairable_annotation_failures
from tests.dashboard_news_fixtures import (
    _append_basic_annotation,
    _basic_annotation_payload,
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

    rows = module._news_evidence_display_rows(connection, current)

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

    rows = module._news_evidence_display_rows(connection, current)

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

    rows = module._news_evidence_display_rows(connection, current)

    assert [row["event_key"] for row in rows] == ["new-unseen", "old-used"]


def test_news_evidence_display_reconciles_event_identity_handover() -> None:
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

    rows = module._news_evidence_display_rows(connection, current)

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

    first = module._news_archive_page(ledger.connection, None, 2)
    second = module._news_archive_page(ledger.connection, first["next_cursor"], 2)
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

    keys = module._news_mirror_candidate_keys(
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
    first = module._news_archive_page(ledger.connection, None, 20)
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

    changed = module._news_archive_page(
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
    before = module._news_archive_page(ledger.connection, None, 20)
    authorized_at = now + timedelta(seconds=1)

    recovered = authorize_repairable_annotation_failures(
        ledger.connection,
        prompt_version=PROMPT_VERSION,
        recovery_version=ANNOTATION_FAILURE_RECOVERY_VERSION,
        now=authorized_at,
    )
    changed = module._news_archive_page(
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

    item = module._news_archive_page(ledger.connection, None, 20)["items"][0]

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

    item = module._news_archive_page(ledger.connection, None, 20)["items"][0]

    assert item["annotation_status"] == "REPAIRING_DISPLAY"
    assert item["annotation_reason_code"] == "DISPLAY_REPAIR_IN_PROGRESS"
    assert item["model_visibility"] == "REPAIRING_DISPLAY"
    assert "修复中文显示" in item["annotation_reason"]
    ledger.close()


def test_duplicate_collection_copy_is_not_reported_as_queue_anomaly() -> None:
    now = datetime.now(UTC)
    code, reason = module._not_required_reason({
        "source": "google_news_gold_context",
        "headline": "CPI report",
        "source_published_time": now.isoformat(),
        "collector_first_seen_time": now.isoformat(),
        "has_canonical_content_peer": 1,
    }, (now - timedelta(days=30)).isoformat())

    assert code == "CANONICAL_COPY_HANDLES_ANNOTATION"
    assert "不会重复消耗模型配额" in reason


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

    archive = module._news_archive_page(ledger.connection, None, 20)

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




def test_dashboard_category_is_semantic_not_processing_state() -> None:
    assert module._news_category_label("central_bank_gold") == "央行购金"
    assert module._news_category_label("risk_sentiment") == "风险情绪 / 避险"
    assert module._news_category_label(None) == "其他"
    assert module._news_category_label("") == "其他"
    assert module._news_category_label("other-custom-topic") == "其他"


def test_news_evidence_pages_are_byte_bounded_and_complete_at_large_scale() -> None:
    rows = [{
        "event_key": f"{index:064x}",
        "collector_first_seen_time": f"2026-08-{(index % 28) + 1:02d}T00:00:00+00:00",
        "source_published_time": None,
        "broad_model_eligible": index % 2 == 0,
        "model_seen": index % 3 == 0,
        "canonical_headline": f"event {index}",
        "reason_codes": ["TEST_EVIDENCE"],
        "detail": "x" * 3_000,
    } for index in range(1_000)]
    module._publish_news_evidence_snapshot(rows)

    cursor = None
    received = []
    snapshot_id = None
    while True:
        page = module._news_evidence_page(cursor, 50)
        snapshot_id = snapshot_id or page["snapshot_id"]
        assert page["snapshot_id"] == snapshot_id
        encoded = json.dumps(
            page, ensure_ascii=False, separators=(",", ":"),
        ).encode("utf-8")
        assert len(encoded) <= module.NEWS_EVIDENCE_PAGE_LIMIT_BYTES
        received.extend(page["items"])
        if not page["has_more"]:
            break
        assert page["next_cursor"] != cursor
        cursor = page["next_cursor"]

    assert received == rows
    assert len(received) == 1_000
    assert len({row["event_key"] for row in received}) == len(rows)


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
    first_id, first_rows = module._materialize_news_evidence_generation(
        [base], manifest,
    )
    later_id, later_rows = module._materialize_news_evidence_generation([{
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

    age_only_id, age_only_rows = module._materialize_news_evidence_generation(
        [{**base, "economic_age_minutes": 240.0}],
        manifest,
        activated_snapshot_id=first_id,
    )
    assert age_only_id == first_id
    assert age_only_rows == first_rows

    expired_id, expired_rows = module._materialize_news_evidence_generation(
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
