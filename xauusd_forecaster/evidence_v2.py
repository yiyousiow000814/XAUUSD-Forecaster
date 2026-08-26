"""Compatibility shim for xauusd_forecaster.evidence.schema."""

from xauusd_forecaster.evidence.schema import (
    ELIGIBILITY_VERSION,
    EVIDENCE_CONTRACT_VERSION,
    FEATURE_VERSION,
    LABEL_VERSION,
    NEWS_FEATURE_VERSION,
    UTC,
    V2_IMMUTABLE_TABLES,
    V2_SCHEMA,
    evaluation_epoch,
    install_v2_schema,
)

__all__ = [
    "ELIGIBILITY_VERSION",
    "EVIDENCE_CONTRACT_VERSION",
    "FEATURE_VERSION",
    "LABEL_VERSION",
    "NEWS_FEATURE_VERSION",
    "UTC",
    "V2_IMMUTABLE_TABLES",
    "V2_SCHEMA",
    "evaluation_epoch",
    "install_v2_schema",
]
