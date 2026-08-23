"""Collector reconciliation and quote-backed grid append runtime rules."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from xauusd_forecaster.decision.engine import ForwardEngine, floor_five_minutes
from xauusd_forecaster.evidence.ledger import ForwardLedger
from xauusd_forecaster.market import JsonlMarketProvider, NullMarketProvider
from xauusd_forecaster.market_session import skipped_grid_reason
from xauusd_forecaster.news.semantics.migration import append_missing_current_news_snapshots
from xauusd_forecaster.training.generation import require_current_contract_generation, train_due_v2

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
