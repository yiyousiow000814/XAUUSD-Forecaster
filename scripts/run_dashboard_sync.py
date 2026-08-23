#!/usr/bin/env python
"""Mirror the read-only dashboard snapshot to independent remote dashboards."""
from __future__ import annotations
import argparse
import copy
import hashlib
import http.client
import json
import math
import os
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime, timedelta
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
MODULE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MODULE_ROOT))
DEFAULT_CONFIG = MODULE_ROOT / ".local" / "forward" / "dashboard-sync.json"
DEFAULT_STATUS = MODULE_ROOT / ".local" / "forward" / "dashboard-sync-status.json"
DEFAULT_RUNTIME_SIGNAL = (
    MODULE_ROOT / ".local" / "forward" / "remote-main-signal.json"
)
DEFAULT_NEWS_STATE = (
    MODULE_ROOT / ".local" / "forward" / "dashboard-news-sync-state.json"
)
DEFAULT_LEARNING_STATE = (
    MODULE_ROOT / ".local" / "forward" / "dashboard-learning-sync-state.json"
)
DEFAULT_LEARNING_HISTORY_STATE = (
    MODULE_ROOT / ".local" / "forward" / "dashboard-learning-history-sync-state.json"
)
DEFAULT_MARKET_HISTORY_STATE = (
    MODULE_ROOT / ".local" / "forward" / "dashboard-market-history-sync-state.json"
)
DEFAULT_NEWS_EVIDENCE_STATE = (
    MODULE_ROOT / ".local" / "forward" / "dashboard-news-evidence-sync-state.json"
)
DEFAULT_RESOURCE_SCHEDULE_STATE = (
    MODULE_ROOT / ".local" / "forward" / "dashboard-resource-schedule-state.json"
)
LOCAL_STATUS_TIMEOUT_SECONDS = 20
REMOTE_POST_TIMEOUT_SECONDS = 30
NEWS_PROJECTION_BATCHES_PER_CYCLE = 4
NEWS_EVIDENCE_WRITE_BATCH_ITEMS = 8

NEWS_EVIDENCE_BATCH_LIMIT_BYTES = 80_000
NEWS_EVIDENCE_PAGES_PER_CYCLE = 1
NEWS_EVIDENCE_CLEANUP_STEPS_PER_CYCLE = 8
MARKET_HISTORY_PAGES_PER_CYCLE = 1
MARKET_OVERVIEWS_PER_CYCLE = 2
OPERATOR_RETRY_COMMANDS_PER_CYCLE = 10
HEAVY_RESOURCES_PER_CYCLE = 1
RESOURCE_BACKOFF_MAX_SECONDS = 3_600
NEWS_READER_WINDOW_DAYS = 60
NEWS_EVIDENCE_CONTRACT_VERSION = "news-evidence-paged-v2"
MARKET_HISTORY_CONTRACT_VERSION = "market-history-d1-v2"
MARKET_HISTORY_BATCH_LIMIT_BYTES = 350_000
MARKET_HISTORY_BATCH_ITEMS = 25
MARKET_HISTORY_OVERLAP_SECONDS = 2 * 3_600
LEARNING_HISTORY_FULL_REFRESH_SECONDS = 86_400

_RESOURCE_SCHEDULE_LOCK = threading.Lock()

