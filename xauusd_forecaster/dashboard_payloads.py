"""Compatibility shim for xauusd_forecaster.dashboard.payloads."""

from xauusd_forecaster.dashboard.payloads import (
    AUDIT_FIRST_PAGE_FIELDS,
    AUDIT_STORYLINE_LIMIT,
    AUDIT_STORY_CANDIDATE_LIMIT,
    AUDIT_STORY_FIELDS,
    AUDIT_STORY_STREAM_LIMIT,
    AUDIT_STORY_TIMELINE_LIMIT,
    CRITICAL_STATUS_FIELDS,
    DAILY_BRIEF_SUMMARY_FIELDS,
    audit_briefs_payload,
    audit_decisions_payload,
    audit_status_payload,
    audit_stories_payload,
    bounded_evidence_window,
    critical_status_payload,
)

__all__ = [
    "AUDIT_FIRST_PAGE_FIELDS",
    "AUDIT_STORYLINE_LIMIT",
    "AUDIT_STORY_CANDIDATE_LIMIT",
    "AUDIT_STORY_FIELDS",
    "AUDIT_STORY_STREAM_LIMIT",
    "AUDIT_STORY_TIMELINE_LIMIT",
    "CRITICAL_STATUS_FIELDS",
    "DAILY_BRIEF_SUMMARY_FIELDS",
    "audit_briefs_payload",
    "audit_decisions_payload",
    "audit_status_payload",
    "audit_stories_payload",
    "bounded_evidence_window",
    "critical_status_payload",
]
