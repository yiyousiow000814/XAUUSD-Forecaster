from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from xauusd_forecaster.dashboard import resource_contracts as module


def test_critical_status_excludes_growing_resources_and_keeps_references() -> None:
    body = "完整正文" * 2_000
    payload = {
        "news_metrics": {
            "schema_version": "news-metrics-v1",
            "articles": {"received": 7_678, "stored_revisions": 7_681},
            "events": {"independent": 3_469, "currently_model_eligible": 115},
        },
        "training": {"complete_rows": 200, "models": [{"duplicate": body}]},
        "learning_curves": {
            "models": [
                {"lifecycle_status": "LATEST", "model_version": "latest"},
                {"lifecycle_status": "ARCHIVED", "model_version": "old"},
            ],
            "identity_curves": [body],
            "full_minus_market": [body],
            "broad_full_minus_core_full": [body],
        },
        "recent_news": [{
            "source": "example", "source_item_id": str(index),
            "revision_number": 1, "headline": f"新闻 {index}",
            "summary_zh": body, "category": "其他",
            "content_fetch_status": "UNAVAILABLE",
            "content_error_type": "HTTPError",
            "annotation_status": "NOT_REQUIRED",
            "annotation_reason_code": "SEARCH_LEAD",
            "annotation_reason": "搜索线索：来自聚合发现源，不是独立官方发布",
        } for index in range(100)],
        "recent_decisions": [{
            "id": index, "features": {"unused": index},
            "predictions": list(range(12)),
        } for index in range(30)],
        "daily_news_briefs": [
            {"brief_date": f"2026-08-{20 - index:02d}", "revision_number": 1}
            for index in range(5)
        ],
        "news_evidence": [
            {"id": index, "model_seen": index < 97}
            for index in range(202)
        ],
        "market_chart": {
            "candles": [{"time": "2026-08-06T00:00:00Z", "open": 1,
                         "high": 2, "low": 0.5, "close": 1.5}],
            "overview_candles": [{"time": "2026-08-05T00:00:00Z", "open": 1,
                                  "high": 2, "low": 0.5, "close": 1.5}],
            "training_markers": [{"time": "2026-08-06T00:00:00Z"}],
            "decisions": [{
            "source_decision_id": "d1", "decision_time": "2026-08-06T00:00:00+00:00",
            "model_identity": "MARKET_ONLY", "model_version": "large-unused-field",
            "recommended_action": "SHORT", "ev_long_u5": -0.2,
            "ev_short_u5": 0.1, "policy_expected_action": "SHORT",
            "policy_consistent": True, "frozen_record": True,
        }]},
    }

    encoded = module.remote_snapshot(payload)
    mirrored = json.loads(encoded)
    audit = json.loads(module.audit_snapshot(payload))
    audit_briefs = json.loads(module.audit_briefs_snapshot(payload))
    audit_decisions = json.loads(module.audit_decisions_snapshot(payload))
    audit_stories = json.loads(module.audit_stories_snapshot(payload))
    index_rows, detail_rows = module.news_mirror_parts(payload)
    learning = json.loads(module.learning_snapshot(payload))

    assert len(encoded) <= module.REMOTE_PAYLOAD_LIMIT_BYTES
    assert mirrored["news_index_resource"] == "/api/news-index"
    assert mirrored["news_evidence_resource"] == "/api/news-evidence"
    assert mirrored["audit_resource"] == "/api/audit"
    assert mirrored["learning_resource"] == "/api/learning"
    assert mirrored["news_metrics"] == payload["news_metrics"]
    assert detail_rows[0]["payload"]["summary_zh"] == body
    assert index_rows[0]["content_fetch_status"] == "UNAVAILABLE"
    assert index_rows[0]["content_error_type"] == "HTTPError"
    assert index_rows[0]["annotation_reason_code"] == "SEARCH_LEAD"
    assert index_rows[0]["annotation_reason"].startswith("搜索线索")
    assert "content_fetch_status" not in detail_rows[0]["payload"]
    assert "annotation_reason" not in detail_rows[0]["payload"]
    assert len(detail_rows[0]["detail_key"]) == 64
    assert mirrored["market_chart"]["decisions"] == []
    assert "news_evidence" not in mirrored
    assert "recent_news" not in mirrored
    assert len(mirrored["recent_decisions"]) == 18
    assert "features" not in mirrored["recent_decisions"][0]
    assert len(mirrored["recent_decisions"][0]["predictions"]) == 8
    market_decision = json.loads(module.market_chart_snapshot(payload))["decisions"][0]
    assert market_decision["source_decision_id"] == "d1"
    assert market_decision["model_version"] == "unused-field"
    assert "recent_decisions" not in audit
    assert "daily_news_briefs" not in audit
    assert "storylines" not in audit
    assert audit["audit_briefs_resource"] == "/api/audit-briefs"
    assert audit["audit_stories_resource"] == "/api/audit-stories"
    assert audit["audit_decisions_resource"] == "/api/audit-decisions"
    assert len(audit_decisions["recent_decisions"]) == module.REMOTE_DECISION_LIMIT
    assert len(audit_briefs["daily_news_briefs"]) == min(
        len(payload["daily_news_briefs"]), module.REMOTE_DAILY_BRIEF_LIMIT,
    )
    assert audit_stories.get("storylines", []) == []
    assert audit["news_evidence_resource"] == "/api/news-evidence"
    assert learning["learning_curves"]["models"] == [
        {"lifecycle_status": "LATEST", "model_version": "latest"},
    ]
    assert learning["learning_curves"]["archived_model_count"] == 1
    assert learning["learning_history_resource"] == "/api/learning-history"
    assert learning["learning_curves"]["identity_curves"] == [body]
    assert learning["learning_curves"]["full_minus_market"] == [body]
    assert learning["learning_curves"]["broad_full_minus_core_full"] == [body]
    assert "learning_curves" not in mirrored
    assert "models" not in mirrored["training"]
    assert len(index_rows) == 100


