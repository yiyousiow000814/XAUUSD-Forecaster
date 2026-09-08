#!/usr/bin/env python
"""Mirror the read-only dashboard snapshot to independent remote dashboards."""
from __future__ import annotations
import argparse
import hashlib
import http.client
import json
import os
import re
import subprocess
import sys
import threading
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime, timedelta
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
MODULE_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(MODULE_ROOT))

from xauusd_forecaster.dashboard.sync.resources import (
    DEFERRED_PROJECTION_CONTRACT,
    DEFERRED_PROJECTION_ROUTES,
    NEWS_EVIDENCE_WRITE_BATCH_ITEMS,
    NEWS_EVIDENCE_BATCH_LIMIT_BYTES,
    NEWS_EVIDENCE_PAGES_PER_CYCLE,
    NEWS_EVIDENCE_CLEANUP_STEPS_PER_CYCLE,
    NEWS_EVIDENCE_CONTRACT_VERSION,
    MARKET_HISTORY_BATCH_LIMIT_BYTES,
    MARKET_HISTORY_BATCH_ITEMS,
    _RESOURCE_SCHEDULE_LOCK,
    _learning_record_identity,
    _projection_producer_revision,
    _post_json,
    _sync_operator_retry_mirror,
    _sync_operator_retries,
    _sync_assistant_chat,
    _sync_news_questions,
    _read_news_sync_state,
    _write_news_sync_state,
    _sync_learning_history,
    _sync_learning_summary,
    _sync_learning,
    _sync_market,
    _market_history_payloads,
    _market_decision_overview_payload,
    _overlap_cursor,
    _sync_market_history,
    _verify_news_projection_state,
    _sync_news,
    _sync_audit,
    _deferred_projection_request_digest,
    _read_deferred_projection_request,
    _deferred_projection_pending,
    sync_deferred_projection_once,
    _read_local_resource,
    _local_critical_status_url,
    _post_news_evidence,
    _cleanup_news_evidence_snapshots,
    _sync_news_evidence,
    _schedule_epoch,
    _record_resource_schedule,
    _resource_schedule_path,
    _persist_resource_schedule_result,
)
HEAVY_RESOURCES_PER_CYCLE = 1


from xauusd_forecaster.dashboard.resource_contracts import (
    REMOTE_PAYLOAD_LIMIT_BYTES,
    REMOTE_DECISION_LIMIT,
    REMOTE_DAILY_BRIEF_LIMIT,
    LEARNING_HISTORY_CONTRACT_VERSION,
    LEARNING_HISTORY_BATCH_LIMIT_BYTES,
    LEARNING_OVERVIEW_GROUPS_PER_IDENTITY,
    MARKET_OVERVIEW_DECISIONS_PER_SERIES,
    REMOTE_MARKET_DECISION_LIMIT,
    REMOTE_MARKET_CANDLE_LIMIT,
    MARKET_CHART_SNAPSHOT_LIMIT_BYTES,
    AUDIT_DETAIL_LIMIT_BYTES,
    PayloadContractError,
    _json_hash,
    news_mirror_parts,
    news_detail_batches,
    news_index_batches,
    _epoch,
    _learning_record,
    _visual_curve_overview,
    _visual_version_overview,
    _update_decision_overviews,
    learning_history_records,
    learning_history_batches,
    _learning_summary,
    _downsample_market_overview,
    market_chart_snapshot,
    learning_snapshot,
    remote_snapshot,
    audit_snapshot,
    audit_briefs_snapshot,
    audit_decisions_snapshot,
    audit_stories_snapshot,
)
from xauusd_forecaster.dashboard.payloads import (
    audit_stories_payload,
    critical_status_payload,
)
from xauusd_forecaster.news_projection import (
    NEWS_DETAIL_BATCH_ITEMS,
    NEWS_DETAIL_BATCH_LIMIT_BYTES,
    NEWS_INDEX_BATCH_LIMIT_BYTES,
    NEWS_MIRROR_CONTRACT_VERSION,
    NewsProjectionGeneration,
    NEWS_INDEX_BATCH_ITEMS as NEWS_WRITE_BATCH_ITEMS,
)
from xauusd_forecaster.runtime_paths import (
    authoritative_runtime_root,
    runtime_child_path,
)
from xauusd_forecaster.dashboard.sync.progress import (
    OPERATOR_RETRY_COMMANDS_PER_CYCLE,
    RUNTIME_STATE_ROOT_KEY,
    AllTargetsRejected,
    SyncResourceResults,
    sync_error_code,
)
from xauusd_forecaster.dashboard.sync.transport import (
    LOCAL_STATUS_TIMEOUT_SECONDS,
    RemoteInvariantViolation,
    _get_json,
    _get_local_json,
    _local_retry_url,
    _operator_retry_worker_url,
    _post_local_json,
    _validated_sync_state_path,
    configure_runtime_state,
    configured_targets,
)






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
        current_degraded = list(getattr(error, "degraded_resources", None) or [])
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




