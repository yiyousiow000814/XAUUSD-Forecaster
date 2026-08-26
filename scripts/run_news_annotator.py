#!/usr/bin/env python
"""Run local structured news annotation separately from the decision clock."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import os
import socket
import sys
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable

MODULE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MODULE_ROOT))

from xauusd_forecaster.evidence.ledger import ForwardLedger
from xauusd_forecaster.news.scheduler.state import (
    ApiCredential, configured_api_credentials, scheduler_counts, sync_pending_jobs,
)
from xauusd_forecaster.runtime_health import RuntimeHeartbeatPulse, write_runtime_heartbeat
from xauusd_forecaster.news.scheduler import runtime as scheduler_runtime
from xauusd_forecaster.news.scheduler.runtime import (
    EMBEDDING_PREREQUISITE_FAILURE_CODES, MAINTENANCE_DEFERRAL_CODES,
    PRODUCTION_LANES_PER_ACCOUNT, _credentials_for_job, _execute_job,
    _execute_job_safely, _may_try_another_credential, _next_retry,
    _run_scheduled_lane, _scheduler_sleep_seconds, _with_scheduler_failure_code,
)
from xauusd_forecaster.news.brief.runtime import run_daily_brief_batch





def write_heartbeat(
    path: Path, *, work_items: int, state: str = "RUNNING",
) -> None:
    write_runtime_heartbeat(
        path, service="annotator", state=state, work_items=work_items,
    )


def _account_thread_pool(*, max_workers: int, thread_name_prefix: str):
    return ThreadPoolExecutor(
        max_workers=max_workers, thread_name_prefix=thread_name_prefix,
    )


def run_scheduled_batch(
    ledger: ForwardLedger,
    *,
    batch_size: int | None,
    progress_callback: Callable[[int], None] | None = None,
    task_types: tuple[str, ...] | None = None,
    gemma_reserved_accounts: frozenset[str] = frozenset(),
) -> list[dict[str, object]]:
    return scheduler_runtime.run_scheduled_batch(
        ledger,
        batch_size=batch_size,
        progress_callback=progress_callback,
        task_types=task_types,
        gemma_reserved_accounts=gemma_reserved_accounts,
        executor_factory=_account_thread_pool,
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
    return scheduler_runtime.run_scheduled_batch_with_lock_retry(
        ledger,
        batch_runner=run_scheduled_batch,
        batch_size=batch_size,
        progress_callback=progress_callback,
        task_types=task_types,
        gemma_reserved_accounts=gemma_reserved_accounts,
        sleep=sleep,
    )


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
                    if status.get("reason") == "MODEL_CAPACITY_DEFERRED"
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
            time.sleep(_scheduler_sleep_seconds(
                [*brief_statuses, *statuses],
                interval_seconds=args.interval_seconds,
            ))
    finally:
        ledger.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