from xauusd_forecaster.dashboard.resource_contracts import (
    AUDIT_DETAIL_LIMIT_BYTES,
    AUDIT_FIRST_PAGE_LIMIT_BYTES,
    LEARNING_HISTORY_BATCH_LIMIT_BYTES,
    LEARNING_HISTORY_CONTRACT_VERSION,
    LEARNING_OVERVIEW_CURVE_POINTS,
    LEARNING_OVERVIEW_GROUPS_PER_IDENTITY,
    LEARNING_SUMMARY_CURVE_POINTS,
    LEARNING_SUMMARY_EXECUTION_RESULTS,
    LEARNING_SUMMARY_GROUPS_PER_IDENTITY,
    MARKET_CHART_SNAPSHOT_LIMIT_BYTES,
    MARKET_DECISION_FIELDS,
    MARKET_OVERVIEW_DECISIONS_PER_SERIES,
    NEWS_DETAIL_BATCH_ITEMS,
    NEWS_DETAIL_BATCH_LIMIT_BYTES,
    NEWS_INDEX_BATCH_LIMIT_BYTES,
    NEWS_INDEX_FIELDS,
    NEWS_MIRROR_CONTRACT_VERSION,
    NEWS_WRITE_BATCH_ITEMS,
    REMOTE_DAILY_BRIEF_LIMIT,
    REMOTE_DECISION_LIMIT,
    REMOTE_MARKET_CANDLE_LIMIT,
    REMOTE_MARKET_DECISION_LIMIT,
    REMOTE_MARKET_DENSE_LIMITS,
    REMOTE_MARKET_OVERVIEW_LIMITS,
    REMOTE_NEWS_LIMIT,
    REMOTE_PAYLOAD_LIMIT_BYTES,
    PayloadContractError,
    _bounded_audit_snapshot,
    _bounded_item_batches,
    _decision_key,
    _downsample_market_overview,
    _encoded_snapshot,
    _epoch,
    _json_hash,
    _learning_overview_records,
    _learning_record,
    _learning_summary,
    _stable_news_key,
    _update_decision_overviews,
    _version_metric,
    _visual_curve_overview,
    _visual_decision_overview,
    _visual_version_overview,
    audit_briefs_snapshot,
    audit_decisions_snapshot,
    audit_snapshot,
    audit_stories_snapshot,
    compact_market_chart,
    learning_history_batches,
    learning_history_records,
    learning_snapshot,
    market_chart_snapshot,
    news_detail_batches,
    news_index_batches,
    news_mirror_parts,
    news_withdrawal_keys,
    remote_snapshot,
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


def _projection_producer_revision() -> str:
    try:
        revision = subprocess.check_output(
            ["git", "-C", str(MODULE_ROOT), "rev-parse", "HEAD"],
            text=True, timeout=5,
        ).strip().lower()
    except (OSError, subprocess.SubprocessError):
        return ""
    return revision if re.fullmatch(r"[0-9a-f]{40}", revision) else ""


class RemoteInvariantViolation(RuntimeError):
    """A remote resource answered but its persisted state is contradictory."""

    def __init__(self, payload: dict) -> None:
        self.error_code = str(
            payload.get("error_code") or "REMOTE_STATE_INVARIANT_VIOLATION"
        )
        checks = payload.get("checks")
        self.evidence = {
            "violation_count": int(payload.get("violation_count") or 0),
            "checks": checks[:12] if isinstance(checks, list) else [],
        }
        if isinstance(payload.get("contradictions"), dict):
            self.evidence["contradictions"] = dict(
                list(payload["contradictions"].items())[:12]
            )
        if payload.get("staging_generation_id"):
            self.evidence["staging_generation_id"] = str(
                payload["staging_generation_id"]
            )[:64]
        super().__init__(
            f"remote invariant check failed: {self.error_code} "
            f"({self.evidence['violation_count']} violations)"
        )


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


def _remote_request_headers(url: str, config: dict) -> dict[str, str]:
    headers = {
        "Authorization": f"Bearer {config['token']}",
        "User-Agent": "AurumSignalRoomMirror/1.0",
    }
    sites_bypass_token = os.environ.get("SITES_BYPASS_TOKEN", "").strip()
    remote_host = (urllib.parse.urlsplit(url).hostname or "").lower()
    if sites_bypass_token and remote_host.endswith(".chatgpt.site"):
        headers["OAI-Sites-Authorization"] = f"Bearer {sites_bypass_token}"
    return headers


def _post_json(url: str, payload: bytes, config: dict) -> dict:
    headers = {
        **_remote_request_headers(url, config),
        "Content-Type": "application/json",
    }
    request = urllib.request.Request(url, data=payload, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(
            request, timeout=REMOTE_POST_TIMEOUT_SECONDS
        ) as response:
            if response.status != 200:
                raise RuntimeError(f"dashboard sync returned HTTP {response.status}")
            body = response.read()
    except urllib.error.HTTPError as error:
        try:
            failure = json.loads(error.read())
        except (TypeError, ValueError):
            raise error
        if isinstance(failure, dict) and failure.get("error_code"):
            raise RemoteInvariantViolation(failure) from error
        raise error
    try:
        result = json.loads(body) if body else {}
    except (TypeError, ValueError):
        result = {}
    _write_runtime_signal(result)
    return result if isinstance(result, dict) else {}


def _get_json(
    url: str,
    config: dict,
    *,
    timeout_seconds: float = REMOTE_POST_TIMEOUT_SECONDS,
) -> dict:
    if (
        not isinstance(timeout_seconds, (int, float))
        or isinstance(timeout_seconds, bool)
        or not math.isfinite(float(timeout_seconds))
        or float(timeout_seconds) <= 0
    ):
        raise ValueError("dashboard GET timeout is invalid")
    request = urllib.request.Request(url, headers={
        **_remote_request_headers(url, config),
        "Accept": "application/json",
    })
    try:
        with urllib.request.urlopen(
            request, timeout=min(REMOTE_POST_TIMEOUT_SECONDS, float(timeout_seconds)),
        ) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as error:
        try:
            payload = json.loads(error.read())
        except (TypeError, ValueError):
            raise error
        if isinstance(payload, dict) and payload.get("error_code"):
            raise RemoteInvariantViolation(payload) from error
        raise error


def _assistant_worker_id() -> str:
    worker_suffix = re.sub(
        r"[^A-Za-z0-9._:-]", "-",
        os.environ.get("COMPUTERNAME", "windows-sync"),
    )[:64]
    return f"dashboard-sync:{worker_suffix}"


def _operator_retry_worker_url(config: dict) -> str:
    remote = urllib.parse.urlsplit(str(config["remote_ingest_url"]))
    return urllib.parse.urlunsplit((
        remote.scheme, remote.netloc, "/api/operator-retry-worker", "", "",
    ))


def _local_retry_url(config: dict, path: str) -> str:
    local = urllib.parse.urlsplit(str(config["local_status_url"]))
    return urllib.parse.urlunsplit((local.scheme, local.netloc, path, "", ""))


def _post_local_json(url: str, payload: dict) -> dict:
    token = os.environ.get("DASHBOARD_OPERATOR_BRIDGE_TOKEN", "").strip()
    if not 32 <= len(token) <= 512:
        raise RuntimeError("dashboard operator bridge credential is not configured")
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "User-Agent": "AurumOperatorBridge/1.0",
            "X-Aurum-Operator-Bridge-Token": token,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=LOCAL_STATUS_TIMEOUT_SECONDS) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as error:
        if error.code != 207:
            raise
        return json.loads(error.read())


def _get_local_json(url: str) -> dict:
    token = os.environ.get("DASHBOARD_OPERATOR_BRIDGE_TOKEN", "").strip()
    if not 32 <= len(token) <= 512:
        raise RuntimeError("dashboard operator bridge credential is not configured")
    request = urllib.request.Request(
        url, headers={
            "Accept": "application/json",
            "User-Agent": "AurumOperatorBridge/1.0",
            "X-Aurum-Operator-Bridge-Token": token,
        },
    )
    with urllib.request.urlopen(request, timeout=LOCAL_STATUS_TIMEOUT_SECONDS) as response:
        return json.loads(response.read())


def _sync_operator_retries(_local_payload: dict, config: dict) -> None:
    local_jobs = _get_local_json(_local_retry_url(config, "/api/retry-jobs"))
    worker_url = _operator_retry_worker_url(config)
    _post_json(
        worker_url,
        json.dumps({"action": "SYNC_JOBS", "items": local_jobs.get("items", [])}).encode(),
        config,
    )
    worker_id = _assistant_worker_id()
    processed = False
    for _ in range(OPERATOR_RETRY_COMMANDS_PER_CYCLE):
        command = _get_json(
            f"{worker_url}?{urllib.parse.urlencode({'worker_id': worker_id})}", config,
        ).get("item")
        if not isinstance(command, dict):
            break
        local_result = _post_local_json(
            _local_retry_url(config, "/api/retry-overrides"),
            {
                "operator_id": command.get("operator_id"),
                "items": [{
                    "request_id": command.get("request_id"),
                    "job_id": command.get("job_id"),
                    "mode": command.get("mode"),
                    "reason": command.get("reason"),
                    "expected_state": command.get("expected_state"),
                    "expected_available_at": command.get("expected_available_at"),
                    "requested_available_at": command.get("requested_available_at"),
                }],
            },
        )
        result = (local_result.get("results") or [{}])[0]
        status = str(result.get("status") or "REJECTED")
        _post_json(
            worker_url,
            json.dumps({
                "action": "FINISH",
                "request_id": command.get("request_id"),
                "lease_token": command.get("lease_token"),
                "status": status,
                "result": result,
            }).encode(),
            config,
        )
        processed = True
    if processed:
        # A command result and the scheduler mirror advance in the same bounded
        # sync pass; the browser need not wait for an unrelated later cycle.
        refreshed_jobs = _get_local_json(_local_retry_url(config, "/api/retry-jobs"))
        _post_json(
            worker_url,
            json.dumps({
                "action": "SYNC_JOBS", "items": refreshed_jobs.get("items", []),
            }).encode(),
            config,
        )


def _sync_assistant_chat(_local_payload: dict, _config: dict):
    """Assistant is intentionally paused until an API model is configured."""
    return {"status": "PAUSED_NO_MODEL"}


def _sync_news_questions(_local_payload: dict, _config: dict) -> None:
    # Private Assistant Q&A, titles, compaction, and memory indexing are paused
    # together. News annotation, impact, and Daily Brief use separate workers.
    return None

def _target_state_path(path: Path, target_name: str, *, legacy: bool) -> Path:
    if legacy or target_name == "sites":
        return path
    safe_name = "".join(
        character if character.isalnum() or character in "-_" else "-"
        for character in target_name.lower()
    ).strip("-") or "mirror"
    return path.with_name(f"{path.stem}-{safe_name}{path.suffix}")


def configured_targets(config: dict) -> list[dict]:
    """Resolve legacy or multi-target mirror configuration without sharing state."""
    declared = config.get("targets")
    if not isinstance(declared, list):
        declared = [{**config, "name": config.get("name", "sites"), "legacy": True}]
        cloudflare_url = os.environ.get("CLOUDFLARE_INGEST_URL", "").strip()
        cloudflare_token = os.environ.get("CLOUDFLARE_INGEST_TOKEN", "").strip()
        if cloudflare_url or cloudflare_token:
            declared.append({
                "name": "cloudflare",
                "remote_ingest_url": cloudflare_url,
                "token": cloudflare_token,
                "legacy": False,
            })

    targets = []
    for index, target in enumerate(declared):
        if not isinstance(target, dict):
            raise ValueError(f"dashboard target {index + 1} must be an object")
        if target.get("enabled") is False:
            continue
        name = str(target.get("name") or f"mirror-{index + 1}").strip()
        remote_url = str(target.get("remote_ingest_url") or "").strip()
        token_env = str(target.get("token_env") or "").strip()
        token = str(
            target.get("token") or (os.environ.get(token_env) if token_env else "") or ""
        ).strip()
        if not remote_url.startswith("https://") or not token:
            raise ValueError(f"dashboard target {name!r} needs https URL and token")
        scoped = {
            **config,
            **target,
            "name": name,
            "token": token,
            "legacy": bool(target.get("legacy", False)),
        }
        scoped.pop("targets", None)
        scoped["learning_state_file"] = str(_target_state_path(
            Path(target.get(
                "learning_state_file",
                config.get("learning_state_file", DEFAULT_LEARNING_STATE),
            )),
            name,
            legacy=scoped["legacy"],
        ))
        scoped["news_state_file"] = str(_target_state_path(
            Path(target.get(
                "news_state_file",
                config.get("news_state_file", DEFAULT_NEWS_STATE),
            )),
            name,
            legacy=scoped["legacy"],
        ))
        scoped["market_history_state_file"] = str(_target_state_path(
            Path(target.get(
                "market_history_state_file",
                config.get("market_history_state_file", DEFAULT_MARKET_HISTORY_STATE),
            )),
            name,
            legacy=scoped["legacy"],
        ))
        scoped["learning_history_state_file"] = str(_target_state_path(
            Path(target.get(
                "learning_history_state_file",
                config.get("learning_history_state_file", DEFAULT_LEARNING_HISTORY_STATE),
            )),
            name,
            legacy=scoped["legacy"],
        ))
        scoped["news_evidence_state_file"] = str(_target_state_path(
            Path(target.get(
                "news_evidence_state_file",
                config.get("news_evidence_state_file", DEFAULT_NEWS_EVIDENCE_STATE),
            )),
            name,
            legacy=scoped["legacy"],
        ))
        scoped["resource_schedule_state_file"] = str(_target_state_path(
            Path(target.get(
                "resource_schedule_state_file",
                config.get(
                    "resource_schedule_state_file",
                    DEFAULT_RESOURCE_SCHEDULE_STATE,
                ),
            )),
            name,
            legacy=scoped["legacy"],
        ))
        targets.append(scoped)
    if not targets:
        raise ValueError("dashboard sync has no configured targets")
    return targets


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


def _learning_payload(local_payload: dict, config: dict) -> dict:
    if not local_payload and config.get("local_status_url"):
        return _read_local_resource(config, "/api/learning")
    return local_payload


def _sync_learning_history(local_payload: dict, config: dict) -> None:
    local_payload = _learning_payload(local_payload, config)
    remote_host = urllib.parse.urlsplit(config["remote_ingest_url"]).hostname or ""
    if remote_host.lower().endswith(".chatgpt.site"):
        return
    history_url = config.get("remote_learning_history_url") or (
        config["remote_ingest_url"].rsplit("/", 1)[0] + "/learning-history"
    )
    history_state_path = Path(config.get(
        "learning_history_state_file", DEFAULT_LEARNING_HISTORY_STATE,
    ))
    history_state = _read_news_sync_state(history_state_path)
    hashes = history_state.get("hashes", {})
    if not isinstance(hashes, dict):
        hashes = {}
    now = datetime.now(UTC)
    last_full = history_state.get("last_full_sync")
    refresh_in_progress = bool(history_state.get("full_refresh_started_at"))
    try:
        full_refresh_due = (
            history_state.get("contract_version") != LEARNING_HISTORY_CONTRACT_VERSION
            or not last_full
            or (now - datetime.fromisoformat(str(last_full))).total_seconds()
            >= LEARNING_HISTORY_FULL_REFRESH_SECONDS
        )
    except (TypeError, ValueError):
        full_refresh_due = True
    if full_refresh_due and not refresh_in_progress:
        hashes = {}
        history_state["full_refresh_started_at"] = now.isoformat()

    records = learning_history_records(local_payload)
    pending = [
        row for row in records
        if hashes.get(f"{row['resource']}\0{row['record_key']}")
        != row["payload_hash"]
    ]
    batches = learning_history_batches(pending)
    if batches:
        batch = batches[0]
        encoded = json.dumps(
            {"records": batch}, ensure_ascii=False, allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")
        _post_json(history_url, encoded, config)
        for row in batch:
            hashes[f"{row['resource']}\0{row['record_key']}"] = row["payload_hash"]
        _write_news_sync_state(history_state_path, {
            "contract_version": LEARNING_HISTORY_CONTRACT_VERSION,
            "hashes": hashes,
            "last_full_sync": last_full,
            "full_refresh_started_at": history_state.get("full_refresh_started_at"),
            "pending_record_count": len(pending) - len(batch),
            "last_progress": now.isoformat(),
        })
        return

    _write_news_sync_state(history_state_path, {
        "contract_version": LEARNING_HISTORY_CONTRACT_VERSION,
        "hashes": hashes,
        "last_full_sync": now.isoformat() if full_refresh_due else last_full,
        "last_success": now.isoformat(),
        "pending_record_count": 0,
    })


def _sync_learning_summary(local_payload: dict, config: dict) -> None:
    local_payload = _learning_payload(local_payload, config)
    learning_url = config.get("remote_learning_url") or (
        config["remote_ingest_url"].rsplit("/", 1)[0] + "/learning"
    )
    learning_state_path = Path(
        config.get("learning_state_file", DEFAULT_LEARNING_STATE)
    )
    learning_state = _read_news_sync_state(learning_state_path)
    learning_payload = learning_snapshot(local_payload)
    learning_hash = hashlib.sha256(learning_payload).hexdigest()
    if learning_state.get("payload_hash") != learning_hash:
        _post_json(learning_url, learning_payload, config)
        _write_news_sync_state(learning_state_path, {
            "payload_hash": learning_hash,
            "last_success": datetime.now(UTC).isoformat(),
        })


def _sync_learning(local_payload: dict, config: dict) -> None:
    """Compatibility helper; the scheduler owns these as separate resources."""
    payload = _learning_payload(local_payload, config)
    _sync_learning_history(payload, config)
    _sync_learning_summary(payload, config)


def _sync_market(local_payload: dict, config: dict) -> None:
    if not local_payload and config.get("local_status_url"):
        local_payload = _read_local_resource(config, "/api/market-chart")
    market_url = config.get("remote_market_chart_url") or (
        config["remote_ingest_url"].rsplit("/", 1)[0] + "/market-chart"
    )
    snapshot = market_chart_snapshot(local_payload)
    _post_json(market_url, snapshot, config)
    if (urllib.parse.urlsplit(config["remote_ingest_url"]).hostname or "").lower().endswith(
        ".chatgpt.site"
    ):
        return
    market = json.loads(snapshot)
    overview_candles = market.get("overview_candles") or _downsample_market_overview(
        market.get("candles", []), REMOTE_MARKET_OVERVIEW_LIMITS[0],
    )
    history_url = config.get("remote_market_history_url") or (
        config["remote_ingest_url"].rsplit("/", 1)[0] + "/market-history"
    )
    overview = {
        "candles": overview_candles,
        "source_candle_count": int(market.get("source_candle_count") or len(overview_candles)),
        "history_start": market.get("history_start"),
        "history_end": market.get("history_end"),
    }
    _post_json(history_url, json.dumps(
        {"overview": overview}, ensure_ascii=False, allow_nan=False,
        separators=(",", ":"),
    ).encode("utf-8"), config)


def _market_history_payloads(candles: list[dict], decisions: list[dict]) -> list[bytes]:
    """Keep D1 ingest requests bounded while preserving every row."""
    compacted = compact_market_chart({
        "market_chart": {
            "candles": candles, "overview_candles": [], "decisions": decisions,
        },
    }, dense_limit=max(1, len(decisions)), overview_limit=1)
    candles = compacted["candles"]
    decisions = compacted["decisions"]
    payloads = []
    for key, rows in (("candles", candles), ("decisions", decisions)):
        current: list[dict] = []
        for row in rows:
            candidate = [*current, row]
            encoded = json.dumps(
                {key: candidate}, ensure_ascii=False, allow_nan=False,
                separators=(",", ":"),
            ).encode("utf-8")
            if current and (
                len(candidate) > MARKET_HISTORY_BATCH_ITEMS
                or len(encoded) > MARKET_HISTORY_BATCH_LIMIT_BYTES
            ):
                payloads.append(json.dumps(
                    {key: current}, ensure_ascii=False, allow_nan=False,
                    separators=(",", ":"),
                ).encode("utf-8"))
                current = [row]
            else:
                current = candidate
        if current:
            payloads.append(json.dumps(
                {key: current}, ensure_ascii=False, allow_nan=False,
                separators=(",", ":"),
            ).encode("utf-8"))
    return payloads


def _market_decision_overview_payload(summary: dict) -> bytes:
    """Bound a replace-in-place overview without splitting its D1 row."""
    source = summary.get("decisions", [])
    decisions = [row for row in source if isinstance(row, dict)]
    limit = min(len(decisions), MARKET_OVERVIEW_DECISIONS_PER_SERIES)
    while True:
        bounded = {
            **summary,
            "decisions": _visual_decision_overview(decisions, limit),
        }
        bounded["decision_count"] = len(bounded["decisions"])
        bounded["decision_downsampled"] = (
            int(bounded.get("source_decision_count") or 0)
            > bounded["decision_count"]
        )
        encoded = json.dumps(
            {"decision_overviews": [bounded]}, ensure_ascii=False,
            allow_nan=False, separators=(",", ":"),
        ).encode("utf-8")
        if len(encoded) <= MARKET_HISTORY_BATCH_LIMIT_BYTES:
            return encoded
        if limit <= 1:
            raise PayloadContractError(
                "market decision overview row exceeds payload limit"
            )
        limit = max(1, limit // 2)


def _local_market_history_url(config: dict, after: str | None) -> str:
    status_url = urllib.parse.urlsplit(config["local_status_url"])
    query = {"limit": "500"}
    if after:
        query["after"] = after
    return urllib.parse.urlunsplit((
        status_url.scheme, status_url.netloc, "/api/market-history",
        urllib.parse.urlencode(query), "",
    ))


def _overlap_cursor(cursor: str | None) -> str | None:
    if not cursor:
        return None
    try:
        value = datetime.fromisoformat(cursor.replace("Z", "+00:00"))
        return datetime.fromtimestamp(
            value.timestamp() - MARKET_HISTORY_OVERLAP_SECONDS, UTC,
        ).isoformat()
    except (TypeError, ValueError):
        return None


def _sync_market_history(config: dict) -> None:
    remote_host = urllib.parse.urlsplit(config["remote_ingest_url"]).hostname or ""
    if remote_host.lower().endswith(".chatgpt.site"):
        return  # Sites remains on the bounded compatibility snapshot; D1 is Cloudflare-only.
    remote_url = config.get("remote_market_history_url") or (
        config["remote_ingest_url"].rsplit("/", 1)[0] + "/market-history"
    )
    state_path = Path(config.get(
        "market_history_state_file", DEFAULT_MARKET_HISTORY_STATE,
    ))
    state = _read_news_sync_state(state_path)
    cursor = (
        state.get("cursor")
        if state.get("contract_version") == MARKET_HISTORY_CONTRACT_VERSION
        else None
    )
    decision_overviews = (
        state.get("decision_overviews", {})
        if state.get("contract_version") == MARKET_HISTORY_CONTRACT_VERSION
        else {}
    )
    new_after = cursor
    after = _overlap_cursor(cursor)
    pages = 0
    while pages < MARKET_HISTORY_PAGES_PER_CYCLE:
        with urllib.request.urlopen(
            _local_market_history_url(config, after),
            timeout=LOCAL_STATUS_TIMEOUT_SECONDS,
        ) as response:
            page = json.loads(response.read())
        candles = page.get("candles") if isinstance(page.get("candles"), list) else []
        decisions = page.get("decisions") if isinstance(page.get("decisions"), list) else []
        decision_overviews = _update_decision_overviews(
            decision_overviews, decisions, new_after,
        )
        for payload in _market_history_payloads(candles, decisions):
            _post_json(remote_url, payload, config)
        next_cursor = page.get("next_cursor")
        if next_cursor:
            cursor = str(next_cursor)
            _write_news_sync_state(state_path, {
                "contract_version": MARKET_HISTORY_CONTRACT_VERSION,
                "cursor": cursor,
                "decision_overviews": decision_overviews,
                "last_success": datetime.now(UTC).isoformat(),
            })
        pages += 1
        if not page.get("has_more") or not next_cursor or next_cursor == after:
            break
        after = str(next_cursor)
    summaries = sorted(decision_overviews.items())
    overview_offset = int(state.get("overview_offset") or 0)
    selected_summaries = []
    if summaries:
        for index in range(min(MARKET_OVERVIEWS_PER_CYCLE, len(summaries))):
            selected_summaries.append(
                summaries[(overview_offset + index) % len(summaries)][1]
            )
        overview_offset = (
            overview_offset + len(selected_summaries)
        ) % len(summaries)
    for summary in selected_summaries:
        _post_json(
            remote_url, _market_decision_overview_payload(summary), config,
        )
    _write_news_sync_state(state_path, {
        "contract_version": MARKET_HISTORY_CONTRACT_VERSION,
        "cursor": cursor,
        "decision_overviews": decision_overviews,
        "overview_offset": overview_offset,
        "has_more": bool(page.get("has_more")),
        "last_success": datetime.now(UTC).isoformat(),
    })


def _local_news_archive_url(
    config: dict, *, mode: str, snapshot_id: str | None = None,
    kind: str | None = None, offset: int | None = None,
    activated_snapshot_id: str | None = None,
) -> str:
    status_url = urllib.parse.urlsplit(config["local_status_url"])
    query = {"mode": mode}
    if snapshot_id:
        query["snapshot_id"] = snapshot_id
    if kind:
        query["kind"] = kind
    if offset is not None:
        query["offset"] = str(offset)
    if activated_snapshot_id:
        query["activated_snapshot_id"] = activated_snapshot_id
    return urllib.parse.urlunsplit((
        status_url.scheme, status_url.netloc, "/api/news-archive",
        urllib.parse.urlencode(query), "",
    ))


def _verify_news_projection_state(
    news_index_url: str, config: dict, manifest: dict,
) -> dict:
    payload = _get_json(news_index_url + "?health_check=1", config)
    expected = {
        "status": "OK",
        "projection_state": "CURRENT",
        "verified_complete": True,
        "active_generation_id": manifest["generation_id"],
        "snapshot_id": manifest["snapshot_id"],
        "source_digest": manifest["source_digest"],
        "receipt_digest": manifest["expected_receipt_digest"],
        "index_count": manifest["expected_index_count"],
        "detail_count": manifest["expected_detail_count"],
        "missing_detail_count": 0,
        "invariant_violation_count": 0,
    }
    contradictions = {
        key: {"expected": value, "received": payload.get(key)}
        for key, value in expected.items() if payload.get(key) != value
    }
    if contradictions:
        raise RemoteInvariantViolation({
            "status": "ERROR", "error_code": "NEWS_PROJECTION_HEALTH_MISMATCH",
            "violation_count": len(contradictions), "contradictions": contradictions,
        })
    return payload


def _sync_news(_local_payload: dict, config: dict) -> None:
    """Advance one immutable generation without exposing partial replacement."""
    state_path = Path(config.get("news_state_file", DEFAULT_NEWS_STATE))
    state = _read_news_sync_state(state_path)
    if state.get("contract_version") != NEWS_MIRROR_CONTRACT_VERSION:
        state = {"contract_version": NEWS_MIRROR_CONTRACT_VERSION}
    news_index_url = config.get("remote_news_index_url") or (
        config["remote_ingest_url"].rsplit("/", 1)[0] + "/news-index"
    )
    news_url = config.get("remote_news_ingest_url") or (
        config["remote_ingest_url"].rsplit("/", 1)[0] + "/news-content"
    )
    if config.get("local_status_url"):
        manifest_page = _get_local_json(_local_news_archive_url(
            config, mode="manifest",
            activated_snapshot_id=state.get("active_snapshot_id"),
        ))
        manifest = manifest_page.get("manifest")
    else:
        raise PayloadContractError(
            "news generation sync requires local_status_url for frozen batch replay"
        )
    if not isinstance(manifest, dict):
        raise PayloadContractError("local news projection manifest is missing")
    generation_id = str(manifest.get("generation_id") or "")
    previous_generation = state.get("generation_id")
    if (
        previous_generation and previous_generation != generation_id
        and state.get("projection_state") != "CURRENT"
    ):
        _post_json(news_index_url, json.dumps({
            "action": "abandon", "generation_id": previous_generation,
        }, separators=(",", ":")).encode(), config)
        state = {"contract_version": NEWS_MIRROR_CONTRACT_VERSION}

    prepare_payload = json.dumps({
        "action": "prepare", "generation_id": generation_id,
        "manifest": manifest,
    }, ensure_ascii=False, separators=(",", ":")).encode()
    # A busy generation may belong to another exact producer (for example the
    # still-active Stable mirror while a Candidate bootstrap is replaying).
    # Only the generation recorded in this producer's own state may be
    # abandoned above. Preserve a foreign staging generation and let the
    # caller retry after the owning producer advances it.
    prepare = _post_json(news_index_url, prepare_payload, config)
    detail_offset = int(prepare.get("next_detail_offset", 0))
    index_offset = int(prepare.get("next_index_offset", 0))
    work = 0
    snapshot_id = str(manifest["snapshot_id"])
    while (
        not prepare.get("active") and work < NEWS_PROJECTION_BATCHES_PER_CYCLE
        and detail_offset < int(manifest["expected_detail_count"])
    ):
        page = _get_local_json(_local_news_archive_url(
            config, mode="batch", snapshot_id=snapshot_id,
            kind="detail", offset=detail_offset,
        ))
        items = page.get("items")
        if not isinstance(items, list) or not items:
            raise PayloadContractError("local news detail batch did not advance")
        result = _post_json(news_url, json.dumps({
            "action": "stage_details", "generation_id": generation_id,
            "offset": detail_offset, "items": items,
        }, ensure_ascii=False, separators=(",", ":")).encode(), config)
        detail_offset += len(items)
        if int(result.get("received", -1)) != len(items):
            raise PayloadContractError("remote news detail receipt count mismatched")
        work += 1
    while (
        not prepare.get("active") and work < NEWS_PROJECTION_BATCHES_PER_CYCLE
        and detail_offset == int(manifest["expected_detail_count"])
        and index_offset < int(manifest["expected_index_count"])
    ):
        page = _get_local_json(_local_news_archive_url(
            config, mode="batch", snapshot_id=snapshot_id,
            kind="index", offset=index_offset,
        ))
        items = page.get("items")
        if not isinstance(items, list) or not items:
            raise PayloadContractError("local news index batch did not advance")
        result = _post_json(news_index_url, json.dumps({
            "action": "stage_index", "generation_id": generation_id,
            "offset": index_offset, "items": items,
        }, ensure_ascii=False, separators=(",", ":")).encode(), config)
        index_offset += len(items)
        if int(result.get("received", -1)) != len(items):
            raise PayloadContractError("remote news index receipt count mismatched")
        work += 1

    complete = (
        detail_offset == int(manifest["expected_detail_count"])
        and index_offset == int(manifest["expected_index_count"])
    )
    if not prepare.get("active") and complete:
        _post_json(news_index_url, json.dumps({
            "action": "activate", "generation_id": generation_id,
        }, separators=(",", ":")).encode(), config)
        _post_json(news_index_url, json.dumps({
            "action": "verify", "generation_id": generation_id,
        }, separators=(",", ":")).encode(), config)
    if prepare.get("active") or complete:
        _verify_news_projection_state(news_index_url, config, manifest)
        state["active_snapshot_id"] = snapshot_id
        state["projection_state"] = "CURRENT"
        state["last_success"] = datetime.now(UTC).isoformat()
    else:
        state["projection_state"] = "REPLAYING"
    state.update({
        "generation_id": generation_id, "snapshot_id": snapshot_id,
        "source_digest": manifest["source_digest"],
        "expected_receipt_digest": manifest["expected_receipt_digest"],
        "next_detail_offset": detail_offset, "next_index_offset": index_offset,
        "expected_detail_count": manifest["expected_detail_count"],
        "expected_index_count": manifest["expected_index_count"],
        "updated_at": datetime.now(UTC).isoformat(),
    })
    _write_news_sync_state(state_path, state)


def _sync_audit(local_payload: dict, config: dict) -> None:
    if not local_payload and config.get("local_status_url"):
        local_payload = _read_local_resource(config, "/api/audit")
    audit_url = config.get("remote_audit_url") or (
        config["remote_ingest_url"].rsplit("/", 1)[0] + "/audit"
    )
    _post_json(audit_url, audit_snapshot(local_payload), config)
    root = audit_url.rsplit("/", 1)[0]
    producer_revision = _projection_producer_revision()
    if not producer_revision:
        raise PayloadContractError("projection producer revision is unavailable")
    for resource, snapshot in (
        ("audit-briefs", audit_briefs_snapshot(local_payload, producer_revision)),
        ("audit-stories", audit_stories_snapshot(local_payload, producer_revision)),
        ("audit-decisions", audit_decisions_snapshot(local_payload, producer_revision)),
    ):
        _post_json(f"{root}/{resource}", snapshot, config)


def _local_news_evidence_url(
    config: dict, cursor: str | None, *, activated_snapshot_id: str | None = None,
) -> str:
    status_url = urllib.parse.urlsplit(config["local_status_url"])
    query = {"limit": str(NEWS_EVIDENCE_WRITE_BATCH_ITEMS)}
    if cursor:
        query["cursor"] = cursor
    if activated_snapshot_id:
        query["activated_snapshot_id"] = activated_snapshot_id
    return urllib.parse.urlunsplit((
        status_url.scheme, status_url.netloc, "/api/news-evidence",
        urllib.parse.urlencode(query), "",
    ))


def _local_resource_url(config: dict, path: str) -> str:
    status_url = urllib.parse.urlsplit(config["local_status_url"])
    return urllib.parse.urlunsplit((
        status_url.scheme, status_url.netloc, path, "", "",
    ))


def _read_local_resource(config: dict, path: str) -> dict:
    with urllib.request.urlopen(
        _local_resource_url(config, path),
        timeout=LOCAL_STATUS_TIMEOUT_SECONDS,
    ) as response:
        payload = json.loads(response.read())
    if not isinstance(payload, dict):
        raise PayloadContractError(f"local resource {path} is not an object")
    return payload


def _local_critical_status_url(config: dict) -> str:
    return _local_resource_url(config, "/api/critical-status")


def _cleanup_news_evidence_snapshots(
    remote_url: str, snapshot_id: str, config: dict,
) -> bool:
    """Drain bounded cleanup debt faster than one replacement can create it."""
    payload = json.dumps({
        "contract_version": NEWS_EVIDENCE_CONTRACT_VERSION,
        "cleanup_active_snapshot": snapshot_id,
    }, separators=(",", ":")).encode("utf-8")
    cleanup_pending = False
    for _ in range(NEWS_EVIDENCE_CLEANUP_STEPS_PER_CYCLE):
        result = _post_json(remote_url, payload, config)
        cleanup_pending = result.get("cleanup_pending") is True
        if not cleanup_pending:
            return False
    return cleanup_pending


def _sync_news_evidence(_local_payload: dict, config: dict) -> None:
    """Advance a bounded staging window and activate only a complete snapshot."""
    if not config.get("local_status_url"):
        return
    remote_url = config.get("remote_news_evidence_url") or (
        config["remote_ingest_url"].rsplit("/", 1)[0] + "/news-evidence"
    )
    state_path = Path(config.get(
        "news_evidence_state_file", DEFAULT_NEWS_EVIDENCE_STATE,
    ))
    state = _read_news_sync_state(state_path)
    cursor = None
    snapshot_id = None
    total = None
    received = 0
    first_page = None
    with urllib.request.urlopen(
        _local_news_evidence_url(
            config,
            None,
            activated_snapshot_id=(
                str(state.get("active_snapshot_id"))
                if state.get("contract_version") == NEWS_EVIDENCE_CONTRACT_VERSION
                and state.get("active_snapshot_id") else None
            ),
        ),
        timeout=LOCAL_STATUS_TIMEOUT_SECONDS,
    ) as response:
        first_page = json.loads(response.read())
    first_snapshot = str(first_page.get("snapshot_id") or "")
    if not re.fullmatch(r"[a-f0-9]{64}", first_snapshot):
        raise PayloadContractError("local news evidence snapshot id is invalid")
    active_snapshot = (
        str(state.get("active_snapshot_id"))
        if state.get("contract_version") == NEWS_EVIDENCE_CONTRACT_VERSION
        and state.get("active_snapshot_id") else ""
    )
    if active_snapshot and _cleanup_news_evidence_snapshots(
        remote_url, active_snapshot, config,
    ):
        return
    if (
        state.get("contract_version") == NEWS_EVIDENCE_CONTRACT_VERSION
        and state.get("active_snapshot_id") == first_snapshot
    ):
        return
    snapshot_id = first_snapshot
    total = int(first_page.get("total") or 0)
    prepared = _post_json(remote_url, json.dumps({
        "contract_version": NEWS_EVIDENCE_CONTRACT_VERSION,
        "prepare_snapshot": snapshot_id,
        "expected_count": total,
    }, separators=(",", ":")).encode("utf-8"), config) or {}
    if prepared.get("active") is True:
        _write_news_sync_state(state_path, {
            "contract_version": NEWS_EVIDENCE_CONTRACT_VERSION,
            "active_snapshot_id": snapshot_id,
            "record_count": total,
            "last_success": datetime.now(UTC).isoformat(),
        })
        return
    received = int(prepared.get("next_offset") or 0)
    if received < 0 or received > total:
        raise PayloadContractError("remote news evidence staging offset is invalid")
    cursor = f"{snapshot_id}:{received}" if received else None

    for page_number in range(NEWS_EVIDENCE_PAGES_PER_CYCLE):
        if page_number == 0 and cursor is None:
            page = first_page
        else:
            with urllib.request.urlopen(
                _local_news_evidence_url(config, cursor),
                timeout=LOCAL_STATUS_TIMEOUT_SECONDS,
            ) as response:
                page = json.loads(response.read())
        page_snapshot = str(page.get("snapshot_id") or "")
        if not re.fullmatch(r"[a-f0-9]{64}", page_snapshot):
            raise PayloadContractError("local news evidence snapshot id is invalid")
        if page_snapshot != snapshot_id:
            raise PayloadContractError("local news evidence snapshot changed during paging")
        items = page.get("items")
        if not isinstance(items, list):
            raise PayloadContractError("local news evidence page has invalid items")
        if items:
            encoded = json.dumps({
                "contract_version": NEWS_EVIDENCE_CONTRACT_VERSION,
                "snapshot_id": snapshot_id,
                "offset": received,
                "items": items,
            }, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
            if len(encoded) > NEWS_EVIDENCE_BATCH_LIMIT_BYTES:
                raise PayloadContractError(
                    f"news evidence batch is {len(encoded)} bytes "
                    f"(limit {NEWS_EVIDENCE_BATCH_LIMIT_BYTES})"
                )
            _post_json(remote_url, encoded, config)
            received += len(items)
        next_cursor = page.get("next_cursor")
        if not page.get("has_more"):
            if total is None or received != total:
                raise PayloadContractError(
                    f"news evidence snapshot expected {total} rows but staged {received}"
                )
            _post_json(remote_url, json.dumps({
                "contract_version": NEWS_EVIDENCE_CONTRACT_VERSION,
                "activate_snapshot": snapshot_id,
                "expected_count": total,
            }, separators=(",", ":")).encode("utf-8"), config)
            _write_news_sync_state(state_path, {
                "contract_version": NEWS_EVIDENCE_CONTRACT_VERSION,
                "active_snapshot_id": snapshot_id,
                "record_count": total,
                "last_success": datetime.now(UTC).isoformat(),
            })
            _cleanup_news_evidence_snapshots(remote_url, snapshot_id, config)
            return
        if not isinstance(next_cursor, str) or not next_cursor or next_cursor == cursor:
            raise PayloadContractError("local news evidence cursor did not advance")
        cursor = next_cursor
    _write_news_sync_state(state_path, {
        "contract_version": NEWS_EVIDENCE_CONTRACT_VERSION,
        "staging_snapshot_id": snapshot_id,
        "record_count": total,
        "staged_count": received,
        "next_cursor": cursor,
        "last_progress": datetime.now(UTC).isoformat(),
    })


RESOURCE_POLICIES = (
    # Control-plane commands are bounded independently from historical mirrors.
    ("operator_retries", "_sync_operator_retries", 30, False),
    ("news_questions", "_sync_news_questions", 300, False),
    # At most one of these accumulated resources runs in a sync cycle.
    ("audit", "_sync_audit", 300, True),
    ("learning", "_sync_learning_summary", 300, True),
    ("learning_history", "_sync_learning_history", 300, True),
    ("market_chart", "_sync_market", 60, True),
    ("market_history", "_sync_market_history", 120, True),
    ("news", "_sync_news", 60, True),
    ("news_evidence", "_sync_news_evidence", 300, True),
)


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


def sync_heartbeat_once(config: dict) -> tuple[list[dict], SyncResourceResults]:
    """Publish only the critical heartbeat and return currently healthy targets."""
    with urllib.request.urlopen(
        _local_critical_status_url(config), timeout=LOCAL_STATUS_TIMEOUT_SECONDS
    ) as response:
        critical_payload = json.loads(response.read())

    degraded = []
    observations = []
    healthy = []
    live_payload = remote_snapshot(critical_payload)
    for target in configured_targets(config):
        target_name = target["name"]
        started = time.perf_counter()
        try:
            _post_json(target["remote_ingest_url"], live_payload, target)
            healthy.append(target)
            observations.append({
                "target": target_name,
                "resource": "heartbeat",
                "status": "OK",
                "duration_ms": round((time.perf_counter() - started) * 1000, 1),
                "completed_at": datetime.now(UTC).isoformat(),
            })
        except Exception as error:
            duration_ms = round((time.perf_counter() - started) * 1000, 1)
            degraded.append({
                "target": target_name,
                "resource": "heartbeat",
                "error_type": type(error).__name__,
                "error_code": sync_error_code(error),
                "error": str(error)[:500],
                "duration_ms": duration_ms,
            })
            observations.append({
                "target": target_name,
                "resource": "heartbeat",
                "status": "ERROR",
                "duration_ms": duration_ms,
                "completed_at": datetime.now(UTC).isoformat(),
            })
    if not healthy:
        error = AllTargetsRejected(degraded)
        error.resource_observations = observations
        raise error
    return healthy, SyncResourceResults(degraded, observations)


def sync_resource_lane(
    targets: list[dict], *, lane: str | None = None,
) -> SyncResourceResults:
    """Advance control or accumulated resources independently of heartbeat."""
    degraded = []
    observations = []
    for target in targets:
        target_name = target["name"]
        schedule_path = _resource_schedule_path(target)
        with _RESOURCE_SCHEDULE_LOCK:
            schedule_state = _read_news_sync_state(schedule_path)
        now = datetime.now(UTC)
        for resource, operation_name, cadence_seconds, _heavy in (
            _due_resource_policies(schedule_state, now, lane=lane)
        ):
            started = time.perf_counter()
            try:
                operation = globals()[operation_name]
                if operation_name == "_sync_market_history":
                    operation(target)
                else:
                    operation({}, target)
                completed_at = datetime.now(UTC)
                _persist_resource_schedule_result(
                    schedule_path, resource, cadence_seconds,
                    now=completed_at, success=True,
                )
                observations.append({
                    "target": target_name,
                    "resource": resource,
                    "status": "OK",
                    "duration_ms": round(
                        (time.perf_counter() - started) * 1000, 1,
                    ),
                    "completed_at": datetime.now(UTC).isoformat(),
                })
            except Exception as error:
                duration_ms = round((time.perf_counter() - started) * 1000, 1)
                completed_at = datetime.now(UTC)
                _persist_resource_schedule_result(
                    schedule_path, resource, cadence_seconds,
                    now=completed_at, success=False,
                )
                failure = {
                    "target": target_name,
                    "resource": resource,
                    "error_type": type(error).__name__,
                    "error_code": sync_error_code(error),
                    "error": str(error)[:500],
                    "duration_ms": duration_ms,
                }
                evidence = getattr(error, "evidence", None)
                if isinstance(evidence, dict):
                    failure["evidence"] = evidence
                degraded.append(failure)
                observations.append({
                    "target": target_name,
                    "resource": resource,
                    "status": "ERROR",
                    "duration_ms": duration_ms,
                    "completed_at": datetime.now(UTC).isoformat(),
                })
    return SyncResourceResults(degraded, observations)


def sync_once(config: dict) -> SyncResourceResults:
    healthy, heartbeat = sync_heartbeat_once(config)
    optional = sync_resource_lane(healthy)
    return SyncResourceResults(
        [*heartbeat, *optional],
        [*heartbeat.resource_observations, *optional.resource_observations],
    )


def sync_with_retry(config: dict, *, attempts: int = 3) -> tuple[int, list[dict]]:
    """Retry transient transport failures without waiting for the next sync cycle."""
    for attempt in range(1, attempts + 1):
        try:
            degraded = sync_once(config)
            if degraded is None:
                degraded = []
            return attempt, degraded
        except Exception as error:
            transient = isinstance(
                error,
                (ConnectionError, TimeoutError, http.client.RemoteDisconnected),
            ) or (
                isinstance(error, urllib.error.HTTPError)
                and (error.code == 429 or error.code >= 500)
            )
            if not transient or attempt >= attempts:
                raise
            print(
                json.dumps(
                    {
                        "event": "DASHBOARD_SYNC_RETRY",
                        "attempt": attempt,
                        "error_type": type(error).__name__,
                        "error": str(error)[:500],
                    }
                ),
                flush=True,
            )
            time.sleep(float(attempt * 2))
    raise RuntimeError("dashboard sync retry loop exhausted")


def _lane_failure(lane: str, error: Exception) -> SyncResourceResults:
    failure = {
        "target": "scheduler",
        "resource": f"{lane}_lane",
        "error_type": type(error).__name__,
        "error_code": sync_error_code(error),
        "error": str(error)[:500],
    }
    observation = {
        "target": "scheduler",
        "resource": f"{lane}_lane",
        "status": "ERROR",
        "duration_ms": None,
        "completed_at": datetime.now(UTC).isoformat(),
    }
    return SyncResourceResults([failure], [observation])


def _consume_lane_future(
    lane: str, future: Future | None,
) -> tuple[Future | None, SyncResourceResults | None]:
    if future is None or not future.done():
        return future, None
    try:
        return None, future.result()
    except Exception as error:
        return None, _lane_failure(lane, error)


def run_continuous_sync(
    config: dict,
    *,
    status_file: Path,
    interval_seconds: float = 30.0,
    stop_event: threading.Event | None = None,
    max_heartbeats: int | None = None,
) -> int:
    """Keep heartbeat, control work, and accumulated work on separate owners."""
    stop = stop_event or threading.Event()
    interval = max(5.0, interval_seconds)
    latest_lane_results: dict[str, SyncResourceResults] = {
        "control": SyncResourceResults([], []),
        "heavy": SyncResourceResults([], []),
    }
    futures: dict[str, Future | None] = {"control": None, "heavy": None}
    executors = {
        lane: ThreadPoolExecutor(max_workers=1, thread_name_prefix=f"dashboard-{lane}")
        for lane in futures
    }
    heartbeat_count = 0
    try:
        while not stop.is_set():
            cycle_started = time.monotonic()
            for lane in futures:
                futures[lane], completed = _consume_lane_future(
                    lane, futures[lane],
                )
                if completed is not None:
                    latest_lane_results[lane] = completed
            try:
                healthy, heartbeat = sync_heartbeat_once(config)
                degraded = [
                    *heartbeat,
                    *latest_lane_results["control"],
                    *latest_lane_results["heavy"],
                ]
                observations = [
                    *heartbeat.resource_observations,
                    *latest_lane_results["control"].resource_observations,
                    *latest_lane_results["heavy"].resource_observations,
                ]
                write_sync_status(
                    status_file,
                    success=True,
                    attempts_used=1,
                    degraded_resources=degraded,
                    resource_observations=observations,
                )
                for lane in futures:
                    if futures[lane] is None:
                        futures[lane] = executors[lane].submit(
                            sync_resource_lane, healthy, lane=lane,
                        )
                print(json.dumps({
                    "event": "DASHBOARD_HEARTBEAT_OK",
                    "heartbeat_sequence": heartbeat_count + 1,
                    "degraded_resources": degraded,
                }), flush=True)
            except Exception as error:
                write_sync_status(status_file, success=False, error=error)
                print(json.dumps({
                    "event": "DASHBOARD_HEARTBEAT_ERROR",
                    "heartbeat_sequence": heartbeat_count + 1,
                    "error_type": type(error).__name__,
                    "error": str(error)[:500],
                }), flush=True)
            heartbeat_count += 1
            if max_heartbeats is not None and heartbeat_count >= max_heartbeats:
                break
            remaining = interval - (time.monotonic() - cycle_started)
            stop.wait(max(0.0, remaining))
    finally:
        for executor in executors.values():
            executor.shutdown(wait=True, cancel_futures=False)
    return heartbeat_count


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--status-file", type=Path, default=DEFAULT_STATUS)
    parser.add_argument("--interval-seconds", type=float, default=30.0)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    config = json.loads(args.config.read_text(encoding="utf-8"))
    if not args.once:
        return run_continuous_sync(
            config,
            status_file=args.status_file,
            interval_seconds=args.interval_seconds,
        )
    while True:
        try:
            attempts_used, degraded_resources = sync_with_retry(config)
            write_sync_status(
                args.status_file,
                success=True,
                attempts_used=attempts_used,
                degraded_resources=degraded_resources,
                resource_observations=getattr(
                    degraded_resources, "resource_observations", [],
                ),
            )
            print(
                json.dumps(
                    {
                        "event": (
                            "DASHBOARD_SYNC_DEGRADED"
                            if degraded_resources else "DASHBOARD_SYNC_OK"
                        ),
                        "attempts_used": attempts_used,
                        "degraded_resources": degraded_resources,
                    }
                ),
                flush=True,
            )
        except Exception as error:
            write_sync_status(args.status_file, success=False, error=error)
            print(
                json.dumps(
                    {
                        "event": "DASHBOARD_SYNC_ERROR",
                        "error_type": type(error).__name__,
                        "error": str(error)[:500],
                    }
                ),
                flush=True,
            )
        break
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
