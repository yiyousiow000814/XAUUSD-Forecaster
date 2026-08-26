"""Compatibility shim for xauusd_forecaster.news.semantics.features."""

from xauusd_forecaster.news.semantics.features import (
    COLLECTION_SOURCES,
    EVIDENCE_GRADE_WEIGHT,
    aggregate_news_features_v2,
    event_raw_weight,
    frozen_rule_rows,
)

__all__ = [
    "COLLECTION_SOURCES",
    "EVIDENCE_GRADE_WEIGHT",
    "aggregate_news_features_v2",
    "event_raw_weight",
    "frozen_rule_rows",
]
