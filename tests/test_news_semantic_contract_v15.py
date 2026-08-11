from __future__ import annotations

import hashlib
from datetime import UTC, datetime

import pytest

from xauusd_forecaster import annotation as annotation_module
from xauusd_forecaster.annotation import (
    annotate_pending_news,
    pending_annotation_records,
)
from xauusd_forecaster.forward_ledger import ForwardLedger
from xauusd_forecaster.gemini_quota import GeminiQuotaLedger
from xauusd_forecaster.news_semantics import (
    CURRENT_NEWS_PROMPT_VERSION,
    TARGET_NEWS_PROMPT_VERSION,
    news_annotation_schema,
    validate_news_annotation,
)
from xauusd_forecaster.news_evidence import event_evidence_rows
from xauusd_forecaster.news_impact import (
    TARGET_IMPACT_PROMPT_VERSION,
    pending_impact_records,
)


def _target_annotation(evidence: str) -> dict:
    return {
        "headline_zh": "美国职位空缺数据下降",
        "summary_zh": "美国劳工统计局报告职位空缺下降，该数据可能影响利率预期。",
        "primary_category": "inflation_employment",
        "secondary_categories": ["rates_fed"],
        "emerging_topic_zh": "职位空缺",
        "record_kind": "FACT_EVENT",
        "actor": "Bureau of Labor Statistics",
        "action": "reported",
        "object": "job openings",
        "location": "United States",
        "event_time": "2026-08-11",
        "claim_status": "OFFICIAL",
        "materiality": 0.8,
        "canonical_actor_id": "bureau_of_labor_statistics",
        "action_family": "ECONOMIC_RELEASE",
        "canonical_object_id": "us_job_openings",
        "canonical_location_id": "united_states",
        "episode_key": "us_job_openings_2026_06",
        "primary_story_title_zh": "美国职位空缺报告",
        "secondary_contexts_zh": [],
        "relation_to_prior": "NONE",
        "document_kind": "REPORT",
        "material_event_key": "us_job_openings_2026_06",
        "source_organization_id": "bureau_of_labor_statistics",
        "evidence_role": "CORE_CLAIM",
        "event_type": "jobs_report",
        "entities": ["Bureau of Labor Statistics"],
        "hawkishness": -0.2,
        "inflation_impulse": -0.1,
        "growth_impulse": -0.3,
        "geopolitical_risk": 0.0,
        "usd_impulse": -0.2,
        "novelty": 0.9,
        "confidence": 0.9,
        "xauusd_relevance": "MACRO_DRIVER",
        "review_priority": "FAST",
        "material_change": "NEW_EVENT",
        "time_sensitivity": "SAME_DAY",
        "semantic_reason_zh": "官方就业数据可能改变利率预期。",
        "supporting_evidence": [evidence],
    }


def test_v15_schema_is_versioned_without_mutating_v14() -> None:
    active = news_annotation_schema(CURRENT_NEWS_PROMPT_VERSION)
    target = news_annotation_schema(TARGET_NEWS_PROMPT_VERSION)

    assert "review_priority" not in active["properties"]
    assert "review_priority" in target["required"]
    assert active["$id"] == "xauusd.forward.news-annotation.v1"
    assert target["$id"] == "xauusd.forward.news-annotation.v15"


def test_v15_requires_evidence_copied_from_the_source() -> None:
    source = "The Bureau of Labor Statistics reported job openings fell in June."
    annotation = _target_annotation("job openings fell in June")
    validate_news_annotation(
        annotation,
        prompt_version=TARGET_NEWS_PROMPT_VERSION,
        source_text=source,
    )

    annotation["supporting_evidence"] = ["payrolls rose sharply"]
    with pytest.raises(ValueError, match="evidence is absent"):
        validate_news_annotation(
            annotation,
            prompt_version=TARGET_NEWS_PROMPT_VERSION,
            source_text=source,
        )


