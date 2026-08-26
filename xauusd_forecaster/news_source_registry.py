"""Compatibility shim for xauusd_forecaster.news.collection.source_registry."""

from xauusd_forecaster.news.collection.source_registry import (
    NEWS_SOURCE_BY_NAME,
    NEWS_SOURCE_REGISTRY,
    NewsSourceHealthSpec,
)

__all__ = [
    "NEWS_SOURCE_BY_NAME",
    "NEWS_SOURCE_REGISTRY",
    "NewsSourceHealthSpec",
]
