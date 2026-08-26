#!/usr/bin/env python
"""Stage and verify the first atomic News CURRENT through an exact Version host."""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
import tempfile
import time
import urllib.parse
from datetime import UTC, datetime
from pathlib import Path

MODULE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MODULE_ROOT))

from scripts.run_dashboard_sync import (  # noqa: E402
    NEWS_MIRROR_CONTRACT_VERSION,
    PayloadContractError,
    RemoteInvariantViolation,
    _get_json,
    _post_json,
    _read_news_sync_state,
    _sync_news,
    _validated_sync_state_path,
    _write_news_sync_state,
)
from scripts.run_dashboard_api import (  # noqa: E402
    _build_news_projection_source_from_database,
    _read_news_projection_generation_artifact,
    _write_news_projection_generation_artifact,
)
from xauusd_forecaster.forward_ledger import ForwardLedger  # noqa: E402
from xauusd_forecaster.news_projection import NewsProjectionGeneration  # noqa: E402

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
    frozen_generation: NewsProjectionGeneration | None = None,
) -> dict:
    if not token.strip():
        raise ValueError("ingest token is missing")
    if (
        frozen_generation is None
        and not str(base_config.get("local_status_url") or "").startswith(
            "http://127.0.0.1:"
        )
    ):
        raise ValueError("bootstrap requires the local Dashboard API authority")
    config = {
        **base_config,
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
            }
            mismatches = {
                key: {"expected": expected, "actual": health.get(key)}
                for key, expected in required.items() if health.get(key) != expected
            }
            if mismatches:
                raise RuntimeError(f"first CURRENT verification failed: {mismatches}")
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
) -> NewsProjectionGeneration:
    if artifact_path.exists():
        restored = _read_news_projection_generation_artifact(artifact_path)
        if restored is None:
            raise ValueError("frozen News generation artifact is missing")
        return restored
    frozen = _freeze_news_projection_generation(source_database)
    _write_news_projection_generation_artifact(artifact_path, frozen)
    return frozen


def _record_recovery_required(
    state_file: Path,
    generation: NewsProjectionGeneration | None,
    error_code: str,
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
    _write_news_sync_state(state_file, state)


def _require_recoverable_artifact(state_file: Path, artifact_path: Path) -> None:
    state = _read_news_sync_state(state_file)
    if (
        state.get("projection_state") in {"REPLAYING", "VERIFYING"}
        and state.get("generation_id")
        and not artifact_path.exists()
    ):
        _record_recovery_required(
            state_file, None, "FROZEN_GENERATION_ARTIFACT_MISSING",
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
    state_file = _validated_sync_state_path(args.state_file)
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
        _require_recoverable_artifact(state_file, artifact_path)
    frozen_generation = (
        _load_or_freeze_news_projection_generation(
            args.source_database, artifact_path,
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
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
