"""Compatibility shim for xauusd_forecaster.news.collection.content."""

from xauusd_forecaster.news.collection.content import (
    ARTICLE_USER_AGENT,
    FED_SOURCES,
    NON_FED_FULL_TEXT_SOURCES,
    USER_AGENT,
    extract_article_full_text,
    extract_federal_reserve_full_text,
    fetch_content,
    hydrate_pending_federal_reserve_content,
    hydrate_pending_non_fed_content,
)

__all__ = [
    "ARTICLE_USER_AGENT",
    "FED_SOURCES",
    "NON_FED_FULL_TEXT_SOURCES",
    "USER_AGENT",
    "extract_article_full_text",
    "extract_federal_reserve_full_text",
    "fetch_content",
    "hydrate_pending_federal_reserve_content",
    "hydrate_pending_non_fed_content",
]
