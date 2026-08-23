"""Compatibility shim for xauusd_forecaster.news.semantics.input_coverage."""

from xauusd_forecaster.news.semantics.input_coverage import (
    NEWS_INPUT_STATES,
    NEWS_OBSERVATION_OUTAGE_REASONS,
    classify_news_input_coverage,
    news_input_coverage_at,
    news_source_observability_summary,
)

__all__ = [
    "NEWS_INPUT_STATES",
    "NEWS_OBSERVATION_OUTAGE_REASONS",
    "classify_news_input_coverage",
    "news_input_coverage_at",
    "news_source_observability_summary",
]
