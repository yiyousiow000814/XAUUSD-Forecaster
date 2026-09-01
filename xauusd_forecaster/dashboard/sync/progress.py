"""Durable Dashboard Sync progress and status primitives."""

from __future__ import annotations

import http.client
import json
import math
import os
import re
import urllib.error
from datetime import UTC, datetime
from pathlib import Path


OPERATOR_RETRY_COMMANDS_PER_CYCLE = 10
RUNTIME_STATE_ROOT_KEY = "_runtime_state_root"


def _authorized_runtime_state_file(path: Path, config: dict) -> Path:
    """Revalidate one mutable file at the filesystem sink boundary."""
    raw_root = str(config.get(RUNTIME_STATE_ROOT_KEY) or "").strip()
    if not raw_root:
        raise ValueError("dashboard sync runtime state root is required")
    authority = os.path.realpath(raw_root)
    candidate = os.path.realpath(os.fspath(path))
    filename = os.path.basename(candidate)
    allowed_characters = frozenset(
        "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._-"
    )
    if (
        os.path.normcase(os.path.dirname(candidate))
        != os.path.normcase(authority)
        or not 6 <= len(filename) <= 128
        or not filename[0].isalnum()
        or not filename.endswith(".json")
        or any(character not in allowed_characters for character in filename)
    ):
        raise ValueError(
            f"dashboard sync state path must be one JSON file under {authority}"
        )
    return Path(candidate)


class AllTargetsRejected(RuntimeError):
    """Every configured target rejected the critical heartbeat."""

    def __init__(self, degraded_resources: list[dict]) -> None:
        super().__init__("all dashboard mirror targets rejected the heartbeat")
        self.degraded_resources = degraded_resources
        codes = {
            str(item.get("error_code"))
            for item in degraded_resources
            if item.get("error_code")
        }
        self.error_code = next(iter(codes)) if len(codes) == 1 else "ALL_TARGETS_REJECTED"


class SyncResourceResults(list[dict]):
    """Degraded resources plus bounded timing evidence for one sync cycle."""

    def __init__(
        self, degraded_resources: list[dict], resource_observations: list[dict],
    ) -> None:
        super().__init__(degraded_resources)
        self.resource_observations = resource_observations


def operator_retry_bulk_sla_seconds(
    command_count: int,
    *,
    cadence_seconds: int = 30,
    commands_per_cycle: int = OPERATOR_RETRY_COMMANDS_PER_CYCLE,
) -> int:
    """Return the product SLA for draining an already queued command batch."""
    if command_count <= 0:
        return 0
    if cadence_seconds <= 0 or commands_per_cycle <= 0:
        raise ValueError("retry cadence and batch size must be positive")
    return math.ceil(command_count / commands_per_cycle) * cadence_seconds


def write_sync_status(
    path: Path,
    config: dict,
    *,
    success: bool,
    attempts_used: int | None = None,
    error: Exception | None = None,
    degraded_resources: list[dict] | None = None,
    resource_observations: list[dict] | None = None,
) -> None:
    """Atomically publish the synchronizer's actual operational heartbeat."""
    path = _authorized_runtime_state_file(path, config)
    existing: dict = {}
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            existing = {}
    now = datetime.now(UTC).isoformat()
    if success:
        degraded_resources = degraded_resources or []
        existing.update(
            {
                "last_success": now,
                "last_attempt": now,
                "last_error": None,
                "last_error_type": None,
                "last_error_code": None,
                "attempts_used": attempts_used,
                "status": "DEGRADED" if degraded_resources else "OK",
                "degraded_resources": degraded_resources,
                "resource_observations": resource_observations or [],
            }
        )
    else:
        current_degraded = list(
            getattr(error, "degraded_resources", None) or []
        )
        current_observations = list(
            getattr(error, "resource_observations", None) or []
        )
        existing.update(
            {
                "last_attempt": now,
                "last_error": str(error)[:500] if error else "Unknown sync error",
                "last_error_type": type(error).__name__ if error else "UnknownError",
                "last_error_code": sync_error_code(error),
                "status": "ERROR",
                "degraded_resources": current_degraded,
                "resource_observations": current_observations,
            }
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(existing, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)


def sync_error_code(error: Exception | None) -> str:
    """Classify transport failures once, before they enter persisted status."""
    if error is None:
        return "UNKNOWN"
    declared = getattr(error, "error_code", None)
    if declared:
        return str(declared)
    if isinstance(error, urllib.error.HTTPError):
        if error.code == 413:
            return "PAYLOAD_LIMIT_EXCEEDED"
        if error.code in {401, 403}:
            return "AUTH_REJECTED"
        if error.code == 429:
            return "RATE_LIMITED"
        if error.code >= 500:
            return "REMOTE_UNAVAILABLE"
        return "HTTP_REJECTED"
    if isinstance(error, (
        TimeoutError,
        ConnectionError,
        http.client.RemoteDisconnected,
        urllib.error.URLError,
    )):
        return "TRANSPORT_UNAVAILABLE"
    return "UNCLASSIFIED"


def _write_runtime_signal(payload: object, config: dict) -> None:
    if not isinstance(payload, dict):
        return
    revision = str(payload.get("main_revision") or "").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        return
    target = _authorized_runtime_state_file(
        Path(config["runtime_signal_file"]), config,
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(
        json.dumps(
            {
                "main_revision": revision,
                "observed_at": datetime.now(UTC).isoformat(),
                "source": "CLOUDFLARE_MAIN_DEPLOYMENT",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    temporary.replace(target)


def _read_news_sync_state(path: Path, config: dict) -> dict:
    path = _authorized_runtime_state_file(path, config)
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
        return state if isinstance(state, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _write_news_sync_state(path: Path, config: dict, state: dict) -> None:
    path = _authorized_runtime_state_file(path, config)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)
