"""Compatibility shim for xauusd_forecaster.evidence.ledger."""

from xauusd_forecaster.evidence.ledger import (
    ForwardLedger,
    IMMUTABLE_TABLES,
    SCHEMA,
    UTC,
    canonical_hash,
)

__all__ = [
    "ForwardLedger",
    "IMMUTABLE_TABLES",
    "SCHEMA",
    "UTC",
    "canonical_hash",
]
