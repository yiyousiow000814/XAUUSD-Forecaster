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
from xauusd_forecaster.market_session import skipped_grid_reason  # noqa: E402
from xauusd_forecaster.u5_state import U5State  # noqa: E402
from xauusd_forecaster.maintenance import (  # noqa: E402
    archive_completed_quote_days,
    backup_forward_ledger,
)
from xauusd_forecaster.training_v2 import (  # noqa: E402
    require_current_contract_generation,
    train_due_v2,
)
from xauusd_forecaster.news_contract_migration import (  # noqa: E402
    append_missing_current_news_snapshots,
)
from xauusd_forecaster.execution_learning import (  # noqa: E402
    append_due_exit_predictions,
    train_due_execution,
)
from xauusd_forecaster.runtime_health import write_runtime_heartbeat  # noqa: E402


UTC = timezone.utc
NEWS_CONTRACT_RECONCILE_SECONDS = 300


def reconcile_news_contract(ledger, cutoff: datetime, artifact_root: Path) -> dict:
    """Migrate PIT news snapshots and build any missing current generation."""
    migration = append_missing_current_news_snapshots(ledger, cutoff)
    training = train_due_v2(ledger, cutoff, artifact_root)
    generation_id = require_current_contract_generation(ledger.connection)
    return {
        "migration": migration,
        "training": training,
        "active_generation_id": generation_id,
    }


DEFAULT_LOCAL_ROOT = MODULE_ROOT / ".local" / "forward"


def append_due_grid_events(
    ledger: ForwardLedger,
    engine: ForwardEngine,
    provider: JsonlMarketProvider | NullMarketProvider,
    last_decision: datetime,
    boundary: datetime,
    collected_at: datetime,
    news_status: list[dict[str, object]],
) -> tuple[datetime, list[tuple[datetime, str, str]], dict[str, int]]:
    """Append only broker-confirmed, quote-backed live decision grids."""
    try:
        visible_observations = provider.observations(boundary)
    except (OSError, ValueError, json.JSONDecodeError):
        visible_observations = []
    try:
        broker_session = provider.market_session(collected_at)
    except (OSError, ValueError, KeyError, json.JSONDecodeError):
        broker_session = None
    appended: list[tuple[datetime, str, str]] = []
    skipped_grids: dict[str, int] = {}
    candidate = last_decision + timedelta(minutes=5)
    while candidate <= boundary:
        if candidate >= ledger.forward_epoch:
            skip_reason = skipped_grid_reason(
                candidate, boundary, visible_observations,
                broker_session, collected_at,
            )
            if skip_reason:
                skipped_grids[skip_reason] = skipped_grids.get(skip_reason, 0) + 1
            else:
                snapshot_id, decision_id = engine.append_clock_event(
                    candidate, collected_at, news_status
                )
                appended.append((candidate, snapshot_id, decision_id))
        last_decision = candidate
        candidate += timedelta(minutes=5)
    return last_decision, appended, skipped_grids


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
    write_runtime_heartbeat(
        status_file, service="collector", state="STARTING",
    )
    # Reconcile at startup even in --once mode.  A rule release must build its
    # compatible news generation from already matured point-in-time evidence;
    # it must not wait for 96 brand-new direction rows.
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

    last_news_poll = datetime.now(UTC)
    last_news_reconciliation = last_news_poll
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
            if ((now - last_news_reconciliation).total_seconds()
                    >= NEWS_CONTRACT_RECONCILE_SECONDS):
                reconciliation = reconcile_news_contract(
                    ledger, now, local_root / "models-v2"
                )
                print(
                    json.dumps(
                        {"event": "NEWS_CONTRACT_RECONCILIATION", **reconciliation},
                        sort_keys=True,
                    ),
                    flush=True,
                )
                last_news_reconciliation = now
            boundary = floor_five_minutes(now)
            last_decision, appended_decisions, skipped_grids = append_due_grid_events(
                ledger, engine, provider, last_decision, boundary, now, news_status
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
                # The V1 engine remains permanently quarantined.  Only the V2
                # repaired/Live-OOS lane may create new model versions.
                training_status = train_due_v2(ledger, now, local_root / "models-v2")
                execution_status = train_due_execution(
                    ledger, now, local_root / "execution-models-v1", quote_root
                )
                print(
                    json.dumps(
                        {"event": "AUTO_TRAIN_CHECK", "results": training_status,
                         "execution_results": execution_status},
                        sort_keys=True,
                    ),
                    flush=True,
                )
            write_runtime_heartbeat(
                status_file,
                service="collector",
                work_items=len(appended_decisions) + len(completed_outcomes),
            )
            time.sleep(max(1.0, args.poll_seconds))
    finally:
        ledger.close()


if __name__ == "__main__":
    raise SystemExit(main())
