"""Compatibility shim for xauusd_forecaster.news.collection.source_polling."""

from xauusd_forecaster.news.collection.source_polling import (
    AUTH_RETRY_DELAY,
    MAX_PROVIDER_RETRY_AFTER_SECONDS,
    PollFailureClassification,
    RATE_LIMIT_RETRY_DELAYS,
    RECOVERY_HISTORY_LIMIT,
    TRANSIENT_RETRY_DELAYS,
    classified_poll_error,
    source_poll_gate,
    source_poll_recovery_state,
)

__all__ = [
    "AUTH_RETRY_DELAY",
    "MAX_PROVIDER_RETRY_AFTER_SECONDS",
    "PollFailureClassification",
    "RATE_LIMIT_RETRY_DELAYS",
    "RECOVERY_HISTORY_LIMIT",
    "TRANSIENT_RETRY_DELAYS",
    "classified_poll_error",
    "source_poll_gate",
    "source_poll_recovery_state",
]
