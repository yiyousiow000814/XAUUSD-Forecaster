"""Compatibility shim for xauusd_forecaster.news.semantics.qa."""

from xauusd_forecaster.news.semantics.qa import (
    INSUFFICIENT_EVIDENCE_ANSWER,
    MAX_ANSWER_CHARACTERS,
    MAX_CITED_EVIDENCE,
    MAX_RETRIEVED_EVIDENCE,
    NEWS_QA_MAX_OUTPUT_TOKENS,
    NEWS_QA_PROMPT_VERSION,
    answer_news_question,
    build_news_evidence_packet,
)

__all__ = [
    "INSUFFICIENT_EVIDENCE_ANSWER",
    "MAX_ANSWER_CHARACTERS",
    "MAX_CITED_EVIDENCE",
    "MAX_RETRIEVED_EVIDENCE",
    "NEWS_QA_MAX_OUTPUT_TOKENS",
    "NEWS_QA_PROMPT_VERSION",
    "answer_news_question",
    "build_news_evidence_packet",
]
