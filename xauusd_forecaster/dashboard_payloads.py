"""Shared bounded-payload policies for dashboard producers and mirrors."""

from __future__ import annotations

import copy
from collections.abc import Mapping, Sequence
from typing import Any


CRITICAL_STATUS_FIELDS = (
    "generated_at", "production_contract", "dashboard_sync", "forward_epoch",
    "system", "operational_health", "latest", "research_forecast", "u5_context",
    "counts", "outcome_summary", "news_source_health",
    "news_input_coverage", "annotation_queue", "gemini_quota", "gemini_31_quota",
    "gemma_quota", "gemini_embedding_quota", "llm_routing", "training",
    "factor_coverage", "sources",
)
AUDIT_SNAPSHOT_FIELDS = (
    "generated_at", "recent_decisions", "daily_news_briefs", "news_metrics",
    "daily_news_brief_summary", "storylines", "market_narrative_candidates",
    "archived_storylines", "archived_story_event_candidates",
    "story_event_candidates", "market_reaction_streams", "theme_streams",
    "unassigned_story_events", "storyline_summary", "news_evidence_summary",
    "news_feature_policy",
)
DAILY_BRIEF_SUMMARY_FIELDS = (
    "brief_date", "phase", "received_items", "reviewed_items", "pending_items",
    "terminal_failure_items", "latest_revision", "last_generated_at",
    "next_retry_at", "is_final", "total_brief_days",
)


def critical_status_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Project the fixed critical mirror contract from a full local snapshot."""
    snapshot = {
        key: copy.deepcopy(payload[key])
        for key in CRITICAL_STATUS_FIELDS
        if key in payload
    }
    training = snapshot.get("training")
    if isinstance(training, dict):
        training.pop("models", None)  # Model details belong to /api/learning.
    snapshot.update({
        "learning_resource": "/api/learning",
        "news_index_resource": "/api/news-index",
        "audit_resource": "/api/audit",
        "news_evidence_resource": "/api/news-evidence",
        "market_chart_resource": "/api/market-chart",
        "market_history_resource": "/api/market-history",
        "market_chart": {
            "decision_resource": "/api/market-chart",
            "history_resource": "/api/market-history",
            "candles": [], "overview_candles": [], "decisions": [],
            "training_markers": [],
        },
        "mirror_window": {
            "bounded": True,
            "critical_only": True,
            "audit_embedded": False,
            "growing_collections_embedded": False,
        },
    })
    return snapshot


def audit_status_payload(
    payload: Mapping[str, Any], *, decision_limit: int = 20,
) -> dict[str, Any]:
    """Project the bounded optional audit first page from local authority."""
    snapshot = {
        key: copy.deepcopy(payload[key])
        for key in AUDIT_SNAPSHOT_FIELDS
        if key in payload
    }
    decisions = snapshot.get("recent_decisions")
    if isinstance(decisions, list):
        snapshot["recent_decisions"] = decisions[:decision_limit]
    summary = snapshot.get("daily_news_brief_summary")
    if isinstance(summary, dict):
        snapshot["daily_news_brief_summary"] = {
            key: summary.get(key) for key in DAILY_BRIEF_SUMMARY_FIELDS
            if key in summary
        }
    snapshot["news_evidence_resource"] = "/api/news-evidence"
    return snapshot


def bounded_evidence_window(
    rows: Sequence[Mapping[str, Any]], per_state_limit: int,
) -> list[Mapping[str, Any]]:
    """Keep an independent window for used and unused evidence.

    Current model-eligible events are retained first because their headline
    count is an actionable dashboard state, not merely a historical total. Each
    visibility state then receives up to ``per_state_limit`` rows instead of
    sharing one combined allowance. Input order remains authoritative within
    every group. A state may exceed its limit only when retaining every current
    event requires it.
    """
    if per_state_limit < 0:
        raise ValueError("evidence window limit must not be negative")

    indexed = list(enumerate(rows))
    current = [
        index for index, row in indexed if bool(row.get("broad_model_eligible"))
    ]
    selected = set(current)

    seen = [
        index for index, row in indexed
        if index not in selected and bool(row.get("model_seen"))
    ]
    unseen = [
        index for index, row in indexed
        if index not in selected and not bool(row.get("model_seen"))
    ]
    current_seen = sum(bool(rows[index].get("model_seen")) for index in current)
    current_unseen = len(current) - current_seen
    selected.update(seen[:max(0, per_state_limit - current_seen)])
    selected.update(unseen[:max(0, per_state_limit - current_unseen)])
    return [row for index, row in indexed if index in selected]
