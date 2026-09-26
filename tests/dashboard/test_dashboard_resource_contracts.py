from __future__ import annotations

import ast
import copy
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from xauusd_forecaster.dashboard import resource_contracts as module


def test_resource_serializers_do_not_depend_on_entrypoints_or_runtime_io() -> None:
    source = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
    imports = {
        name
        for node in ast.walk(source)
        for name in (
            [alias.name for alias in node.names] if isinstance(node, ast.Import)
            else [node.module] if isinstance(node, ast.ImportFrom) else []
        )
    }
    assert imports <= {
        "__future__", "copy", "json", "math", "datetime",
        'xauusd_forecaster.dashboard.payloads', "xauusd_forecaster.news_projection",
    }


@pytest.mark.parametrize("serializer", [
    module.remote_snapshot, module.audit_snapshot, module.audit_briefs_snapshot,
    module.audit_stories_snapshot,
    module.market_chart_snapshot,
])
def test_resource_serializers_preserve_caller_owned_source_values(serializer) -> None:
    payload = {
        "generated_at": "2026-09-06T00:00:00+00:00",
        "projection_contract": "audit-detail-source-v1",
        "daily_news_briefs": [{
            "brief_date": "2026-09-06", "brief_json": "retained source",
            "brief": {"items": [{"headline": "source", "evidence_ids": ["event-1"]}]},
        }],
        "recent_decisions": [{
            "decision_id": "decision-1", "features": {"retained": [1, 2]},
            "predictions": [{"model_identity": f"model-{i}"} for i in range(9)],
        }],
        "storylines": [{
            "storyline_id": "story-1",
            "timeline": [{"headline": f"source-{i}"} for i in range(100)],
            "market_reactions": [{"headline": f"reaction-{i}"} for i in range(5)],
        }],
        "learning_curves": {
            "models": [{"model_identity": "FULL", "lifecycle_status": "ARCHIVED"}],
            "version_groups": [{"model_identity": "FULL", "generation": i} for i in range(7)],
            "identity_curves": [{"model_identity": "FULL", "points": [
                {"decision_time": str(i), "cumulative_quote_return": i / 100}
                for i in range(49)
            ]}],
        },
        "market_chart": {"candles": [{
            "time": "2026-09-06T00:00:00+00:00", "open": 2.00123,
            "high": 3.00123, "low": 1.00123, "close": 2.50123, "ticks": 7,
        }], "decisions": []},
    }
    before = copy.deepcopy(payload)
    first = serializer(payload)
    second = serializer(payload)
    assert first == second
    assert payload == before


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
        "storylines": [],
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
    audit_stories = json.loads(module.audit_stories_snapshot(payload))
    index_rows, detail_rows = module.news_mirror_parts(payload)

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
