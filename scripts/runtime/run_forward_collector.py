#!/usr/bin/env python
"""Collect news and maintain local evidence storage."""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path


MODULE_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(MODULE_ROOT))

from xauusd_forecaster.evidence.ledger import ForwardLedger  # noqa: E402
from xauusd_forecaster.maintenance import (  # noqa: E402
    DailyBackupOwner,
    apply_backup_retention,
    archive_completed_quote_days,
    ensure_daily_forward_backup,
)
from xauusd_forecaster.runtime.health import (
    RuntimeHeartbeatPulse,
    write_runtime_heartbeat,
)
from xauusd_forecaster.runtime_paths import (  # noqa: E402
    authoritative_runtime_root,
    runtime_child_path,
)
from xauusd_forecaster.sqlite_wal import (  # noqa: E402
    ForwardWalCheckpointOwner,
)
from xauusd_forecaster.news.collection.runtime import NewsCollectionOwner  # noqa: E402
from xauusd_forecaster.news.collection.intake import collect_official_news

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-root", type=Path, required=True)
    parser.add_argument("--market-jsonl", type=Path)
    parser.add_argument("--poll-seconds", type=float, default=10.0)
    parser.add_argument("--news-poll-seconds", type=float, default=60.0)
    parser.add_argument("--status-file", type=Path)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()

    local_root = authoritative_runtime_root(args.state_root)
    local_root.mkdir(parents=True, exist_ok=True)
    status_file = runtime_child_path(
        local_root, args.status_file, name="collector-status.json",
    )
    quote_path = runtime_child_path(
        local_root, args.market_jsonl, name="quotes",
    )
    initialized_at = datetime.now(UTC)
    ledger = ForwardLedger(local_root / "forward-evidence.sqlite3", now=initialized_at)
    try:
        epoch_receipt = local_root / "forward-epoch.json"
        if not epoch_receipt.exists():
            epoch_receipt.write_text(
                json.dumps(
                    {
                        "schema": "xauusd.forward.epoch.v1",
                        "forward_epoch": ledger.forward_epoch.isoformat(),
                        "created_by": "run_forward_collector.py",
                        "historical_training_allowed": False,
                        "warmup_role": "WARMUP_ONLY",
                    },
                    indent=2,
                    sort_keys=True,
                ),
                encoding="utf-8",
            )
        write_runtime_heartbeat(
            status_file, service="collector", state="STARTING",
        )
        with RuntimeHeartbeatPulse(
            status_file, service="collector", state="STARTING",
        ):
            news_status = (
                collect_official_news(ledger, datetime.now(UTC))
                if args.once
                else [{
                    "source": "NEWS_COLLECTION_OWNER",
                    "status": "DEGRADED",
                    "reason_code": "NEWS_COLLECTION_PENDING",
                }]
            )
            annotation_status = [{"status": "SEPARATE_PROCESS"}]
            quote_root = (
                quote_path
                if quote_path.is_dir()
                else None
            )
            archived_quotes = []
        write_runtime_heartbeat(status_file, service="collector")
        backup_result = None
        if args.once:
            with RuntimeHeartbeatPulse(status_file, service="collector"):
                if quote_root:
                    archived_quotes = archive_completed_quote_days(
                        quote_root, initialized_at,
                    )
                backup_result = ensure_daily_forward_backup(
                    ledger.path, local_root / "backups", initialized_at,
                    source_connection=ledger.connection,
                )
                apply_backup_retention(
                    ledger.path,
                    local_root / "backups",
                    initialized_at,
                    source_connection=ledger.connection,
                )
        print(
            json.dumps(
                {
                    "event": "COLLECTOR_INITIALIZED",
                    "forward_epoch": ledger.forward_epoch.isoformat(),
                    "database": str(ledger.path),
                    "quote_source": str(quote_path),
                    "news_status": news_status,
                    "annotation_status": annotation_status,
                    "archived_quotes": [str(path) for path in archived_quotes],
                    "backup_state": (
                        backup_result.status if backup_result else "BACKGROUND_SCHEDULED"
                    ),
                    "local_backup": str(backup_result.path) if backup_result else None,
                },
                sort_keys=True,
            ),
            flush=True,
        )
        if args.once:
            return 0

        last_archive_day = initialized_at.date()
        last_backup_observation = None
        last_wal_checkpoint_observation = None
        heartbeat = RuntimeHeartbeatPulse(status_file, service="collector")
        backup_owner = DailyBackupOwner(ledger.path, local_root / "backups")
        wal_checkpoint_owner = ForwardWalCheckpointOwner(ledger.path, local_root)
        news_owner = NewsCollectionOwner(
            ledger.path, poll_seconds=args.news_poll_seconds,
        )
        try:
            news_owner.start()
            heartbeat.start()
            if quote_root:
                archive_completed_quote_days(quote_root, initialized_at)
            backup_owner.start()
            wal_checkpoint_owner.start()
            while True:
                now = datetime.now(UTC)
                backup_observation = backup_owner.snapshot()
                if backup_observation != last_backup_observation:
                    print(json.dumps(
                        {"event": "DAILY_BACKUP_MAINTENANCE", **backup_observation},
                        sort_keys=True,
                    ), flush=True)
                    last_backup_observation = backup_observation
                wal_checkpoint_observation = wal_checkpoint_owner.snapshot()
                if wal_checkpoint_observation != last_wal_checkpoint_observation:
                    print(json.dumps(
                        {"event": "FORWARD_WAL_CHECKPOINT", **wal_checkpoint_observation},
                        sort_keys=True,
                    ), flush=True)
                    last_wal_checkpoint_observation = wal_checkpoint_observation
                if quote_root and now.date() != last_archive_day:
                    archive_completed_quote_days(quote_root, now)
                    last_archive_day = now.date()
                heartbeat.update(work_items=0, state="RUNNING", clear_error=True)
                time.sleep(max(1.0, args.poll_seconds))
        finally:
            wal_checkpoint_owner.close()
            backup_owner.close()
            news_owner.close()
            heartbeat.close()
    finally:
        ledger.close()


if __name__ == "__main__":
    raise SystemExit(main())
