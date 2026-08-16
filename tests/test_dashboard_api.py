from __future__ import annotations

import concurrent.futures
import hashlib
import gzip
import importlib.util
import json
import sqlite3
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from xauusd_forecaster.annotation import (
    ANNOTATION_FAILURE_RECOVERY_VERSION,
    INVALID_CHINESE_TITLE,
    PROMPT_VERSION,
)
from xauusd_forecaster.ai_provider_registry import AI_QUOTA_SURFACES
from xauusd_forecaster.forward_ledger import ForwardLedger
from xauusd_forecaster.gemini_quota import GeminiQuotaLedger
from xauusd_forecaster.news_scheduler import (
    authorize_repairable_annotation_failures,
    configured_api_credentials,
    reserve_account_request,
)
from xauusd_forecaster.news_source_registry import NEWS_SOURCE_REGISTRY


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


def test_dashboard_reads_only_fresh_ctrader_market_session(tmp_path) -> None:
    module = _dashboard_module()
    now = datetime(2026, 8, 11, 21, 0, tzinfo=UTC)
    database = tmp_path / "forward-evidence.sqlite3"
    quotes = tmp_path / "quotes"
    quotes.mkdir()
    session_path = quotes / "market-session.json"
    session_path.write_text(json.dumps({
        "schema": "xauusd.forward.market-session.v1",
        "symbol": "XAUUSD",
        "observed_at": now.isoformat(),
        "is_open": False,
        "next_open_time": (now + timedelta(hours=1)).isoformat(),
        "next_close_time": None,
    }), encoding="utf-8")

    session = module._broker_market_session(database, now)

    assert session == {
        "is_open": False,
        "observed_at": now.isoformat(),
        "next_open_time": (now + timedelta(hours=1)).isoformat(),
        "next_close_time": None,
    }
    assert module._broker_market_session(database, now + timedelta(seconds=21)) is None


def test_dashboard_distinguishes_weekly_close_from_missing_open_market_data() -> None:
    module = _dashboard_module()
    saturday = datetime(2026, 8, 15, 12, 0, tzinfo=UTC)
    monday = datetime(2026, 8, 17, 12, 0, tzinfo=UTC)

    assert module._market_session_status(
        None, online=False, now=saturday,
    ) == "WEEKLY_CLOSED"
    assert module._market_session_status(
        None, online=False, now=monday,
    ) == "DATA_UNAVAILABLE"
    assert module._market_session_status(
        {"is_open": False}, online=False, now=monday,
    ) == "CLOSED"
    assert module._market_session_status(
        {"is_open": True}, online=True, now=monday,
    ) == "OPEN"
    assert module._market_session_status(
        {"is_open": True}, online=False, now=saturday,
    ) == "DATA_UNAVAILABLE"
    assert module._market_session_observed_at(
        None, market_session="WEEKLY_CLOSED", now=saturday,
    ) == saturday.isoformat()
    assert module._market_session_observed_at(
        None, market_session="DATA_UNAVAILABLE", now=monday,
    ) is None
    assert module._market_session_observed_at(
        {"observed_at": monday.isoformat()},
        market_session="OPEN",
        now=monday,
    ) == monday.isoformat()


def test_dashboard_reports_broker_close_and_reopen_time(tmp_path) -> None:
    now = datetime.now(UTC).replace(microsecond=0)
    database = tmp_path / "forward-evidence.sqlite3"
    ForwardLedger(database, now=now).close()
    quotes = tmp_path / "quotes"
    quotes.mkdir()
    reopens_at = now + timedelta(hours=1)
    (quotes / "market-session.json").write_text(json.dumps({
        "schema": "xauusd.forward.market-session.v1",
        "symbol": "XAUUSD",
        "observed_at": now.isoformat(),
        "is_open": False,
        "next_open_time": reopens_at.isoformat(),
        "next_close_time": None,
    }), encoding="utf-8")

    payload = _dashboard_module()._dashboard_payload(database)

    assert payload["system"]["market_session"] == "CLOSED"
    assert payload["system"]["market_reopens_at"] == reopens_at.isoformat()
    for component in ("quote_bridge", "decision_collector", "outcome_settler"):
        assert payload["system"]["components"][component]["status"] == "MARKET_CLOSED"


