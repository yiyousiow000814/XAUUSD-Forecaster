"""Shared bounded-payload policies for dashboard producers and mirrors."""

from __future__ import annotations

import copy
import math
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any


CRITICAL_STATUS_FIELDS = (
    "generated_at", "production_contract", "dashboard_sync", "forward_epoch",
    "system", "operational_health", "latest", "research_forecast", "u5_context",
    "counts", "outcome_summary", "news_metrics", "news_source_health",
    "news_input_coverage", "annotation_queue", "gemini_quota", "gemini_31_quota",
    "gemma_quota", "gemini_embedding_quota", "llm_routing", "training",
    "factor_coverage", "sources",
)
AUDIT_FIRST_PAGE_FIELDS = (
    "generated_at", "news_metrics", "daily_news_brief_summary",
    "storyline_summary", "news_evidence_summary", "news_feature_policy",
)
AUDIT_STORY_FIELDS = (
    "storylines", "market_narrative_candidates", "archived_storylines",
    "archived_story_event_candidates", "story_event_candidates",
    "market_reaction_streams", "theme_streams", "unassigned_story_events",
    "storyline_summary",
)
AUDIT_STORYLINE_LIMIT = 12
AUDIT_STORY_TIMELINE_LIMIT = 6
AUDIT_STORY_CANDIDATE_LIMIT = 12
AUDIT_STORY_STREAM_LIMIT = 8
AUDIT_DETAIL_SOURCE_CONTRACT = "audit-detail-source-v1"
AUDIT_DETAIL_ARRAYS = {
    "briefs": "daily_news_briefs",
    "stories": "storylines",
    "decisions": "recent_decisions",
}
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
    # The Live first paint owns one fixed 90-minute decision window.  Keep it
    # on the bounded status contract while all older/detail history remains on
    # the paged market/audit resources.
    snapshot["recent_decisions"] = audit_decisions_payload(
        payload, decision_limit=18, prediction_limit=8,
    ).get("recent_decisions", [])
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
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    """Project the fixed audit summary; growing detail has separate owners."""
    snapshot = {
        key: copy.deepcopy(payload[key])
        for key in AUDIT_FIRST_PAGE_FIELDS
        if key in payload
    }
    summary = snapshot.get("daily_news_brief_summary")
    if isinstance(summary, dict):
        snapshot["daily_news_brief_summary"] = {
            key: summary.get(key) for key in DAILY_BRIEF_SUMMARY_FIELDS
            if key in summary
        }
    snapshot["news_evidence_resource"] = "/api/news-evidence"
    snapshot["audit_briefs_resource"] = "/api/audit-briefs"
    snapshot["audit_stories_resource"] = "/api/audit-stories"
    snapshot["audit_decisions_resource"] = "/api/audit-decisions"
    return snapshot


def audit_briefs_payload(
    payload: Mapping[str, Any], *, brief_limit: int = 3,
) -> dict[str, Any]:
    """Keep a bounded set of rendered briefs without duplicate raw JSON."""
    if not _audit_detail_source_present(payload, "briefs"):
        return {"generated_at": payload.get("generated_at")}
    rows = copy.deepcopy(payload["daily_news_briefs"])
    for row in rows[:brief_limit]:
        if isinstance(row, dict):
            row.pop("brief_json", None)
    return {
        "projection_contract": AUDIT_DETAIL_SOURCE_CONTRACT,
        "generated_at": payload.get("generated_at"),
        "daily_news_briefs": rows[:brief_limit],
    }


def audit_decisions_payload(
    payload: Mapping[str, Any], *, decision_limit: int = 20,
    prediction_limit: int = 8,
) -> dict[str, Any]:
    """Keep recent decision presentation evidence, excluding unused features."""
    if not _audit_detail_source_present(payload, "decisions"):
        return {"generated_at": payload.get("generated_at")}
    rows = copy.deepcopy(payload["recent_decisions"])
    compact = []
    for row in rows[:decision_limit]:
        if not isinstance(row, dict):
            continue
        row.pop("features", None)
        predictions = row.get("predictions")
        if isinstance(predictions, list):
            row["predictions"] = predictions[:prediction_limit]
        compact.append(row)
    return {
        "projection_contract": AUDIT_DETAIL_SOURCE_CONTRACT,
        "generated_at": payload.get("generated_at"),
        "recent_decisions": compact,
    }


def _audit_detail_source_present(payload: Mapping[str, Any], family: str) -> bool:
    rows = payload.get(AUDIT_DETAIL_ARRAYS[family])
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        return False
    if family == "stories":
        return all(
            field not in payload or (
                isinstance(payload[field], list)
                and all(isinstance(row, dict) for row in payload[field])
            )
            for field in AUDIT_STORY_FIELDS if field != "storyline_summary"
        )
    return True


