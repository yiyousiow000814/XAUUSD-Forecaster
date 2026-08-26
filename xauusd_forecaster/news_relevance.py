"""Compatibility shim for xauusd_forecaster.news.semantics.relevance."""

from xauusd_forecaster.news.semantics.relevance import (
    GOOGLE_NEWS_MAX_AGE,
    google_news_item_is_relevant,
    is_google_news_source,
)

__all__ = [
    "GOOGLE_NEWS_MAX_AGE",
    "google_news_item_is_relevant",
    "is_google_news_source",
]