def test_outcome_settler_health_uses_successful_loop_heartbeat_not_output_age(
    tmp_path,
) -> None:
    now = datetime.now(UTC).replace(microsecond=0)
    database = tmp_path / "forward-evidence.sqlite3"
    ForwardLedger(database, now=now).close()
    (tmp_path / "collector-status.json").write_text(json.dumps({
        "service": "collector",
        "state": "RUNNING",
        "last_success": now.isoformat(),
        "last_error": None,
        "work_items": 0,
    }), encoding="utf-8")

    payload = _dashboard_module()._dashboard_payload(database)

    outcome = payload["system"]["components"]["outcome_settler"]
    assert outcome["status"] == "OK"
    assert outcome["last_success"] == now.isoformat()
    assert not any(
        alert["code"] == "OPS_COMPONENT_UNHEALTHY"
        and alert["scope"] == "outcome_settler"
        for alert in payload["operational_health"]["alerts"]
    )


def test_dashboard_exposes_only_runtime_update_failures(tmp_path) -> None:
    now = datetime.now(UTC).replace(microsecond=0)
    database = tmp_path / "forward-evidence.sqlite3"
    ForwardLedger(database, now=now).close()
    state_path = tmp_path / "runtime-update-state.json"
    state_path.write_text(json.dumps({
        "update_status": "ACTIVE", "user_visible_failure": False,
        "failure_message": None,
    }), encoding="utf-8")

    healthy = _dashboard_module()._dashboard_payload(database)
    assert healthy["system"]["runtime_update_failure"] is None

    state_path.write_text(json.dumps({
        "update_status": "ROLLED_BACK", "user_visible_failure": True,
        "failure_message": "新版运行验证失败，已自动恢复上一版。",
        "failed_at": now.isoformat(),
    }), encoding="utf-8")
    failed = _dashboard_module()._dashboard_payload(database)
    assert failed["system"]["runtime_update_failure"] == {
        "status": "ROLLED_BACK",
        "failed_at": now.isoformat(),
    }
    assert any(
        alert["code"] == "OPS_RUNTIME_UPDATE_FAILED"
        for alert in failed["operational_health"]["alerts"]
    )


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


def test_news_evidence_display_includes_current_event_from_prior_prompt() -> None:
    module = _dashboard_module()
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
    prompt_version: str = PROMPT_VERSION,
    event_time: datetime | None = None,
    xauusd_relevance: str = "MACRO_DRIVER",
) -> None:
    news = ledger.connection.execute(
        """SELECT headline,body,source_published_time FROM news_revisions
        WHERE source=? AND source_item_id=? AND revision_number=1""",
        (source, item_id),
    ).fetchone()
    evidence = " ".join(str(news["body"] or news["headline"]).split())[:120]
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
        "summary_zh": "已取得完整来源正文并完成结构化测试解析，相关证据已经保存。",
        "headline_zh": "测试经济数据发布",
        "primary_category": "growth_economy", "secondary_categories": [],
        "emerging_topic_zh": "", "record_kind": "FACT_EVENT",
        "actor": "US Treasury", "action": "published", "object": "official event",
        "location": "United States",
        "event_time": (
            event_time.isoformat() if event_time
            else str(news["source_published_time"] or parsed_at.isoformat())
        ),
        "claim_status": "CONFIRMED", "materiality": 0.8,
        "canonical_actor_id": "us_treasury", "action_family": "ECONOMIC_RELEASE",
        "canonical_object_id": item_id, "canonical_location_id": "us",
        "episode_key": item_id, "primary_story_title_zh": "测试事件",
        "secondary_contexts_zh": [], "relation_to_prior": "NONE",
        "document_kind": "REPORT", "material_event_key": item_id,
        "source_organization_id": source, "evidence_role": "CORE_CLAIM",
        "xauusd_relevance": xauusd_relevance, "review_priority": "FAST",
        "material_change": "NEW_EVENT", "time_sensitivity": "SAME_DAY",
        "semantic_reason_zh": "完整正文显示这是可能影响黄金的宏观事件。",
        "supporting_evidence": [evidence],
    }
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
    active_identities = {
        row["model_identity"]
        for row in payload["learning_curves"]["models"]
        if row["active_rank"] is not None
    }
    assert payload["counts"]["live_oos_model_groups"] == len(active_identities)
    transition = payload["learning_curves"]["news_contract_transition"]
    assert payload["news_evidence_summary"]["current_contract_exposed_rows"] == transition["current_contract_exposed_rows"]
    assert payload["news_evidence_summary"]["current_contract_distinct_events"] == transition["current_contract_distinct_events"]
    metrics = payload["news_metrics"]
    assert metrics["schema_version"] == "news-metrics-v1"
    assert metrics["articles"]["stored_revisions"] == payload["counts"]["news_revisions"]
    assert metrics["articles"]["semantic_reviews_complete"] == payload["counts"]["parsed_news_items"]
    assert metrics["training"]["current_contract_rows"] == transition["current_contract_exposed_rows"]
    assert metrics["training"]["distinct_events"] == transition["current_contract_distinct_events"]


