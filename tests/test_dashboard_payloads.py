from __future__ import annotations

import pytest

from xauusd_forecaster.dashboard.payloads import (
    audit_briefs_payload,
    audit_decisions_payload,
    audit_status_payload,
    audit_stories_payload,
    bounded_evidence_window,
    critical_status_payload,
)


def test_critical_status_keeps_fixed_news_totals_without_news_rows() -> None:
    metrics = {
        "schema_version": "news-metrics-v1",
        "articles": {"received": 7_678, "stored_revisions": 7_681},
        "events": {"independent": 3_469, "currently_model_eligible": 115},
    }
    payload = {
        "generated_at": "2026-08-25T20:45:28+00:00",
        "news_metrics": metrics,
        "recent_news": [{"body": "x" * 100_000}] * 1_000,
    }

    projected = critical_status_payload(payload)

    assert projected["news_metrics"] == metrics
    assert "recent_news" not in projected


def test_audit_summary_is_independent_of_every_growing_detail_family() -> None:
    summary = {
        "generated_at": "2026-08-20T00:00:00+00:00",
        "news_metrics": {"events": 12},
        "daily_news_brief_summary": {"brief_date": "2026-08-20"},
        "storyline_summary": {"total": 7},
    }
    baseline = audit_status_payload(summary)
    grown = {
        **summary,
        **{
            field: [{"value": "x" * 10_000}] * 1_000
            for field in (
                "recent_decisions", "daily_news_briefs", "storylines",
                "market_narrative_candidates", "archived_storylines",
                "archived_story_event_candidates", "story_event_candidates",
                "market_reaction_streams", "theme_streams",
                "unassigned_story_events",
            )
        },
    }

    assert audit_status_payload(grown) == baseline
    assert baseline["audit_briefs_resource"] == "/api/audit-briefs"
    assert baseline["audit_stories_resource"] == "/api/audit-stories"
    assert baseline["audit_decisions_resource"] == "/api/audit-decisions"


def test_audit_detail_projections_bound_items_and_nested_growth() -> None:
    payload = {
        "generated_at": "2026-08-20T00:00:00+00:00",
        "daily_news_briefs": [{
            "brief_date": str(index), "brief": {"title": "kept"},
            "brief_json": "duplicate" * 1_000,
        } for index in range(10)],
        "recent_decisions": [{
            "decision_id": str(index), "features": {"unused": "x" * 1_000},
            "predictions": [{"model_identity": str(model)} for model in range(20)],
        } for index in range(30)],
        "storylines": [{
            "storyline_id": str(index),
            "timeline": list(range(100)),
            "market_reactions": list(range(100)),
            "commentary": list(range(100)),
            "background": list(range(100)),
        } for index in range(30)],
        "story_event_candidates": list(range(100)),
        "unassigned_story_events": list(range(100)),
        "theme_streams": list(range(30)),
        "market_reaction_streams": list(range(30)),
    }

    briefs = audit_briefs_payload(payload, brief_limit=3)
    decisions = audit_decisions_payload(payload, decision_limit=20)
    stories = audit_stories_payload(payload)

    assert len(briefs["daily_news_briefs"]) == 3
    assert all("brief_json" not in row for row in briefs["daily_news_briefs"])
    assert len(decisions["recent_decisions"]) == 20
    assert all("features" not in row for row in decisions["recent_decisions"])
    assert all(len(row["predictions"]) == 8 for row in decisions["recent_decisions"])
    assert len(stories["storylines"]) == 12
    assert all(len(row["timeline"]) == 6 for row in stories["storylines"])
    assert all(row["timeline"][:3] == [0, 1, 2] for row in stories["storylines"])
    assert all(row["timeline"][-3:] == [97, 98, 99] for row in stories["storylines"])
    assert all(len(row["commentary"]) == 4 for row in stories["storylines"])
    assert len(stories["story_event_candidates"]) == 12
    assert len(stories["unassigned_story_events"]) == 12
    assert len(stories["theme_streams"]) == 8
    assert len(stories["market_reaction_streams"]) == 8


@pytest.mark.parametrize(
    ("seen_count", "unseen_count", "limit", "expected_seen", "expected_unseen"),
    [
        (97, 105, 60, 60, 60),
        (97, 105, 100, 97, 100),
        (4, 100, 60, 4, 60),
        (100, 3, 60, 60, 3),
        (2, 2, 60, 2, 2),
    ],
)
def test_bounded_evidence_window_keeps_each_visibility_state_inspectable(
    seen_count: int,
    unseen_count: int,
    limit: int,
    expected_seen: int,
    expected_unseen: int,
) -> None:
    rows = [
        {"event_key": f"seen-{index}", "model_seen": True}
        for index in range(seen_count)
    ] + [
        {"event_key": f"unseen-{index}", "model_seen": False}
        for index in range(unseen_count)
    ]

    bounded = bounded_evidence_window(rows, limit)

    assert len(bounded) == expected_seen + expected_unseen
    assert sum(bool(row["model_seen"]) for row in bounded) == expected_seen
    assert sum(not bool(row["model_seen"]) for row in bounded) == expected_unseen
    assert [row["event_key"] for row in bounded if row["model_seen"]] == [
        f"seen-{index}" for index in range(expected_seen)
    ]
    assert [row["event_key"] for row in bounded if not row["model_seen"]] == [
        f"unseen-{index}" for index in range(expected_unseen)
    ]


def test_bounded_evidence_window_rejects_invalid_limit() -> None:
    with pytest.raises(ValueError, match="must not be negative"):
        bounded_evidence_window([], -1)


def test_bounded_evidence_window_retains_every_current_event_before_history(
) -> None:
    rows = [
        {
            "event_key": f"seen-{index}",
            "model_seen": True,
            "broad_model_eligible": False,
        }
        for index in range(100)
    ] + [
        {
            "event_key": f"unseen-{index}",
            "model_seen": False,
            "broad_model_eligible": 70 <= index < 86,
        }
        for index in range(100)
    ]

    bounded = bounded_evidence_window(rows, 60)

    assert len(bounded) == 120
    assert {
        row["event_key"] for row in bounded if row["broad_model_eligible"]
    } == {f"unseen-{index}" for index in range(70, 86)}
    assert sum(bool(row["model_seen"]) for row in bounded) == 60
    assert sum(not bool(row["model_seen"]) for row in bounded) == 60
