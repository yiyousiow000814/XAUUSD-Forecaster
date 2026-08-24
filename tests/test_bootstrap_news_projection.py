from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "bootstrap_news_projection", ROOT / "scripts" / "bootstrap_news_projection.py",
)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


@pytest.mark.parametrize("value", [
    "https://abc12345-aurum-signal-room.example.workers.dev",
    "https://01abc234-aurum-signal-room.example.workers.dev/",
])
def test_accepts_exact_version_worker_origins(value: str) -> None:
    assert MODULE._version_origin(value).startswith("https://")


@pytest.mark.parametrize("value", [
    "http://abc12345-aurum-signal-room.example.workers.dev",
    "https://aurum-signal-room.example.workers.dev",
    "https://abc12345-aurum-signal-room.example.workers.dev/api/ingest",
    "https://abc12345-aurum-signal-room-preview.example.workers.dev",
])
def test_rejects_non_version_or_preview_origins(value: str) -> None:
    with pytest.raises(ValueError):
        MODULE._version_origin(value)


def test_bootstrap_keeps_partial_replay_then_requires_verified_current(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    states = [
        {"contract_version": MODULE.NEWS_MIRROR_CONTRACT_VERSION,
         "projection_state": "REPLAYING"},
        {"contract_version": MODULE.NEWS_MIRROR_CONTRACT_VERSION,
         "projection_state": "CURRENT"},
    ]
    monkeypatch.setattr(MODULE, "_sync_news", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(MODULE, "_read_news_sync_state", lambda _path: states.pop(0))
    monkeypatch.setattr(MODULE, "_get_json", lambda *_args, **_kwargs: {
        "status": "OK", "projection_state": "CURRENT", "verified_complete": True,
        "active_generation_id": "a" * 64, "snapshot_id": "b" * 64,
        "index_count": 12, "detail_count": 12,
        "source_digest": "c" * 64, "receipt_digest": "d" * 64,
        "missing_detail_count": 0, "invariant_violation_count": 0,
    })
    result = MODULE.bootstrap(
        base_config={"local_status_url": "http://127.0.0.1:8765/api/status"},
        origin="https://abc12345-aurum-signal-room.example.workers.dev",
        token="secret", state_file=tmp_path / "state.json",
        max_cycles=2, retry_seconds=0,
    )
    assert result["status"] == "PASSED"
    assert result["cycles"] == 2
    assert result["missing_detail_count"] == 0
