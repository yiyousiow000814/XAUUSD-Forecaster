"""Compatibility shim for xauusd_forecaster.decision.engine."""

from xauusd_forecaster.decision.engine import (
    ForwardEngine,
    UTC,
    floor_five_minutes,
)

__all__ = [
    "ForwardEngine",
    "UTC",
    "floor_five_minutes",
]