def _due_resource_policies(
    state: dict, now: datetime, *, lane: str | None = None,
    excluded: frozenset[str] = frozenset(),
) -> list[tuple]:
    resources = state.get("resources")
    if not isinstance(resources, dict):
        resources = {}
    due = []
    for policy_index, policy in enumerate(RESOURCE_POLICIES):
        resource = policy[0]
        if resource in excluded:
            continue
        resource_state = resources.get(resource)
        if not isinstance(resource_state, dict):
            resource_state = {}
        due_at = _schedule_epoch(resource_state.get("next_run_at"))
        if due_at <= now.timestamp():
            due.append((due_at, policy_index, policy))
    controls = [entry[2] for entry in due if not entry[2][3]]
    heavy = [
        entry[2] for entry in sorted(
            (entry for entry in due if entry[2][3]),
            key=lambda entry: (entry[0], entry[1]),
        )[:HEAVY_RESOURCES_PER_CYCLE]
    ]
    if lane == "control":
        return controls
    if lane == "heavy":
        return heavy
    return [*controls, *heavy]








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
    excluded: frozenset[str] = frozenset(),
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
            _due_resource_policies(schedule_state, now, lane=lane, excluded=excluded)
        ):
            started = time.perf_counter()
            try:
                operation = globals()[operation_name]
                if operation_name == "_sync_market_history":
                    operation(target)
                else:
                    operation({}, target)
                completed_at = datetime.now(UTC)
                evidence_state = (
                    _read_news_sync_state(Path(target["news_evidence_state_file"]))
                    if resource == "news_evidence" and target.get("news_evidence_state_file")
                    else {}
                )
                _persist_resource_schedule_result(
                    schedule_path, target, resource, cadence_seconds,
                    now=completed_at, success=True,
                    pending=(
                        resource == "news"
                        and bool(target.get("news_state_file"))
                        and _read_news_sync_state(Path(target["news_state_file"])).get(
                            "projection_state"
                        ) == "REPLAYING"
                        or resource == "news_evidence"
                        and bool(evidence_state.get("staging_snapshot_id")
                                 or evidence_state.get("cleanup_pending"))
                    ),
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
                evidence_state = (
                    _read_news_sync_state(Path(target["news_evidence_state_file"]))
                    if resource == "news_evidence" and target.get("news_evidence_state_file")
                    else {}
                )
                _persist_resource_schedule_result(
                    schedule_path, target, resource, cadence_seconds,
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


def _merge_lane_results(
    previous: SyncResourceResults, current: SyncResourceResults,
) -> SyncResourceResults:
    """Retain one bounded latest status per target/resource across a drain."""
    observations = {
        (str(row.get("target")), str(row.get("resource"))): row
        for row in previous.resource_observations
    }
    failures = {
        (str(row.get("target")), str(row.get("resource"))): row
        for row in previous
    }
    for row in current.resource_observations:
        key = (str(row.get("target")), str(row.get("resource")))
        observations[key] = row
        if row.get("status") == "OK":
            failures.pop(key, None)
    for row in current:
        failures[(str(row.get("target")), str(row.get("resource")))] = row
    return SyncResourceResults(list(failures.values()), list(observations.values()))


def _submit_resource_lane(
    executor: ThreadPoolExecutor,
    lane: str,
    healthy: list[dict],
    config: dict,
    *,
    prefer_regular: bool = False,
) -> Future:
    if lane == "heavy":
        try:
            deferred_pending = _deferred_projection_pending(config)
        except Exception:
            deferred_pending = True
        if deferred_pending:
            if prefer_regular:
                def regular_or_deferred():
                    # One heavy operation still owns each turn. The deferred
                    # owner alone handles its resources until completion.
                    request = _read_deferred_projection_request(config)
                    routes = request.get("routes", []) if request else []
                    excluded = set()
                    if "/api/news-evidence" in routes:
                        excluded.add("news_evidence")
                    if any(route in DEFERRED_PROJECTION_ROUTES for route in routes):
                        excluded.add("audit")
                    regular = sync_resource_lane(
                        healthy, lane="heavy", excluded=frozenset(excluded),
                    )
                    if regular or regular.resource_observations:
                        return regular
                    return sync_deferred_projection_once(healthy, config)
                return executor.submit(regular_or_deferred)
            return executor.submit(sync_deferred_projection_once, healthy, config)
    return executor.submit(sync_resource_lane, healthy, lane=lane)


def run_continuous_sync(
    config: dict,
    *,
    status_file: Path,
    interval_seconds: float = 30.0,
    stop_event: threading.Event | None = None,
    max_heartbeats: int | None = None,
) -> int:
    """Keep heartbeat, control work, and accumulated work on separate owners.

    The serial heavy owner drains already-overdue work immediately after each
    completion. Heartbeats remain the discovery/wakeup boundary when no heavy
    work is due, but they are not an artificial one-operation admission limit.
    """
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
    heavy_turn = 0

    def submit(lane, healthy):
        nonlocal heavy_turn
        prefer_regular = lane == "heavy" and heavy_turn % 2 == 1
        if lane == "heavy":
            heavy_turn += 1
        return _submit_resource_lane(
            executors[lane], lane, healthy, config, prefer_regular=prefer_regular,
        )

    try:
        while not stop.is_set():
            cycle_started = time.monotonic()
            for lane in futures:
                futures[lane], completed = _consume_lane_future(
                    lane, futures[lane],
                )
                if completed is not None:
                    latest_lane_results[lane] = _merge_lane_results(
                        latest_lane_results[lane], completed,
                    )
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
                        futures[lane] = submit(lane, healthy)
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
            deadline = cycle_started + interval
            while not stop.is_set():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                stop.wait(min(0.25, remaining))
                for lane in futures:
                    futures[lane], completed = _consume_lane_future(
                        lane, futures[lane],
                    )
                    if completed is None:
                        continue
                    latest_lane_results[lane] = _merge_lane_results(
                        latest_lane_results[lane], completed,
                    )
                    if (
                        lane == "heavy"
                        and completed.resource_observations
                        and not completed
                        and not stop.is_set()
                        and time.monotonic() < deadline
                    ):
                        futures[lane] = submit(lane, healthy)
    finally:
        for executor in executors.values():
            executor.shutdown(wait=True, cancel_futures=False)
    return heartbeat_count


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-root", type=Path, required=True)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--status-file", type=Path)
    parser.add_argument("--interval-seconds", type=float, default=30.0)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    state_root = authoritative_runtime_root(args.state_root)
    config_path = runtime_child_path(
        state_root, args.config, name="dashboard-sync.json",
    )
    status_file = _validated_sync_state_path(
        args.status_file or Path("dashboard-sync-status.json"), state_root,
    )
    config = configure_runtime_state(
        json.loads(config_path.read_text(encoding="utf-8")), state_root,
    )
    if not args.once:
        return run_continuous_sync(
            config,
            status_file=status_file,
            interval_seconds=args.interval_seconds,
        )
    while True:
        try:
            attempts_used, degraded_resources = sync_with_retry(config)
            write_sync_status(
                status_file,
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
            write_sync_status(status_file, success=False, error=error)
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
