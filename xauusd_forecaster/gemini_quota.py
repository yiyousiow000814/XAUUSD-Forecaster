"""Compatibility shim for xauusd_forecaster.ai.quota."""

from xauusd_forecaster.ai.quota import (
    GEMINI_REQUESTS_PER_DAY_PER_KEY,
    GeminiQuotaLedger,
    PACIFIC,
    UTC,
    key_fingerprint,
)

__all__ = [
    "GEMINI_REQUESTS_PER_DAY_PER_KEY",
    "GeminiQuotaLedger",
    "PACIFIC",
    "UTC",
    "key_fingerprint",
]
