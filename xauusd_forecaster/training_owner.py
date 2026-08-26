"""Compatibility shim for xauusd_forecaster.training.runtime."""

from xauusd_forecaster.training.runtime import (
    BackgroundTrainingOwner,
    LEASE_HEARTBEAT_SECONDS,
    LEASE_SECONDS,
    UTC,
    install_training_owner_schema,
    request_background_training,
)

__all__ = [
    "BackgroundTrainingOwner",
    "LEASE_HEARTBEAT_SECONDS",
    "LEASE_SECONDS",
    "UTC",
    "install_training_owner_schema",
    "request_background_training",
]