def test_audit_story_projection_keeps_production_shaped_detail_below_transport_limit() -> None:
    payload = {
        "generated_at": "2026-08-25T20:12:49+00:00",
        "storyline_summary": {"total": 500},
        "storylines": [{
            "storyline_id": f"story-{index}",
            "title": f"Story {index}",
            "timeline": [{"headline": "黄金与宏观事件" * 20}] * 8,
            "market_reactions": [{"headline": "市场反应" * 10}] * 6,
            "commentary": [{"headline": "评论" * 10}] * 6,
            "background": [{"headline": "背景" * 10}] * 6,
        } for index in range(20)],
        "story_event_candidates": [
            {"candidate_id": index, "headline": "候选事件" * 20}
            for index in range(50)
        ],
        "unassigned_story_events": [
            {"event_key": index, "headline": "未分配事件" * 20}
            for index in range(50)
        ],
        "theme_streams": [{"theme_id": index} for index in range(12)],
        "market_reaction_streams": [
            {"stream_id": index} for index in range(12)
        ],
    }

    previous_selection = module.audit_stories_payload(
        payload, storyline_limit=20, timeline_limit=8,
        candidate_limit=50, stream_limit=12,
    )
    previous_bytes = json.dumps(
        previous_selection, ensure_ascii=False, allow_nan=False,
        separators=(",", ":"),
    ).encode("utf-8")
    encoded = module.audit_stories_snapshot(payload, "a" * 40)
    projected = json.loads(encoded)

    assert len(previous_bytes) > module.AUDIT_DETAIL_LIMIT_BYTES
    assert 0 < len(projected["storylines"]) == 12
    assert projected["storyline_summary"]["total"] == 500
    assert len(projected["story_event_candidates"]) == 12
    assert len(projected["unassigned_story_events"]) == 12
    assert len(encoded) <= module.AUDIT_DETAIL_LIMIT_BYTES


