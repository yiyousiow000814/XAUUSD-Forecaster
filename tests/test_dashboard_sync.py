from __future__ import annotations

import importlib.util
import io
import urllib.error
from pathlib import Path


def _sync_module():
    path = Path(__file__).resolve().parents[1] / "scripts" / "run_dashboard_sync.py"
    spec = importlib.util.spec_from_file_location("run_dashboard_sync_test", path)
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
