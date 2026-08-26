"""Compatibility shim for xauusd_forecaster.decision.inference."""

from xauusd_forecaster.decision.inference import (
    ACTIVE_VERSIONS_PER_IDENTITY,
    MIN_CALIBRATION_BLOCKS,
    MODEL_IDENTITIES,
    NEWS_MODEL_IDENTITIES,
    append_live_predictions_v2,
    news_model_activation_status,
)

__all__ = [
    "ACTIVE_VERSIONS_PER_IDENTITY",
    "MIN_CALIBRATION_BLOCKS",
    "MODEL_IDENTITIES",
    "NEWS_MODEL_IDENTITIES",
    "append_live_predictions_v2",
    "news_model_activation_status",
]