def test_dashboard_quota_uses_scheduler_ledger(tmp_path, monkeypatch) -> None:
    import xauusd_forecaster.news_scheduler as news_scheduler

    configured = {"GEMINI_API_KEYS": "key-a;key-b", "GEMINI_API_KEY": ""}
    monkeypatch.setattr(
        news_scheduler, "_runtime_environment_value",
        lambda name: configured.get(name, ""),
    )
    now = datetime.now(UTC)
    database = tmp_path / "forward.sqlite3"
    ledger = ForwardLedger(database, now=now)
    monkeypatch.setenv("GEMINI_API_KEYS", "key-a;key-b")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    credentials = configured_api_credentials()
    for credential in credentials:
        assert reserve_account_request(
            ledger.connection, account_id=credential.account_id,
            model_family="gemini-3.5-flash-lite", daily_limit=500,
            requests_per_minute=12, now=now,
        )
    assert reserve_account_request(
        ledger.connection, account_id=credentials[0].account_id,
        model_family="gemma-impact", daily_limit=15_000,
        requests_per_minute=12, now=now,
    )
    assert reserve_account_request(
        ledger.connection, account_id=credentials[0].account_id,
        model_family="gemma-title", daily_limit=15_000,
        requests_per_minute=12, now=now,
    )
    ledger.close()

    payload = _dashboard_module()._dashboard_payload(database)

    assert payload["gemini_quota"]["accounting_source"] == "SCHEDULER_DB"
    assert payload["gemini_quota"]["total_sent"] == 2
    assert [row["sent"] for row in payload["gemini_quota"]["keys"]] == [1, 1]
    assert payload["gemma_quota"]["total_sent"] == 2
    assert [row["sent"] for row in payload["gemma_quota"]["keys"]] == [2, 0]
    scheduler_usage = payload["production_contract"]["scheduler_usage"]
    for surface in AI_QUOTA_SURFACES:
        assert scheduler_usage[surface.payload_key] == payload[
            surface.payload_key
        ]["total_sent"]


def test_dashboard_quota_keeps_pre_scheduler_file_compatibility(
    tmp_path, monkeypatch,
) -> None:
    import xauusd_forecaster.news_scheduler as news_scheduler

    configured = {"GEMINI_API_KEYS": "legacy-key", "GEMINI_API_KEY": ""}
    monkeypatch.setattr(
        news_scheduler, "_runtime_environment_value",
        lambda name: configured.get(name, ""),
    )
    now = datetime.now(UTC)
    database = tmp_path / "forward.sqlite3"
    ledger = ForwardLedger(database, now=now)
    ledger.connection.execute("DROP TABLE news_ai_account_daily_usage_v1")
    ledger.connection.commit()
    ledger.close()
    monkeypatch.setenv("GEMINI_API_KEYS", "legacy-key")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    GeminiQuotaLedger(tmp_path / "gemini-quota.json").seed(
        "legacy-key", 7, now=now,
    )

    payload = _dashboard_module()._dashboard_payload(database)

    assert payload["gemini_quota"]["total_sent"] == 7
    assert payload["gemini_quota"]["keys"][0]["sent"] == 7
    assert "accounting_source" not in payload["gemini_quota"]


