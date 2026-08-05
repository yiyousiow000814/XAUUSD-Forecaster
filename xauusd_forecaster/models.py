"""Immutable value objects for forecasts, decisions, and completed labels."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class Action(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"
    WAIT = "WAIT"


class DataHealth(str, Enum):
    OK = "OK"
    STALE = "STALE"
    MISSING = "MISSING"
    ERROR = "ERROR"


def _require_utc(value: datetime, field: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")
    if value.utcoffset().total_seconds() != 0:
        raise ValueError(f"{field} must be UTC")


def _require_finite(values: dict[str, float]) -> None:
    for name, value in values.items():
        if not math.isfinite(value):
            raise ValueError(f"{name} must be finite")


@dataclass(frozen=True)
class Forecast:
    decision_id: str
    decision_time: datetime
    model_version: str
    feature_snapshot_hash: str
    ev_long_u5: float
    ev_short_u5: float
    lcb_long_u5: float
    lcb_short_u5: float
    uncertainty_long_u5: float
    uncertainty_short_u5: float
    estimated_cost_long_u5: float
    estimated_cost_short_u5: float
    data_health: DataHealth
    signal_expiry_seconds: int = 20
    reason_codes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.decision_id.strip():
            raise ValueError("decision_id is required")
        if not self.model_version.strip():
            raise ValueError("model_version is required")
        if not self.feature_snapshot_hash.strip():
            raise ValueError("feature_snapshot_hash is required")
        _require_utc(self.decision_time, "decision_time")
        _require_finite(
            {
                "ev_long_u5": self.ev_long_u5,
                "ev_short_u5": self.ev_short_u5,
                "lcb_long_u5": self.lcb_long_u5,
                "lcb_short_u5": self.lcb_short_u5,
                "uncertainty_long_u5": self.uncertainty_long_u5,
                "uncertainty_short_u5": self.uncertainty_short_u5,
                "estimated_cost_long_u5": self.estimated_cost_long_u5,
                "estimated_cost_short_u5": self.estimated_cost_short_u5,
            }
        )
        if self.uncertainty_long_u5 < 0 or self.uncertainty_short_u5 < 0:
            raise ValueError("uncertainty must be non-negative")
        if self.signal_expiry_seconds <= 0:
            raise ValueError("signal_expiry_seconds must be positive")


@dataclass(frozen=True)
class Decision:
    forecast: Forecast
    recommended_action: Action
    effective_action: Action
    decision_reason: str
    active_until: datetime | None

    def __post_init__(self) -> None:
        if self.active_until is not None:
            _require_utc(self.active_until, "active_until")


@dataclass(frozen=True)
class OutcomeLabel:
    decision_id: str
    label_time: datetime
    label_contract_version: str
    long_return_u5: float
    short_return_u5: float
    mfe_long_u5: float
    mae_long_u5: float
    mfe_short_u5: float
    mae_short_u5: float
    maximum_spread: float
    quote_coverage: float
    ambiguity_state: str

    def __post_init__(self) -> None:
        _require_utc(self.label_time, "label_time")
        _require_finite(
            {
                "long_return_u5": self.long_return_u5,
                "short_return_u5": self.short_return_u5,
                "mfe_long_u5": self.mfe_long_u5,
                "mae_long_u5": self.mae_long_u5,
                "mfe_short_u5": self.mfe_short_u5,
                "mae_short_u5": self.mae_short_u5,
                "maximum_spread": self.maximum_spread,
                "quote_coverage": self.quote_coverage,
            }
        )
        if not 0 <= self.quote_coverage <= 1:
            raise ValueError("quote_coverage must be between 0 and 1")
