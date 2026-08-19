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
from xauusd_forecaster.model_gateway import GeminiModelGateway
from xauusd_forecaster.news_semantics import (
    CURRENT_NEWS_PROMPT_VERSION,
    LEGACY_NEWS_PROMPT_VERSION,
    LEGACY_SEMANTIC_NEWS_PROMPT_VERSION,
    PREVIOUS_NEWS_PROMPT_VERSION,
    canonicalize_active_annotation,
    news_annotation_schema,
    validate_news_annotation,
)
from xauusd_forecaster.news_evidence import event_evidence_rows
from xauusd_forecaster.news_impact import (
    IMPACT_PROMPT_VERSION,
    pending_impact_records,
)
from tests.model_accounting_fakes import CallbackModelAccountant


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


def test_v17_schema_is_versioned_without_mutating_history() -> None:
    news_annotation_schema.cache_clear()
    legacy = news_annotation_schema(LEGACY_NEWS_PROMPT_VERSION)
    legacy_semantic = news_annotation_schema(LEGACY_SEMANTIC_NEWS_PROMPT_VERSION)
    previous = news_annotation_schema(PREVIOUS_NEWS_PROMPT_VERSION)
    active = news_annotation_schema(CURRENT_NEWS_PROMPT_VERSION)

    assert "review_priority" not in legacy["properties"]
    assert "review_priority" in active["required"]
    assert legacy["$id"] == "xauusd.forward.news-annotation.v1"
    assert legacy_semantic["$id"] == "xauusd.forward.news-annotation.v15"
    assert previous["$id"] == "xauusd.forward.news-annotation.v16"
    assert active["$id"] == "xauusd.forward.news-annotation.v17"
    assert "named_references" not in previous["required"]
    assert "named_references" not in active["required"]
    assert "named_references" not in active["properties"]
    assert active["properties"]["supporting_evidence"]["items"]["maxLength"] == 240


def test_semantic_generations_require_evidence_copied_from_the_source() -> None:
    source = "The Bureau of Labor Statistics reported job openings fell in June."
    annotation = _target_annotation("job openings fell in June")
    validate_news_annotation(
        annotation,
        prompt_version=CURRENT_NEWS_PROMPT_VERSION,
        source_text=source,
    )

    annotation["supporting_evidence"] = ["payrolls rose sharply"]
    with pytest.raises(ValueError, match="evidence is absent"):
        validate_news_annotation(
            annotation,
            prompt_version=CURRENT_NEWS_PROMPT_VERSION,
            source_text=source,
        )


def test_v17_accepts_source_grounded_latin_without_provider_declaration() -> None:
    prose = "Market expects growth to be strong"
    source = f"{prose} after the policy update."
    annotation = _target_annotation(prose)
    annotation["supporting_evidence"] = [prose]
    annotation["summary_zh"] = f"报道引用了 {prose} 这一原文表述，并继续解释事件影响。"

    annotation_module._validate_current_result(
        annotation, headline="Market commentary", body=source,
        prompt_version=CURRENT_NEWS_PROMPT_VERSION,
    )


def test_v17_ledger_cannot_persist_ungrounded_visible_latin(
    tmp_path,
) -> None:
    now = datetime(2026, 8, 19, 2, 0, tzinfo=UTC)
    prose = "Market expects growth to be strong"
    body = f"{prose} after the policy update. " * 12
    digest = hashlib.sha256(body.encode()).hexdigest()
    ledger = ForwardLedger(tmp_path / "forward.sqlite3", now=now)
    ledger.append_news_revision({
        "source": "named-reference-contract",
        "source_item_id": "prose-abuse",
        "source_published_time": now,
        "collector_first_seen_time": now,
        "fetched_time": now,
        "headline": "Market commentary",
        "body": body,
        "content_hash": digest,
        "cluster_id": "prose-abuse",
    })
    annotation = _target_annotation(prose)
    annotation["supporting_evidence"] = [prose]
    annotation["summary_zh"] = (
        "报道称 OpenAI Launches New Model，并继续讨论市场反应与政策影响。"
    )

    with pytest.raises(ValueError, match="UNGROUNDED_LATIN_DISPLAY"):
        ledger.append_annotation({
            "annotation_id": "prose-abuse",
            "source": "named-reference-contract",
            "source_item_id": "prose-abuse",
            "revision_number": 1,
            "raw_content_hash": digest,
            "llm_model_version": annotation_module.DEFAULT_GEMINI_MODEL,
            "prompt_version": CURRENT_NEWS_PROMPT_VERSION,
            "parse_started_at": now,
            "parsed_at": now,
            "annotation": annotation,
        })
    assert ledger.count("news_annotations") == 0
    ledger.close()