@pytest.mark.parametrize(
    "field",
    (
        "news_evidence", "daily_news_briefs", "storylines",
        "future_accumulated_records", "future_user_history",
    ),
)
def test_critical_status_size_is_independent_of_unknown_growing_state(field) -> None:
    base = {
        "generated_at": "2026-08-19T10:00:00+00:00",
        "system": {"online": True, "components": {}},
        "counts": {"decision_events": 10},
    }
    baseline = module.remote_snapshot(base)
    grown = {
        **base,
        field: [{"id": index, "body": "x" * 2_000} for index in range(10_000)],
    }

    encoded = module.remote_snapshot(grown)

    assert encoded == baseline
    assert len(encoded) < module.REMOTE_PAYLOAD_LIMIT_BYTES // 4


def test_news_detail_batches_stay_bounded() -> None:
    rows = [{
        "detail_key": f"{index:064x}", "detail_hash": f"{index + 1:064x}",
        "payload": {"summary_zh": "摘要" * 20_000},
    } for index in range(8)]
    batches = module.news_detail_batches(rows)
    assert len(batches) > 1
    assert sum(len(batch) for batch in batches) == len(rows)
    for batch in batches:
        assert len(batch) <= module.NEWS_DETAIL_BATCH_ITEMS
        encoded = json.dumps(
            {"items": batch}, ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
        assert len(encoded) <= module.NEWS_DETAIL_BATCH_LIMIT_BYTES


def test_news_index_batches_stay_bounded() -> None:
    rows = [{
        "detail_key": f"{index:064x}", "category": "战争/地缘",
        "collector_first_seen_time": f"2026-08-07T00:{index:02d}:00+00:00",
        "headline": "标题" * 5_000,
    } for index in range(45)]
    batches = module.news_index_batches(rows)
    assert sum(len(batch) for batch in batches) == len(rows)
    for batch in batches:
        assert len(batch) <= module.NEWS_WRITE_BATCH_ITEMS
        encoded = json.dumps(
            {"items": batch}, ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
        assert len(encoded) <= module.NEWS_INDEX_BATCH_LIMIT_BYTES


def test_learning_history_records_have_stable_keys_and_bounded_batches() -> None:
    payload = {
        "learning_curves": {
            "models": [{
                "model_identity": "FULL", "model_version": "model-v1",
                "created_at": "2026-08-10T01:00:00+00:00",
            }],
            "version_groups": [{
                "model_identity": "FULL", "training_dataset_hash": "hash-1",
                "created_at": "2026-08-10T01:00:00+00:00", "generation": 3,
            }],
            "identity_curves": [{
                "model_identity": "FULL",
                "points": [{
                    "decision_time": "2026-08-10T01:05:00+00:00",
                    "cumulative_quote_return": 0.01,
                }],
                "points_30m": [{
                    "decision_time": "2026-08-10T01:30:00+00:00",
                    "cumulative_quote_return": 0.02,
                }],
            }],
        },
        "execution_learning": {"models": []},
    }

    first = module.learning_history_records(payload)
    second = module.learning_history_records(payload)

    assert first == second
    assert {row["resource"] for row in first} == {
        "model", "version-group", "curve-5m", "curve-30m",
        "curve-overview", "version-overview",
    }
    assert all(len(row["payload_hash"]) == 64 for row in first)
    batches = module.learning_history_batches(first * 2_000)
    assert len(batches) > 1
    for batch in batches:
        encoded = json.dumps(
            {"records": batch}, ensure_ascii=False, separators=(",", ":"),
        ).encode("utf-8")
        assert len(encoded) <= module.LEARNING_HISTORY_BATCH_LIMIT_BYTES


def test_visual_overviews_stay_bounded_and_preserve_the_full_span() -> None:
    points = [{
        "decision_time": (
            datetime(2026, 1, 1, tzinfo=timezone.utc)
            + timedelta(minutes=5 * index)
        ).isoformat(),
        "cumulative_quote_return": (-1 if index == 50_000 else index / 100_000),
    } for index in range(100_000)]

    overview = module._visual_curve_overview(points, 240)

    assert len(overview) <= 240
    assert overview[0]["decision_time"] == points[0]["decision_time"]
    assert overview[-1]["decision_time"] == points[-1]["decision_time"]
    assert any(
        row["decision_time"] == points[50_000]["decision_time"]
        for row in overview
    )
    assert not any(row["source_gap_before"] for row in overview)

    groups = [{
        "created_at": point["decision_time"],
        "generation": index,
        "cumulative_quote_return": point["cumulative_quote_return"],
        "cadence_metrics": {
            "FIXED_30M": {
                "cumulative_quote_return": 2 if index == 75_000 else -index / 100_000,
            },
        },
    } for index, point in enumerate(points)]
    group_overview = module._visual_version_overview(groups, 60)

    assert len(group_overview) <= 60
    assert group_overview[0] == groups[0]
    assert group_overview[-1] == groups[-1]
    assert groups[50_000] in group_overview
    assert groups[75_000] in group_overview


def test_curve_overview_marks_only_real_source_gaps() -> None:
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    offsets = [0, 5, 10, 120, 125]
    points = [{
        "decision_time": (start + timedelta(minutes=offset)).isoformat(),
        "cumulative_quote_return": index / 100,
    } for index, offset in enumerate(offsets)]

    overview = module._visual_curve_overview(points, 240)

    assert [row["source_gap_before"] for row in overview] == [
        False, False, False, True, False,
    ]

    compressed_source = module._visual_curve_overview(
        points, 240, infer_source_gaps=False,
    )
    assert not any(row["source_gap_before"] for row in compressed_source)


def test_decision_overviews_are_incremental_bounded_and_frequency_scoped() -> None:
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    decisions = [{
        "source_decision_id": f"decision-{index}",
        "decision_time": (start + timedelta(minutes=5 * index)).isoformat(),
        "model_identity": "FULL",
        "recommended_action": ("SHORT" if index % 7 == 0 else "LONG"),
    } for index in range(2_000)]

    summaries = module._update_decision_overviews({}, decisions, None)
    five = summaries["FULL\0" "5m"]
    half_hour = summaries["FULL\0" "30m"]

    assert five["source_decision_count"] == 2_000
    assert len(five["decisions"]) <= module.MARKET_OVERVIEW_DECISIONS_PER_SERIES
    assert five["decisions"][0]["source_decision_id"] == "decision-0"
    assert five["decisions"][-1]["source_decision_id"] == "decision-1999"
    assert half_hour["source_decision_count"] == 334
    assert all(
        datetime.fromisoformat(row["decision_time"]).minute % 30 == 0
        for row in half_hour["decisions"]
    )

    unchanged = module._update_decision_overviews(
        summaries, decisions[-24:], decisions[-1]["decision_time"],
    )
    assert unchanged == summaries

    settled = {
        **five["decisions"][0],
        "outcome_status": "MATURE",
        "value_quote_return": 0.001,
    }
    refreshed = module._update_decision_overviews(
        summaries, [settled], decisions[-1]["decision_time"],
    )
    refreshed_five = refreshed["FULL\0" "5m"]
    assert refreshed_five["source_decision_count"] == 2_000
    refreshed_row = next(
        row for row in refreshed_five["decisions"]
        if row["source_decision_id"] == settled["source_decision_id"]
    )
    assert refreshed_row["outcome_status"] == "MATURE"
    assert refreshed_row["value_quote_return"] == 0.001


def test_learning_summary_size_is_fixed_as_history_grows() -> None:
    groups = []
    points = []
    for index in range(1_000):
        stamp = (datetime(2026, 8, 1, tzinfo=timezone.utc)
                 + timedelta(minutes=5 * index)).isoformat()
        groups.append({
            "model_identity": "FULL", "training_dataset_hash": f"hash-{index}",
            "created_at": stamp, "generation": index, "lifecycle_status": "ARCHIVED",
        })
        points.append({"decision_time": stamp, "cumulative_quote_return": index / 1000})
    payload = {
        "learning_curves": {
            "models": [], "version_groups": groups,
            "identity_curves": [{"model_identity": "FULL", "points": points}],
        },
        "execution_learning": {"models": []},
    }

    summary = json.loads(module.learning_snapshot(payload))

    assert len(summary["learning_curves"]["version_groups"]) == 6
    assert len(summary["learning_curves"]["identity_curves"][0]["points"]) == 48
    assert summary["learning_history_manifest"]["version_group_total"] == 1_000
    assert len(module.learning_snapshot(payload)) < 100_000
