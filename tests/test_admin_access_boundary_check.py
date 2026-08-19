from __future__ import annotations

import pytest

from scripts import check_admin_access_boundary as probe


def _protected_response(url: str) -> tuple[int, bytes, str | None]:
    path = url.removeprefix("https://example.invalid")
    if path in probe.PUBLIC_PATHS:
        return 200, b"public", None
    if path in probe.HUMAN_PATHS:
        return 302, b"", "https://aurum.cloudflareaccess.com/cdn-cgi/access/login/example"
    if path in probe.APPLICATION_AUTH_PATHS:
        return 401, b'{"error":"dashboard operator authentication required"}', None
    if path in probe.MACHINE_PATHS:
        return 401, b'{"error":"machine authorization failed"}', None
    raise AssertionError(path)


def test_access_probe_separates_public_human_and_machine_surfaces(monkeypatch) -> None:
    monkeypatch.setattr(probe, "read", _protected_response)

    evidence = probe.check_admin_access_boundary("https://example.invalid/")

    assert len(evidence) == (
        len(probe.PUBLIC_PATHS) + len(probe.HUMAN_PATHS)
        + len(probe.APPLICATION_AUTH_PATHS) + len(probe.MACHINE_PATHS)
    )
    assert "public:/=200" in evidence
    assert "access:/admin=302" in evidence
    assert "application-auth:/api/assistant-health=401" in evidence
    assert "machine:/api/operator-retry-worker=401" in evidence


def test_access_probe_rejects_an_admin_page_that_reaches_the_worker(monkeypatch) -> None:
    def missing_access(url: str) -> tuple[int, bytes, str | None]:
        if url.endswith("/admin"):
            return 200, b"private shell", None
        return _protected_response(url)

    monkeypatch.setattr(probe, "read", missing_access)

    with pytest.raises(probe.ProbeFailure, match="human /admin"):
        probe.check_admin_access_boundary("https://example.invalid")


def test_access_probe_rejects_machine_routes_placed_behind_human_access(monkeypatch) -> None:
    def protected_machine(url: str) -> tuple[int, bytes, str | None]:
        if url.endswith("/api/operator-retry-worker"):
            return 302, b"", "https://aurum.cloudflareaccess.com/cdn-cgi/access/login/example"
        return _protected_response(url)

    monkeypatch.setattr(probe, "read", protected_machine)

    with pytest.raises(probe.ProbeFailure, match="incorrectly covered"):
        probe.check_admin_access_boundary("https://example.invalid")