def test_v17_schema_rejects_obsolete_named_reference_declarations() -> None:
    source = "OpenAI released an update."
    annotation = _target_annotation("OpenAI released an update")
    annotation["named_references"] = [{"exact_text": "OpenAI"}]
    with pytest.raises(ValueError, match="unknown schema fields: named_references"):
        validate_news_annotation(
            annotation,
            prompt_version=CURRENT_NEWS_PROMPT_VERSION,
            source_text=source,
        )


def test_v17_only_applies_lossless_active_cleanup() -> None:
    source = (
        "FinCEN announced: it will delete previously reported information. "
        "The same evidence appears twice. The same evidence appears twice."
    )
    annotation = _target_annotation(
        "FinCEN announced, it will delete previously reported information"
    )
    annotation["secondary_categories"] = [
        "rates_fed", "rates_fed", "inflation_employment",
    ]
    annotation["primary_category"] = "inflation_employment"
    canonicalize_active_annotation(annotation, source_text=source)
    assert annotation["secondary_categories"] == ["rates_fed"]
    assert annotation["supporting_evidence"] == [
        "FinCEN announced: it will delete previously reported information"
    ]
    validate_news_annotation(
        annotation,
        prompt_version=CURRENT_NEWS_PROMPT_VERSION,
        source_text=source,
    )

    long_source = "A" * 260
    annotation["supporting_evidence"] = [long_source]
    canonicalize_active_annotation(annotation, source_text=long_source)
    assert annotation["supporting_evidence"] == ["A" * 240]
    validate_news_annotation(
        annotation,
        prompt_version=CURRENT_NEWS_PROMPT_VERSION,
        source_text=long_source,
    )

    for unsafe in (
        "FinCEN claimed it will delete previously reported information",
        "The same evidence appears twice",
    ):
        annotation["supporting_evidence"] = [unsafe]
        canonicalize_active_annotation(annotation, source_text=source)
        assert annotation["supporting_evidence"] == [unsafe]

    annotation["secondary_categories"] = [{"unexpected": "object"}]
    canonicalize_active_annotation(annotation, source_text=source)
    with pytest.raises(ValueError, match="contains a non-string"):
        validate_news_annotation(
            annotation,
            prompt_version=CURRENT_NEWS_PROMPT_VERSION,
            source_text=source,
        )


def test_v15_prompt_uses_context_not_keyword_or_casing() -> None:
    prompt = annotation_module._annotation_prompt(
        CURRENT_NEWS_PROMPT_VERSION,
        "earthquake jolts city",
        "A local earthquake damaged buildings.",
    )

    assert "never from casing" in prompt
    assert "lowercase 'bls jolts report'" in prompt
    assert "'earthquake jolts city' is not JOLTS" in prompt
    assert "investment guide remains commentary" in prompt


def test_v17_retains_v16_current_event_and_transmission_evidence() -> None:
    prompt = annotation_module._annotation_prompt(
        CURRENT_NEWS_PROMPT_VERSION,
        "Miner reports quarterly earnings",
        "The company mentioned rates while discussing its mine results.",
    )

    assert "Apply a narrow XAUUSD transmission test" in prompt
    assert "mine drill results" in prompt
    assert "Incidental mentions of the Fed" in prompt
    assert "MACRO_DRIVER requires exact evidence of both" in prompt
    assert "official US CPI surprise" in prompt
    assert "CONTEXT_ONLY is not a parking class" in prompt
    assert "non-US local inflation" in prompt
    assert "Global or non-US employment" in prompt
    assert "being official macro data is not enough" in prompt
    assert "genre does not erase quoted current market facts" in prompt
    assert "supporting_evidence is a copy field" in prompt
    assert "Never translate, paraphrase" in prompt
    assert "Any Latin text retained in a Chinese display field" in prompt
    assert "final visible field must remain Chinese-primary overall" in prompt
    assert "named_references" not in prompt