def test_status_snapshot_cache_singleflights_concurrent_builds(tmp_path) -> None:
    module = _dashboard_module()
    cache = module.StatusSnapshotCache(wait_seconds=1.0)
    database = tmp_path / "forward.sqlite3"
    started = threading.Event()
    release = threading.Event()
    calls = 0
    call_lock = threading.Lock()

    def builder(_database):
        nonlocal calls
        with call_lock:
            calls += 1
        started.set()
        assert release.wait(timeout=2)
        return {"generated_at": "2026-08-12T00:00:00+00:00"}

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(cache.get, database, builder) for _ in range(8)]
        assert started.wait(timeout=1)
        time.sleep(0.05)
        assert calls == 1
        release.set()
        results = [future.result(timeout=2) for future in futures]

    assert calls == 1
    assert len({body for body, _state, _age in results}) == 1
    assert {state for _body, state, _age in results} == {"fresh"}


def test_status_snapshot_cache_fails_closed_during_slow_refresh(tmp_path) -> None:
    module = _dashboard_module()
    now = [0.0]
    cache = module.StatusSnapshotCache(
        ttl_seconds=15, wait_seconds=0.01, max_stale_seconds=90,
        clock=lambda: now[0],
    )
    database = tmp_path / "forward.sqlite3"
    cache.get(database, lambda _database: {"version": 1})

    now[0] = 16.0
    started = threading.Event()
    release = threading.Event()

    def slow_refresh(_database):
        started.set()
        assert release.wait(timeout=2)
        return {"version": 2}

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        refresh = pool.submit(cache.get, database, slow_refresh)
        assert started.wait(timeout=1)
        try:
            cache.get(database, slow_refresh)
        except module.StatusSnapshotUnavailable as error:
            assert str(error) == "dashboard snapshot refresh is still running"
        else:
            raise AssertionError("a stale snapshot must not look like a sync success")
        release.set()
        refreshed_body, refreshed_state, _age = refresh.result(timeout=2)

    assert json.loads(refreshed_body) == {"version": 2}
    assert refreshed_state == "fresh"

    now[0] = 32.0
    try:
        cache.get(
            database, lambda _database: (_ for _ in ()).throw(RuntimeError("boom")),
        )
    except RuntimeError as error:
        assert str(error) == "boom"
    else:
        raise AssertionError("a failed refresh must not masquerade as success")
    health_status, health = cache.health()
    assert health_status == 503
    assert health["status"] == "ERROR"

    now[0] = 123.0
    try:
        cache.get(
            database,
            lambda _database: (_ for _ in ()).throw(RuntimeError("still broken")),
        )
    except RuntimeError as error:
        assert str(error) == "still broken"
    else:
        raise AssertionError("expired dashboard snapshot must fail closed")


