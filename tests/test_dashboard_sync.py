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


def test_remote_snapshot_is_bounded_and_keeps_retained_content() -> None:
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
        "recent_news": [
            {"headline": f"新闻 {index}", "body": body} for index in range(100)
        ],
        "recent_decisions": [{"id": index} for index in range(30)],
        "news_evidence": [{"id": index} for index in range(100)],
    }

    encoded = module.remote_snapshot(payload)
    mirrored = json.loads(encoded)

    assert len(encoded) <= module.REMOTE_PAYLOAD_LIMIT_BYTES
    assert mirrored["recent_news"][0]["body"] == body
    assert len(mirrored["recent_decisions"]) == module.REMOTE_DECISION_LIMIT
    assert len(mirrored["news_evidence"]) == module.REMOTE_EVIDENCE_LIMIT
    assert mirrored["learning_curves"]["models"] == [
        {"lifecycle_status": "LATEST", "model_version": "latest"}
    ]
    assert mirrored["learning_curves"]["archived_model_count"] == 1
    assert mirrored["learning_curves"]["identity_curves"] == [body]
    assert "full_minus_market" not in mirrored["learning_curves"]
    assert "models" not in mirrored["training"]


def test_annotator_heartbeat_reports_idle_loop_as_healthy(tmp_path) -> None:
    module = _annotator_module()
    status_file = tmp_path / "news-annotator-status.json"
    module.write_heartbeat(status_file, work_items=0)
    status = json.loads(status_file.read_text(encoding="utf-8"))
    assert datetime.fromisoformat(status["last_success"])
    assert status["last_error"] is None
    assert status["work_items"] == 0
