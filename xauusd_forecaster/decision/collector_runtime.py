"""Domain owner extracted from the current production entry point."""

from __future__ import annotations

import json


import sqlite3


from datetime import datetime, timedelta, timezone


from pathlib import Path


from xauusd_forecaster.decision.engine import ForwardEngine


from xauusd_forecaster.decision.engine import floor_five_minutes  # noqa: E402


from xauusd_forecaster.evidence.ledger import ForwardLedger  # noqa: E402


from xauusd_forecaster.market import (  # noqa: E402
    LIVE_QUOTE_OBSERVATION_LOOKBACK,
    JsonlMarketProvider,
    NullMarketProvider,
)


from xauusd_forecaster.market_session import skipped_grid_reason  # noqa: E402


from xauusd_forecaster.clock_recovery import ExcludedIncompleteClock  # noqa: E402


from xauusd_forecaster.training.generation import require_current_contract_generation


from xauusd_forecaster.training.generation import train_due_v2


from xauusd_forecaster.news.semantics.migration import append_missing_current_news_snapshots


from xauusd_forecaster.sqlite_wal import (  # noqa: E402
    ForwardWalCheckpointOwner,
    is_forward_sqlite_contention,
)


UTC = timezone.utc


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
    appended: list[tuple[datetime, str, str]] = []
    skipped_grids: dict[str, int] = {}
    candidate = last_decision + GRID_INTERVAL
    if candidate > boundary:
        return last_decision, appended, skipped_grids

    first_eligible = _first_grid_at_or_after(candidate, ledger.forward_epoch)
    if first_eligible > boundary:
        return boundary, appended, skipped_grids

    eligible_count = _grid_count_before(first_eligible, boundary + GRID_INTERVAL)
    try:
        broker_session = provider.market_session(collected_at)
    except (OSError, ValueError, KeyError, json.JSONDecodeError):
        broker_session = None
    if broker_session is None or not broker_session.is_fresh(collected_at):
        _record_skipped(
            skipped_grids, "BROKER_MARKET_STATUS_UNAVAILABLE", eligible_count,
        )
        return boundary, appended, skipped_grids
    if not broker_session.is_open:
        _record_skipped(skipped_grids, "BROKER_MARKET_CLOSED", eligible_count)
        return boundary, appended, skipped_grids

    # Source-first admission: a poll tick or a closed/unknown session is not
    # permission to read the quote window. Independent owners still run below.
    try:
        visible_observations = provider.observations(boundary)
    except (OSError, ValueError, json.JSONDecodeError):
        visible_observations = []

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
            except ExcludedIncompleteClock:
                _record_skipped(skipped_grids, "CLOCK_EVENT_EXCLUDED_INCOMPLETE", 1)
            except sqlite3.Error as exc:
                if not is_forward_sqlite_contention(exc):
                    raise
                _database_contention(ledger, "append_clock_event", exc)
                _record_skipped(skipped_grids, "DATABASE_CONTENTION_DEFERRED", 1)
                break
            else:
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
