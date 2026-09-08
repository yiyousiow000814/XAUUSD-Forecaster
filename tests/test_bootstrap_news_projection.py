from __future__ import annotations

import importlib.util
import json
import re
import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "bootstrap_news_projection", ROOT / "scripts" / "bootstrap_news_projection.py",
)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


@pytest.mark.parametrize("context", ["inside", "outside", "linked-outside", "linked-inside"])
def test_cli_state_path_uses_runtime_authority(tmp_path, monkeypatch, context):
    authority = tmp_path / "runtime"
    authority.mkdir()
    config = tmp_path / "config.json"
    config.write_text("{}", encoding="utf-8")
    state = (tmp_path if context == "outside" else authority) / "bootstrap.json"
    target = None
    if context.startswith("linked-"):
        target = (authority if context == "linked-inside" else tmp_path) / "raw.sqlite3"
        target.write_bytes(b"original-fact-sentinel")
        try:
            (authority / "bootstrap-generation.json.gz").symlink_to(target)
        except OSError:
            pytest.skip("creating filesystem links requires platform permission")
    monkeypatch.setattr(MODULE, "PRODUCTION_RUNTIME_STATE_ROOT", authority)
    monkeypatch.setattr(MODULE.sys, "argv", [
        "bootstrap_news_projection.py", "--config", str(config),
        "--state-file", str(state), "--version-host", "not-a-version",
    ])
    # Rejection precedes network access, artifact reads, and any write.
    expected = ("NEWS_GENERATION_ARTIFACT_OUTSIDE_RUNTIME" if target else
                "sync state path" if context == "outside" else "version host")
    with pytest.raises(ValueError, match=expected):
        MODULE.main()
    assert not state.exists()
    if target:
        assert target.read_bytes() == b"original-fact-sentinel"


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


@pytest.mark.parametrize("corruption", [None, "active_generation_id", "receipt_digest", "index_count", "verified_complete", "malformed"])
def test_bootstrap_keeps_partial_replay_then_requires_verified_current(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, corruption,
) -> None:
    states = [
        {"contract_version": MODULE.NEWS_MIRROR_CONTRACT_VERSION,
         "projection_state": "REPLAYING"},
        {"contract_version": MODULE.NEWS_MIRROR_CONTRACT_VERSION,
         "projection_state": "CURRENT", "generation_id": "a" * 64,
         "snapshot_id": "b" * 64, "source_digest": "c" * 64,
         "expected_receipt_digest": "d" * 64,
         "expected_index_count": 12, "expected_detail_count": 12},
    ]
    monkeypatch.setattr(MODULE, "_sync_news", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(MODULE, "_read_news_sync_state", lambda _path: states.pop(0))
    health = {
        "status": "OK", "projection_state": "CURRENT", "verified_complete": True,
        "active_generation_id": "a" * 64, "snapshot_id": "b" * 64,
        "index_count": 12, "detail_count": 12,
        "source_digest": "c" * 64, "receipt_digest": "d" * 64,
        "missing_detail_count": 0, "invariant_violation_count": 0,
    }
    if corruption == "malformed":
        health = []
    elif corruption:
        health[corruption] = {
            "active_generation_id": "e" * 64, "receipt_digest": "f" * 64,
            "index_count": 12.0, "verified_complete": 1,
        }[corruption]
    monkeypatch.setattr(MODULE, "_get_json", lambda *_args, **_kwargs: health)
    arguments = dict(
        base_config={"local_status_url": "http://127.0.0.1:8765/api/status"},
        origin="https://abc12345-aurum-signal-room.example.workers.dev",
        token="secret", state_file=tmp_path / "state.json",
        max_cycles=2, retry_seconds=0,
        state_root=tmp_path,
    )
    if corruption:
        with pytest.raises(MODULE.PayloadContractError, match="final health"):
            MODULE.bootstrap(**arguments)
        return
    result = MODULE.bootstrap(**arguments)
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
         "projection_state": "CURRENT", "generation_id": "a" * 64,
         "snapshot_id": "b" * 64, "source_digest": "c" * 64,
         "expected_receipt_digest": "d" * 64,
         "expected_index_count": 12, "expected_detail_count": 12},
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
        state_root=tmp_path,
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
            state_root=tmp_path,
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


