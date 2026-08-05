"""Foundational contracts for the shadow-only XAUUSD forecaster."""

from .decision import ShadowDecisionGate, select_recommended_action
from .labeling import build_fixed_horizon_label
from .ledger import PredictionLedger
from .forward_ledger import ForwardLedger
from .models import Action, DataHealth, Decision, Forecast, OutcomeLabel
from .quotes import Quote, read_xautk002

__all__ = [
    "Action",
    "DataHealth",
    "Decision",
    "Forecast",
    "ForwardLedger",
    "OutcomeLabel",
    "PredictionLedger",
    "Quote",
    "ShadowDecisionGate",
    "build_fixed_horizon_label",
    "read_xautk002",
    "select_recommended_action",
]
