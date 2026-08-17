#!/usr/bin/env python
"""Run local structured news annotation separately from the decision clock."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import os
import sqlite3
import socket
import sys
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Callable


MODULE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MODULE_ROOT))

from xauusd_forecaster.annotation import (  # noqa: E402
    DEFAULT_GEMINI_MODEL,
    FALLBACK_GEMINI_MODEL,
    IMPACT_PROMPT_VERSION,
    PROMPT_VERSION,
    TITLE_PROMPT_VERSION,
    annotate_pending_news,
    assess_pending_news_impacts,
    translate_pending_headlines,
)
from xauusd_forecaster.daily_brief import (  # noqa: E402
    brief_dates_to_process,
    update_daily_brief,
)
from xauusd_forecaster.forward_ledger import ForwardLedger  # noqa: E402
from xauusd_forecaster.ai_task_registry import route_for_task  # noqa: E402
from xauusd_forecaster.news_scheduler import (  # noqa: E402
    ApiCredential,
    PREEMPTIBLE_POOL,
    ROUTINE_POOL,
    URGENT_PRIORITIES,
    backoff_job,
    claim_job,
    complete_job,
    configured_api_credentials,
    credentials_for_background_task,
    pending_record_for_job,
    record_job_attempt,
    rank_accounts_for_models,
    release_job,
    scheduler_counts,
    sync_pending_jobs,
)
from xauusd_forecaster.news_retrieval import (  # noqa: E402
    NewsEmbeddingBackfillPending,
)
from xauusd_forecaster.runtime_health import (  # noqa: E402
    RuntimeHeartbeatPulse,
    write_runtime_heartbeat,
)
from xauusd_forecaster.scheduler_model_gateway import (  # noqa: E402
    SchedulerModelAccountant,
)


from xauusd_forecaster.model_limits import GEMMA_PROVIDER_LANES_PER_ACCOUNT  # noqa: E402

PRODUCTION_LANES_PER_ACCOUNT = GEMMA_PROVIDER_LANES_PER_ACCOUNT


def write_heartbeat(
    path: Path, *, work_items: int, state: str = "RUNNING",
) -> None:
    write_runtime_heartbeat(
        path, service="annotator", state=state, work_items=work_items,
    )


def run_scheduled_batch_with_lock_retry(
    ledger: ForwardLedger,
    *,
    batch_size: int | None,
    progress_callback: Callable[[int], None],
    task_types: tuple[str, ...] | None = None,
    gemma_reserved_accounts: frozenset[str] = frozenset(),
    sleep: Callable[[float], None] = time.sleep,
) -> list[dict[str, object]]:
    """Keep the independent annotator alive through transient WAL writer contention."""
    while True:
        try:
            return run_scheduled_batch(
                ledger,
                batch_size=batch_size,
                progress_callback=progress_callback,
                task_types=task_types,
                gemma_reserved_accounts=gemma_reserved_accounts,
            )
        except sqlite3.OperationalError as error:
            if "locked" not in str(error).lower():
                raise
            ledger.connection.rollback()
            print(json.dumps({
                "event": "NEWS_AI_SCHEDULER_DATABASE_BUSY",
                "retry_seconds": 5,
            }), flush=True)
            sleep(5.0)


def _next_retry(status: dict[str, object], now: datetime) -> datetime:
    raw = status.get("next_retry_at")
    if raw:
        try:
            return datetime.fromisoformat(str(raw))
        except ValueError:
            pass
    return now + timedelta(minutes=1)


def _execute_job(
    ledger: ForwardLedger,
    credential: ApiCredential,
    job,
    *,
    now: datetime,
) -> dict[str, object]:
    record = pending_record_for_job(ledger.connection, job, now=now)
    if record is None:
        return {"status": "NOT_CURRENT"}
    urgent = job.priority in URGENT_PRIORITIES
    accountant = SchedulerModelAccountant(
        ledger.connection, credential, urgent=urgent,
    )

    if job.task_type == "ACTIVE_ANNOTATION":
        common = {
            "ledger": ledger,
            "api_key": credential.api_key,
            "limit": 1,
            "prompt_version": PROMPT_VERSION,
            "allow_priority_reserve": False,
            "records": [record],
        }
        status = annotate_pending_news(
            **common,
            model=DEFAULT_GEMINI_MODEL,
            request_accountant=accountant,
        )[0]
        if status.get("status") == "DEFERRED":
            status = annotate_pending_news(
                **common,
                model=FALLBACK_GEMINI_MODEL,
                request_accountant=accountant,
            )[0]
        return status
    if job.task_type == "ACTIVE_IMPACT":
        return assess_pending_news_impacts(
            ledger,
            api_key=credential.api_key,
            limit=1,
            annotation_prompt_version=PROMPT_VERSION,
            impact_prompt_version=IMPACT_PROMPT_VERSION,
            records=[record],
            request_accountant=accountant,
            use_hybrid_retrieval=True,
        )[0]
    return translate_pending_headlines(
        ledger,
        api_key=credential.api_key,
        records=[record],
        request_accountant=accountant,
    )[0]


def _execute_job_safely(
    ledger: ForwardLedger,
    credential: ApiCredential,
    job,
    *,
    now: datetime,
) -> dict[str, object]:
    try:
        return _execute_job(ledger, credential, job, now=now)
    except sqlite3.Error:
        raise
    except Exception as error:
        if isinstance(error, NewsEmbeddingBackfillPending):
            return {
                "status": "DEFERRED",
                "failure_code": "NEWS_EMBEDDING_BACKFILL_PENDING",
                "error_type": type(error).__name__,
                "error": str(error)[:500],
            }
        return {
            "status": "ERROR",
            "failure_code": "SCHEDULER_EXECUTION_FAILED",
            "error_type": type(error).__name__,
            "error": str(error)[:500],
        }


def _credentials_for_job(
    ledger: ForwardLedger,
    credentials: tuple[ApiCredential, ...],
    job,
    *,
    now: datetime,
    preferred_account_id: str | None = None,
) -> tuple[ApiCredential, ...]:
    """Order every compatible credential by current account headroom."""
    eligible = tuple(sorted(
        (
            credential for credential in credentials
            if credential.pool == ROUTINE_POOL
            or job.priority in URGENT_PRIORITIES
        ),
        key=lambda item: (
            job.priority in URGENT_PRIORITIES
            and item.pool != PREEMPTIBLE_POOL,
        ),
    ))
    route = route_for_task(job.task_type)
    accounts = rank_accounts_for_models(
        ledger.connection,
        eligible,
        models=route.models,
        priority_reserve_models=route.priority_reserve_models,
        urgent=job.priority in URGENT_PRIORITIES,
        now=now,
    )
    if preferred_account_id in accounts:
        accounts = (
            preferred_account_id,
            *(account for account in accounts if account != preferred_account_id),
        )
    by_account: dict[str, list[ApiCredential]] = {}
    for credential in eligible:
        by_account.setdefault(credential.account_id, []).append(credential)
    ordered: list[ApiCredential] = []
    # Rotate transport keys inside an account without pretending they create
    # additional account/project quota.
    for account_id in accounts:
        keys = sorted(by_account[account_id], key=lambda item: item.credential_id)
        offset = max(0, job.attempt_count - 1) % len(keys)
        ordered.extend(keys[offset:] + keys[:offset])
    return tuple(ordered)


def _may_try_another_credential(status: dict[str, object]) -> bool:
    if status.get("failure_code") == "NEWS_EMBEDDING_BACKFILL_PENDING":
        return False
    if status.get("status") in {"DEFERRED", "DISABLED"}:
        return True
    return status.get("provider_http_status") in {
        401, 403, 429, 500, 502, 503, 504,
    }


def _with_scheduler_failure_code(
    status: dict[str, object],
) -> dict[str, object]:
    """Attach one stable diagnostic code before persisting an attempt."""
    if status.get("failure_code"):
        return status
    outcome = status.get("status")
    if outcome == "DEFERRED":
        return {**status, "failure_code": "MODEL_CAPACITY_DEFERRED"}
    if outcome == "DISABLED":
        return {**status, "failure_code": "MODEL_ROUTE_DISABLED"}
    return status


def _run_scheduled_lane(
    ledger: ForwardLedger,
    *,
    credentials: tuple[ApiCredential, ...],
    maximum: int,
    worker_prefix: str,
    task_types: tuple[str, ...] | None = None,
    gemma_reserved_accounts: frozenset[str] = frozenset(),
    preferred_account_id: str | None = None,
    progress_callback: Callable[[], None] | None = None,
    shared_blocked_task_types: set[str] | None = None,
    shared_block_lock: threading.Lock | None = None,
) -> list[dict[str, object]]:
    statuses: list[dict[str, object]] = []
    blocked_task_types = (
        shared_blocked_task_types
        if shared_blocked_task_types is not None else set()
    )

    def blocked_snapshot() -> frozenset[str]:
        if shared_block_lock is None:
            return frozenset(blocked_task_types)
        with shared_block_lock:
            return frozenset(blocked_task_types)

    def block_task_type(task_type: str) -> None:
        if shared_block_lock is None:
            blocked_task_types.add(task_type)
            return
        with shared_block_lock:
            blocked_task_types.add(task_type)

    has_routine = any(item.pool == ROUTINE_POOL for item in credentials)
    has_preemptible = any(item.pool == PREEMPTIBLE_POOL for item in credentials)
    while len(statuses) < maximum:
        worker_id = f"{worker_prefix}-{len(statuses)}"
        job = None
        if has_routine:
            job = claim_job(
                ledger.connection,
                worker_id=worker_id,
                pool=ROUTINE_POOL,
                task_types=task_types,
                excluded_task_types=blocked_snapshot(),
                now=datetime.now(UTC),
            )
        elif has_preemptible:
            job = claim_job(
                ledger.connection,
                worker_id=worker_id,
                pool=PREEMPTIBLE_POOL,
                task_types=task_types,
                excluded_task_types=blocked_snapshot(),
                now=datetime.now(UTC),
            )
        if job is None:
            break
        executed_at = datetime.now(UTC)
        candidates = _credentials_for_job(
            ledger, credentials, job, now=executed_at,
            preferred_account_id=preferred_account_id,
        )
        if job.task_type in {"ACTIVE_IMPACT", "TITLE_TRANSLATION"}:
            candidates = tuple(
                item for item in candidates
                if item.account_id not in gemma_reserved_accounts
            )
        status: dict[str, object] = {
            "status": "DEFERRED",
            "reason": "NO_COMPATIBLE_ACCOUNT_CAPACITY",
            "failure_code": "MODEL_CAPACITY_DEFERRED",
        }
        outcome_credential = candidates[0] if candidates else credentials[0]
        outcome_at = executed_at
        attempted_credentials = 0
        attempted_accounts: set[str] = set()
        blocked_accounts: set[str] = set()
        route_capacity_deferred = True
        for credential in candidates:
            if credential.account_id in blocked_accounts:
                continue
            attempted_at = datetime.now(UTC)
            status = _with_scheduler_failure_code(_execute_job_safely(
                ledger, credential, job, now=attempted_at,
            ))
            record_job_attempt(
                ledger.connection,
                job=job,
                credential=credential,
                status=status,
                attempted_at=attempted_at,
            )
            attempted_credentials += 1
            attempted_accounts.add(credential.account_id)
            outcome_credential = credential
            outcome_at = attempted_at
            if status.get("status") not in {"DEFERRED", "DISABLED"}:
                route_capacity_deferred = False
            if not _may_try_another_credential(status):
                break
            # Account quota and transient provider pressure are shared by its
            # keys. Authentication failures may still try another key from the
            # same account before moving on.
            if status.get("provider_http_status") not in {401, 403}:
                blocked_accounts.add(credential.account_id)
        outcome = str(status.get("status") or "ERROR")
        if outcome == "OK":
            complete_job(ledger.connection, job.job_id, worker_id)
        elif outcome == "NOT_CURRENT":
            if job.attempt_count >= 2:
                backoff_job(
                    ledger.connection, job.job_id, worker_id,
                    available_at=outcome_at,
                    error="CURRENT_EVIDENCE_NO_LONGER_ELIGIBLE", terminal=True,
                )
            else:
                release_job(
                    ledger.connection, job.job_id, worker_id,
                    available_at=outcome_at + timedelta(minutes=1),
                    error="CURRENT_EVIDENCE_NOT_AVAILABLE",
                )
        elif outcome in {"DEFERRED", "DISABLED"}:
            release_job(
                ledger.connection, job.job_id, worker_id,
                available_at=outcome_at + timedelta(minutes=1),
                error=str(status.get("failure_code") or outcome),
            )
            if route_capacity_deferred:
                block_task_type(job.task_type)
        else:
            retry_at = _next_retry(status, outcome_at)
            backoff_job(
                ledger.connection, job.job_id, worker_id,
                available_at=retry_at,
                error=str(status.get("error") or outcome),
                terminal=bool(status.get("is_terminal")),
            )
        statuses.append({
            "job_id": job.job_id,
            "task_type": job.task_type,
            "priority": job.priority,
            "pool": outcome_credential.pool,
            "account_id": outcome_credential.account_id,
            "attempted_accounts": len(attempted_accounts),
            "attempted_credentials": attempted_credentials,
            **status,
        })
        if progress_callback is not None:
            progress_callback()
    return statuses


def run_scheduled_batch(
    ledger: ForwardLedger,
    *,
    batch_size: int | None,
    progress_callback: Callable[[int], None] | None = None,
    task_types: tuple[str, ...] | None = None,
    gemma_reserved_accounts: frozenset[str] = frozenset(),
) -> list[dict[str, object]]:
    now = datetime.now(UTC)
    sync_pending_jobs(ledger.connection, now=now)
    credentials = configured_api_credentials()
    if not credentials:
        return [{"status": "DISABLED", "reason": "GEMINI_API_KEY_MISSING"}]
    account_ids = tuple(dict.fromkeys(item.account_id for item in credentials))
    if not gemma_reserved_accounts.issubset(account_ids):
        raise ValueError("Gemma reserved account is not configured")
    maximum = batch_size or max(1, len(account_ids) * 10)
    worker_prefix = f"{socket.gethostname()}-{os.getpid()}"

    # Explicit batches preserve deterministic maintenance/test behavior. The
    # production default uses two lanes per independent account. Atomic quota
    # admission hides provider latency without exceeding account RPM or TPM.
    concurrent = (
        batch_size is None
        and str(ledger.path) != ":memory:"
    )
    if not concurrent:
        completed = 0

        def report_progress() -> None:
            nonlocal completed
            completed += 1
            if progress_callback is not None:
                progress_callback(completed)

        return _run_scheduled_lane(
            ledger,
            credentials=credentials,
            maximum=maximum,
            worker_prefix=worker_prefix,
            task_types=task_types,
            gemma_reserved_accounts=gemma_reserved_accounts,
            progress_callback=report_progress,
        )

    lanes = tuple(
        (account_id, lane_index)
        for account_id in account_ids
        for lane_index in range(PRODUCTION_LANES_PER_ACCOUNT)
    )
    base, remainder = divmod(maximum, len(lanes))
    allocations = tuple(
        base + (1 if index < remainder else 0)
        for index in range(len(lanes))
    )
    blocked_by_account = {account_id: set() for account_id in account_ids}
    blocked_locks = {
        account_id: threading.Lock() for account_id in account_ids
    }
    progress_lock = threading.Lock()
    completed = 0

    def report_progress() -> None:
        nonlocal completed
        with progress_lock:
            completed += 1
            if progress_callback is not None:
                progress_callback(completed)

    def run_account_lane(
        account_id: str, lane_index: int, allocation: int, index: int,
    ) -> list[dict[str, object]]:
        # sqlite connections are thread-affine; each account lane owns the
        # connection it creates and closes inside that same worker thread.
        lane_ledger = ForwardLedger(ledger.path)
        try:
            lane_task_types = task_types
            if account_id in gemma_reserved_accounts:
                lane_task_types = (
                    ("ACTIVE_ANNOTATION",)
                    if task_types is None or "ACTIVE_ANNOTATION" in task_types
                    else ()
                )
            return _run_scheduled_lane(
                lane_ledger,
                credentials=tuple(
                    item for item in credentials
                    if item.account_id == account_id
                ),
                maximum=allocation,
                worker_prefix=(
                    f"{worker_prefix}-account-{index}-lane-{lane_index}"
                ),
                task_types=lane_task_types,
                gemma_reserved_accounts=gemma_reserved_accounts,
                preferred_account_id=account_id,
                progress_callback=report_progress,
                shared_blocked_task_types=blocked_by_account[account_id],
                shared_block_lock=blocked_locks[account_id],
            )
        finally:
            lane_ledger.close()

    with ThreadPoolExecutor(
        max_workers=len(lanes), thread_name_prefix="news-ai-account",
    ) as executor:
        futures = [
            executor.submit(
                run_account_lane, account_id, lane_index, allocation, index,
            )
            for index, ((account_id, lane_index), allocation) in enumerate(
                zip(lanes, allocations, strict=True)
            )
            if allocation > 0
        ]
        return [status for future in futures for status in future.result()]


def run_daily_brief_batch(
    ledger: ForwardLedger, *, now: datetime | None = None,
    credentials: tuple[ApiCredential, ...] | None = None,
) -> list[dict[str, object]]:
    """Advance the bounded brief backlog using scheduler-owned routine capacity."""
    instant = now or datetime.now(UTC)
    configured = credentials if credentials is not None else configured_api_credentials()
    results = []
    for day in brief_dates_to_process(ledger.connection, now=instant):
        # Account usage changes after every model request. Re-rank for each
        # date so one exhausted account cannot starve the remaining backlog.
        ranking_instant = instant if now is not None else datetime.now(UTC)
        ordered = credentials_for_background_task(
            ledger.connection, configured, task_type="DAILY_BRIEF",
            now=ranking_instant,
        )
        credential = ordered[0] if ordered else None
        result = update_daily_brief(
            ledger, brief_date=day, now=instant,
            api_key=credential.api_key if credential else None,
            request_accountant=(SchedulerModelAccountant(
                ledger.connection, credential, urgent=False,
            ) if credential else None),
        )
        results.append({
            **result,
            "pool": credential.pool if credential else ROUTINE_POOL,
            "account_id": credential.account_id if credential else None,
        })
    return results


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--database",
        type=Path,
        default=MODULE_ROOT / ".local" / "forward" / "forward-evidence.sqlite3",
    )
    parser.add_argument("--interval-seconds", type=float, default=60.0)
    parser.add_argument(
        "--batch-size", type=int, default=0,
        help="0 uses the safe per-key Gemini capacity automatically",
    )
    parser.add_argument("--once", action="store_true")
    parser.add_argument(
        "--status-file",
        type=Path,
        default=MODULE_ROOT / ".local" / "forward" / "news-annotator-status.json",
    )
    args = parser.parse_args()
    ledger = ForwardLedger(args.database)
    try:
        completed_cycle = False
        while True:
            limit = None if args.batch_size <= 0 else args.batch_size
            write_heartbeat(
                args.status_file,
                work_items=0,
                state="RUNNING" if completed_cycle else "STARTING",
            )
            with RuntimeHeartbeatPulse(
                args.status_file,
                service="annotator",
                state="RUNNING",
            ) as heartbeat:
                # Reconcile protected backlog jobs before Daily Brief reads
                # their lifecycle state. Model calls still begin with the
                # bounded brief, preserving its first use of ROUTINE capacity.
                sync_pending_jobs(ledger.connection, now=datetime.now(UTC))
                brief_statuses = run_daily_brief_batch(ledger)
                print(
                    json.dumps({"event": "DAILY_NEWS_BRIEF_BATCH",
                                "statuses": brief_statuses}),
                    flush=True,
                )
                gemma_reserved_accounts = frozenset(
                    str(status["account_id"])
                    for status in brief_statuses
                    if status.get("reason") == "NO_GEMMA_CAPACITY"
                    and status.get("account_id")
                )
                statuses = run_scheduled_batch_with_lock_retry(
                    ledger,
                    batch_size=limit,
                    progress_callback=lambda count: heartbeat.update(
                        work_items=count,
                    ),
                    gemma_reserved_accounts=gemma_reserved_accounts,
                )
            print(
                json.dumps(
                    {
                        "event": "NEWS_AI_SCHEDULER_BATCH",
                        "statuses": statuses,
                        "queue": scheduler_counts(ledger.connection),
                    }
                ),
                flush=True,
            )
            work_items = len(statuses)
            completed_cycle = True
            write_heartbeat(args.status_file, work_items=work_items)
            if args.once:
                break
            time.sleep(max(5.0, args.interval_seconds))
    finally:
        ledger.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
