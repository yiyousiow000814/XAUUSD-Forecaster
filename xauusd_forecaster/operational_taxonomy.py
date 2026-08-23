"""Compatibility shim for xauusd_forecaster.runtime.taxonomy."""

from xauusd_forecaster.runtime.taxonomy import (
    INTENTIONALLY_UNCORRELATED_FAILURE_CODES,
    normalize_operational_event,
    operational_code_index,
    operational_code_registry,
    validate_operational_code_registry,
)

__all__ = [
    "INTENTIONALLY_UNCORRELATED_FAILURE_CODES",
    "normalize_operational_event",
    "operational_code_index",
    "operational_code_registry",
    "validate_operational_code_registry",
]
