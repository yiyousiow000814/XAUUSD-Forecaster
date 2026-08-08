from __future__ import annotations

import hashlib
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


def _append_basic_annotation(
    ledger: ForwardLedger,
    *,
    source: str,
    item_id: str,
    digest: str,
    parsed_at: datetime,
    prompt_version: str = "news-json-v9-local-display-recovery",
) -> None:
    ledger.append_annotation(
        {
            "annotation_id": f"annotation-{source}-{item_id}",
            "source": source,
            "source_item_id": item_id,
            "revision_number": 1,
            "raw_content_hash": digest,
            "annotation": {
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
            },
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
            "prompt_version": "news-json-v9-local-display-recovery",
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


def test_dashboard_marks_readable_late_news_as_not_requiring_annotation(tmp_path) -> None:
    now = datetime(2026, 8, 8, 1, 0, tzinfo=UTC)
    database = tmp_path / "forward.sqlite3"
    ledger = ForwardLedger(database, now=now)
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
    assert payload["recent_news"][0]["annotation_status"] == "NOT_REQUIRED"
    assert payload["annotation_queue"]["queued"] == 0


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
