from __future__ import annotations

import importlib.util
import io
import json
import urllib.error
from datetime import datetime
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


def test_sync_retries_transient_disconnect(monkeypatch) -> None:
    module = _sync_module()
    calls = []

    def flaky(_config):
        calls.append(1)
        if len(calls) < 3:
            raise ConnectionResetError("remote closed")

    monkeypatch.setattr(module, "sync_once", flaky)
    monkeypatch.setattr(module.time, "sleep", lambda _seconds: None)
    assert module.sync_with_retry({"unused": True}) == 3
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

    module.write_sync_status(
        status_file, success=False, error=ConnectionResetError("remote closed")
    )
    failed = json.loads(status_file.read_text(encoding="utf-8"))
    assert failed["last_success"] == succeeded["last_success"]
    assert failed["last_error_type"] == "ConnectionResetError"
    assert failed["last_error"] == "remote closed"


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
    assert len(detail_rows[0]["detail_key"]) == 64
    assert mirrored["market_chart"]["decisions"] == []
    market_decision = json.loads(module.market_chart_snapshot(payload))["decisions"][0]
    assert market_decision["source_decision_id"] == "d1"
    assert market_decision["model_version"] == "unused-field"
    assert len(mirrored["recent_decisions"]) == module.REMOTE_DECISION_LIMIT
    assert len(mirrored["news_evidence"]) == module.REMOTE_EVIDENCE_LIMIT
    assert learning["learning_curves"]["models"] == [
        {"lifecycle_status": "LATEST", "model_version": "latest"}
    ]
    assert learning["learning_curves"]["archived_model_count"] == 1
    assert learning["learning_curves"]["identity_curves"] == [body]
    assert "full_minus_market" not in learning["learning_curves"]
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
    assert posted.count("https://remote/api/news-index") == 1

    posted.clear()
    payload["generated_at"] = "2026-08-07T00:00:30+00:00"
    module.sync_once(config)
    assert "https://remote/api/learning" not in posted
    assert "https://remote/api/news-index" not in posted

    posted.clear()
    payload["learning_curves"]["learning_stage"] = "READY"
    payload["recent_news"][0]["headline"] = "第一条（更新）"
    module.sync_once(config)
    assert posted.count("https://remote/api/learning") == 1
    assert posted.count("https://remote/api/news-index") == 1

    news_state = json.loads((tmp_path / "news-state.json").read_text(encoding="utf-8"))
    assert len(news_state["hashes"]) == 1
    assert len(news_state["index_hashes"]) == 1


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
        "market_chart": {"decisions": list(reversed(decisions))},
    }
    mirrored = json.loads(module.remote_snapshot(payload))
    assert mirrored["market_chart"]["decisions"] == []
    assert mirrored["market_chart"]["decision_resource"] == "/api/market-chart"

    retained = json.loads(module.market_chart_snapshot(payload))["decisions"]
    assert len(retained) == module.REMOTE_MARKET_DECISION_LIMIT
    assert retained[0]["source_decision_id"] == "d-20"
    assert retained[-1]["source_decision_id"] == f"d-{len(decisions) - 1}"
    assert "exit_time" not in retained[0]
    assert retained[0]["model_version"] == "model-20"


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
