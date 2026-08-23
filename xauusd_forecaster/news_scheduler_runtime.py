"""Compatibility shim for xauusd_forecaster.news.scheduler.runtime."""

from xauusd_forecaster.news.scheduler.runtime import (
    EMBEDDING_PREREQUISITE_FAILURE_CODES,
    MAINTENANCE_DEFERRAL_CODES,
    PRODUCTION_LANES_PER_ACCOUNT,
    run_scheduled_batch,
    run_scheduled_batch_with_lock_retry,
)

__all__ = [
    "EMBEDDING_PREREQUISITE_FAILURE_CODES",
    "MAINTENANCE_DEFERRAL_CODES",
    "PRODUCTION_LANES_PER_ACCOUNT",
    "run_scheduled_batch",
    "run_scheduled_batch_with_lock_retry",
]
