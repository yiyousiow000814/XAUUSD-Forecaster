"""Compatibility shim for xauusd_forecaster.dashboard.read_models."""

from xauusd_forecaster.dashboard.read_models import (
    DashboardReadModelOwner,
    DashboardReadModelUnavailable,
    READ_MODEL_CONTRACTS,
    install_dashboard_read_model_schema,
    read_dashboard_read_model,
)

__all__ = [
    "DashboardReadModelOwner",
    "DashboardReadModelUnavailable",
    "READ_MODEL_CONTRACTS",
    "install_dashboard_read_model_schema",
    "read_dashboard_read_model",
]