def test_target_backfill_cannot_bypass_scheduler_capacity_refusal(
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
    def post_json(_key, _model, method, _payload, *, timeout):
        del timeout
        pytest.fail("scheduler-refused generation reached the provider")
    monkeypatch.setattr(GeminiModelGateway, "_post_json", staticmethod(post_json))

    statuses = annotate_pending_news(
        ledger,
        provider="gemini",
        api_key="test-key",
        limit=1,
        prompt_version=CURRENT_NEWS_PROMPT_VERSION,
        allow_priority_reserve=False,
        request_accountant=CallbackModelAccountant(lambda _usage: False),
    )

    assert statuses[0]["status"] == "DEFERRED"
    ledger.close()


def test_current_contract_fails_closed_for_non_gemini_provider(tmp_path) -> None:
    ledger = ForwardLedger(tmp_path / "forward.sqlite3")

    statuses = annotate_pending_news(
        ledger,
        provider="ollama",
        prompt_version=CURRENT_NEWS_PROMPT_VERSION,
    )

    assert statuses == [{
        "status": "DISABLED",
        "reason": "CURRENT_CONTRACT_REQUIRES_GEMINI",
    }]
    ledger.close()


def test_previous_generation_cannot_execute_after_v17_handover(tmp_path) -> None:
    ledger = ForwardLedger(tmp_path / "forward.sqlite3")

    with pytest.raises(ValueError, match="unsupported news prompt version"):
        annotate_pending_news(
            ledger,
            provider="gemini",
            prompt_version=PREVIOUS_NEWS_PROMPT_VERSION,
        )

    ledger.close()


def test_current_annotation_pipeline_persists_versioned_receipt(
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
        _pool, _index, _model, _headline, _body,
        *, prompt_version=CURRENT_NEWS_PROMPT_VERSION,
    ):
        seen_versions.append(prompt_version)
        return (
            _target_annotation("job openings fell in June"),
            annotation_module.DEFAULT_GEMINI_MODEL,
        )

    monkeypatch.setattr(annotation_module._GeminiRequestPool, "call", fake_call)

    statuses = annotate_pending_news(
        ledger,
        provider="gemini",
        api_key="test-key",
        limit=1,
        prompt_version=CURRENT_NEWS_PROMPT_VERSION,
        allow_priority_reserve=False,
        request_accountant=CallbackModelAccountant(lambda _usage: True),
    )

    assert [status["status"] for status in statuses] == ["OK"]
    assert seen_versions == [CURRENT_NEWS_PROMPT_VERSION]
    row = ledger.connection.execute(
        "SELECT prompt_version FROM news_annotations WHERE annotation_id=?",
        (statuses[0]["annotation_id"],),
    ).fetchone()
    assert row["prompt_version"] == CURRENT_NEWS_PROMPT_VERSION
    ledger.close()


def test_previous_v16_and_active_v17_annotations_coexist(tmp_path) -> None:
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
    previous_target = dict(target)
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
        "annotation_id": "previous",
        "prompt_version": PREVIOUS_NEWS_PROMPT_VERSION,
        "annotation": previous_target,
    })

    assert pending_annotation_records(
        ledger.connection,
        prompt_version=PREVIOUS_NEWS_PROMPT_VERSION,
    ) == []
    assert len(pending_annotation_records(
        ledger.connection,
        prompt_version=CURRENT_NEWS_PROMPT_VERSION,
    )) == 1

    ledger.append_annotation({
        **common,
        "annotation_id": "target",
        "prompt_version": CURRENT_NEWS_PROMPT_VERSION,
        "annotation": target,
    })
    rows = ledger.connection.execute(
        "SELECT prompt_version FROM news_annotations ORDER BY prompt_version"
    ).fetchall()
    assert {row["prompt_version"] for row in rows} == {
        CURRENT_NEWS_PROMPT_VERSION,
        PREVIOUS_NEWS_PROMPT_VERSION,
    }
    target_impacts = pending_impact_records(
        ledger.connection,
        observed_at=now,
        annotation_prompt_version=CURRENT_NEWS_PROMPT_VERSION,
        impact_prompt_version=IMPACT_PROMPT_VERSION,
    )
    assert [row["annotation_id"] for row in target_impacts] == ["target"]
    ledger.close()


def test_active_annotation_requires_independent_impact_before_model_visibility(tmp_path) -> None:
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
        "prompt_version": CURRENT_NEWS_PROMPT_VERSION,
        "parse_started_at": now,
        "parsed_at": now,
        "annotation": _target_annotation("job openings fell in June"),
    })

    events = event_evidence_rows(ledger, now)
    assert len(events) == 1
    assert events[0]["broad_model_eligible"] is False
    assert "IMPACT_NOT_ASSESSED" in events[0]["reason_codes"]
    ledger.close()
