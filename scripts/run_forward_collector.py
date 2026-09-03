#!/usr/bin/env python
"""Run the Phase 2F collector; this process has no order API."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path


MODULE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MODULE_ROOT))

from xauusd_forecaster.forward_engine import ForwardEngine, floor_five_minutes  # noqa: E402
from xauusd_forecaster.forward_ledger import ForwardLedger  # noqa: E402
from xauusd_forecaster.market import (  # noqa: E402
    LIVE_QUOTE_OBSERVATION_LOOKBACK,
    JsonlMarketProvider,
    NullMarketProvider,
)
from xauusd_forecaster.market_session import skipped_grid_reason  # noqa: E402
from xauusd_forecaster.u5_state import U5State  # noqa: E402
from xauusd_forecaster.maintenance import (  # noqa: E402
    DailyBackupOwner,
    apply_backup_retention,
    archive_completed_quote_days,
    ensure_daily_forward_backup,
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
from xauusd_forecaster.runtime_health import (  # noqa: E402
    RuntimeHeartbeatPulse,
    write_runtime_heartbeat,
)
from xauusd_forecaster.runtime_paths import (  # noqa: E402
    authoritative_runtime_root,
    runtime_child_path,
)
from xauusd_forecaster.sqlite_wal import (  # noqa: E402
    ForwardWalCheckpointOwner,
    is_forward_sqlite_contention,
)
from xauusd_forecaster.news_collection_owner import NewsCollectionOwner  # noqa: E402
from xauusd_forecaster.training_owner import (  # noqa: E402
    BackgroundTrainingOwner,
    install_training_owner_schema,
    request_background_training,
)


UTC = timezone.utc
NEWS_CONTRACT_RECONCILE_SECONDS = 300
GRID_INTERVAL = timedelta(minutes=5)


def _database_contention(ledger, operation: str, error: sqlite3.Error) -> dict:
    """Rollback and describe one bounded retryable writer-contention result."""
    ledger.connection.rollback()
    return {
        "event": "FORWARD_SQLITE_CONTENTION",
        "status": "DEFERRED",
        "operation": operation,
        "sqlite_error_code": getattr(error, "sqlite_errorcode", None),
        "error": f"{type(error).__name__}: {str(error)[:400]}",
    }


def _first_grid_at_or_after(start: datetime, threshold: datetime) -> datetime:
    """Return the first grid in ``start + n * GRID_INTERVAL`` at a threshold."""
    if start >= threshold:
        return start
    steps = (threshold - start + GRID_INTERVAL - timedelta.resolution) // GRID_INTERVAL
    return start + steps * GRID_INTERVAL


def _grid_count_before(start: datetime, stop: datetime) -> int:
    """Count grids in ``start + n * GRID_INTERVAL`` strictly before ``stop``."""
    if start >= stop:
        return 0
    return int((stop - start + GRID_INTERVAL - timedelta.resolution) // GRID_INTERVAL)


def _record_skipped(skipped: dict[str, int], reason: str, count: int) -> None:
    if count:
        skipped[reason] = skipped.get(reason, 0) + count


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


def startup_reconciliation_plan(connection) -> dict:
    """Choose the bounded startup path without weakening generation safety."""
    try:
        generation_id = require_current_contract_generation(connection)
    except RuntimeError:
        return {"synchronous": True, "active_generation_id": None}
    return {"synchronous": False, "active_generation_id": generation_id}


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
    candidate = last_decision + GRID_INTERVAL
    if candidate > boundary:
        return last_decision, appended, skipped_grids

    first_eligible = _first_grid_at_or_after(candidate, ledger.forward_epoch)
    if first_eligible > boundary:
        return boundary, appended, skipped_grids

    eligible_count = _grid_count_before(first_eligible, boundary + GRID_INTERVAL)
    if broker_session is None or not broker_session.is_fresh(collected_at):
        _record_skipped(
            skipped_grids, "BROKER_MARKET_STATUS_UNAVAILABLE", eligible_count,
        )
        return boundary, appended, skipped_grids
    if not broker_session.is_open:
        _record_skipped(skipped_grids, "BROKER_MARKET_CLOSED", eligible_count)
        return boundary, appended, skipped_grids

    # JsonlMarketProvider cannot return a causally visible quote older than this
    # boundary. Settle that provably non-actionable prefix arithmetically so a
    # long service outage cannot turn startup into an unbounded five-minute loop.
    detailed_start = _first_grid_at_or_after(
        first_eligible, boundary - LIVE_QUOTE_OBSERVATION_LOOKBACK,
    )
    prefix_count = _grid_count_before(first_eligible, detailed_start)
    if prefix_count:
        estimated_close = broker_session.observed_at + broker_session.time_till_close
        close_block_at = estimated_close - timedelta(minutes=30)
        missed_count = _grid_count_before(
            first_eligible, min(detailed_start, close_block_at),
        )
        _record_skipped(
            skipped_grids, "MISSED_GRID_WITHOUT_POINT_IN_TIME_QUOTE", missed_count,
        )
        _record_skipped(
            skipped_grids,
            "FIXED_HORIZON_CROSSES_BROKER_CLOSE",
            prefix_count - missed_count,
        )

    candidate = detailed_start
    while candidate <= boundary:
        skip_reason = skipped_grid_reason(
            candidate, boundary, visible_observations,
            broker_session, collected_at,
        )
        if skip_reason:
            _record_skipped(skipped_grids, skip_reason, 1)
        else:
            try:
                snapshot_id, decision_id = engine.append_clock_event(
                    candidate, collected_at, news_status
                )
            except sqlite3.Error as exc:
                if not is_forward_sqlite_contention(exc):
                    raise
                _database_contention(ledger, "append_clock_event", exc)
                _record_skipped(skipped_grids, "DATABASE_CONTENTION_DEFERRED", 1)
                break
            appended.append((candidate, snapshot_id, decision_id))
        last_decision = candidate
        candidate += GRID_INTERVAL
    return last_decision, appended, skipped_grids


def append_current_grid_events(
    ledger: ForwardLedger,
    engine: ForwardEngine,
    provider: JsonlMarketProvider | NullMarketProvider,
    last_decision: datetime,
    news_status: list[dict[str, object]],
    *,
    clock=lambda: datetime.now(UTC),
) -> tuple[
    datetime,
    datetime,
    list[tuple[datetime, str, str]],
    dict[str, int],
]:
    """Append due grids against a timestamp taken after blocking maintenance."""
    collected_at = clock()
    boundary = floor_five_minutes(collected_at)
    next_decision, appended, skipped = append_due_grid_events(
        ledger,
        engine,
        provider,
        last_decision,
        boundary,
        collected_at,
        news_status,
    )
    return collected_at, next_decision, appended, skipped


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-root", type=Path, required=True)
    parser.add_argument("--market-jsonl", type=Path)
    parser.add_argument("--poll-seconds", type=float, default=10.0)
    parser.add_argument("--news-poll-seconds", type=float, default=60.0)
    parser.add_argument("--minimum-training-rows", type=int, default=200)
    parser.add_argument("--retrain-interval", type=int, default=50)
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
        JsonlMarketProvider(quote_path)
        if quote_path.exists()
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
            quote_path
            if quote_path.is_dir()
            else None
        )
        archived_quotes = []
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
                "market_provider": provider.name,
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
        ledger.close()
        return 0

    last_news_reconciliation = datetime.now(UTC)
    last_archive_day = initialized_at.date()
    last_backup_observation = None
    last_wal_checkpoint_observation = None
    row = ledger.connection.execute(
        "SELECT max(decision_time) AS latest FROM decision_events"
    ).fetchone()
    last_decision = (
        datetime.fromisoformat(row["latest"])
        if row["latest"]
        else floor_five_minutes(ledger.forward_epoch)
    )
    heartbeat = RuntimeHeartbeatPulse(status_file, service="collector")
    backup_owner = DailyBackupOwner(ledger.path, local_root / "backups")
    wal_checkpoint_owner = ForwardWalCheckpointOwner(ledger.path, local_root)
    news_owner = NewsCollectionOwner(
        ledger.path, poll_seconds=args.news_poll_seconds,
    )
    install_training_owner_schema(ledger.connection)
    ledger.connection.commit()
    training_owner = BackgroundTrainingOwner(
        ledger.path, local_root / "models-v2",
        local_root / "execution-models-v1", quote_root,
    )
    try:
        news_owner.start()
        training_owner.start()
        heartbeat.start()
        if quote_root:
            archive_completed_quote_days(quote_root, initialized_at)
        backup_owner.start()
        wal_checkpoint_owner.start()
        if startup_requires_reconciliation:
            startup_request = request_background_training(
                ledger.connection, datetime.now(UTC), reconcile=True,
            )
            if startup_request == "REQUESTED":
                training_owner.wake()
        while True:
            now = datetime.now(UTC)
            loop_contention = False
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
            news_status = news_owner.snapshot(now)
            if ((now - last_news_reconciliation).total_seconds()
                    >= NEWS_CONTRACT_RECONCILE_SECONDS):
                request_state = request_background_training(
                    ledger.connection, now, reconcile=True,
                )
                if request_state == "REQUESTED":
                    training_owner.wake()
                    last_news_reconciliation = now
                else:
                    loop_contention = True
                    diagnostic = {
                        "event": "FORWARD_SQLITE_CONTENTION",
                        "status": "DEFERRED",
                        "operation": "request_background_training",
                    }
                    print(json.dumps(diagnostic, sort_keys=True), flush=True)
                    heartbeat.update(
                        state="DATABASE_CONTENTION",
                        last_error="SQLITE_CONTENTION:request_background_training",
                    )
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
            grid_contention = loop_contention or bool(
                skipped_grids.get("DATABASE_CONTENTION_DEFERRED")
            )
            if quote_root:
                try:
                    checkpoint_quotes = provider.observations(now)
                    checkpoint_count = append_due_exit_predictions(
                        ledger, checkpoint_time=now, created_at=now,
                        quotes=checkpoint_quotes,
                    )
                except sqlite3.Error as exc:
                    if not is_forward_sqlite_contention(exc):
                        raise
                    print(json.dumps(
                        _database_contention(ledger, "append_due_exit_predictions", exc),
                        sort_keys=True,
                    ), flush=True)
                    checkpoint_count = 0
                    grid_contention = True
                if checkpoint_count:
                    print(
                        json.dumps(
                            {"event": "EXIT_CHECKPOINT_PREDICTIONS_APPENDED",
                             "count": checkpoint_count},
                            sort_keys=True,
                        ),
                        flush=True,
                    )
            try:
                completed_outcomes = engine.settle_due_outcomes(now)
            except sqlite3.Error as exc:
                if not is_forward_sqlite_contention(exc):
                    raise
                print(json.dumps(
                    _database_contention(ledger, "settle_due_outcomes", exc),
                    sort_keys=True,
                ), flush=True)
                completed_outcomes = []
                grid_contention = True
            for decision_id in completed_outcomes:
                print(
                    json.dumps(
                        {"event": "OUTCOME_APPENDED", "decision_id": decision_id},
                        sort_keys=True,
                    ),
                    flush=True,
                )
            if completed_outcomes:
                request_state = request_background_training(ledger.connection, now)
                if request_state == "REQUESTED":
                    training_owner.wake()
                else:
                    grid_contention = True
                    print(json.dumps({
                        "event": "FORWARD_SQLITE_CONTENTION",
                        "status": "DEFERRED",
                        "operation": "request_background_training_after_outcomes",
                    }, sort_keys=True), flush=True)
            heartbeat.update(
                work_items=len(appended_decisions) + len(completed_outcomes),
                state="DATABASE_CONTENTION" if grid_contention else "RUNNING",
                last_error=(
                    "SQLITE_CONTENTION:critical_runtime_write"
                    if grid_contention else None
                ),
                clear_error=not grid_contention,
            )
            time.sleep(max(1.0, args.poll_seconds))
    finally:
        wal_checkpoint_owner.close()
        backup_owner.close()
        training_owner.close()
        news_owner.close()
        heartbeat.close()
        ledger.close()


if __name__ == "__main__":
    raise SystemExit(main())