@pytest.mark.parametrize("source_state", ("checkpointed", "nonempty-wal", "executing-mismatch",
                                        "implicit-derivation", "omitted-transition"))
def test_bootstrap_capture_retains_input_and_never_calls_remote_or_initializer(
    tmp_path, monkeypatch, source_state,
):
    from xauusd_forecaster.forward_ledger import ForwardLedger
    from xauusd_forecaster.dashboard import news_resources as api_owner
    from xauusd_forecaster import news_projection as capture_owner

    now = datetime(2026, 9, 7, tzinfo=UTC)
    database = tmp_path / "retained-input.sqlite3"
    ledger = ForwardLedger(database, now=now - timedelta(days=1))
    body = "Retained frozen source, never a live source rewrite. " * 20
    ledger.append_news_revision({
        "source": "bea_economic_releases", "source_item_id": "frozen",
        "source_published_time": now, "collector_first_seen_time": now,
        "fetched_time": now, "headline": "frozen", "body": body,
        "content_hash": hashlib.sha256(body.encode()).hexdigest(), "cluster_id": "frozen",
    })
    epoch = str(ledger.connection.execute(
        "SELECT value FROM runtime_metadata WHERE key='FORWARD_EPOCH'"
    ).fetchone()[0])
    if source_state != "nonempty-wal":
        ledger.close()
    state_root = tmp_path / "runtime-state"
    state_root.mkdir()
    state_file = state_root / "bootstrap.json"
    old_artifact = state_root / "bootstrap-generation.json.gz"
    old_bytes = b"existing pinned bytes stay unchanged during replacement capture"
    old_artifact.write_bytes(old_bytes)
    before = MODULE._news_projection_snapshot_stat(database)
    monkeypatch.setattr(MODULE, "ForwardLedger", lambda *_a, **_k: pytest.fail("capture initialized schema"))
    monkeypatch.setattr(MODULE, "_sync_news", lambda *_a, **_k: pytest.fail("capture called remote Sync"))
    monkeypatch.setattr(MODULE, "_freeze_news_projection_generation", lambda *_a, **_k: pytest.fail("capture copied the input"))
    arguments = {
        "frozen_database": database,
        "input_identity": {"fixture": "test-owned retained consistent SQLite"},
        "source_identity": {"fixture": "current imported test source", "inputs": {
            name: hashlib.sha256(path.read_bytes()).hexdigest() for name, path in {
                "scripts/bootstrap_news_projection.py": Path(MODULE.__file__),
                "xauusd_forecaster/dashboard/news_resources.py": Path(api_owner.__file__),
                "xauusd_forecaster/news_projection.py": Path(capture_owner.__file__),
            }.items()
        }},
        "watermark": now, "epoch": epoch, "state_file": state_file,
        "state_root": state_root,
    }
    try:
        if source_state == "omitted-transition":
            arguments["source_identity"]["inputs"]["xauusd_forecaster/dashboard/news_resources.py"] = "0" * 64
            existing = capture_owner.NewsProjectionSourceCapture(
                state_root / "bootstrap-generation.capture",
                binding={"snapshot_stat": before, "input_identity": arguments["input_identity"],
                         "source_identity": arguments["source_identity"]},
                watermark=now.isoformat(), window_start=(now - timedelta(days=60)).isoformat(), epoch=epoch,
            )
            record = capture_owner.news_source_capture_record(
                {"source": "bea_economic_releases", "source_item_id": "frozen", "revision_number": 1,
                 "headline": "frozen", "body": body},
                [now.isoformat(), "bea_economic_releases", "frozen", 1],
            )
            retained = existing.advance(lambda _state: capture_owner.NewsSourceCapturePage(iter([record]), True))
            manifest_path = existing.directory / "manifest.json"
            manifest_bytes = manifest_path.read_bytes()
            monkeypatch.setattr(MODULE, "_advance_news_projection_capture",
                                lambda *_args: pytest.fail("changed source reached legacy v1 reader"))
            with pytest.raises(ValueError, match="EXECUTING_PRODUCER_MISMATCH"):
                MODULE.advance_frozen_source_capture(**arguments)
            assert retained["schema_version"] == "news-projection-source-capture-v1"
            assert manifest_path.read_bytes() == manifest_bytes
        elif source_state in {"executing-mismatch", "implicit-derivation"}:
            arguments["active_producer_identity"] = {"inputs": {
                name: hashlib.sha256(path.read_bytes()).hexdigest() for name, path in {
                    "scripts/bootstrap_news_projection.py": Path(MODULE.__file__),
                    "xauusd_forecaster/dashboard/news_resources.py": Path(api_owner.__file__),
                    "xauusd_forecaster/news_projection.py": Path(capture_owner.__file__),
                }.items()
            }}
            if source_state == "executing-mismatch":
                arguments["active_producer_identity"]["inputs"]["xauusd_forecaster/dashboard/news_resources.py"] = "0" * 64
            monkeypatch.setattr(MODULE, "_advance_news_projection_capture",
                                lambda *_args: pytest.fail("unadmitted executing producer queried"))
            with pytest.raises(ValueError, match=("EXECUTING_PRODUCER_MISMATCH" if source_state == "executing-mismatch"
                                                  else "ACTIVE_PRODUCER_MISMATCH")):
                MODULE.advance_frozen_source_capture(**arguments)
        elif source_state == "nonempty-wal":
            assert before["wal_size"] > 0
            with pytest.raises(ValueError, match="CHECKPOINTED_SNAPSHOT_REQUIRED"):
                MODULE.advance_frozen_source_capture(**arguments)
        else:
            result = MODULE.advance_frozen_source_capture(**arguments)
            assert result["state"] == "SOURCE_COMPLETE"
            assert result["source_count"] == result["item_count"] == 1
            assert result["admission"] == "CAPACITY_REVIEW_REQUIRED"
            assert not isinstance(result, MODULE.NewsProjectionGeneration)
            planned = MODULE.advance_frozen_source_capture(**arguments)
            assert planned["source_count"] == result["source_count"]
            assert planned["source_plan"]["manifest"]["expected_index_count"] == 1
            assert planned["source_plan"]["minimum_sync_cycles"] == 1
            assert MODULE.advance_frozen_source_capture(**arguments) == planned
            with pytest.raises(ValueError, match="IDENTITY_MISMATCH"):
                MODULE.advance_frozen_source_capture(**{
                    **arguments, "source_identity": {**arguments["source_identity"], "fixture": "wrong source"},
                })
        assert MODULE._news_projection_snapshot_stat(database) == before
        assert old_artifact.read_bytes() == old_bytes
        assert not state_file.exists()
    finally:
        if source_state == "nonempty-wal":
            ledger.close()


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
        lambda *args, state_root: recorded.append((args, state_root)),
    )

    with pytest.raises(MODULE.PayloadContractError, match="explicit recovery"):
        MODULE._require_recoverable_artifact(
            state_file, tmp_path / "missing-generation.json.gz",
            state_root=tmp_path,
        )

    assert recorded == [((
        state_file, None, "FROZEN_GENERATION_ARTIFACT_MISSING",
    ), tmp_path)]


@pytest.mark.parametrize("outside", [False, True])
def test_bootstrap_recovery_write_keeps_explicit_runtime_authority(tmp_path, outside):
    authority = tmp_path / "runtime"
    state = (tmp_path if outside else authority) / "bootstrap.json"
    if outside:
        with pytest.raises(ValueError, match="sync state path"):
            MODULE._record_recovery_required(state, None, "MISSING", state_root=authority)
        assert not state.exists()
        assert not authority.exists()
    else:
        MODULE._record_recovery_required(state, None, "MISSING", state_root=authority)
        assert json.loads(state.read_text(encoding="utf-8"))["projection_state"] == "RECOVERY_REQUIRED"
        assert set(authority.iterdir()) == {state}


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
