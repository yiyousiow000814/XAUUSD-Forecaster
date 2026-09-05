from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "bootstrap_news_projection", ROOT / "scripts" / "bootstrap_news_projection.py",
)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


@pytest.mark.parametrize("authorized", [True, False])
def test_cli_state_path_uses_runtime_authority(tmp_path, monkeypatch, authorized):
    authority = tmp_path / "runtime"
    authority.mkdir()
    config = tmp_path / "config.json"
    config.write_text("{}", encoding="utf-8")
    state = (authority if authorized else tmp_path) / "bootstrap.json"
    monkeypatch.setattr(MODULE, "PRODUCTION_RUNTIME_STATE_ROOT", authority)
    monkeypatch.setattr(MODULE.sys, "argv", [
        "bootstrap_news_projection.py", "--config", str(config),
        "--state-file", str(state), "--version-host", "not-a-version",
    ])
    # Origin rejection happens only after the real runtime-path validation;
    # neither branch can make a network call or write state.
    with pytest.raises(ValueError, match=(
        "version host" if authorized else "sync state path"
    )):
        MODULE.main()
    assert not state.exists()


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


def test_bootstrap_reuses_one_frozen_generation_without_stable_api(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    frozen = object()
    observed = []
    states = [
        {"contract_version": MODULE.NEWS_MIRROR_CONTRACT_VERSION,
         "projection_state": "REPLAYING"},
        {"contract_version": MODULE.NEWS_MIRROR_CONTRACT_VERSION,
         "projection_state": "CURRENT"},
    ]
    monkeypatch.setattr(
        MODULE, "_sync_news",
        lambda *_args, **kwargs: observed.append(kwargs["frozen_generation"]),
    )
    monkeypatch.setattr(MODULE, "_read_news_sync_state", lambda _path: states.pop(0))
    monkeypatch.setattr(MODULE, "_get_json", lambda *_args, **_kwargs: {
        "status": "OK", "projection_state": "CURRENT", "verified_complete": True,
        "active_generation_id": "a" * 64, "snapshot_id": "b" * 64,
        "index_count": 12, "detail_count": 12,
        "source_digest": "c" * 64, "receipt_digest": "d" * 64,
        "missing_detail_count": 0, "invariant_violation_count": 0,
    })

    result = MODULE.bootstrap(
        base_config={},
        origin="https://abc12345-aurum-signal-room.example.workers.dev",
        token="secret", state_file=tmp_path / "state.json",
        max_cycles=2, retry_seconds=0, frozen_generation=frozen,
    )

    assert result["status"] == "PASSED"
    assert observed == [frozen, frozen]


@pytest.mark.parametrize("error", [
    MODULE.PayloadContractError("local news projection manifest is missing"),
    MODULE.RemoteInvariantViolation({
        "error_code": "NEWS_PROJECTION_DETAIL_CONTRADICTION",
    }),
])
def test_bootstrap_does_not_retry_deterministic_contract_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, error: Exception,
) -> None:
    attempts = []

    def reject(*_args, **_kwargs):
        attempts.append(1)
        raise error

    monkeypatch.setattr(MODULE, "_sync_news", reject)
    with pytest.raises(type(error), match=re.escape(str(error))):
        MODULE.bootstrap(
            base_config={"local_status_url": "http://127.0.0.1:8765/api/status"},
            origin="https://abc12345-aurum-signal-room.example.workers.dev",
            token="secret", state_file=tmp_path / "state.json",
            max_cycles=1_000, retry_seconds=0,
        )
    assert attempts == [1]


def test_bootstrap_restores_persisted_generation_before_rebuilding(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    artifact = tmp_path / "candidate-generation.json.gz"
    artifact.write_bytes(b"persisted")
    frozen = object()
    monkeypatch.setattr(
        MODULE, "_read_news_projection_generation_artifact",
        lambda path: frozen if path == artifact else None,
    )
    monkeypatch.setattr(
        MODULE, "_freeze_news_projection_generation",
        lambda _path: pytest.fail("restart must not rebuild a pinned generation"),
    )

    restored = MODULE._load_or_freeze_news_projection_generation(
        tmp_path / "source.sqlite3", artifact,
    )

    assert restored is frozen


def test_bootstrap_persists_generation_before_first_replay(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    artifact = tmp_path / "candidate-generation.json.gz"
    frozen = object()
    writes = []
    monkeypatch.setattr(
        MODULE, "_freeze_news_projection_generation", lambda _path: frozen,
    )
    monkeypatch.setattr(
        MODULE, "_write_news_projection_generation_artifact",
        lambda path, generation: writes.append((path, generation)),
    )

    result = MODULE._load_or_freeze_news_projection_generation(
        tmp_path / "source.sqlite3", artifact,
    )

    assert result is frozen
    assert writes == [(artifact, frozen)]


def test_missing_pinned_artifact_enters_explicit_recovery(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    state_file = tmp_path / "state.json"
    state = {
        "contract_version": MODULE.NEWS_MIRROR_CONTRACT_VERSION,
        "projection_state": "REPLAYING", "generation_id": "a" * 64,
    }
    monkeypatch.setattr(MODULE, "_read_news_sync_state", lambda _path: dict(state))
    recorded = []
    monkeypatch.setattr(
        MODULE, "_record_recovery_required",
        lambda *args: recorded.append(args),
    )

    with pytest.raises(MODULE.PayloadContractError, match="explicit recovery"):
        MODULE._require_recoverable_artifact(
            state_file, tmp_path / "missing-generation.json.gz",
        )

    assert recorded == [(
        state_file, None, "FROZEN_GENERATION_ARTIFACT_MISSING",
    )]


def test_recovery_abandons_only_exact_recorded_staging(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    generation_id = "a" * 64
    state_file = tmp_path / "state.json"
    artifact = tmp_path / "state-generation.json.gz"
    state_file.write_text("{}", encoding="utf-8")
    artifact.write_bytes(b"artifact")
    monkeypatch.setattr(MODULE, "_read_news_sync_state", lambda _path: {
        "projection_state": "RECOVERY_REQUIRED", "generation_id": generation_id,
        "recovery": {
            "generation_id": generation_id,
            "error_code": "FROZEN_GENERATION_ARTIFACT_MISSING",
        },
    })
    health = iter([
        {"staging": {"generation_id": generation_id}},
        {"staging": None},
    ])
    monkeypatch.setattr(MODULE, "_get_json", lambda *_args, **_kwargs: next(health))
    posted = []
    monkeypatch.setattr(
        MODULE, "_post_json",
        lambda url, body, _config: posted.append((url, json.loads(body))) or {},
    )

    result = MODULE.abandon_recovery_generation(
        config={"token": "secret"}, origin="https://candidate.example",
        state_file=state_file, artifact_path=artifact,
        generation_id=generation_id,
    )

    assert result["status"] == "PASSED"
    assert posted == [(
        "https://candidate.example/api/news-index",
        {"action": "abandon", "generation_id": generation_id},
    )]
    assert not state_file.exists()
    assert not artifact.exists()
    assert (tmp_path / "state-recovery.json").exists()
