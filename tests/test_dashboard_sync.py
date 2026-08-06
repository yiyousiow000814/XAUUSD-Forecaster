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

    assert len(encoded) <= module.REMOTE_PAYLOAD_LIMIT_BYTES
    assert len(mirrored["recent_news"]) == 100
    assert mirrored["recent_news"] == index_rows
    assert "summary_zh" not in mirrored["recent_news"][0]
    assert detail_rows[0]["payload"]["summary_zh"] == body
    assert len(detail_rows[0]["detail_key"]) == 64
    market_decision = mirrored["market_chart"]["decisions"][0]
    assert market_decision["model_version"] == "large-unused-field"
    assert market_decision["ev_long_u5"] == -0.2
    assert market_decision["ev_short_u5"] == 0.1
    assert market_decision["policy_expected_action"] == "SHORT"
    assert market_decision["policy_consistent"] is True
    assert market_decision["frozen_record"] is True
    assert len(mirrored["recent_decisions"]) == module.REMOTE_DECISION_LIMIT
    assert len(mirrored["news_evidence"]) == module.REMOTE_EVIDENCE_LIMIT
    assert mirrored["learning_curves"]["models"] == [
        {"lifecycle_status": "LATEST", "model_version": "latest"}
    ]
    assert mirrored["learning_curves"]["archived_model_count"] == 1
    assert mirrored["learning_curves"]["identity_curves"] == [body]
    assert "full_minus_market" not in mirrored["learning_curves"]
    assert "models" not in mirrored["training"]


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
