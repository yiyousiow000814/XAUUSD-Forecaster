from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from scripts import check_public_health as probe


def _response(url: str) -> tuple[int, bytes, str]:
    path = url.removeprefix("https://example.invalid")
    if path in probe.PAGE_MARKERS:
        return 200, probe.PAGE_MARKERS[path].encode(), "text/html"
    if path == "/api/status":
        return 200, json.dumps({"system": {"online": True}}).encode(), "application/json"
    raise AssertionError(path)


def test_external_probe_covers_only_public_pages_and_status(monkeypatch) -> None:
    monkeypatch.setattr(probe, "read", _response)

    evidence = probe.check_public_surface("https://example.invalid/")

    assert len(evidence) == 4
    assert evidence[1].startswith("/health=200")
    assert evidence[2].startswith("/audit=200")
    assert evidence[-1].startswith("/api/status=200")
    assert all(not item.startswith("/status=") for item in evidence)
    assert all("assistant-health" not in item for item in evidence)


def test_external_probe_uses_stable_code_for_render_contract_failure(monkeypatch) -> None:
    def missing_marker(url: str) -> tuple[int, bytes, str]:
        status, body, content_type = _response(url)
        return (status, b"missing", content_type) if url.endswith("/health") else (status, body, content_type)

    monkeypatch.setattr(probe, "read", missing_marker)

    with pytest.raises(probe.ProbeFailure) as captured:
        probe.check_public_surface("https://example.invalid")

    assert captured.value.code == "OPS_PUBLIC_RENDER_CONTRACT_FAILED"


@pytest.mark.parametrize("failure", [None, "closed", "closed_dead_collector", "old_source", "old_worker", "stale", "epoch", "collector", "relay", "missing_ack", "old_ack"])
def test_publication_checks_identity_business_and_real_sync_status(tmp_path, monkeypatch, failure) -> None:
    now = datetime.now(UTC)
    stamp = now.isoformat()
    revision = "a" * 40
    worker = "00000000-0000-4000-8000-000000000001"
    sync = tmp_path / "dashboard-sync-status.json"
    (tmp_path / "collector-status.json").write_text(json.dumps({
        "service": "collector", "state": "STOPPED" if failure == "closed_dead_collector" else "RUNNING", "last_success": stamp,
    }))
    sync.write_text(json.dumps({
        "last_success": stamp,
        "resource_observations": [{"target": "cloudflare", "resource": "heartbeat", "status": "ERROR" if failure == "missing_ack" else "OK", "completed_at": (now - timedelta(hours=1)).isoformat() if failure == "old_ack" else stamp}],
        "degraded_resources": [{"resource": "news_evidence"}],
    }))

    def response(url):
        public = url.startswith("https:")
        payload = {
            "observation_scope": "RELAY" if failure == "relay" else "D1_SNAPSHOT",
            "system": {
                "online": failure not in {"closed", "closed_dead_collector"}, "mode": "SHADOW", "trading_enabled": False,
                "market_session": "CLOSED" if failure in {"closed", "closed_dead_collector"} else "OPEN",
                "components": {"decision_collector": {"status": "ERROR" if failure == "collector" else "OK"}},
                "deployment": {
                    "runtime_git_sha": "b" * 40 if failure == "old_source" else revision,
                    "runtime_dirty": False,
                    "payload_generated_at": (now - timedelta(hours=1)).isoformat() if failure == "stale" else stamp,
                    "source_database_epoch": "other" if public and failure == "epoch" else "fixture-epoch",
                },
            },
        }
        return 200, json.dumps(payload).encode(), {"X-Aurum-Git-SHA": revision, "X-Aurum-Worker-Version": "wrong" if failure == "old_worker" else worker}

    monkeypatch.setattr(probe, "read_response", response)
    kwargs = dict(local_revision=revision, worker_revision=revision, worker_version=worker,
                  started_after=now - timedelta(seconds=1), sync_status=sync)
    if failure and failure != "closed":
        with pytest.raises(probe.ProbeFailure):
            probe.check_publication(**kwargs)
    else:
        result = probe.check_publication(**kwargs)
        assert result["critical_business"] == "PASS"
        assert result["heartbeat_sync"] == "PASS"
        assert result["news_backlog_acceptance"] == "SEPARATE"
        assert result["degraded_resources"] == [{"resource": "news_evidence"}]
