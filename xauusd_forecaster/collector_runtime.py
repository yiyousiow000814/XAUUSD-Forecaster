"""Compatibility shim for xauusd_forecaster.decision.collector_runtime."""

from xauusd_forecaster.decision.collector_runtime import (
    NEWS_CONTRACT_RECONCILE_SECONDS,
    UTC,
    append_current_grid_events,
    append_due_grid_events,
    reconcile_news_contract,
    startup_reconciliation_plan,
)

__all__ = [
    "NEWS_CONTRACT_RECONCILE_SECONDS",
    "UTC",
    "append_current_grid_events",
    "append_due_grid_events",
    "reconcile_news_contract",
    "startup_reconciliation_plan",
]
