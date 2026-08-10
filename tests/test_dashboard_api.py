from __future__ import annotations

import hashlib
import gzip
import importlib.util
import json
import sqlite3
import threading
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

from xauusd_forecaster.annotation import INVALID_CHINESE_TITLE, PROMPT_VERSION
from xauusd_forecaster.forward_ledger import ForwardLedger


UTC = timezone.utc


def _dashboard_module():
    path = Path(__file__).resolve().parents[1] / "scripts" / "run_dashboard_api.py"
    spec = importlib.util.spec_from_file_location("run_dashboard_api_test", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_deployment_status_does_not_mislabel_local_edits_as_remote_drift() -> None:
    module = _dashboard_module()

    assert module._deployment_status("same", "same", False) == "MATCHED"
    assert module._deployment_status("same", "same", True) == "LOCAL_CHANGES"
    assert module._deployment_status("local", "remote", False) == "DEPLOYMENT_DRIFT"
    assert module._deployment_status(None, "remote", False) == "PROVENANCE_UNKNOWN"


def test_news_evidence_display_collapses_frozen_versions_to_one_event() -> None:
    module = _dashboard_module()
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


def test_deployment_provenance_discovers_git_from_standalone_module_root(
    monkeypatch,
    tmp_path: Path,
) -> None:
    module = _dashboard_module()
    calls: list[Path] = []

    def fake_run(args, *, cwd, **_kwargs):
        calls.append(Path(cwd))
        command = tuple(args[1:])
        outputs = {
            ("rev-parse", "HEAD"): "abc123\n",
            ("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"): "origin/main\n",
            ("rev-parse", "origin/main"): "abc123\n",
            ("status", "--porcelain", "--", "."): "",
        }
        return type("Result", (), {"stdout": outputs[command]})()

    monkeypatch.setattr(module, "MODULE_ROOT", tmp_path)
    monkeypatch.setattr(module.subprocess, "run", fake_run)
    provenance = module._deployment_provenance(datetime.now(UTC), None)

    assert provenance["status"] == "MATCHED"
    assert provenance["runtime_git_sha"] == "abc123"
    assert calls and set(calls) == {tmp_path}


def test_detached_runtime_compares_against_origin_main(tmp_path, monkeypatch) -> None:
    module = _dashboard_module()

    def fake_run(args, *, cwd, **_kwargs):
        command = tuple(args[1:])
        outputs = {
            ("rev-parse", "HEAD"): "abc123\n",
            ("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"): "",
            ("rev-parse", "origin/main"): "abc123\n",
            ("status", "--porcelain", "--", "."): "",
        }
        return type("Result", (), {"stdout": outputs[command]})()

    monkeypatch.setattr(module, "MODULE_ROOT", tmp_path)
    monkeypatch.setattr(module.subprocess, "run", fake_run)

    provenance = module._deployment_provenance(datetime.now(UTC), None)

    assert provenance["expected_git_sha"] == "abc123"
    assert provenance["status"] == "MATCHED"


def _append_basic_annotation(
    ledger: ForwardLedger,
    *,
    source: str,
    item_id: str,
    digest: str,
    parsed_at: datetime,
    prompt_version: str = "news-json-v14-material-event-evidence",
    event_time: datetime | None = None,
) -> None:
    annotation = {
        "event_type": "economic_release",
        "entities": [],
        "hawkishness": 0.0,
        "inflation_impulse": 0.0,
        "growth_impulse": 0.0,
        "geopolitical_risk": 0.0,
        "usd_impulse": 0.0,
        "novelty": 0.5,
        "confidence": 0.8,
        "summary_zh": "已取得正文并完成测试解析。",
        "headline_zh": "测试经济数据发布",
    }
    if event_time:
        annotation.update({
            "primary_category": "growth_economy", "secondary_categories": [],
            "emerging_topic_zh": "", "record_kind": "FACT_EVENT",
            "actor": "US Treasury", "action": "published", "object": "official event",
            "location": "United States", "event_time": event_time.isoformat(),
            "claim_status": "CONFIRMED", "materiality": 0.8,
            "canonical_actor_id": "us_treasury", "action_family": "OFFICIAL_RELEASE",
            "canonical_object_id": "official_event", "canonical_location_id": "us",
            "episode_key": "official_event", "primary_story_title_zh": "测试事件",
            "secondary_contexts_zh": [], "relation_to_prior": "NONE",
        })
    ledger.append_annotation(
        {
            "annotation_id": f"annotation-{source}-{item_id}",
            "source": source,
            "source_item_id": item_id,
            "revision_number": 1,
            "raw_content_hash": digest,
            "annotation": annotation,
            "llm_model_version": "gemini-3.5-flash-lite",
            "prompt_version": prompt_version,
            "parse_started_at": parsed_at - timedelta(seconds=1),
            "parsed_at": parsed_at,
        }
    )


def test_dashboard_annotation_counts_match_current_worker_policy(tmp_path) -> None:
    now = datetime(2026, 8, 8, 1, 0, tzinfo=UTC)
    database = tmp_path / "forward.sqlite3"
    ledger = ForwardLedger(database, now=now)
    for item_id in ("completed", "pending"):
        body = (f"Official Treasury release {item_id}. " * 30).strip()
        digest = hashlib.sha256(body.encode()).hexdigest()
        ledger.append_news_revision(
            {
                "source": "us_treasury_press_releases",
                "source_item_id": item_id,
                "source_published_time": now,
                "collector_first_seen_time": now,
                "fetched_time": now,
                "headline": f"Treasury publishes {item_id} economic release",
                "body": body,
                "content_hash": digest,
                "cluster_id": item_id,
            }
        )
        if item_id == "completed":
            _append_basic_annotation(
                ledger,
                source="us_treasury_press_releases",
                item_id=item_id,
                digest=digest,
                parsed_at=now + timedelta(seconds=1),
                prompt_version=PROMPT_VERSION,
            )
    ledger.connection.close()

    payload = _dashboard_module()._dashboard_payload(database)

    assert payload["annotation_queue"]["ready"] == 1
    assert payload["annotation_queue"]["queued"] == 1
    transition = payload["learning_curves"]["news_contract_transition"]
    assert payload["news_evidence_summary"]["current_contract_exposed_rows"] == transition["current_contract_exposed_rows"]
    assert payload["news_evidence_summary"]["current_contract_distinct_events"] == transition["current_contract_distinct_events"]


def test_health_endpoint_does_not_build_dashboard(monkeypatch, tmp_path) -> None:
    module = _dashboard_module()
    module.Handler.database = tmp_path / "unused.sqlite3"
    monkeypatch.setattr(
        module,
        "_dashboard_payload",
        lambda _database: (_ for _ in ()).throw(AssertionError("must not build")),
    )
    server = module.ThreadingHTTPServer(("127.0.0.1", 0), module.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{server.server_port}/api/health", timeout=2
        ) as response:
            assert response.status == 200
            assert json.loads(response.read()) == {"status": "OK"}
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_dashboard_prefers_valid_title_over_later_placeholder(tmp_path) -> None:
    now = datetime.now(UTC).replace(microsecond=0)
    database = tmp_path / "forward.sqlite3"
    ledger = ForwardLedger(database, now=now)
    body = "full evidence body " * 30
    digest = hashlib.sha256(body.encode()).hexdigest()
    ledger.append_news_revision(
        {
            "source": "bea_economic_releases", "source_item_id": "release",
            "collector_first_seen_time": now, "fetched_time": now,
            "headline": "Personal Income and Outlays, June 2026", "body": body,
            "content_hash": digest, "cluster_id": "release",
        }
    )
    common = {
        "source": "bea_economic_releases", "source_item_id": "release",
        "revision_number": 1, "raw_content_hash": digest,
        "llm_model_version": "gemini-3.5-flash-lite",
        "parse_started_at": now,
    }
    ledger.append_title_translation(
        {
            **common, "translation_id": "valid", "headline_zh": "2026年6月个人收入与支出",
            "prompt_version": "headline-zh-v1", "parsed_at": now,
        }
    )
    ledger.append_title_translation(
        {
            **common, "translation_id": "placeholder",
            "headline_zh": INVALID_CHINESE_TITLE,
            "prompt_version": "headline-zh-placeholder-test",
            "parsed_at": now + timedelta(seconds=1),
        }
    )
    _append_basic_annotation(
        ledger,
        source="bea_economic_releases",
        item_id="release",
        digest=digest,
        parsed_at=now + timedelta(seconds=2),
    )
    ledger.connection.close()

    sync_success = now.isoformat()
    (tmp_path / "dashboard-sync-status.json").write_text(
        json.dumps({"last_success": sync_success, "last_error": None}),
        encoding="utf-8",
    )

    payload = _dashboard_module()._dashboard_payload(database)
    assert payload["recent_news"][0]["headline"] == "2026年6月个人收入与支出"
    assert len(payload["news_source_health"]) == 18
    synchronizer = payload["system"]["components"]["sites_synchronizer"]
    assert synchronizer["last_success"] == sync_success
    assert synchronizer["status"] == "OK"


def test_bls_direct_403_reports_healthy_official_domain_fallback(tmp_path) -> None:
    now = datetime.now(UTC).replace(microsecond=0)
    database = tmp_path / "forward.sqlite3"
    ledger = ForwardLedger(database, now=now)
    ledger.append_source_poll({
        "poll_id": "bls-direct-error", "source": "bls_employment_situation",
        "fetched_time": now, "status": "ERROR", "error_type": "HTTPError",
        "error": "HTTP Error 403: Forbidden",
    })
    ledger.append_source_poll({
        "poll_id": "bls-fallback-ok", "source": "google_news_bls_official_releases",
        "fetched_time": now, "status": "OK",
    })
    body = "official BLS employment situation body " * 12
    ledger.append_news_revision({
        "source": "google_news_bls_official_releases",
        "source_item_id": "employment-situation", "source_published_time": now,
        "collector_first_seen_time": now, "fetched_time": now,
        "headline": "Employment Situation Summary", "body": body,
        "link": "https://www.bls.gov/news.release/empsit.nr0.htm",
        "content_hash": hashlib.sha256(body.encode()).hexdigest(),
        "cluster_id": "employment-situation",
    })

    rows = _dashboard_module()._news_source_health(ledger.connection, now)
    direct = next(row for row in rows if row["source"] == "bls_employment_situation")
    assert direct["health"] == "FALLBACK_ACTIVE"
    assert direct["recovery_mode"] == "BLS_DIRECT_BLOCKED"
    assert direct["semantic_status"] == "OFFICIAL_DOMAIN_FALLBACK_ACTIVE"


def test_dashboard_orders_news_by_publisher_time_not_discovery_time(tmp_path) -> None:
    now = datetime(2026, 8, 6, 6, 0, tzinfo=UTC)
    database = tmp_path / "forward.sqlite3"
    ledger = ForwardLedger(database, now=now)

    for item_id, published_at, first_seen in (
        ("visible-first", now - timedelta(minutes=5), now - timedelta(minutes=20)),
        ("arrived-first", now - timedelta(days=2), now - timedelta(minutes=1)),
    ):
        body = f"full evidence for {item_id} " * 30
        digest = hashlib.sha256(body.encode()).hexdigest()
        ledger.append_news_revision(
            {
                "source": "bea_economic_releases",
                "source_item_id": item_id,
                "source_published_time": published_at,
                "collector_first_seen_time": first_seen,
                "fetched_time": first_seen,
                "headline": item_id,
                "body": body,
                "content_hash": digest,
                "cluster_id": item_id,
            }
        )
        _append_basic_annotation(
            ledger,
            source="bea_economic_releases",
            item_id=item_id,
            digest=digest,
            parsed_at=first_seen + timedelta(seconds=1),
        )
    ledger.connection.close()

    payload = _dashboard_module()._dashboard_payload(database)

    assert [row["source_item_id"] for row in payload["recent_news"][:2]] == [
        "visible-first",
        "arrived-first",
    ]


def test_dashboard_distinguishes_unavailable_content_from_pending(tmp_path) -> None:
    now = datetime(2026, 8, 7, 9, 0, tzinfo=UTC)
    database = tmp_path / "forward.sqlite3"
    ledger = ForwardLedger(database, now=now)
    digest = hashlib.sha256(b"headline-only").hexdigest()
    ledger.append_news_revision(
        {
            "source": "google_news_gold_context",
            "source_item_id": "blocked",
            "collector_first_seen_time": now,
            "fetched_time": now,
            "headline": "Publisher blocks automated article access",
            "body": "headline-only",
            "link": "https://publisher.example/blocked",
            "content_hash": digest,
            "cluster_id": "blocked",
        }
    )
    ledger.append_content_failure(
        {
            "failure_id": "blocked-403",
            "source": "google_news_gold_context",
            "source_item_id": "blocked",
            "revision_number": 1,
            "raw_content_hash": digest,
            "attempt_number": 1,
            "error_type": "HTTPError",
            "error_signature": hashlib.sha256(b"403").hexdigest(),
            "error": "HTTP Error 403: Forbidden",
            "failed_at": now,
            "is_terminal": True,
        }
    )
    ledger.connection.close()

    payload = _dashboard_module()._dashboard_payload(database)
    # A failed-body discovery remains in the immutable audit ledger, but the
    # reader surface must not present it as usable news.
    assert payload["recent_news"] == []
    assert payload["annotation_queue"]["waiting_content"] == 0
    assert payload["annotation_queue"]["unavailable_content"] == 1


def test_dashboard_shows_readable_unparsed_news_without_model_visibility(tmp_path) -> None:
    now = datetime(2026, 8, 8, 1, 0, tzinfo=UTC)
    database = tmp_path / "forward.sqlite3"
    ledger = ForwardLedger(database, now=now)
    body = "readable point in time evidence body " * 20
    ledger.append_news_revision(
        {
            "source": "us_treasury_press_releases",
            "source_item_id": "queued-readable",
            "source_published_time": now,
            "collector_first_seen_time": now,
            "fetched_time": now,
            "headline": "Readable official release awaiting annotation",
            "body": body,
            "content_hash": hashlib.sha256(body.encode()).hexdigest(),
            "cluster_id": "queued-readable",
        }
    )
    ledger.connection.close()

    payload = _dashboard_module()._dashboard_payload(database)

    assert len(payload["recent_news"]) == 1
    assert payload["recent_news"][0]["model_visibility"] == "NOT_YET_PARSED"
    assert payload["recent_news"][0]["annotation_status"] == "QUEUED"
    assert payload["counts"]["readable_news_items"] == 1
    assert payload["counts"]["parsed_news_items"] == 0
    assert payload["counts"]["model_candidate_news_items"] == 0


def test_dashboard_keeps_readable_late_news_for_semantic_impact_review(tmp_path) -> None:
    now = datetime(2026, 8, 8, 1, 0, tzinfo=UTC)
    database = tmp_path / "forward.sqlite3"
    ledger = ForwardLedger(database, now=now - timedelta(hours=3))
    body = "readable late official evidence body " * 20
    ledger.append_news_revision(
        {
            "source": "us_treasury_press_releases",
            "source_item_id": "late-readable",
            "source_published_time": now - timedelta(hours=2),
            "collector_first_seen_time": now,
            "fetched_time": now,
            "headline": "Readable official release discovered too late",
            "body": body,
            "content_hash": hashlib.sha256(body.encode()).hexdigest(),
            "cluster_id": "late-readable",
        }
    )
    ledger.connection.close()

    payload = _dashboard_module()._dashboard_payload(database)

    assert len(payload["recent_news"]) == 1
    row = payload["recent_news"][0]
    assert row["annotation_status"] == "QUEUED"
    assert row["impact_status"] == "PENDING_ANNOTATION"
    assert "annotation_reason_code" not in row
    assert payload["annotation_queue"]["queued"] == 1


def test_dashboard_explains_active_and_expired_on_receipt_impacts(tmp_path) -> None:
    now = datetime.now(UTC)
    database = tmp_path / "forward.sqlite3"
    ledger = ForwardLedger(database, now=now - timedelta(hours=4))
    for item_id, published_at, impact_class in (
        ("active", now - timedelta(hours=2), "SAME_DAY"),
        ("expired-on-receipt", now - timedelta(hours=3), "IMMEDIATE"),
    ):
        first_seen = now - timedelta(minutes=30)
        body = f"official impact evidence {item_id} " * 30
        digest = hashlib.sha256(body.encode()).hexdigest()
        ledger.append_news_revision({
            "source": "us_treasury_press_releases",
            "source_item_id": item_id,
            "source_published_time": published_at,
            "collector_first_seen_time": first_seen,
            "fetched_time": first_seen,
            "headline": item_id,
            "body": body,
            "content_hash": digest,
            "cluster_id": item_id,
        })
        parsed_at = first_seen + timedelta(seconds=1)
        _append_basic_annotation(
            ledger, source="us_treasury_press_releases", item_id=item_id,
            digest=digest, parsed_at=parsed_at,
        )
        ledger.append_news_impact_assessment({
            "assessment_id": f"impact-{item_id}",
            "source": "us_treasury_press_releases",
            "source_item_id": item_id,
            "revision_number": 1,
            "raw_content_hash": digest,
            "annotation_id": f"annotation-us_treasury_press_releases-{item_id}",
            "llm_model_version": "gemma-4-31b-it",
            "prompt_version": "news-impact-v2-semantic-prior-candidates",
            "parse_started_at": parsed_at,
            "assessed_at": parsed_at + timedelta(seconds=1),
            "impact_class": impact_class,
            "event_state": "ACTIVE",
            "update_type": "NEW_EVENT",
            "confidence": 0.9,
            "reason_zh": "测试有效期判断。",
        })
    ledger.connection.close()

    payload = _dashboard_module()._dashboard_payload(database)
    rows = {row["source_item_id"]: row for row in payload["recent_news"]}

    assert rows["active"]["impact_status"] == "ACTIVE"
    assert rows["active"]["model_visibility"] == "MODEL_VISIBLE"
    assert rows["expired-on-receipt"]["impact_status"] == "EXPIRED_ON_RECEIPT"
    assert rows["expired-on-receipt"]["model_visibility"] == "IMPACT_EXPIRED"


def test_dashboard_uses_same_explicit_event_clock_as_model(tmp_path) -> None:
    now = datetime.now(UTC)
    database = tmp_path / "forward.sqlite3"
    ledger = ForwardLedger(database, now=now - timedelta(days=2))
    published = now - timedelta(hours=2)
    event_time = now - timedelta(hours=13)
    first_seen = now - timedelta(hours=1)
    body = "official event happened before the publication timestamp " * 20
    digest = hashlib.sha256(body.encode()).hexdigest()
    ledger.append_news_revision({
        "source": "us_treasury_press_releases", "source_item_id": "old-event",
        "source_published_time": published, "collector_first_seen_time": first_seen,
        "fetched_time": first_seen, "headline": "Delayed official event report",
        "body": body, "content_hash": digest, "cluster_id": "old-event",
    })
    parsed_at = first_seen + timedelta(seconds=1)
    _append_basic_annotation(
        ledger, source="us_treasury_press_releases", item_id="old-event",
        digest=digest, parsed_at=parsed_at, event_time=event_time,
    )
    ledger.append_news_impact_assessment({
        "assessment_id": "old-event-impact", "source": "us_treasury_press_releases",
        "source_item_id": "old-event", "revision_number": 1,
        "raw_content_hash": digest,
        "annotation_id": "annotation-us_treasury_press_releases-old-event",
        "llm_model_version": "gemma-4-31b-it",
        "prompt_version": "news-impact-v2-semantic-prior-candidates",
        "parse_started_at": parsed_at, "assessed_at": parsed_at + timedelta(seconds=1),
        "impact_class": "SAME_DAY", "event_state": "ACTIVE",
        "update_type": "NEW_EVENT", "confidence": 0.9,
        "reason_zh": "事件发生时间早于文章发布时间。",
    })
    ledger.connection.close()

    row = _dashboard_module()._dashboard_payload(database)["recent_news"][0]

    assert row["impact_status"] == "EXPIRED_ON_RECEIPT"
    assert datetime.fromisoformat(row["impact_expires_at"]) == event_time + timedelta(hours=12)


def test_dashboard_uses_gemini_controlled_category_before_source_guess() -> None:
    module = _dashboard_module()
    assert module._news_category({
        "primary_category": "central_bank_gold",
        "source": "gdelt_gold_geopolitics",
        "headline": "Central bank increases gold reserves",
        "summary_zh": "央行增加黄金储备。",
        "event_type": "central_bank_purchase",
    }) == "央行购金"
    assert module._news_category({
        "primary_category": None,
        "source": "federal_reserve_press_all",
        "headline": "Application approval",
        "summary_zh": "监管审批。",
        "event_type": "regulatory_approval",
    }) == "监管/其他"


def test_dashboard_reports_gdelt_fallback_and_retry_time(tmp_path) -> None:
    now = datetime(2026, 8, 6, 4, 0, tzinfo=UTC)
    ledger = ForwardLedger(tmp_path / "forward.sqlite3", now=now)
    ledger.append_source_poll({
        "poll_id": "gdelt-429", "source": "gdelt_gold_geopolitics",
        "fetched_time": now - timedelta(minutes=30), "status": "ERROR",
        "error_type": "HTTPError", "error": "HTTP Error 429: Too Many Requests",
    })
    ledger.append_source_poll({
        "poll_id": "google-ok", "source": "google_news_gold_context",
        "fetched_time": now - timedelta(minutes=5), "status": "OK",
    })
    connection = ledger.connection
    rows = _dashboard_module()._news_source_health(connection, now)
    gdelt = next(row for row in rows if row["source"] == "gdelt_gold_geopolitics")
    assert gdelt["health"] == "FALLBACK_ACTIVE"
    assert gdelt["latest_status"] == "RATE_LIMITED"
    assert gdelt["fallback_label"] == "Google News Context"
    assert gdelt["fallback_health"] == "HEALTHY"
    assert gdelt["next_retry_time"] == (now + timedelta(minutes=90)).isoformat()


def test_market_chart_keeps_last_session_on_weekend_and_reads_gzip(tmp_path) -> None:
    module = _dashboard_module()
    now = datetime(2026, 8, 9, 4, 0, tzinfo=UTC)
    database = tmp_path / "forward.sqlite3"
    ledger = ForwardLedger(database, now=now)
    quote_dir = tmp_path / "quotes"
    quote_dir.mkdir()
    friday = datetime(2026, 8, 7, 20, 55, tzinfo=UTC)
    rows = [
        {"received_time": (friday + timedelta(minutes=index)).isoformat(), "bid": 3400 + index, "ask": 3400.2 + index}
        for index in range(2)
    ]
    with gzip.open(quote_dir / "xauusd-quotes-20260807.jsonl.gz", "wt", encoding="utf-8") as handle:
        handle.write("\n".join(json.dumps(row) for row in rows) + "\n")
    (quote_dir / "xauusd-quotes-20260807.jsonl").write_text("", encoding="utf-8")
    (quote_dir / "xauusd-quotes-20260809.jsonl").write_text("", encoding="utf-8")

    payload = module._recent_market_chart(database, ledger.connection, now)

    assert len(payload["candles"]) == 1
    assert payload["candles"][0]["time"] == "2026-08-07T20:55:00+00:00"
    assert payload["history_end"] == "2026-08-07T20:55:00+00:00"
    assert payload["source_candle_count"] == 1
    assert payload["overview_downsampled"] is False
    assert payload["prediction_history_start"] == {}


def test_market_chart_overview_preserves_ohlc_extremes() -> None:
    module = _dashboard_module()
    candles = [{
        "time": f"2026-08-07T00:{index:02d}:00+00:00",
        "open": float(index), "high": float(index + 1), "low": float(index - 1),
        "close": float(index + 0.5), "ticks": 2,
    } for index in range(6)]

    compact = module._downsample_candles(candles, 2)

    assert len(compact) == 2
    assert compact[0] == {
        "time": candles[0]["time"], "open": 0.0, "high": 3.0, "low": -1.0,
        "close": 2.5, "ticks": 6, "source_candles": 3,
    }
    assert compact[1]["open"] == 3.0
    assert compact[1]["close"] == 5.5


def test_market_history_pages_are_complete_and_cursor_safe(tmp_path) -> None:
    module = _dashboard_module()
    database = tmp_path / "forward.sqlite3"
    ledger = ForwardLedger(database, now=datetime(2026, 8, 7, tzinfo=UTC))
    quote_dir = tmp_path / "quotes"
    quote_dir.mkdir()
    start = datetime(2026, 8, 7, 0, 0, tzinfo=UTC)
    rows = [{
        "received_time": (start + timedelta(minutes=5 * index)).isoformat(),
        "bid": 3400 + index, "ask": 3400.2 + index,
    } for index in range(5)]
    (quote_dir / "xauusd-quotes-20260807.jsonl").write_text(
        "\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8",
    )

    first = module._market_history_page(database, ledger.connection, None, 2)
    second = module._market_history_page(
        database, ledger.connection, first["next_cursor"], 2,
    )
    third = module._market_history_page(
        database, ledger.connection, second["next_cursor"], 2,
    )

    times = [row["time"] for page in (first, second, third) for row in page["candles"]]
    assert len(times) == len(set(times)) == 5
    assert first["has_more"] is True
    assert second["has_more"] is True
    assert third["has_more"] is False
    assert third["next_cursor"] == times[-1]


def test_learning_surfaces_rebuild_only_when_source_counts_change(monkeypatch) -> None:
    module = _dashboard_module()
    connection = sqlite3.connect(":memory:")
    for table in module._LEARNING_REVISION_TABLES:
        connection.execute(f"CREATE TABLE {table} (id INTEGER)")
    calls = {"learning": 0, "execution": 0}

    def learning(_connection):
        calls["learning"] += 1
        return {"generation": calls["learning"]}

    def execution(_ledger):
        calls["execution"] += 1
        return {"generation": calls["execution"]}

    monkeypatch.setattr(module, "learning_curve_payload", learning)
    monkeypatch.setattr(module, "execution_learning_status", execution)

    first = module._learning_surfaces(connection)
    second = module._learning_surfaces(connection)
    assert first == second
    assert calls == {"learning": 1, "execution": 1}

    connection.execute("INSERT INTO derived_outcomes VALUES (1)")
    third = module._learning_surfaces(connection)
    assert third != second
    assert calls == {"learning": 2, "execution": 2}
    connection.close()
