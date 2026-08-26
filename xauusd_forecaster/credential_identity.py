"""Compatibility shim for xauusd_forecaster.ai.credentials."""

from xauusd_forecaster.ai.credentials import (
    derived_credential_id,
    legacy_credential_id_for_migration,
)

__all__ = [
    "derived_credential_id",
    "legacy_credential_id_for_migration",
]
