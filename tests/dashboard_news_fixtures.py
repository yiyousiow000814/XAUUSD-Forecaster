"""Shared News test data and isolated credential/cache lifetime."""
from __future__ import annotations

import os
from datetime import datetime, timedelta
import pytest
from xauusd_forecaster.dashboard import news_resources
from xauusd_forecaster.annotation import PROMPT_VERSION
from xauusd_forecaster.forward_ledger import ForwardLedger

@pytest.fixture(autouse=True)
def _isolated_dashboard_credentials(monkeypatch: pytest.MonkeyPatch):
    # API projections inspect quota configuration even when no provider request
    # is sent. Tests must not consult inherited or Windows User credentials;
    # credential-specific cases supply their own explicit fake source below.
    from xauusd_forecaster import news_scheduler

    # Each HTTP fixture represents a fresh process-owned News resource cache.
    monkeypatch.setattr(news_resources, "_NEWS_EVIDENCE_CACHE", {})
    monkeypatch.setattr(news_resources, "_NEWS_PROJECTION_CACHE", {})

    monkeypatch.setattr(news_scheduler, "_runtime_environment_value", lambda _name: "")
    user_reads = []
    if os.name == "nt":
        import winreg

        original_open = winreg.OpenKey

        def forbid_user_environment(root, subkey, *args, **kwargs):
            if root == winreg.HKEY_CURRENT_USER and str(subkey).lower() == "environment":
                user_reads.append(subkey)
                raise AssertionError("dashboard tests must not read Windows User credentials")
            return original_open(root, subkey, *args, **kwargs)

        monkeypatch.setattr(winreg, "OpenKey", forbid_user_environment)
    yield
    assert user_reads == []


def _basic_annotation_payload(
    ledger: ForwardLedger,
    *,
    source: str,
    item_id: str,
    parsed_at: datetime,
    event_time: datetime | None = None,
    xauusd_relevance: str = "MACRO_DRIVER",
) -> dict[str, object]:
    news = ledger.connection.execute(
        """SELECT headline,body,source_published_time FROM news_revisions
        WHERE source=? AND source_item_id=? AND revision_number=1""",
        (source, item_id),
    ).fetchone()
    evidence = " ".join(str(news["body"] or news["headline"]).split())[:120]
    return {
        "event_type": "economic_release",
        "entities": [],
        "hawkishness": 0.0,
        "inflation_impulse": 0.0,
        "growth_impulse": 0.0,
        "geopolitical_risk": 0.0,
        "usd_impulse": 0.0,
        "novelty": 0.5,
        "confidence": 0.8,
        "summary_zh": "已取得完整来源正文并完成结构化测试解析，相关证据已经保存。",
        "headline_zh": "测试经济数据发布",
        "primary_category": "growth_economy", "secondary_categories": [],
        "emerging_topic_zh": "", "record_kind": "FACT_EVENT",
        "actor": "US Treasury", "action": "published", "object": "official event",
        "location": "United States",
        "event_time": (
            event_time.isoformat() if event_time
            else str(news["source_published_time"] or parsed_at.isoformat())
        ),
        "claim_status": "CONFIRMED", "materiality": 0.8,
        "canonical_actor_id": "us_treasury", "action_family": "ECONOMIC_RELEASE",
        "canonical_object_id": item_id, "canonical_location_id": "us",
        "episode_key": item_id, "primary_story_title_zh": "测试事件",
        "secondary_contexts_zh": [], "relation_to_prior": "NONE",
        "document_kind": "REPORT", "material_event_key": item_id,
        "source_organization_id": source, "evidence_role": "CORE_CLAIM",
        "xauusd_relevance": xauusd_relevance, "review_priority": "FAST",
        "material_change": "NEW_EVENT", "time_sensitivity": "SAME_DAY",
        "semantic_reason_zh": "完整正文显示这是可能影响黄金的宏观事件。",
        "supporting_evidence": [evidence],
    }


def _append_basic_annotation(
    ledger: ForwardLedger,
    *,
    source: str,
    item_id: str,
    digest: str,
    parsed_at: datetime,
    prompt_version: str = PROMPT_VERSION,
    event_time: datetime | None = None,
    xauusd_relevance: str = "MACRO_DRIVER",
) -> None:
    annotation = _basic_annotation_payload(
        ledger,
        source=source,
        item_id=item_id,
        parsed_at=parsed_at,
        event_time=event_time,
        xauusd_relevance=xauusd_relevance,
    )
    ledger.append_annotation(
        {
            "annotation_id": f"annotation-{source}-{item_id}",
            "source": source,
            "source_item_id": item_id,
            "revision_number": 1,
            "raw_content_hash": digest,
            "annotation": annotation,
            "llm_model_version": "gemini-3.5-flash-lite",
            "prompt_version": prompt_version,
            "parse_started_at": parsed_at - timedelta(seconds=1),
            "parsed_at": parsed_at,
        }
    )