def test_health_endpoint_requires_recent_dashboard_snapshot(monkeypatch, tmp_path) -> None:
    module = _dashboard_module()
    module.Handler.database = tmp_path / "unused.sqlite3"
    module.Handler.status_cache = module.StatusSnapshotCache()
    monkeypatch.setattr(
        module,
        "_dashboard_payload",
        lambda _database: (_ for _ in ()).throw(AssertionError("must not build")),
    )
    server = module.ThreadingHTTPServer(("127.0.0.1", 0), module.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        try:
            urllib.request.urlopen(
                f"http://127.0.0.1:{server.server_port}/api/health", timeout=2
            )
        except urllib.error.HTTPError as error:
            assert error.code == 503
            assert json.loads(error.read())["status"] == "UNAVAILABLE"
        else:
            raise AssertionError("health must fail before a status snapshot exists")

        module.Handler.status_cache.get(
            module.Handler.database, lambda _database: {"status": "ready"},
        )
        with urllib.request.urlopen(
            f"http://127.0.0.1:{server.server_port}/api/health", timeout=2
        ) as response:
            assert response.status == 200
            assert json.loads(response.read())["status"] == "OK"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_dashboard_status_does_not_scan_live_database_integrity(
    monkeypatch, tmp_path,
) -> None:
    module = _dashboard_module()
    now = datetime.now(UTC).replace(microsecond=0)
    database = tmp_path / "forward.sqlite3"
    ForwardLedger(database, now=now).close()
    real_connect = module.sqlite3.connect
    statements: list[str] = []

    def tracked_connect(target, *args, **kwargs):
        connection = real_connect(target, *args, **kwargs)
        if str(database) in str(target):
            connection.set_trace_callback(statements.append)
        return connection

    monkeypatch.setattr(module.sqlite3, "connect", tracked_connect)
    module._dashboard_payload(database)

    assert not any("integrity_check" in statement.lower() for statement in statements)


def test_live_quote_candle_cache_reads_only_appended_bytes(tmp_path) -> None:
    module = _dashboard_module()
    quote_file = tmp_path / "xauusd-quotes-20260812.jsonl"

    def quote(second: int, bid: float) -> str:
        return json.dumps({
            "received_time": f"2026-08-12T06:30:{second:02d}+00:00",
            "bid": bid,
            "ask": bid + 0.1,
        }) + "\n"

    quote_file.write_text(quote(1, 4300.0) + quote(2, 4301.0), encoding="utf-8")
    first = module._quote_file_candles(quote_file)
    first_offset = module._QUOTE_CANDLE_CACHE[str(quote_file)]["offset"]

    with quote_file.open("a", encoding="utf-8") as handle:
        handle.write(quote(3, 4302.0))
    second = module._quote_file_candles(quote_file)

    assert first[0]["ticks"] == 2
    assert second[0]["ticks"] == 3
    assert second[0]["open"] == 4300.05
    assert second[0]["close"] == 4302.05
    assert module._QUOTE_CANDLE_CACHE[str(quote_file)]["offset"] > first_offset


def test_forward_ledger_adds_dashboard_news_lookup_indexes(tmp_path) -> None:
    now = datetime.now(UTC).replace(microsecond=0)
    ledger = ForwardLedger(tmp_path / "forward.sqlite3", now=now)
    try:
        annotation_indexes = {
            row[1] for row in ledger.connection.execute(
                "PRAGMA index_list(news_annotations)"
            )
        }
        revision_indexes = {
            row[1] for row in ledger.connection.execute(
                "PRAGMA index_list(news_revisions)"
            )
        }
    finally:
        ledger.close()

    assert "news_annotations_revision_contract" in annotation_indexes
    assert "news_revisions_cluster_latest" in revision_indexes


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
    assert {row["source"] for row in payload["news_source_health"]} == {
        spec.source for spec in NEWS_SOURCE_REGISTRY
    }
    assert all(
        row["source"] not in {
            "non_fed_full_text",
            "bls_employment_situation",
            "bls_consumer_price_index",
            "bls_job_openings",
            "google_news_bls_official_releases",
        }
        for row in payload["news_source_health"]
    )
    synchronizer = payload["system"]["components"]["sites_synchronizer"]
    assert synchronizer["last_success"] == sync_success
    assert synchronizer["status"] == "OK"


def test_source_error_does_not_claim_polling_is_normal(tmp_path) -> None:
    now = datetime.now(UTC).replace(microsecond=0)
    ledger = ForwardLedger(tmp_path / "forward.sqlite3", now=now)
    ledger.append_source_poll({
        "poll_id": "eia-error", "source": "eia_press_releases",
        "fetched_time": now, "status": "ERROR", "error_type": "HTTPError",
        "error": "HTTP Error 403: Forbidden",
    })

    rows = _dashboard_module()._news_source_health(ledger.connection, now)
    direct = next(row for row in rows if row["source"] == "eia_press_releases")

    assert direct["health"] == "ERROR"
    assert direct["semantic_status"] == "SOURCE_ERROR"
    assert direct["semantic_message"] == "来源当前轮询失败；请查看最近错误与后备链路状态"


@pytest.mark.parametrize("source", [spec.source for spec in NEWS_SOURCE_REGISTRY])
def test_every_monitored_source_clears_active_failure_state_after_success(
    tmp_path,
    source,
) -> None:
    now = datetime.now(UTC).replace(microsecond=0)
    ledger = ForwardLedger(tmp_path / "forward.sqlite3", now=now)
    ledger.append_source_poll({
        "poll_id": f"{source}-old-error", "source": source,
        "fetched_time": now - timedelta(minutes=5), "status": "ERROR",
        "error_type": "HTTPError", "error": "HTTP Error 429: historical",
    })
    ledger.append_source_poll({
        "poll_id": f"{source}-recovered", "source": source,
        "fetched_time": now, "status": "OK",
    })

    rows = _dashboard_module()._news_source_health(ledger.connection, now)
    recovered = next(row for row in rows if row["source"] == source)

    assert recovered["latest_status"] == "OK"
    assert recovered["health"] not in {"ERROR", "DEGRADED", "FALLBACK_ACTIVE"}
    assert recovered["recovery_mode"] is None
    assert recovered["next_retry_time"] is None
    assert "429" in recovered["last_error"]


def test_polled_release_source_without_items_is_not_reported_healthy(tmp_path) -> None:
    now = datetime.now(UTC).replace(microsecond=0)
    ledger = ForwardLedger(tmp_path / "forward.sqlite3", now=now)
    ledger.append_source_poll({
        "poll_id": "bea-empty-ok", "source": "bea_economic_releases",
        "fetched_time": now, "status": "OK",
    })

    rows = _dashboard_module()._news_source_health(ledger.connection, now)
    bea = next(row for row in rows if row["source"] == "bea_economic_releases")
    assert bea["health"] == "WARMING_UP"
    assert bea["semantic_status"] == "NO_RELEASE_CAPTURED"
    assert bea["item_count"] == 0


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


def test_news_archive_is_60_day_bounded_and_cursor_safe(tmp_path) -> None:
    module = _dashboard_module()
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


def test_news_reader_materializations_exclude_semantically_irrelevant_articles(
    tmp_path,
) -> None:
    module = _dashboard_module()
    now = datetime.now(UTC).replace(microsecond=0)
    database = tmp_path / "forward.sqlite3"
    ledger = ForwardLedger(database, now=now)
    for item_id, relevance in (
        ("relevant-market-report", "MACRO_DRIVER"),
        ("irrelevant-entertainment-report", "IRRELEVANT"),
        ("pending-semantic-review", None),
    ):
        body = f"complete evidence for {item_id} " * 30
        digest = hashlib.sha256(body.encode()).hexdigest()
        ledger.append_news_revision({
            "source": "gdelt_gold_geopolitics",
            "source_item_id": item_id,
            "source_published_time": now,
            "collector_first_seen_time": now,
            "fetched_time": now,
            "headline": item_id,
            "body": body,
            "content_hash": digest,
            "cluster_id": item_id,
        })
        if relevance is not None:
            _append_basic_annotation(
                ledger,
                source="gdelt_gold_geopolitics",
                item_id=item_id,
                digest=digest,
                parsed_at=now + timedelta(seconds=1),
                xauusd_relevance=relevance,
            )
    archive = module._news_archive_page(ledger.connection, None, 20)
    ledger.close()
    dashboard = module._dashboard_payload(database)

    expected = {"relevant-market-report", "pending-semantic-review"}
    assert {row["source_item_id"] for row in archive["items"]} == expected
    assert archive["withdrawals"] == [{
        "source": "gdelt_gold_geopolitics",
        "source_item_id": "irrelevant-entertainment-report",
        "revision_number": 1,
    }]
    assert {row["source_item_id"] for row in dashboard["recent_news"]} == expected


def test_news_archive_reemits_legacy_invalid_annotation_for_recovery(tmp_path) -> None:
    module = _dashboard_module()
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
    module = _dashboard_module()
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
    module = _dashboard_module()
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


def test_news_archive_explains_terminal_model_contract_failure(tmp_path) -> None:
    module = _dashboard_module()
    now = datetime.now(UTC).replace(microsecond=0)
    ledger = ForwardLedger(tmp_path / "forward.sqlite3", now=now)
    body = "Complete source body with one exact evidence sentence. " * 20
    digest = hashlib.sha256(body.encode()).hexdigest()
    ledger.append_news_revision({
        "source": "google_news_fed_rates", "source_item_id": "contract-failure",
        "source_published_time": now, "collector_first_seen_time": now,
        "fetched_time": now, "headline": "Fed policy report", "body": body,
        "content_hash": digest, "cluster_id": "contract-failure",
    })
    cause = "annotation supporting evidence is absent from source"
    ledger.append_llm_failure({
        "failure_id": "terminal-contract-failure", "task_type": "ANNOTATION",
        "source": "google_news_fed_rates", "source_item_id": "contract-failure",
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
    ledger.append_news_revision({
        "source": "google_news_fed_rates", "source_item_id": "provider-backoff",
        "source_published_time": now - timedelta(minutes=1),
        "collector_first_seen_time": now, "fetched_time": now,
        "headline": "Fed service retry", "body": body,
        "content_hash": digest, "cluster_id": "provider-backoff",
    })
    provider_cause = "HTTP Error 429: quota temporarily unavailable"
    ledger.append_llm_failure({
        "failure_id": "provider-backoff", "task_type": "ANNOTATION",
        "source": "google_news_fed_rates", "source_item_id": "provider-backoff",
        "revision_number": 1, "raw_content_hash": digest,
        "llm_model_version": "gemini-3.5-flash-lite",
        "prompt_version": PROMPT_VERSION, "attempt_number": 1,
        "error_type": "HTTPError",
        "error_signature": hashlib.sha256(provider_cause.encode()).hexdigest(),
        "error": provider_cause, "failed_at": now,
        "next_retry_at": now + timedelta(minutes=10), "is_terminal": False,
        "failure_evidence": {
            "failure_code": "PROVIDER_HTTP_ERROR",
            "failure_stage": "PROVIDER_REQUEST", "response_hash": "b" * 64,
            "selected_output": {}, "cause_type": "HTTPError",
            "cause": provider_cause,
        },
    })

    archive = module._news_archive_page(ledger.connection, None, 20)
    ledger.close()
    dashboard = module._dashboard_payload(tmp_path / "forward.sqlite3")
    archive_by_id = {row["source_item_id"]: row for row in archive["items"]}
    dashboard_by_id = {
        row["source_item_id"]: row for row in dashboard["recent_news"]
    }

    terminal = archive_by_id["contract-failure"]
    assert terminal["annotation_status"] == "DEAD_LETTER"
    assert terminal["annotation_reason_code"] == (
        "MODEL_OUTPUT_CONTRACT_FAILED"
    )
    assert terminal["annotation_reason"] == (
        "Gemini 返回的证据片段无法在来源正文中逐字找到。"
    )
    assert archive_by_id["provider-backoff"]["annotation_status"] == "BACKING_OFF"
    assert archive_by_id["provider-backoff"]["annotation_reason_code"] == (
        "PROVIDER_HTTP_ERROR"
    )
    assert archive_by_id["provider-backoff"]["annotation_reason"] == (
        "Gemini 服务返回 HTTP 错误。"
    )
    assert dashboard_by_id["contract-failure"]["annotation_reason_code"] == (
        "MODEL_OUTPUT_CONTRACT_FAILED"
    )
    assert dashboard_by_id["contract-failure"]["annotation_reason"] == (
        "Gemini 返回的证据片段无法在来源正文中逐字找到。"
    )
    assert dashboard_by_id["provider-backoff"]["annotation_reason_code"] == (
        "PROVIDER_HTTP_ERROR"
    )


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
            "prompt_version": "news-impact-v3-independent-semantic-review",
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
        "prompt_version": "news-impact-v3-independent-semantic-review",
        "parse_started_at": parsed_at, "assessed_at": parsed_at + timedelta(seconds=1),
        "impact_class": "SAME_DAY", "event_state": "ACTIVE",
        "update_type": "NEW_EVENT", "confidence": 0.9,
        "reason_zh": "事件发生时间早于文章发布时间。",
    })
    ledger.connection.close()

    row = _dashboard_module()._dashboard_payload(database)["recent_news"][0]

    assert row["impact_status"] == "EXPIRED_ON_RECEIPT"
    assert datetime.fromisoformat(row["impact_expires_at"]) == event_time + timedelta(hours=12)


def test_dashboard_category_is_semantic_not_processing_state() -> None:
    module = _dashboard_module()
    assert module._news_category_label("central_bank_gold") == "央行购金"
    assert module._news_category_label("risk_sentiment") == "风险情绪 / 避险"
    assert module._news_category_label(None) == "其他"
    assert module._news_category_label("") == "其他"
    assert module._news_category_label("other-custom-topic") == "其他"


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
    body = "complete Google News fallback evidence " * 12
    ledger.append_news_revision({
        "source": "google_news_gold_context", "source_item_id": "fallback-item",
        "source_published_time": now - timedelta(minutes=10),
        "collector_first_seen_time": now - timedelta(minutes=5),
        "fetched_time": now - timedelta(minutes=5),
        "headline": "Gold rises as Treasury yields fall",
        "body": body, "link": "https://example.test/fallback",
        "content_hash": hashlib.sha256(body.encode()).hexdigest(),
        "cluster_id": "fallback-item",
    })
    connection = ledger.connection
    rows = _dashboard_module()._news_source_health(connection, now)
    gdelt = next(row for row in rows if row["source"] == "gdelt_gold_geopolitics")
    assert gdelt["health"] == "FALLBACK_ACTIVE"
    assert gdelt["latest_status"] == "RATE_LIMITED"
    assert gdelt["fallback_label"] == "Google News Context"
    assert gdelt["fallback_health"] == "HEALTHY"
    assert gdelt["next_retry_time"] == (now + timedelta(minutes=90)).isoformat()


def test_dashboard_does_not_activate_fallback_from_stale_historical_evidence(
    tmp_path,
) -> None:
    now = datetime(2026, 8, 11, 10, 0, tzinfo=UTC)
    ledger = ForwardLedger(tmp_path / "forward.sqlite3", now=now - timedelta(days=10))
    ledger.append_source_poll({
        "poll_id": "gdelt-429", "source": "gdelt_gold_geopolitics",
        "fetched_time": now - timedelta(minutes=5), "status": "ERROR",
        "error_type": "HTTPError", "error": "HTTP Error 429: Too Many Requests",
    })
    ledger.append_source_poll({
        "poll_id": "google-empty-now", "source": "google_news_gold_context",
        "fetched_time": now - timedelta(minutes=5), "status": "OK",
    })
    body = "historical Google News evidence " * 20
    ledger.append_news_revision({
        "source": "google_news_gold_context", "source_item_id": "old-item",
        "source_published_time": now - timedelta(days=10),
        "collector_first_seen_time": now - timedelta(days=10),
        "fetched_time": now - timedelta(days=10),
        "headline": "Old gold report", "body": body,
        "link": "https://example.test/old",
        "content_hash": hashlib.sha256(body.encode()).hexdigest(),
        "cluster_id": "old-item",
    })

    rows = _dashboard_module()._news_source_health(ledger.connection, now)
    google = next(row for row in rows if row["source"] == "google_news_gold_context")
    gdelt = next(row for row in rows if row["source"] == "gdelt_gold_geopolitics")

    assert google["health"] == "HEALTHY"
    assert google["semantic_status"] == "NO_RECENT_EVIDENCE"
    assert google["recent_evidence"] is False
    assert gdelt["health"] == "ERROR"
    assert gdelt["fallback_health"] == "NO_RECENT_EVIDENCE"


def test_dashboard_clears_historical_gdelt_429_after_successful_gkg_poll(
    tmp_path,
) -> None:
    now = datetime(2026, 8, 13, 3, 0, tzinfo=UTC)
    ledger = ForwardLedger(tmp_path / "forward.sqlite3", now=now)
    ledger.append_source_poll({
        "poll_id": "gdelt-old-doc-429", "source": "gdelt_gold_geopolitics",
        "fetched_time": now - timedelta(hours=2), "status": "ERROR",
        "error_type": "HTTPError", "error": "HTTP Error 429: Too Many Requests",
    })
    ledger.append_source_poll({
        "poll_id": "gdelt-gkg-ok", "source": "gdelt_gold_geopolitics",
        "fetched_time": now - timedelta(minutes=5), "status": "OK",
    })
    body = "current GDELT GKG evidence " * 20
    ledger.append_news_revision({
        "source": "gdelt_gold_geopolitics", "source_item_id": "gkg-item",
        "source_published_time": now - timedelta(minutes=10),
        "collector_first_seen_time": now - timedelta(minutes=5),
        "fetched_time": now - timedelta(minutes=5),
        "headline": "Current GKG report", "body": body,
        "link": "https://example.test/gkg",
        "content_hash": hashlib.sha256(body.encode()).hexdigest(),
        "cluster_id": "gkg-item",
    })

    rows = _dashboard_module()._news_source_health(ledger.connection, now)
    gdelt = next(row for row in rows if row["source"] == "gdelt_gold_geopolitics")

    assert gdelt["health"] == "HEALTHY"
    assert gdelt["latest_status"] == "OK"
    assert gdelt["recovery_mode"] is None
    assert gdelt["fallback_label"] is None
    assert gdelt["next_retry_time"] is None
    assert "429" in gdelt["last_error"]


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
