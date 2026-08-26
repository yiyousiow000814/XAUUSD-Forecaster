"""Compatibility shim for xauusd_forecaster.news.semantics.evidence."""

from xauusd_forecaster.news.semantics.evidence import (
    ACTIONABLE_EVIDENCE_ROLES,
    ACTION_TOPICS,
    BROAD_NEWS_FEATURES,
    CURRENT_EVENT_PROMPT_VERSION,
    EVIDENCE_POLICY_VERSION,
    FIRST_PARTY_SOURCES,
    MIN_ACTIONABLE_MATERIALITY,
    RELIABLE_PUBLISHER_DOMAINS,
    TOPIC_FEATURES,
    annotation_is_actionable_candidate,
    event_evidence_rows,
    event_evidence_rows_from_connection,
    resolve_event_clock,
)

__all__ = [
    "ACTIONABLE_EVIDENCE_ROLES",
    "ACTION_TOPICS",
    "BROAD_NEWS_FEATURES",
    "CURRENT_EVENT_PROMPT_VERSION",
    "EVIDENCE_POLICY_VERSION",
    "FIRST_PARTY_SOURCES",
    "MIN_ACTIONABLE_MATERIALITY",
    "RELIABLE_PUBLISHER_DOMAINS",
    "TOPIC_FEATURES",
    "annotation_is_actionable_candidate",
    "event_evidence_rows",
    "event_evidence_rows_from_connection",
    "resolve_event_clock",
]
