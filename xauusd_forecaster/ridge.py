"""Compatibility shim for xauusd_forecaster.training.ridge."""

from xauusd_forecaster.training.ridge import (
    MIN_FEATURE_SCALE,
    RidgeArtifact,
    train_ridge,
)

__all__ = [
    "MIN_FEATURE_SCALE",
    "RidgeArtifact",
    "train_ridge",
]
