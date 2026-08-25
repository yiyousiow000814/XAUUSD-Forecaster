#!/usr/bin/env python
"""Mirror the read-only dashboard snapshot to independent remote dashboards."""
from __future__ import annotations

import argparse
import http.client
import json
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

MODULE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MODULE_ROOT))
DEFAULT_CONFIG = MODULE_ROOT / ".local" / "forward" / "dashboard-sync.json"

from xauusd_forecaster.dashboard.resource_contracts import (
    AUDIT_DETAIL_LIMIT_BYTES, AUDIT_FIRST_PAGE_LIMIT_BYTES,
    LEARNING_HISTORY_BATCH_LIMIT_BYTES, LEARNING_HISTORY_CONTRACT_VERSION,
    LEARNING_OVERVIEW_CURVE_POINTS, LEARNING_OVERVIEW_GROUPS_PER_IDENTITY,
    LEARNING_SUMMARY_CURVE_POINTS, LEARNING_SUMMARY_EXECUTION_RESULTS,
    LEARNING_SUMMARY_GROUPS_PER_IDENTITY, MARKET_CHART_SNAPSHOT_LIMIT_BYTES,
    MARKET_DECISION_FIELDS, MARKET_OVERVIEW_DECISIONS_PER_SERIES,
    NEWS_DETAIL_BATCH_ITEMS, NEWS_DETAIL_BATCH_LIMIT_BYTES,
    NEWS_INDEX_BATCH_LIMIT_BYTES, NEWS_INDEX_FIELDS, NEWS_MIRROR_CONTRACT_VERSION,
    NEWS_READER_WINDOW_DAYS, NEWS_WRITE_BATCH_ITEMS, REMOTE_DAILY_BRIEF_LIMIT,
    REMOTE_DECISION_LIMIT, REMOTE_MARKET_CANDLE_LIMIT,
    REMOTE_MARKET_DECISION_LIMIT, REMOTE_MARKET_DENSE_LIMITS,
    REMOTE_MARKET_OVERVIEW_LIMITS, REMOTE_NEWS_LIMIT, REMOTE_PAYLOAD_LIMIT_BYTES,
    PayloadContractError, _bounded_audit_snapshot, _bounded_item_batches,
    _decision_key, _downsample_market_overview, _encoded_snapshot, _epoch,
    _json_hash, _learning_overview_records, _learning_record, _learning_summary,
    _stable_news_key, _update_decision_overviews, _version_metric,
    _visual_curve_overview, _visual_decision_overview, _visual_version_overview,
    audit_briefs_snapshot, audit_decisions_snapshot, audit_snapshot,
    audit_stories_snapshot, compact_market_chart, learning_history_batches,
    learning_history_records, learning_snapshot, market_chart_snapshot,
    news_detail_batches, news_index_batches, news_mirror_parts,
    news_withdrawal_keys, remote_snapshot,
)
from xauusd_forecaster.dashboard.sync.progress import (
    DEFAULT_LEARNING_HISTORY_STATE, DEFAULT_LEARNING_STATE,
    DEFAULT_MARKET_HISTORY_STATE, DEFAULT_NEWS_EVIDENCE_STATE,
    DEFAULT_NEWS_STATE, DEFAULT_RESOURCE_SCHEDULE_STATE,
    DEFAULT_RUNTIME_SIGNAL, DEFAULT_STATUS, HEAVY_RESOURCES_PER_CYCLE,
    OPERATOR_RETRY_COMMANDS_PER_CYCLE, RESOURCE_BACKOFF_MAX_SECONDS,
    RESOURCE_POLICIES, SyncResourceResults, AllTargetsRejected,
    _RESOURCE_SCHEDULE_LOCK, _due_resource_policies,
    _persist_resource_schedule_result, _read_news_sync_state,
    _record_resource_schedule, _resource_schedule_path, _schedule_epoch,
    _target_state_path, _write_news_sync_state, _write_runtime_signal,
    operator_retry_bulk_sla_seconds, sync_error_code, write_sync_status,
)
from xauusd_forecaster.dashboard.sync.transport import (
    LOCAL_STATUS_TIMEOUT_SECONDS, REMOTE_POST_TIMEOUT_SECONDS,
    SYNC_STATE_ROOT,
    RemoteInvariantViolation, _assistant_worker_id, _get_json, _get_local_json,
    _local_retry_url, _operator_retry_worker_url, _post_json, _post_local_json,
    _remote_request_headers, _validated_sync_state_path, configured_targets,
)
from xauusd_forecaster.dashboard.sync.resource_protocols import (
    LEARNING_HISTORY_FULL_REFRESH_SECONDS, MARKET_HISTORY_BATCH_ITEMS,
    MARKET_HISTORY_BATCH_LIMIT_BYTES, MARKET_HISTORY_CONTRACT_VERSION,
    MARKET_HISTORY_OVERLAP_SECONDS, MARKET_HISTORY_PAGES_PER_CYCLE,
    MARKET_OVERVIEWS_PER_CYCLE, NEWS_EVIDENCE_CONTRACT_VERSION,
    NEWS_EVIDENCE_CLEANUP_STEPS_PER_CYCLE, NEWS_EVIDENCE_PAGES_PER_CYCLE,
    NEWS_EVIDENCE_WRITE_BATCH_ITEMS,
    NEWS_PROJECTION_BATCHES_PER_CYCLE,
    _cleanup_news_evidence_snapshots as _cleanup_news_evidence_snapshots_owned,
    _learning_payload,
    _local_critical_status_url, _local_market_history_url,
    _local_news_archive_url, _local_news_evidence_url, _local_resource_url,
    _market_decision_overview_payload, _market_history_payloads, _overlap_cursor,
    _read_local_resource, _sync_assistant_chat, _sync_audit, _sync_learning,
    _sync_learning_history, _sync_learning_summary, _sync_market,
    _sync_market_history, _sync_news,
    _sync_news_evidence as _sync_news_evidence_owned, _sync_news_questions,
    _sync_operator_retries, _verify_news_projection_state,
)


def _cleanup_news_evidence_snapshots(
    remote_url: str, snapshot_id: str, config: dict,
) -> bool:
    """Delegate cleanup through the entrypoint's replaceable transport seam."""
    return _cleanup_news_evidence_snapshots_owned(
        remote_url, snapshot_id, config, post_json=_post_json,
    )


def _sync_news_evidence(local_payload: dict, config: dict) -> None:
    """Delegate evidence sync through the entrypoint's transport seam."""
    _sync_news_evidence_owned(local_payload, config, post_json=_post_json)


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
