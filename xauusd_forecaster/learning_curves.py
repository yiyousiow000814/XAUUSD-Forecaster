"""Compatibility shim for xauusd_forecaster.dashboard.learning_curves."""

from xauusd_forecaster.dashboard.learning_curves import (
    MAX_CURVE_POINTS,
    OUTCOME_SETTLEMENT_WINDOW,
    learning_curve_payload,
)

__all__ = [
    "MAX_CURVE_POINTS",
    "OUTCOME_SETTLEMENT_WINDOW",
    "learning_curve_payload",
]