def test_v15_prompt_uses_context_not_keyword_or_casing() -> None:
    prompt = annotation_module._annotation_prompt(
        TARGET_NEWS_PROMPT_VERSION,
        "earthquake jolts city",
        "A local earthquake damaged buildings.",
    )

    assert "never from casing" in prompt
    assert "lowercase 'bls jolts report'" in prompt
    assert "'earthquake jolts city' is not JOLTS" in prompt
    assert "investment guide remains commentary" in prompt


def test_target_backfill_cannot_consume_active_priority_reserve(
    tmp_path, monkeypatch
) -> None:
    now = datetime(2026, 8, 11, 10, 0, tzinfo=UTC)
    ledger = ForwardLedger(tmp_path / "forward.sqlite3", now=now)
    body = "Complete Federal Reserve policy statement. " * 20
    ledger.append_news_revision({
        "source": "federal_reserve_monetary",
        "source_item_id": "priority-target",
        "source_published_time": now,
        "collector_first_seen_time": now,
        "fetched_time": now,
        "headline": "FOMC statement",
        "body": body,
        "content_hash": hashlib.sha256(body.encode()).hexdigest(),
        "cluster_id": "priority-target",
    })
    key = "test-key"
    quota = GeminiQuotaLedger(tmp_path / "gemini-quota.json")
    quota.seed(
        key,
        500 - annotation_module.GEMINI_DAILY_PRIORITY_RESERVE,
    )
    monkeypatch.setattr(
        annotation_module,
        "_call_gemini",
        lambda *_args, **_kwargs: pytest.fail("target backfill used reserved quota"),
    )

    statuses = annotate_pending_news(
        ledger,
        provider="gemini",
        api_key=key,
        limit=1,
        prompt_version=TARGET_NEWS_PROMPT_VERSION,
        allow_priority_reserve=False,
    )

    assert statuses == []
    assert quota.snapshot((key,))["total_sent"] == (
        500 - annotation_module.GEMINI_DAILY_PRIORITY_RESERVE
    )
    ledger.close()


def test_target_contract_fails_closed_for_non_gemini_provider(tmp_path) -> None:
    ledger = ForwardLedger(tmp_path / "forward.sqlite3")

    statuses = annotate_pending_news(
        ledger,
        provider="ollama",
        prompt_version=TARGET_NEWS_PROMPT_VERSION,
    )

    assert statuses == [{
        "status": "DISABLED",
        "reason": "TARGET_CONTRACT_REQUIRES_GEMINI",
    }]
    ledger.close()


def test_target_annotation_pipeline_persists_versioned_receipt(
    tmp_path, monkeypatch
) -> None:
    now = datetime(2026, 8, 11, 10, 0, tzinfo=UTC)
    ledger = ForwardLedger(tmp_path / "forward.sqlite3", now=now)
    body = (
        "The Bureau of Labor Statistics reported job openings fell in June. "
        * 12
    )
    digest = hashlib.sha256(body.encode()).hexdigest()
    ledger.append_news_revision({
        "source": "semantic-contract-test",
        "source_item_id": "target-pipeline",
        "source_published_time": now,
        "collector_first_seen_time": now,
        "fetched_time": now,
        "headline": "bls jolts report",
        "body": body,
        "content_hash": digest,
        "cluster_id": "target-pipeline",
    })
    seen_versions: list[str] = []

    def fake_call(
        _key, _model, _headline, _body, *, prompt_version=CURRENT_NEWS_PROMPT_VERSION
    ):
        seen_versions.append(prompt_version)
        return (
            _target_annotation("job openings fell in June"),
            annotation_module.DEFAULT_GEMINI_MODEL,
        )

    monkeypatch.setattr(annotation_module, "_call_gemini", fake_call)

    statuses = annotate_pending_news(
        ledger,
        provider="gemini",
        api_key="test-key",
        limit=1,
        prompt_version=TARGET_NEWS_PROMPT_VERSION,
        allow_priority_reserve=False,
    )

    assert [status["status"] for status in statuses] == ["OK"]
    assert seen_versions == [TARGET_NEWS_PROMPT_VERSION]
    row = ledger.connection.execute(
        "SELECT prompt_version FROM news_annotations WHERE annotation_id=?",
        (statuses[0]["annotation_id"],),
    ).fetchone()
    assert row["prompt_version"] == TARGET_NEWS_PROMPT_VERSION
    ledger.close()


