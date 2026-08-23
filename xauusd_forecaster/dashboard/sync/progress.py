"""Durable Dashboard Sync progress, schedule, and status contracts."""
from __future__ import annotations

import http.client
import json
import math
import re
import threading
import urllib.error
from datetime import UTC, datetime, timedelta
from pathlib import Path

MODULE_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_STATUS = MODULE_ROOT / ".local" / "forward" / "dashboard-sync-status.json"
DEFAULT_RUNTIME_SIGNAL = MODULE_ROOT / ".local" / "forward" / "remote-main-signal.json"
DEFAULT_NEWS_STATE = MODULE_ROOT / ".local" / "forward" / "dashboard-news-sync-state.json"
DEFAULT_LEARNING_STATE = MODULE_ROOT / ".local" / "forward" / "dashboard-learning-sync-state.json"
DEFAULT_LEARNING_HISTORY_STATE = MODULE_ROOT / ".local" / "forward" / "dashboard-learning-history-sync-state.json"
DEFAULT_MARKET_HISTORY_STATE = MODULE_ROOT / ".local" / "forward" / "dashboard-market-history-sync-state.json"
DEFAULT_NEWS_EVIDENCE_STATE = MODULE_ROOT / ".local" / "forward" / "dashboard-news-evidence-sync-state.json"
DEFAULT_RESOURCE_SCHEDULE_STATE = MODULE_ROOT / ".local" / "forward" / "dashboard-resource-schedule-state.json"
OPERATOR_RETRY_COMMANDS_PER_CYCLE = 10
HEAVY_RESOURCES_PER_CYCLE = 1
RESOURCE_BACKOFF_MAX_SECONDS = 3_600
_RESOURCE_SCHEDULE_LOCK = threading.Lock()

RESOURCE_POLICIES = (
    ("operator_retries", "_sync_operator_retries", 30, False),
    ("news_questions", "_sync_news_questions", 300, False),
    ("audit", "_sync_audit", 300, True),
    ("learning", "_sync_learning_summary", 300, True),
    ("learning_history", "_sync_learning_history", 300, True),
    ("market_chart", "_sync_market", 60, True),
    ("market_history", "_sync_market_history", 120, True),
    ("news", "_sync_news", 60, True),
    ("news_evidence", "_sync_news_evidence", 300, True),
)

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
    *,
    success: bool,
    attempts_used: int | None = None,
    error: Exception | None = None,
    degraded_resources: list[dict] | None = None,
    resource_observations: list[dict] | None = None,
) -> None:
    """Atomically publish the synchronizer's actual operational heartbeat."""
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


def _write_runtime_signal(payload: object) -> None:
    if not isinstance(payload, dict):
        return
    revision = str(payload.get("main_revision") or "").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        return
    target = DEFAULT_RUNTIME_SIGNAL
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


def _target_state_path(path: Path, target_name: str, *, legacy: bool) -> Path:
    if legacy or target_name == "sites":
        return path
    safe_name = "".join(
        character if character.isalnum() or character in "-_" else "-"
        for character in target_name.lower()
    ).strip("-") or "mirror"
    return path.with_name(f"{path.stem}-{safe_name}{path.suffix}")


def _read_news_sync_state(path: Path) -> dict:
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
        return state if isinstance(state, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _write_news_sync_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)


def _schedule_epoch(value: object) -> float:
    try:
        return datetime.fromisoformat(str(value)).timestamp()
    except (TypeError, ValueError):
        return 0.0


def _due_resource_policies(
    state: dict, now: datetime, *, lane: str | None = None,
) -> list[tuple]:
    resources = state.get("resources")
    if not isinstance(resources, dict):
        resources = {}
    due = []
    for policy in RESOURCE_POLICIES:
        resource = policy[0]
        resource_state = resources.get(resource)
        if not isinstance(resource_state, dict):
            resource_state = {}
        if _schedule_epoch(resource_state.get("next_run_at")) <= now.timestamp():
            due.append(policy)
    controls = [policy for policy in due if not policy[3]]
    heavy = [policy for policy in due if policy[3]][:HEAVY_RESOURCES_PER_CYCLE]
    if lane == "control":
        return controls
    if lane == "heavy":
        return heavy
    return [*controls, *heavy]


def _record_resource_schedule(
    state: dict,
    resource: str,
    cadence_seconds: int,
    *,
    now: datetime,
    success: bool,
) -> None:
    resources = state.setdefault("resources", {})
    current = resources.get(resource)
    if not isinstance(current, dict):
        current = {}
    failures = 0 if success else int(current.get("consecutive_failures") or 0) + 1
    delay = cadence_seconds if success else min(
        RESOURCE_BACKOFF_MAX_SECONDS,
        max(cadence_seconds, 30 * (2 ** min(failures - 1, 7))),
    )
    resources[resource] = {
        **current,
        "last_attempt_at": now.isoformat(),
        "last_success_at": now.isoformat() if success else current.get("last_success_at"),
        "consecutive_failures": failures,
        "next_run_at": (now + timedelta(seconds=delay)).isoformat(),
    }
    state["schema_version"] = 1
    state["updated_at"] = now.isoformat()


def _resource_schedule_path(config: dict) -> Path:
    return Path(config.get(
        "resource_schedule_state_file", DEFAULT_RESOURCE_SCHEDULE_STATE,
    ))


def _persist_resource_schedule_result(
    path: Path,
    resource: str,
    cadence_seconds: int,
    *,
    now: datetime,
    success: bool,
) -> None:
    """Merge one lane's result without overwriting another lane's progress."""
    with _RESOURCE_SCHEDULE_LOCK:
        state = _read_news_sync_state(path)
        _record_resource_schedule(
            state, resource, cadence_seconds, now=now, success=success,
        )
        _write_news_sync_state(path, state)

