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

from xauusd_forecaster.decision.engine import ForwardEngine, floor_five_minutes  # noqa: E402
from xauusd_forecaster.evidence.ledger import ForwardLedger  # noqa: E402
from xauusd_forecaster.market import JsonlMarketProvider, NullMarketProvider  # noqa: E402
from xauusd_forecaster.u5_state import U5State  # noqa: E402
from xauusd_forecaster.maintenance import (  # noqa: E402
    archive_completed_quote_days,
    backup_forward_ledger,
)
from xauusd_forecaster.execution_learning import (  # noqa: E402
    append_due_exit_predictions,
    train_due_execution,
)
from xauusd_forecaster.runtime_health import (  # noqa: E402
    RuntimeHeartbeatPulse,
    write_runtime_heartbeat,
)
from xauusd_forecaster.news_collection_owner import NewsCollectionOwner  # noqa: E402
from xauusd_forecaster.training.runtime import (  # noqa: E402
    BackgroundTrainingOwner,
    install_training_owner_schema,
    request_background_training,
)
from xauusd_forecaster.collector_runtime import (  # noqa: E402
    NEWS_CONTRACT_RECONCILE_SECONDS,
    append_current_grid_events,
    append_due_grid_events,
    reconcile_news_contract,
    startup_reconciliation_plan,
)


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
    parser.add_argument("--status-file", type=Path)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()

    local_root = args.local_root.resolve()
    local_root.mkdir(parents=True, exist_ok=True)
    status_file = args.status_file or local_root / "collector-status.json"
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
    write_runtime_heartbeat(
        status_file, service="collector", state="STARTING",
    )
    with RuntimeHeartbeatPulse(
        status_file, service="collector", state="STARTING",
    ):
        news_status = (
            engine.collect_news(datetime.now(UTC))
            if args.once
            else [{
                "source": "NEWS_COLLECTION_OWNER",
                "status": "DEGRADED",
                "reason_code": "NEWS_COLLECTION_PENDING",
            }]
        )
        annotation_status = [{"status": "SEPARATE_PROCESS"}]
        quote_root = (
            args.market_jsonl
            if args.market_jsonl and args.market_jsonl.is_dir()
            else None
        )
        archived_quotes = (
            archive_completed_quote_days(quote_root, initialized_at)
            if quote_root else []
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
    # A valid current generation is sufficient to begin the decision clock.
    # Reconciliation is durable background work and must not make a healthy
    # restart wait behind historical materialization.  A missing/incompatible
    # generation remains fail-closed and is built before any decision append.
    startup_plan = startup_reconciliation_plan(ledger.connection)
    startup_requires_reconciliation = not startup_plan["synchronous"]
    if not startup_plan["synchronous"]:
        startup_reconciliation = {
            "status": "BACKGROUND_SCHEDULED",
            "active_generation_id": startup_plan["active_generation_id"],
        }
    else:
        with RuntimeHeartbeatPulse(
            status_file, service="collector", state="STARTING",
        ):
            startup_reconciliation = reconcile_news_contract(
                ledger, datetime.now(UTC), local_root / "models-v2"
            )
    print(
        json.dumps(
            {"event": "NEWS_CONTRACT_RECONCILIATION", **startup_reconciliation},
            sort_keys=True,
        ),
        flush=True,
    )
    write_runtime_heartbeat(status_file, service="collector")
    if args.once:
        ledger.close()
        return 0

    last_news_reconciliation = datetime.now(UTC)
    last_maintenance_day = initialized_at.date()
    row = ledger.connection.execute(
        "SELECT max(decision_time) AS latest FROM decision_events"
    ).fetchone()
    last_decision = (
        datetime.fromisoformat(row["latest"])
        if row["latest"]
        else floor_five_minutes(ledger.forward_epoch)
    )
    heartbeat = RuntimeHeartbeatPulse(status_file, service="collector")
    news_owner = NewsCollectionOwner(
        ledger.path, poll_seconds=args.news_poll_seconds,
    )
    install_training_owner_schema(ledger.connection)
    training_owner = BackgroundTrainingOwner(
        ledger.path, local_root / "models-v2",
        local_root / "execution-models-v1", quote_root,
    )
    news_owner.start()
    training_owner.start()
    heartbeat.start()
    if startup_requires_reconciliation:
        request_background_training(
            ledger.connection, datetime.now(UTC), reconcile=True,
        )
        training_owner.wake()
    try:
        while True:
            now = datetime.now(UTC)
            if now.date() != last_maintenance_day:
                if quote_root:
                    archive_completed_quote_days(quote_root, now)
                backup_forward_ledger(ledger, local_root / "backups", now)
                last_maintenance_day = now.date()
            news_status = news_owner.snapshot(now)
            if ((now - last_news_reconciliation).total_seconds()
                    >= NEWS_CONTRACT_RECONCILE_SECONDS):
                request_background_training(
                    ledger.connection, now, reconcile=True,
                )
                training_owner.wake()
                last_news_reconciliation = now
            now, last_decision, appended_decisions, skipped_grids = (
                append_current_grid_events(
                    ledger, engine, provider, last_decision, news_status,
                )
            )
            for decision_time, snapshot_id, decision_id in appended_decisions:
                print(
                    json.dumps(
                        {
                            "event": "DECISION_APPENDED",
                            "decision_time": decision_time.isoformat(),
                            "snapshot_id": snapshot_id,
                            "decision_id": decision_id,
                        },
                        sort_keys=True,
                    ),
                    flush=True,
                )
                u5_state.save(u5_path)
            if skipped_grids:
                print(
                    json.dumps(
                        {"event": "NON_LIVE_GRIDS_SKIPPED", "counts": skipped_grids},
                        sort_keys=True,
                    ),
                    flush=True,
                )
            if quote_root:
                checkpoint_quotes = provider.observations(now)
                checkpoint_count = append_due_exit_predictions(
                    ledger, checkpoint_time=now, created_at=now,
                    quotes=checkpoint_quotes,
                )
                if checkpoint_count:
                    print(
                        json.dumps(
                            {"event": "EXIT_CHECKPOINT_PREDICTIONS_APPENDED",
                             "count": checkpoint_count},
                            sort_keys=True,
                        ),
                        flush=True,
                    )
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
                request_background_training(ledger.connection, now)
                training_owner.wake()
            heartbeat.update(
                work_items=len(appended_decisions) + len(completed_outcomes),
            )
            time.sleep(max(1.0, args.poll_seconds))
    finally:
        training_owner.close()
        news_owner.close()
        heartbeat.close()
        ledger.close()


if __name__ == "__main__":
    raise SystemExit(main())
