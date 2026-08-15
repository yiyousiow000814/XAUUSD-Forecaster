#!/usr/bin/env python
"""Run local structured news annotation separately from the decision clock."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import socket
import sys
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
from xauusd_forecaster.daily_brief import update_daily_brief  # noqa: E402
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
    pending_record_for_job,
    record_job_attempt,
    rank_accounts_for_models,
    release_job,
    scheduler_counts,
    sync_pending_jobs,
)
from xauusd_forecaster.runtime_health import write_runtime_heartbeat  # noqa: E402
from xauusd_forecaster.scheduler_model_gateway import (  # noqa: E402
    SchedulerModelAccountant,
)


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
    sleep: Callable[[float], None] = time.sleep,
) -> list[dict[str, object]]:
    """Keep the independent annotator alive through transient WAL writer contention."""
    while True:
        try:
            return run_scheduled_batch(
                ledger,
                batch_size=batch_size,
                progress_callback=progress_callback,
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
    if status.get("status") in {"DEFERRED", "DISABLED"}:
        return True
    return status.get("provider_http_status") in {
        401, 403, 429, 500, 502, 503, 504,
    }


def run_scheduled_batch(
    ledger: ForwardLedger,
    *,
    batch_size: int | None,
    progress_callback: Callable[[int], None] | None = None,
) -> list[dict[str, object]]:
    now = datetime.now(UTC)
    sync_pending_jobs(ledger.connection, now=now)
    credentials = configured_api_credentials()
    if not credentials:
        return [{"status": "DISABLED", "reason": "GEMINI_API_KEY_MISSING"}]
    account_count = len({item.account_id for item in credentials})
    maximum = batch_size or max(1, account_count * 10)
    statuses: list[dict[str, object]] = []
    worker_prefix = f"{socket.gethostname()}-{os.getpid()}"
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
                now=datetime.now(UTC),
            )
        elif has_preemptible:
            job = claim_job(
                ledger.connection,
                worker_id=worker_id,
                pool=PREEMPTIBLE_POOL,
                now=datetime.now(UTC),
            )
        if job is None:
            break
        executed_at = datetime.now(UTC)
        candidates = _credentials_for_job(
            ledger, credentials, job, now=executed_at,
        )
        status: dict[str, object] = {
            "status": "DEFERRED", "reason": "NO_COMPATIBLE_ACCOUNT_CAPACITY",
        }
        outcome_credential = candidates[0] if candidates else credentials[0]
        outcome_at = executed_at
        attempted_credentials = 0
        attempted_accounts: set[str] = set()
        blocked_accounts: set[str] = set()
        for credential in candidates:
            if credential.account_id in blocked_accounts:
                continue
            attempted_at = datetime.now(UTC)
            status = _execute_job_safely(
                ledger, credential, job, now=attempted_at,
            )
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
                error=str(status.get("reason") or outcome),
            )
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
            progress_callback(len(statuses))
    return statuses


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
            statuses = run_scheduled_batch_with_lock_retry(
                ledger,
                batch_size=limit,
                progress_callback=lambda count: write_heartbeat(
                    args.status_file, work_items=count,
                ),
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
            brief_credential = next(
                (credential for credential in configured_api_credentials()
                 if credential.pool == "PREEMPTIBLE"),
                None,
            )
            if brief_credential is None:
                brief_status = {
                    "status": "DISABLED", "reason": "NO_PREEMPTIBLE_ACCOUNT",
                }
            else:
                brief_status = update_daily_brief(
                    ledger,
                    api_key=brief_credential.api_key,
                    request_accountant=SchedulerModelAccountant(
                        ledger.connection, brief_credential, urgent=False,
                    ),
                )
            print(
                json.dumps({"event": "DAILY_NEWS_BRIEF", **brief_status}),
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
