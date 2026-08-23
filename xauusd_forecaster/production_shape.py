"""Compatibility shim for xauusd_forecaster.runtime.production_shape."""

from xauusd_forecaster.runtime.production_shape import (
    PAYLOAD_CONTRACT_REJECTED,
    PAYLOAD_ERROR_CODES,
    PAYLOAD_LIMIT_EXCEEDED,
    production_contract_snapshot,
    production_shape_violations,
)

__all__ = [
    "PAYLOAD_CONTRACT_REJECTED",
    "PAYLOAD_ERROR_CODES",
    "PAYLOAD_LIMIT_EXCEEDED",
    "production_contract_snapshot",
    "production_shape_violations",
]
