from __future__ import annotations

import json

import pytest

from scripts import check_public_health as probe


def _response(url: str) -> tuple[int, bytes, str]:
    path = url.removeprefix("https://example.invalid")
    if path in probe.PAGE_MARKERS:
        return 200, probe.PAGE_MARKERS[path].encode(), "text/html"
    if path == "/api/status":
        return 200, json.dumps({"system": {"online": True}}).encode(), "application/json"
    if path == "/api/assistant-health":
        return 200, json.dumps({
            "schema_version": "assistant-operational-health.v1",
            "current": True,
        }).encode(), "application/json"
    raise AssertionError(path)


def test_external_probe_covers_pages_status_and_assistant_health(monkeypatch) -> None:
    monkeypatch.setattr(probe, "read", _response)

    evidence = probe.check_public_surface("https://example.invalid/")

    assert len(evidence) == 5
    assert evidence[-1].startswith("/api/assistant-health=200")


def test_external_probe_uses_stable_code_for_render_contract_failure(monkeypatch) -> None:
    def missing_marker(url: str) -> tuple[int, bytes, str]:
        status, body, content_type = _response(url)
        return (status, b"missing", content_type) if url.endswith("/health") else (status, body, content_type)

    monkeypatch.setattr(probe, "read", missing_marker)

    with pytest.raises(probe.ProbeFailure) as captured:
        probe.check_public_surface("https://example.invalid")

    assert captured.value.code == "OPS_PUBLIC_RENDER_CONTRACT_FAILED"


def test_external_probe_rejects_noncurrent_assistant_health(monkeypatch) -> None:
    def stale_assistant(url: str) -> tuple[int, bytes, str]:
        if url.endswith("/api/assistant-health"):
            return 200, json.dumps({
                "schema_version": "assistant-operational-health.v1",
                "current": False,
            }).encode(), "application/json"
        return _response(url)

    monkeypatch.setattr(probe, "read", stale_assistant)

    with pytest.raises(probe.ProbeFailure) as captured:
        probe.check_public_surface("https://example.invalid")

    assert captured.value.code == "OPS_PUBLIC_ASSISTANT_HEALTH_UNAVAILABLE"