def _renderable_audit_row(row: dict, family: str) -> bool:
    def records(value):
        return isinstance(value, list) and all(isinstance(item, dict) for item in value)

    def strings(value):
        return isinstance(value, list) and all(isinstance(item, str) for item in value)

    def numbers(source, fields):
        return all(source.get(field) is None or (
            type(source[field]) in (int, float) and math.isfinite(source[field])
        ) for field in fields)

    if family == "briefs":
        brief = row.get("brief")
        return (isinstance(row.get("model_version"), str)
                and (row.get("phase") is None or isinstance(row["phase"], str))
                and isinstance(brief, dict) and records(brief.get("items"))
                and all(isinstance(item.get("headline"), str)
                        and isinstance(item.get("summary"), str)
                        and strings(item.get("evidence_ids")) for item in brief["items"])
                and (brief.get("drivers") is None or strings(brief["drivers"]))
                and all(brief.get(field) is None or isinstance(brief[field], str)
                        for field in ("overview", "watch_next")))
    if family == "stories":
        return all(records(row.get(field)) for field in (
            "covered_roles", "missing_roles", "timeline", "market_reactions", "commentary", "background",
        ))
    return (records(row.get("predictions"))
            and numbers(row, ("bid", "ask", "long_return", "short_return"))
            and (row.get("outcome_status") in (None, "VALID") or strings(row.get("outcome_reason_codes")))
            and all(numbers(prediction, (
                "predicted_direction_u5", "predicted_news_residual_u5", "ev_long_u5", "ev_short_u5", "uncertainty_u5",
            )) for prediction in row["predictions"]))


def valid_audit_detail_payload(payload: Any, family: str, *, renderable: bool = False) -> bool:
    """Distinguish proven empty detail from old summary-derived placeholders."""
    if not isinstance(payload, dict) or "error" in payload:
        return False
    if not _audit_detail_source_present(payload, family):
        return False
    if renderable:
        if not all(_renderable_audit_row(row, family) for row in payload[AUDIT_DETAIL_ARRAYS[family]]):
            return False
        if family == "stories" and not all(
            _renderable_audit_row(row, family) for row in payload.get("archived_storylines", [])
        ):
            return False
        if "generated_at" in payload:
            try:
                datetime.fromisoformat(payload["generated_at"].replace("Z", "+00:00"))
            except (AttributeError, TypeError, ValueError):
                return False
    contract = payload.get("projection_contract")
    return contract in (None, AUDIT_DETAIL_SOURCE_CONTRACT) and (
        bool(payload[AUDIT_DETAIL_ARRAYS[family]])
        or contract == AUDIT_DETAIL_SOURCE_CONTRACT
    )


def _bounded_storyline(row: Any, *, timeline_limit: int) -> Any:
    if not isinstance(row, dict):
        return row
    timeline = row.get("timeline")
    if isinstance(timeline, list) and len(timeline) > timeline_limit:
        first = timeline_limit // 2
        row["timeline"] = timeline[:first] + timeline[-(timeline_limit - first):]
    for field in ("market_reactions", "commentary", "background"):
        values = row.get(field)
        if isinstance(values, list):
            row[field] = values[-4:]
    return row


def audit_stories_payload(
    payload: Mapping[str, Any], *,
    storyline_limit: int = AUDIT_STORYLINE_LIMIT,
    timeline_limit: int = AUDIT_STORY_TIMELINE_LIMIT,
    candidate_limit: int = AUDIT_STORY_CANDIDATE_LIMIT,
    stream_limit: int = AUDIT_STORY_STREAM_LIMIT,
) -> dict[str, Any]:
    """Project bounded story presentation detail and retain exact totals."""
    if not _audit_detail_source_present(payload, "stories"):
        return {"generated_at": payload.get("generated_at")}
    snapshot = {
        key: copy.deepcopy(payload[key])
        for key in AUDIT_STORY_FIELDS if key in payload
    }
    for field in (
        "storylines", "market_narrative_candidates", "archived_storylines",
    ):
        rows = snapshot.get(field)
        if isinstance(rows, list):
            snapshot[field] = [
                _bounded_storyline(row, timeline_limit=timeline_limit)
                for row in rows[:storyline_limit]
            ]
    for field in (
        "archived_story_event_candidates", "story_event_candidates",
        "unassigned_story_events",
    ):
        rows = snapshot.get(field)
        if isinstance(rows, list):
            snapshot[field] = rows[:candidate_limit]
    for field in ("market_reaction_streams", "theme_streams"):
        rows = snapshot.get(field)
        if isinstance(rows, list):
            snapshot[field] = rows[:stream_limit]
    snapshot["generated_at"] = payload.get("generated_at")
    snapshot["projection_contract"] = AUDIT_DETAIL_SOURCE_CONTRACT
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
