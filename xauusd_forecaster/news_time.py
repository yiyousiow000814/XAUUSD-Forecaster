"""Compatibility shim for xauusd_forecaster.news.semantics.time."""

from xauusd_forecaster.news.semantics.time import (
    MAX_ACTIONABLE_DISCOVERY_DELAY,
    MAX_ACTIONABLE_NEWS_AGE,
    MAX_PUBLICATION_CLOCK_SKEW,
    MIXED_PRECISE_OR_BATCH_PROXY_TIME,
    NEWS_SEMANTIC_ELIGIBILITY_CONTRACT_VERSION,
    NewsSemanticEligibility,
    NewsTimeAssessment,
    PublicationReceiptClockAssessment,
    SOURCE_REPORTED_TIME,
    assess_news_semantic_eligibility,
    assess_news_time,
    assess_publication_receipt_clock,
    category_time_rule,
    register_news_semantic_eligibility_sql,
    semantic_eligibility_sql_predicate,
)

__all__ = [
    "MAX_ACTIONABLE_DISCOVERY_DELAY",
    "MAX_ACTIONABLE_NEWS_AGE",
    "MAX_PUBLICATION_CLOCK_SKEW",
    "MIXED_PRECISE_OR_BATCH_PROXY_TIME",
    "NEWS_SEMANTIC_ELIGIBILITY_CONTRACT_VERSION",
    "NewsSemanticEligibility",
    "NewsTimeAssessment",
    "PublicationReceiptClockAssessment",
    "SOURCE_REPORTED_TIME",
    "assess_news_semantic_eligibility",
    "assess_news_time",
    "assess_publication_receipt_clock",
    "category_time_rule",
    "register_news_semantic_eligibility_sql",
    "semantic_eligibility_sql_predicate",
]
