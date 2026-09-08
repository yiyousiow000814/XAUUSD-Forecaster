#!/usr/bin/env python
"""Stage and verify the first atomic News CURRENT through an exact Version host."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import sys
import tempfile
import time
import urllib.parse
from datetime import UTC, datetime, timedelta
from pathlib import Path

MODULE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MODULE_ROOT))

from scripts.run_dashboard_sync import (  # noqa: E402
    NEWS_MIRROR_CONTRACT_VERSION,
    NEWS_PROJECTION_BATCHES_PER_CYCLE,
    PayloadContractError,
    RemoteInvariantViolation,
    _get_json,
    _post_json,
    _read_news_sync_state,
    _require_news_ack,
    _sync_news,
    _validated_sync_state_path,
    _write_news_sync_state,
    RUNTIME_STATE_ROOT_KEY,
)
from scripts.run_dashboard_api import (  # noqa: E402
    _advance_news_projection_capture,
    _build_news_projection_source_from_database,
    _news_projection_snapshot_stat,
    _read_news_projection_generation_artifact,
    _write_news_projection_generation_artifact,
)
from xauusd_forecaster.forward_ledger import ForwardLedger  # noqa: E402
from xauusd_forecaster.news_projection import (  # noqa: E402
    NewsProjectionGeneration, NewsProjectionRetainedGeneration, NewsProjectionSourceCapture,
)
from xauusd_forecaster.runtime_paths import PRODUCTION_RUNTIME_STATE_ROOT  # noqa: E402

VERSION_HOST = re.compile(
    r"^[a-z0-9-]+-aurum-signal-room\.[a-z0-9-]+\.workers\.dev$"
)


def _version_origin(value: str) -> str:
    parsed = urllib.parse.urlsplit(value.strip())
    host = (parsed.hostname or "").lower()
    if (
        parsed.scheme != "https"
        or parsed.path not in ("", "/")
        or parsed.query
        or parsed.fragment
        or not VERSION_HOST.fullmatch(host)
    ):
        raise ValueError("version host must be an exact aurum-signal-room workers.dev origin")
    return urllib.parse.urlunsplit(("https", host, "", "", ""))


def bootstrap(
    *, base_config: dict, origin: str, token: str, state_file: Path,
    max_cycles: int, retry_seconds: float,
    frozen_generation: NewsProjectionGeneration | NewsProjectionRetainedGeneration | None = None,
    state_root: Path,
) -> dict:
    if not token.strip():
        raise ValueError("ingest token is missing")
    if isinstance(frozen_generation, NewsProjectionRetainedGeneration):
        frozen_generation.require_target(
            index_url=origin + "/api/news-index", detail_url=origin + "/api/news-content",
            contract_version=NEWS_MIRROR_CONTRACT_VERSION,
        )
        if type(max_cycles) is not int or max_cycles < 1:
            raise ValueError("NEWS_SOURCE_CAPTURE_REPLAY_WORK_BOUND")
        frozen_generation.require_work_budget(max_cycles * NEWS_PROJECTION_BATCHES_PER_CYCLE)
    if (
        frozen_generation is None
        and not str(base_config.get("local_status_url") or "").startswith(
            "http://127.0.0.1:"
        )
    ):
        raise ValueError("bootstrap requires the local Dashboard API authority")
    config = {
        **base_config,
        RUNTIME_STATE_ROOT_KEY: str(state_root),
        "name": "candidate-news-bootstrap",
        "legacy": False,
        "token": token.strip(),
        "remote_ingest_url": origin + "/api/ingest",
        "remote_news_index_url": origin + "/api/news-index",
        "remote_news_ingest_url": origin + "/api/news-content",
        "news_state_file": str(state_file),
    }
    config.pop("targets", None)
    for cycle in range(1, max_cycles + 1):
        try:
            _sync_news({}, config, frozen_generation=frozen_generation)
        except RemoteInvariantViolation as error:
            _record_recovery_required(
                state_file, frozen_generation, error.error_code,
                state_root=state_root,
            )
            raise
        except PayloadContractError:
            raise
        except Exception:
            if cycle >= max_cycles:
                raise
            time.sleep(retry_seconds)
            continue
        state = _read_news_sync_state(state_file)
        if (
            state.get("contract_version") == NEWS_MIRROR_CONTRACT_VERSION
            and state.get("projection_state") == "CURRENT"
        ):
            health = _get_json(origin + "/api/news-index?health_check=1", config)
            required = {
                "status": "OK", "projection_state": "CURRENT",
                "verified_complete": True, "missing_detail_count": 0,
                "invariant_violation_count": 0,
                "active_generation_id": state["generation_id"],
                "snapshot_id": state["snapshot_id"],
                "source_digest": state["source_digest"],
                "receipt_digest": state["expected_receipt_digest"],
                "index_count": state["expected_index_count"],
                "detail_count": state["expected_detail_count"],
            }
            _require_news_ack(health, required, action="bootstrap final health")
            return {
                "status": "PASSED", "version_host": origin,
                "cycles": cycle,
                "generation_id": health.get("active_generation_id"),
                "snapshot_id": health.get("snapshot_id"),
                "index_count": health.get("index_count"),
                "detail_count": health.get("detail_count"),
                "source_digest": health.get("source_digest"),
                "receipt_digest": health.get("receipt_digest"),
                "missing_detail_count": health.get("missing_detail_count"),
                "invariant_violation_count": health.get("invariant_violation_count"),
            }
    raise RuntimeError("first CURRENT did not complete within the cycle bound")


def _freeze_news_projection_generation(
    source_database: Path,
) -> NewsProjectionGeneration:
    """Build Candidate source semantics from one online SQLite snapshot."""
    source_database = source_database.resolve()
    if not source_database.is_file():
        raise ValueError("authoritative source database is missing")
    with tempfile.TemporaryDirectory(prefix="xauusd-news-bootstrap-") as temp_root:
        snapshot = Path(temp_root) / "forward-evidence.sqlite3"
        source = sqlite3.connect(source_database.as_uri() + "?mode=ro", uri=True)
        destination = sqlite3.connect(snapshot)
        try:
            source.backup(destination)
        finally:
            destination.close()
            source.close()
        ledger = ForwardLedger(snapshot)
        ledger.close()
        return _build_news_projection_source_from_database(snapshot)


def _load_or_freeze_news_projection_generation(
    source_database: Path, artifact_path: Path,
    *, origin: str | None = None,
) -> NewsProjectionGeneration | NewsProjectionRetainedGeneration:
    if artifact_path.exists():
        restored = (
            _read_news_projection_generation_artifact(artifact_path, expected_target={
                "origin": _version_origin(origin), "contract_version": NEWS_MIRROR_CONTRACT_VERSION,
            }) if origin is not None else _read_news_projection_generation_artifact(artifact_path)
        )
        if restored is None:
            raise ValueError("frozen News generation artifact is missing")
        return restored
    frozen = _freeze_news_projection_generation(source_database)
    _write_news_projection_generation_artifact(artifact_path, frozen)
    return frozen


def pin_frozen_source_capture(
    *, capture: NewsProjectionSourceCapture, state_file: Path, state_root: Path,
    origin: str, max_cycles: int,
) -> NewsProjectionRetainedGeneration:
    """Explicit local admission; never recapture or overwrite an existing pin."""
    state_file = _validated_sync_state_path(state_file, state_root)
    origin = _version_origin(origin)
    if type(max_cycles) is not int or max_cycles < 1:
        raise ValueError("NEWS_SOURCE_CAPTURE_REPLAY_WORK_BOUND")
    artifact = state_file.with_name(f"{state_file.stem}-generation.json.gz")
    target = {"origin": origin, "contract_version": NEWS_MIRROR_CONTRACT_VERSION}
    maximum_batches = max_cycles * NEWS_PROJECTION_BATCHES_PER_CYCLE
    candidate = capture.open_replay_generation(maximum_batches=maximum_batches)
    # This checks both the authorized sibling location and the current complete
    # plan before an old artifact can be interpreted as the requested source.
    payload = candidate.artifact_payload(artifact)
    _require_recoverable_artifact(state_file, artifact, state_root=state_root)
    if artifact.exists():
        restored = _read_news_projection_generation_artifact(artifact, expected_target=target)
        if not isinstance(restored, NewsProjectionRetainedGeneration):
            raise ValueError("NEWS_SOURCE_CAPTURE_ARTIFACT_FORMAT_MISMATCH")
        # A stored work allowance cannot authorize a larger current invocation.
        # Content/source binding is exact; only the caller's allowance may differ.
        prior = restored.artifact_payload(artifact)
        if any(prior[key] != value for key, value in payload.items() if key != "maximum_batches"):
            raise ValueError("NEWS_SOURCE_CAPTURE_ARTIFACT_BINDING_MISMATCH")
        return restored
    _write_news_projection_generation_artifact(artifact, candidate, target=target)
    restored = _read_news_projection_generation_artifact(artifact, expected_target=target)
    if not isinstance(restored, NewsProjectionRetainedGeneration):
        raise ValueError("NEWS_SOURCE_CAPTURE_ARTIFACT_FORMAT_MISMATCH")
    return restored


def advance_frozen_source_capture(
    *, frozen_database: Path, input_identity: dict, source_identity: dict,
    watermark: datetime, epoch: str, state_file: Path, state_root: Path,
    active_producer_identity: dict | None = None,
    reader_transition: dict | None = None,
) -> dict:
    """Capture one step from the bootstrap owner's retained immutable input.

    The execution producer supplies its already-verified WAL-aware input and
    exact source identities. This function binds them; filesystem stat alone
    does not establish provenance. It never backs up a live database, rewrites
    a pinned generation, initializes schema, or invokes remote Sync. The online
    backup owner must retain a completed snapshot before calling this entrypoint.
    """
    state_file = _validated_sync_state_path(state_file, state_root)
    if not input_identity or not source_identity:
        raise ValueError("NEWS_SOURCE_CAPTURE_PROVENANCE_REQUIRED")
    if watermark.utcoffset() is None:
        raise ValueError("NEWS_SOURCE_CAPTURE_TIME_INVALID")
    from scripts import run_dashboard_api as source_owner
    from xauusd_forecaster import news_projection as capture_owner

    actual_files = {
        "scripts/bootstrap_news_projection.py": Path(__file__),
        "scripts/run_dashboard_api.py": Path(source_owner.__file__),
        "xauusd_forecaster/news_projection.py": Path(capture_owner.__file__),
    }
    executing_identity = active_producer_identity if active_producer_identity is not None else source_identity
    if (Path(source_owner._news_reader_rows.__code__.co_filename).resolve()
            != actual_files["scripts/run_dashboard_api.py"].resolve()
            or any(hashlib.sha256(path.read_bytes()).hexdigest()
                   != executing_identity.get("inputs", {}).get(name)
                   for name, path in actual_files.items())):
        raise ValueError("NEWS_SOURCE_CAPTURE_EXECUTING_PRODUCER_MISMATCH")
    if active_producer_identity is None and reader_transition is not None:
        raise ValueError("NEWS_SOURCE_CAPTURE_ACTIVE_PRODUCER_REQUIRED")
    directory = state_file.with_name(f"{state_file.stem}-generation.capture")
    capture = NewsProjectionSourceCapture(
        directory,
        binding={
            "snapshot_stat": _news_projection_snapshot_stat(frozen_database),
            "input_identity": input_identity, "source_identity": source_identity,
        },
        watermark=watermark.isoformat(),
        window_start=(watermark - timedelta(days=60)).isoformat(), epoch=epoch,
        active_producer_identity=active_producer_identity,
    )
    if reader_transition is not None:
        return capture.derive_reader_segment(**reader_transition)
    state = capture.read()
    capture._require_active_reader(state)
    if state["state"] == "SOURCE_COMPLETE":
        # Source stepping and final canonical planning have independent bounded
        # turns. Neither turn can invoke prepare or replace the pinned artifact.
        return capture.finalize_plan(producer_identity=active_producer_identity)
    return _advance_news_projection_capture(frozen_database, capture)


def _record_recovery_required(
    state_file: Path,
    generation: NewsProjectionGeneration | None,
    error_code: str,
    *, state_root: Path,
) -> None:
    state = _read_news_sync_state(state_file)
    manifest = generation.manifest if generation is not None else {}
    generation_id = str(state.get("generation_id") or manifest.get("generation_id") or "")
    state.update({
        "contract_version": NEWS_MIRROR_CONTRACT_VERSION,
        "projection_state": "RECOVERY_REQUIRED",
        "generation_id": generation_id,
        "snapshot_id": str(state.get("snapshot_id") or manifest.get("snapshot_id") or ""),
        "recovery": {
            "error_code": error_code,
            "generation_id": generation_id,
            "recorded_at": datetime.now(UTC).isoformat(),
        },
        "updated_at": datetime.now(UTC).isoformat(),
    })
    _write_news_sync_state(state_file, state, state_root=state_root)


def _require_recoverable_artifact(state_file: Path, artifact_path: Path, *, state_root: Path) -> None:
    state = _read_news_sync_state(state_file)
    if (
        state.get("projection_state") in {"REPLAYING", "VERIFYING"}
        and state.get("generation_id")
        and not artifact_path.exists()
    ):
        _record_recovery_required(
            state_file, None, "FROZEN_GENERATION_ARTIFACT_MISSING",
            state_root=state_root,
        )
        raise PayloadContractError(
            "pinned News generation artifact is missing; explicit recovery is required"
        )


def abandon_recovery_generation(
    *, config: dict, origin: str, state_file: Path, artifact_path: Path,
    generation_id: str,
) -> dict:
    state = _read_news_sync_state(state_file)
    recovery = state.get("recovery")
    if (
        state.get("projection_state") != "RECOVERY_REQUIRED"
        or not isinstance(recovery, dict)
        or state.get("generation_id") != generation_id
        or recovery.get("generation_id") != generation_id
    ):
        raise PayloadContractError("News recovery identity is not authoritative")
    health_url = origin + "/api/news-index?health_check=1"
    health = _get_json(health_url, config, allow_error_payload=True)
    staging = health.get("staging")
    if not isinstance(staging, dict) or staging.get("generation_id") != generation_id:
        raise PayloadContractError("remote staging identity does not match recovery")
    _post_json(
        origin + "/api/news-index",
        json.dumps({"action": "abandon", "generation_id": generation_id},
                   separators=(",", ":")).encode(),
        config,
    )
    verified = _get_json(health_url, config, allow_error_payload=True)
    if isinstance(verified.get("staging"), dict) and (
        verified["staging"].get("generation_id") == generation_id
    ):
        raise RuntimeError("rejected News staging still exists after recovery")
    receipt_path = state_file.with_name(f"{state_file.stem}-recovery.json")
    receipt = {
        "status": "PASSED", "action": "ABANDON_REJECTED_STAGING",
        "generation_id": generation_id,
        "error_code": recovery.get("error_code"),
        "recovered_at": datetime.now(UTC).isoformat(),
    }
    temporary = receipt_path.with_suffix(receipt_path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(receipt, ensure_ascii=False, sort_keys=True), encoding="utf-8",
    )
    temporary.replace(receipt_path)
    state_file.unlink(missing_ok=True)
    artifact_path.unlink(missing_ok=True)
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--version-host", required=True)
    parser.add_argument("--state-file", type=Path, required=True)
    parser.add_argument("--source-database", type=Path)
    parser.add_argument("--abandon-recovery-generation")
    parser.add_argument("--token-env", default="CLOUDFLARE_INGEST_TOKEN")
    parser.add_argument("--max-cycles", type=int, default=1_000)
    parser.add_argument("--retry-seconds", type=float, default=2.0)
    args = parser.parse_args()
    if args.max_cycles < 1 or args.retry_seconds < 0:
        parser.error("cycle and retry bounds are invalid")
    config = json.loads(args.config.read_text(encoding="utf-8"))
    state_file = _validated_sync_state_path(args.state_file, PRODUCTION_RUNTIME_STATE_ROOT)
    artifact_path = state_file.with_name(
        f"{state_file.stem}-generation.json.gz"
    )
    origin = _version_origin(args.version_host)
    token = os.environ.get(args.token_env, "")
    remote_config = {
        **config, "token": token,
        "remote_ingest_url": origin + "/api/ingest",
    }
    if args.abandon_recovery_generation:
        if not token.strip():
            raise ValueError("ingest token is missing")
        result = abandon_recovery_generation(
            config=remote_config, origin=origin, state_file=state_file,
            artifact_path=artifact_path,
            generation_id=args.abandon_recovery_generation,
        )
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    if args.source_database:
        _require_recoverable_artifact(state_file, artifact_path, state_root=PRODUCTION_RUNTIME_STATE_ROOT)
    frozen_generation = (
        _load_or_freeze_news_projection_generation(
            args.source_database, artifact_path, origin=origin,
        )
        if args.source_database else None
    )
    result = bootstrap(
        base_config=config,
        origin=origin,
        token=token,
        state_file=state_file,
        max_cycles=args.max_cycles,
        retry_seconds=args.retry_seconds,
        frozen_generation=frozen_generation,
        state_root=PRODUCTION_RUNTIME_STATE_ROOT,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
