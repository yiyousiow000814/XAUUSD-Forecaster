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
