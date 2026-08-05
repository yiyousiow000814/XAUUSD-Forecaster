#!/usr/bin/env python
"""Run the Phase 2F collector; this process has no order API."""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path


MODULE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MODULE_ROOT))

from xauusd_forecaster.forward_engine import ForwardEngine, floor_five_minutes  # noqa: E402
from xauusd_forecaster.forward_ledger import ForwardLedger  # noqa: E402
from xauusd_forecaster.market import JsonlMarketProvider, NullMarketProvider  # noqa: E402
from xauusd_forecaster.u5_state import U5State  # noqa: E402
from xauusd_forecaster.maintenance import (  # noqa: E402
    archive_completed_quote_days,
    backup_forward_ledger,
)
from xauusd_forecaster.training_v2 import train_due_v2  # noqa: E402


UTC = timezone.utc
DEFAULT_LOCAL_ROOT = MODULE_ROOT / ".local" / "forward"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--local-root", type=Path, default=DEFAULT_LOCAL_ROOT)
    parser.add_argument("--market-jsonl", type=Path)
    parser.add_argument("--poll-seconds", type=float, default=10.0)
    parser.add_argument("--news-poll-seconds", type=float, default=60.0)
    parser.add_argument("--minimum-training-rows", type=int, default=200)
    parser.add_argument("--retrain-interval", type=int, default=50)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()

    local_root = args.local_root.resolve()
    local_root.mkdir(parents=True, exist_ok=True)
    initialized_at = datetime.now(UTC)
    ledger = ForwardLedger(local_root / "forward-evidence.sqlite3", now=initialized_at)
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
    provider = (
        JsonlMarketProvider(args.market_jsonl)
        if args.market_jsonl is not None
        else NullMarketProvider()
    )
    u5_path = local_root / "u5-state.json"
    u5_state = U5State.load(u5_path) if u5_path.exists() else U5State()
    engine = ForwardEngine(ledger, provider, u5_state)
    news_status = engine.collect_news(datetime.now(UTC))
    annotation_status = [{"status": "SEPARATE_PROCESS"}]
    quote_root = args.market_jsonl if args.market_jsonl and args.market_jsonl.is_dir() else None
    archived_quotes = (
        archive_completed_quote_days(quote_root, initialized_at) if quote_root else []
    )
    backup_path = backup_forward_ledger(
        ledger, local_root / "backups", initialized_at
    )
    print(
        json.dumps(
            {
                "event": "COLLECTOR_INITIALIZED",
                "forward_epoch": ledger.forward_epoch.isoformat(),
                "database": str(ledger.path),
                "market_provider": provider.name,
                "news_status": news_status,
                "annotation_status": annotation_status,
                "archived_quotes": [str(path) for path in archived_quotes],
                "local_backup": str(backup_path),
            },
            sort_keys=True,
        ),
        flush=True,
    )
    if args.once:
        ledger.close()
        return 0

    last_news_poll = datetime.now(UTC)
    last_maintenance_day = initialized_at.date()
    row = ledger.connection.execute(
        "SELECT max(decision_time) AS latest FROM decision_events"
    ).fetchone()
    last_decision = (
        datetime.fromisoformat(row["latest"])
        if row["latest"]
        else floor_five_minutes(ledger.forward_epoch)
    )
    try:
        while True:
            now = datetime.now(UTC)
            if now.date() != last_maintenance_day:
                if quote_root:
                    archive_completed_quote_days(quote_root, now)
                backup_forward_ledger(ledger, local_root / "backups", now)
                last_maintenance_day = now.date()
            if (now - last_news_poll).total_seconds() >= args.news_poll_seconds:
                news_status = engine.collect_news(now)
                last_news_poll = now
            boundary = floor_five_minutes(now)
            candidate = last_decision + timedelta(minutes=5)
            while candidate <= boundary:
                if candidate >= ledger.forward_epoch:
                    snapshot_id, decision_id = engine.append_clock_event(
                        candidate, now, news_status
                    )
                    print(
                        json.dumps(
                            {
                                "event": "DECISION_APPENDED",
                                "decision_time": candidate.isoformat(),
                                "snapshot_id": snapshot_id,
                                "decision_id": decision_id,
                            },
                            sort_keys=True,
                        ),
                        flush=True,
                    )
                    u5_state.save(u5_path)
                last_decision = candidate
                candidate += timedelta(minutes=5)
            completed_outcomes = engine.settle_due_outcomes(now)
            for decision_id in completed_outcomes:
                print(
                    json.dumps(
                        {"event": "OUTCOME_APPENDED", "decision_id": decision_id},
                        sort_keys=True,
                    ),
                    flush=True,
                )
            if completed_outcomes:
                # The V1 engine remains permanently quarantined.  Only the V2
                # repaired/Live-OOS lane may create new model versions.
                training_status = train_due_v2(ledger, now, local_root / "models-v2")
                print(
                    json.dumps(
                        {"event": "AUTO_TRAIN_CHECK", "results": training_status},
                        sort_keys=True,
                    ),
                    flush=True,
                )
            time.sleep(max(1.0, args.poll_seconds))
    finally:
        ledger.close()


if __name__ == "__main__":
    raise SystemExit(main())
