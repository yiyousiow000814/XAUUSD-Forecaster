"""Compatibility shim for xauusd_forecaster.news.scheduler.health."""

from xauusd_forecaster.news.scheduler.health import (
    ANNOTATION_DECISION_GRACE,
    ANNOTATOR_HEARTBEAT_MAX_AGE,
    news_semantic_pipeline_health,
    news_semantic_pipeline_health_at,
)

__all__ = [
    "ANNOTATION_DECISION_GRACE",
    "ANNOTATOR_HEARTBEAT_MAX_AGE",
    "news_semantic_pipeline_health",
    "news_semantic_pipeline_health_at",
]