def test_v14_and_v15_annotations_coexist_without_activating_v15(tmp_path) -> None:
    now = datetime(2026, 8, 11, 10, 0, tzinfo=UTC)
    body = (
        "The Bureau of Labor Statistics reported job openings fell in June. "
        * 12
    )
    digest = hashlib.sha256(body.encode()).hexdigest()
    ledger = ForwardLedger(tmp_path / "forward.sqlite3", now=now)
    ledger.append_news_revision({
        "source": "semantic-contract-test",
        "source_item_id": "jobs",
        "source_published_time": now,
        "collector_first_seen_time": now,
        "fetched_time": now,
        "headline": "bls jolts report",
        "body": body,
        "content_hash": digest,
        "cluster_id": "jobs",
    })
    target = _target_annotation("job openings fell in June")
    active = {
        key: value for key, value in target.items()
        if key not in {
            "xauusd_relevance", "review_priority", "material_change",
            "time_sensitivity", "semantic_reason_zh", "supporting_evidence",
        }
    }
    common = {
        "source": "semantic-contract-test",
        "source_item_id": "jobs",
        "revision_number": 1,
        "raw_content_hash": digest,
        "llm_model_version": annotation_module.DEFAULT_GEMINI_MODEL,
        "parse_started_at": now,
        "parsed_at": now,
    }
    ledger.append_annotation({
        **common,
        "annotation_id": "active",
        "prompt_version": CURRENT_NEWS_PROMPT_VERSION,
        "annotation": active,
    })

    assert pending_annotation_records(
        ledger.connection,
        prompt_version=CURRENT_NEWS_PROMPT_VERSION,
    ) == []
    assert len(pending_annotation_records(
        ledger.connection,
        prompt_version=TARGET_NEWS_PROMPT_VERSION,
    )) == 1

    ledger.append_annotation({
        **common,
        "annotation_id": "target",
        "prompt_version": TARGET_NEWS_PROMPT_VERSION,
        "annotation": target,
    })
    rows = ledger.connection.execute(
        "SELECT prompt_version FROM news_annotations ORDER BY prompt_version"
    ).fetchall()
    assert {row["prompt_version"] for row in rows} == {
        CURRENT_NEWS_PROMPT_VERSION,
        TARGET_NEWS_PROMPT_VERSION,
    }
    target_impacts = pending_impact_records(
        ledger.connection,
        observed_at=now,
        annotation_prompt_version=TARGET_NEWS_PROMPT_VERSION,
        impact_prompt_version=TARGET_IMPACT_PROMPT_VERSION,
    )
    assert [row["annotation_id"] for row in target_impacts] == ["target"]
    ledger.close()


def test_target_annotation_is_not_visible_to_active_evidence(tmp_path) -> None:
    now = datetime(2026, 8, 11, 10, 0, tzinfo=UTC)
    body = (
        "The Bureau of Labor Statistics reported job openings fell in June. "
        * 12
    )
    digest = hashlib.sha256(body.encode()).hexdigest()
    ledger = ForwardLedger(tmp_path / "target-only.sqlite3", now=now)
    ledger.append_news_revision({
        "source": "semantic-contract-test",
        "source_item_id": "target-only",
        "source_published_time": now,
        "collector_first_seen_time": now,
        "fetched_time": now,
        "headline": "bls jolts report",
        "body": body,
        "content_hash": digest,
        "cluster_id": "target-only",
    })
    ledger.append_annotation({
        "annotation_id": "target-only",
        "source": "semantic-contract-test",
        "source_item_id": "target-only",
        "revision_number": 1,
        "raw_content_hash": digest,
        "llm_model_version": annotation_module.DEFAULT_GEMINI_MODEL,
        "prompt_version": TARGET_NEWS_PROMPT_VERSION,
        "parse_started_at": now,
        "parsed_at": now,
        "annotation": _target_annotation("job openings fell in June"),
    })

    assert event_evidence_rows(ledger, now) == []
    ledger.close()
