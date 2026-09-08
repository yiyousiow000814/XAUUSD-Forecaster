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


MODULE_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(MODULE_ROOT))

from xauusd_forecaster.news.scheduler.runtime import (
    PRODUCTION_LANES_PER_ACCOUNT,
    _execute_job,
    _execute_job_safely,
    _may_try_another_credential,
    run_scheduled_batch,
)

from xauusd_forecaster.news.brief.runtime import (
    run_daily_brief_batch,
)

from xauusd_forecaster.news.annotation.product import (
    DEFAULT_GEMINI_MODEL,
    FALLBACK_GEMINI_MODEL,
    IMPACT_PROMPT_VERSION,
    PROMPT_VERSION,
    annotate_pending_news,
)
from xauusd_forecaster.news.brief.product import (
    brief_dates_to_process,
    update_daily_brief,
)
from xauusd_forecaster.evidence.ledger import ForwardLedger  # noqa: E402
from xauusd_forecaster.news.scheduler.state import (
    ApiCredential,
    LIVE_LANE,
    ROUTINE_POOL,
    configured_api_credentials,
    credentials_for_background_task,
    pending_record_for_job,
    record_scheduler_deferral,
    scheduler_counts,
    sync_pending_jobs,
)
from xauusd_forecaster.runtime.health import (
    RuntimeHeartbeatPulse,
    write_runtime_heartbeat,
)
from xauusd_forecaster.runtime_paths import (  # noqa: E402
    authoritative_runtime_root,
    runtime_child_path,
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




def _scheduler_sleep_seconds(
    statuses: list[dict[str, object]],
    *,
    interval_seconds: float,
    now: datetime | None = None,
) -> float:
    """Wake for the earliest durable retry without busy-spinning."""
    anti_spin_floor = 0.05
    instant = now or datetime.now(UTC)
    maximum = max(5.0, interval_seconds)
    retry_times = []
    for status in statuses:
        raw = status.get("next_retry_at")
        if not raw:
            continue
        try:
            retry_times.append(datetime.fromisoformat(str(raw)))
        except ValueError:
            continue
    if not retry_times:
        return maximum
    until_retry = min(
        max(0.0, (retry_at - instant).total_seconds())
        for retry_at in retry_times
    )
    return max(anti_spin_floor, min(maximum, until_retry))


















def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-root", type=Path, required=True)
    parser.add_argument(
        "--database",
        type=Path,
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
    )
    args = parser.parse_args()
    state_root = authoritative_runtime_root(args.state_root)
    database = runtime_child_path(
        state_root, args.database, name="forward-evidence.sqlite3",
    )
    status_file = runtime_child_path(
        state_root, args.status_file, name="news-annotator-status.json",
    )
    ledger = ForwardLedger(database)
    try:
        completed_cycle = False
        while True:
            limit = None if args.batch_size <= 0 else args.batch_size
            write_heartbeat(
                status_file,
                work_items=0,
                state="RUNNING" if completed_cycle else "STARTING",
            )
            with RuntimeHeartbeatPulse(
                status_file,
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
            write_heartbeat(status_file, work_items=work_items)
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
