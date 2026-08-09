from __future__ import annotations

import importlib.util
import io
import json
import urllib.error
from datetime import datetime, timedelta, timezone
from pathlib import Path


def _sync_module():
    path = Path(__file__).resolve().parents[1] / "scripts" / "run_dashboard_sync.py"
    spec = importlib.util.spec_from_file_location("run_dashboard_sync_test", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _annotator_module():
    path = Path(__file__).resolve().parents[1] / "scripts" / "run_news_annotator.py"
    spec = importlib.util.spec_from_file_location("run_news_annotator_test", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _preview_module():
    path = Path(__file__).resolve().parents[1] / "scripts" / "build_preview_bundle.py"
    spec = importlib.util.spec_from_file_location("build_preview_bundle_test", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_preview_does_not_call_late_aggregated_news_expired() -> None:
    module = _preview_module()
    news_index = {"items": [{
        "annotation_status": "NOT_REQUIRED",
        "source": "google_news_gold_context",
        "source_published_time": "2026-08-08T20:40:28+00:00",
        "collector_first_seen_time": "2026-08-08T23:34:06+00:00",
    }]}

    module._backfill_annotation_reasons(
        news_index, {"forward_epoch": "2026-08-05T00:00:00+00:00"}
    )

    row = news_index["items"][0]
    assert row["annotation_reason_code"] == "SEARCH_LEAD"
    assert row["annotation_reason"] == "搜索线索：来自聚合发现源，不是独立官方发布"


def test_sync_retries_transient_disconnect(monkeypatch) -> None:
    module = _sync_module()
    calls = []

    def flaky(_config):
        calls.append(1)
        if len(calls) < 3:
            raise ConnectionResetError("remote closed")

    monkeypatch.setattr(module, "sync_once", flaky)
    monkeypatch.setattr(module.time, "sleep", lambda _seconds: None)
    assert module.sync_with_retry({"unused": True}) == (3, [])
    assert len(calls) == 3


def test_sync_does_not_retry_authentication_error(monkeypatch) -> None:
    module = _sync_module()
    calls = []

    def rejected(_config):
        calls.append(1)
        raise urllib.error.HTTPError("https://example", 403, "Forbidden", {}, io.BytesIO())

    monkeypatch.setattr(module, "sync_once", rejected)
    try:
        module.sync_with_retry({"unused": True})
    except urllib.error.HTTPError as error:
        assert error.code == 403
    else:
        raise AssertionError("403 must be raised immediately")
    assert len(calls) == 1


def test_sync_status_records_real_success_and_preserves_it_on_error(tmp_path) -> None:
    module = _sync_module()
    status_file = tmp_path / "dashboard-sync-status.json"

    module.write_sync_status(status_file, success=True, attempts_used=2)
    succeeded = json.loads(status_file.read_text(encoding="utf-8"))
    assert datetime.fromisoformat(succeeded["last_success"])
    assert succeeded["attempts_used"] == 2
    assert succeeded["last_error"] is None
    assert succeeded["status"] == "OK"

    module.write_sync_status(
        status_file, success=False, error=ConnectionResetError("remote closed")
    )
    failed = json.loads(status_file.read_text(encoding="utf-8"))
    assert failed["last_success"] == succeeded["last_success"]
    assert failed["last_error_type"] == "ConnectionResetError"
    assert failed["last_error"] == "remote closed"
    assert failed["status"] == "ERROR"


def test_sync_status_reports_optional_resource_degradation(tmp_path) -> None:
    module = _sync_module()
    status_file = tmp_path / "dashboard-sync-status.json"
    degraded = [{"resource": "learning", "error": "too large"}]
    module.write_sync_status(
        status_file, success=True, attempts_used=1,
        degraded_resources=degraded,
    )
    status = json.loads(status_file.read_text(encoding="utf-8"))
    assert status["status"] == "DEGRADED"
    assert status["last_error"] is None
    assert status["degraded_resources"] == degraded


def test_configured_targets_adds_independent_cloudflare_mirror(
    monkeypatch, tmp_path
) -> None:
    module = _sync_module()
    monkeypatch.setenv(
        "CLOUDFLARE_INGEST_URL", "https://example.workers.dev/api/ingest"
    )
    monkeypatch.setenv("CLOUDFLARE_INGEST_TOKEN", "cloudflare-token")
    config = {
        "remote_ingest_url": "https://example.chatgpt.site/api/ingest",
        "token": "sites-token",
        "learning_state_file": str(tmp_path / "learning.json"),
        "news_state_file": str(tmp_path / "news.json"),
    }

    sites, cloudflare = module.configured_targets(config)

    assert sites["name"] == "sites"
    assert sites["learning_state_file"].endswith("learning.json")
    assert cloudflare["name"] == "cloudflare"
    assert cloudflare["remote_ingest_url"].endswith("workers.dev/api/ingest")
    assert cloudflare["learning_state_file"].endswith("learning-cloudflare.json")
    assert cloudflare["news_state_file"].endswith("news-cloudflare.json")


def test_configured_targets_can_disable_retired_sites_mirror(
    monkeypatch, tmp_path
) -> None:
    module = _sync_module()
    monkeypatch.setenv(
        "CLOUDFLARE_INGEST_URL", "https://example.workers.dev/api/ingest"
    )
    monkeypatch.setenv("CLOUDFLARE_INGEST_TOKEN", "cloudflare-token")
    config = {
        "enabled": False,
        "remote_ingest_url": "https://retired.chatgpt.site/api/ingest",
        "token": "retired-token",
        "learning_state_file": str(tmp_path / "learning.json"),
        "news_state_file": str(tmp_path / "news.json"),
    }

    targets = module.configured_targets(config)

    assert [target["name"] for target in targets] == ["cloudflare"]
    assert targets[0]["remote_ingest_url"].endswith("workers.dev/api/ingest")


def test_sites_bypass_header_is_not_sent_to_cloudflare(monkeypatch) -> None:
    module = _sync_module()
    monkeypatch.setenv("SITES_BYPASS_TOKEN", "sites-bypass")
    captured = []

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    def fake_urlopen(request, **_kwargs):
        captured.append(dict(request.header_items()))
        return Response()

    monkeypatch.setattr(module.urllib.request, "urlopen", fake_urlopen)
    config = {"token": "ingest-token"}

    module._post_json(
        "https://example.chatgpt.site/api/ingest", b"{}", config
    )
    module._post_json("https://example.workers.dev/api/ingest", b"{}", config)

    assert "Oai-sites-authorization" in captured[0]
    assert "Oai-sites-authorization" not in captured[1]
    assert captured[1]["User-agent"] == "AurumSignalRoomMirror/1.0"


def test_remote_snapshot_keeps_full_news_index_and_splits_details() -> None:
    module = _sync_module()
    body = "完整正文" * 2_000
    payload = {
        "training": {"complete_rows": 200, "models": [{"duplicate": body}]},
        "learning_curves": {
            "models": [
                {"lifecycle_status": "LATEST", "model_version": "latest"},
                {"lifecycle_status": "ARCHIVED", "model_version": "old"},
            ],
            "identity_curves": [body],
            "full_minus_market": [body],
            "broad_full_minus_official_full": [body],
        },
        "recent_news": [{
            "source": "example", "source_item_id": str(index),
            "revision_number": 1, "headline": f"新闻 {index}",
            "summary_zh": body, "category": "其他",
            "content_fetch_status": "UNAVAILABLE",
            "content_error_type": "HTTPError",
            "annotation_status": "NOT_REQUIRED",
            "annotation_reason_code": "SEARCH_LEAD",
            "annotation_reason": "搜索线索：来自聚合发现源，不是独立官方发布",
        } for index in range(100)],
        "recent_decisions": [{"id": index} for index in range(30)],
        "news_evidence": [{"id": index} for index in range(100)],
        "market_chart": {"decisions": [{
            "source_decision_id": "d1", "decision_time": "2026-08-06T00:00:00+00:00",
            "model_identity": "MARKET_ONLY", "model_version": "large-unused-field",
            "recommended_action": "SHORT", "ev_long_u5": -0.2,
            "ev_short_u5": 0.1, "policy_expected_action": "SHORT",
            "policy_consistent": True, "frozen_record": True,
        }]},
    }

    encoded = module.remote_snapshot(payload)
    mirrored = json.loads(encoded)
    index_rows, detail_rows = module.news_mirror_parts(payload)
    learning = json.loads(module.learning_snapshot(payload))

    assert len(encoded) <= module.REMOTE_PAYLOAD_LIMIT_BYTES
    assert mirrored["recent_news"] == []
    assert mirrored["news_index_resource"] == "/api/news-index"
    assert mirrored["learning_resource"] == "/api/learning"
    assert detail_rows[0]["payload"]["summary_zh"] == body
    assert index_rows[0]["content_fetch_status"] == "UNAVAILABLE"
    assert index_rows[0]["content_error_type"] == "HTTPError"
    assert index_rows[0]["annotation_reason_code"] == "SEARCH_LEAD"
    assert index_rows[0]["annotation_reason"].startswith("搜索线索")
    assert "content_fetch_status" not in detail_rows[0]["payload"]
    assert "annotation_reason" not in detail_rows[0]["payload"]
    assert len(detail_rows[0]["detail_key"]) == 64
    assert mirrored["market_chart"]["decisions"] == []
    market_decision = json.loads(module.market_chart_snapshot(payload))["decisions"][0]
    assert market_decision["source_decision_id"] == "d1"
    assert market_decision["model_version"] == "unused-field"
    assert len(mirrored["recent_decisions"]) == module.REMOTE_DECISION_LIMIT
    assert len(mirrored["news_evidence"]) == module.REMOTE_EVIDENCE_LIMIT
    assert learning["learning_curves"]["models"] == [
        {"lifecycle_status": "LATEST", "model_version": "latest"},
        {"lifecycle_status": "ARCHIVED", "model_version": "old"},
    ]
    assert learning["learning_curves"]["archived_model_count"] == 1
    assert learning["learning_curves"]["identity_curves"] == [body]
    assert learning["learning_curves"]["full_minus_market"] == [body]
    assert learning["learning_curves"]["broad_full_minus_official_full"] == [body]
    assert "learning_curves" not in mirrored
    assert "models" not in mirrored["training"]
    assert len(index_rows) == 100


def test_news_detail_batches_stay_bounded() -> None:
    module = _sync_module()
    rows = [{
        "detail_key": f"{index:064x}", "detail_hash": f"{index + 1:064x}",
        "payload": {"summary_zh": "摘要" * 20_000},
    } for index in range(8)]
    batches = module.news_detail_batches(rows)
    assert len(batches) > 1
    assert sum(len(batch) for batch in batches) == len(rows)
    for batch in batches:
        encoded = json.dumps(
            {"items": batch}, ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
        assert len(encoded) <= module.NEWS_DETAIL_BATCH_LIMIT_BYTES


def test_news_index_batches_stay_bounded() -> None:
    module = _sync_module()
    rows = [{
        "detail_key": f"{index:064x}", "category": "战争/地缘",
        "collector_first_seen_time": f"2026-08-07T00:{index:02d}:00+00:00",
        "headline": "标题" * 5_000,
    } for index in range(20)]
    batches = module.news_index_batches(rows)
    assert sum(len(batch) for batch in batches) == len(rows)
    for batch in batches:
        encoded = json.dumps(
            {"items": batch}, ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
        assert len(encoded) <= module.NEWS_INDEX_BATCH_LIMIT_BYTES


def test_sync_skips_unchanged_news_index_and_learning(monkeypatch, tmp_path) -> None:
    module = _sync_module()
    payload = {
        "generated_at": "2026-08-07T00:00:00+00:00",
        "learning_curves": {"learning_stage": "EARLY"},
        "execution_learning": {"models": []},
        "recent_news": [{
            "source": "example", "source_item_id": "1", "revision_number": 1,
            "category": "其他", "collector_first_seen_time": "2026-08-07T00:00:00+00:00",
            "headline": "第一条", "summary_zh": "摘要",
        }],
        "market_chart": {"decisions": []},
    }

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return json.dumps(payload, ensure_ascii=False).encode("utf-8")

    posted: list[str] = []
    monkeypatch.setattr(module.urllib.request, "urlopen", lambda *_args, **_kwargs: Response())
    monkeypatch.setattr(module, "_post_json", lambda url, _body, _config: posted.append(url))
    config = {
        "local_status_url": "http://local/status",
        "remote_ingest_url": "https://remote/api/ingest",
        "token": "test",
        "news_state_file": str(tmp_path / "news-state.json"),
        "learning_state_file": str(tmp_path / "learning-state.json"),
    }

    module.sync_once(config)
    assert posted.count("https://remote/api/learning") == 1
    # An unknown mirror contract is reset once before the authoritative rows
    # are repopulated, so stale remote-only news cannot survive forever.
    assert posted.count("https://remote/api/news-index") == 2
    assert posted[0] == "https://remote/api/ingest"

    posted.clear()
    payload["generated_at"] = "2026-08-07T00:00:30+00:00"
    module.sync_once(config)
    assert "https://remote/api/learning" not in posted
    assert "https://remote/api/news-index" not in posted
    assert posted[0] == "https://remote/api/ingest"

    posted.clear()
    payload["learning_curves"]["learning_stage"] = "READY"
    payload["recent_news"][0]["headline"] = "第一条（更新）"
    module.sync_once(config)
    assert posted.count("https://remote/api/learning") == 1
    assert posted.count("https://remote/api/news-index") == 1
    assert posted[0] == "https://remote/api/ingest"

    news_state = json.loads((tmp_path / "news-state.json").read_text(encoding="utf-8"))
    assert len(news_state["hashes"]) == 1
    assert len(news_state["index_hashes"]) == 1
    assert news_state["last_index_full_sync"]
    assert news_state["mirror_contract_version"] == module.NEWS_MIRROR_CONTRACT_VERSION


def test_sync_repopulates_news_index_without_full_refresh_marker(
    monkeypatch, tmp_path
) -> None:
    module = _sync_module()
    payload = {
        "generated_at": "2026-08-07T00:00:00+00:00",
        "learning_curves": {},
        "execution_learning": {"models": []},
        "recent_news": [{
            "source": "Federal Reserve",
            "source_item_id": "press-1",
            "revision_number": 1,
            "category": "利率/Fed",
            "collector_first_seen_time": "2026-08-07T00:00:00+00:00",
            "headline": "第一条",
        }],
        "market_chart": {"decisions": []},
    }
    index_rows, _ = module.news_mirror_parts(payload)
    state_file = tmp_path / "news-state.json"
    state_file.write_text(json.dumps({
        "index_hashes": {
            index_rows[0]["detail_key"]: module._json_hash(index_rows[0]),
        },
        "hashes": {},
        "last_full_sync": "2026-08-07T00:00:00+00:00",
    }), encoding="utf-8")

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return json.dumps(payload, ensure_ascii=False).encode("utf-8")

    posted: list[str] = []
    monkeypatch.setattr(
        module.urllib.request, "urlopen", lambda *_args, **_kwargs: Response()
    )
    monkeypatch.setattr(
        module, "_post_json", lambda url, _body, _config: posted.append(url)
    )
    config = {
        "local_status_url": "http://local/status",
        "remote_ingest_url": "https://remote/api/ingest",
        "token": "test",
        "news_state_file": str(state_file),
        "learning_state_file": str(tmp_path / "learning-state.json"),
    }

    module.sync_once(config)

    assert posted.count("https://remote/api/news-index") == 2
    state = json.loads(state_file.read_text(encoding="utf-8"))
    assert state["last_index_full_sync"]
    assert state["mirror_contract_version"] == module.NEWS_MIRROR_CONTRACT_VERSION


def test_remote_market_chart_is_split_from_status_and_keeps_complete_window() -> None:
    module = _sync_module()
    decisions = [{
        "source_decision_id": f"d-{index}",
        "decision_time": f"2026-08-06T{index // 60:02d}:{index % 60:02d}:00+00:00",
        "model_identity": "BROAD_FULL",
        "model_version": f"model-{index}",
        "recommended_action": "SHORT",
        "prediction_status": "PROVISIONAL",
        "outcome_status": "VALID",
        "ev_long_u5": -0.2,
        "ev_short_u5": 0.1,
    } for index in range(module.REMOTE_MARKET_DECISION_LIMIT + 20)]
    payload = {
        "market_chart": {
            "decisions": list(reversed(decisions)),
            "candles": [{"time": "2026-08-05T00:00:00+00:00", "open": 1.1234567, "high": 2, "low": 0, "close": 1.5, "ticks": 8}],
            "history_start": "2026-08-05T00:00:00+00:00",
            "history_end": "2026-08-07T20:55:00+00:00",
            "source_candle_count": 736,
        },
    }
    mirrored = json.loads(module.remote_snapshot(payload))
    assert mirrored["market_chart"]["decisions"] == []
    assert mirrored["market_chart"]["decision_resource"] == "/api/market-chart"

    market = json.loads(module.market_chart_snapshot(payload))
    retained = market["decisions"]
    assert len(retained) == module.REMOTE_MARKET_DECISION_LIMIT + 1
    assert retained[0]["source_decision_id"] == "d-0"
    assert all(row["source_decision_id"] != "d-1" for row in retained)
    assert retained[-1]["source_decision_id"] == f"d-{len(decisions) - 1}"
    assert "exit_time" not in retained[1]
    assert retained[1]["model_version"] == "model-20"
    assert len(market["candles"]) == 1
    assert market["candles"][0]["open"] == 1.123
    assert market["candles"][0]["time"] == "2026-08-05T00:00:00Z"
    assert "ticks" not in market["candles"][0]
    assert market["history_end"] == "2026-08-07T20:55:00+00:00"
    assert market["source_candle_count"] == 736


def test_seven_day_market_snapshot_keeps_every_half_hour_under_limit() -> None:
    module = _sync_module()
    start = datetime(2026, 8, 1, tzinfo=timezone.utc)
    identities = ("MARKET_ONLY", "NEWS_RESIDUAL", "FULL", "BROAD_NEWS_RESIDUAL", "BROAD_FULL")
    candles = []
    decisions = []
    for index in range(7 * 288):
        decision_time = (start + timedelta(minutes=5 * index)).isoformat()
        candles.append({
            "time": decision_time, "open": 4300.123456, "high": 4301.123456,
            "low": 4299.123456, "close": 4300.623456, "ticks": 100,
        })
        decisions.extend({
            "source_decision_id": f"d-{index}", "decision_time": decision_time,
            "model_identity": identity, "model_version": "model-version-long",
            "recommended_action": "LONG", "outcome_status": "VALID",
            "ev_long_u5": 0.123456789, "ev_short_u5": -0.123456789,
            "long_quote_return": 0.00123456789,
            "short_quote_return": -0.00123456789,
        } for identity in identities)

    encoded = module.market_chart_snapshot({
        "market_chart": {
            "candles": candles, "overview_candles": candles[::4][:480],
            "decisions": decisions,
            "prediction_history_start": {
                identity: start.isoformat() for identity in identities
            },
        },
    })
    market = json.loads(encoded)

    assert len(encoded) <= module.REMOTE_PAYLOAD_LIMIT_BYTES
    assert len(market["candles"]) == 7 * 288
    assert 0 < len(market["overview_candles"]) < 480
    retained_half_hours = {
        row["decision_time"] for row in market["decisions"]
        if row["model_identity"] == "MARKET_ONLY" and row["decision_time"][14:16] in ("00", "30")
    }
    assert len(retained_half_hours) == 7 * 48
    assert min(retained_half_hours) == "2026-08-01T00:00:00Z"
    assert max(retained_half_hours) == "2026-08-07T23:30:00Z"


def test_market_overview_downsampling_preserves_ohlc_extremes() -> None:
    module = _sync_module()
    rows = [{
        "time": f"t-{index}", "open": float(index), "high": float(index + 2),
        "low": float(index - 2), "close": float(index + 0.5),
        "source_candles": 2,
    } for index in range(6)]

    compact = module._downsample_market_overview(rows, 2)

    assert len(compact) == 2
    assert compact[0] == {
        "time": "t-0", "open": 0.0, "high": 4.0, "low": -2.0,
        "close": 2.5, "source_candles": 6,
    }


def test_market_history_ingest_batches_are_bounded_and_complete() -> None:
    module = _sync_module()
    candles = [{
        "time": f"2026-08-07T00:{index:02d}:00+00:00",
        "open": 4300.1234, "high": 4301.1234,
        "low": 4299.1234, "close": 4300.6234, "ticks": 20,
    } for index in range(12)]
    decisions = [{
        "source_decision_id": f"d-{index}",
        "decision_time": f"2026-08-07T00:{index:02d}:00+00:00",
        "model_identity": "BROAD_FULL", "model_version": "very-long-model-version",
        "recommended_action": "LONG", "outcome_status": "VALID",
        "ev_long_u5": 0.12, "ev_short_u5": -0.12,
        "long_quote_return": 0.001, "short_quote_return": -0.001,
    } for index in range(12)]

    payloads = module._market_history_payloads(candles, decisions)
    decoded = [json.loads(payload) for payload in payloads]

    assert all(len(payload) <= module.MARKET_HISTORY_BATCH_LIMIT_BYTES for payload in payloads)
    assert sum(len(row.get("candles", [])) for row in decoded) == len(candles)
    assert sum(len(row.get("decisions", [])) for row in decoded) == len(decisions)
    assert module._overlap_cursor("2026-08-07T04:00:00Z") == "2026-08-07T02:00:00+00:00"


def test_curve_compaction_preserves_extremes_and_version_boundaries() -> None:
    module = _sync_module()
    points = [{
        "decision_time": f"point-{index}",
        "cumulative_quote_return": float(index),
        **({"model_version": "new-version"} if index == 501 else {}),
    } for index in range(960)]
    points[410]["cumulative_quote_return"] = -999.0
    points[720]["cumulative_quote_return"] = 999.0
    compact = module.compact_curve_points(points)
    retained = {point["cumulative_quote_return"] for point in compact}
    assert -999.0 in retained
    assert 999.0 in retained
    assert any(point.get("model_version") == "new-version" for point in compact)
    assert compact[0] == points[0]
    assert compact[-1] == points[-1]


def test_annotator_heartbeat_reports_idle_loop_as_healthy(tmp_path) -> None:
    module = _annotator_module()
    status_file = tmp_path / "news-annotator-status.json"
    module.write_heartbeat(status_file, work_items=0)
    status = json.loads(status_file.read_text(encoding="utf-8"))
    assert datetime.fromisoformat(status["last_success"])
    assert status["last_error"] is None
    assert status["work_items"] == 0
