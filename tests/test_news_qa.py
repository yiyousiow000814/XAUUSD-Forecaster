from __future__ import annotations

import pytest

from tests.model_accounting_fakes import CallbackModelAccountant
from xauusd_forecaster import news_qa


def _news(evidence_id: str = "evidence-1", **overrides) -> dict:
    return {
        "evidence_id": evidence_id,
        "headline": "市场关注利率",
        "emerging_topic_zh": "利率预期变化",
        "impact_reason_zh": "美元和黄金重新定价",
        "category": "利率/Fed",
        "source": "Reuters",
        "source_published_time": "2026-08-10T00:00:00+00:00",
        "collector_first_seen_time": "2026-08-10T00:01:00+00:00",
        "body": "this raw body must never enter the model packet",
        **overrides,
    }


def test_news_question_uses_only_the_bounded_shared_retrieval_packet(monkeypatch) -> None:
    calls = []

    def generate(api_key, **kwargs):
        calls.append(kwargs)
        return {
            "claims": [{
                "text": "根据现有新闻，市场正在关注利率。",
                "evidence_ids": ["evidence-1"],
            }],
        }, "gemma-test"

    monkeypatch.setattr(news_qa, "generate_metered_json", generate)
    result = news_qa.answer_news_question(
        "今天市场关注什么？",
        [_news()] + [_news(f"evidence-{index}") for index in range(2, 30)],
        prompt_version=news_qa.NEWS_QA_PROMPT_VERSION,
        api_key="test-key",
        request_accountant=CallbackModelAccountant(lambda usage: True),
        model="gemma-routed",
        thinking_level="high",
    )
    assert {key: value for key, value in result.items() if key != "evidence_validation"} == {
        "answer_status": "ANSWERED",
        "answer": "根据现有新闻，市场正在关注利率。",
        "evidence_ids": ["evidence-1"],
        "model_version": "gemma-test",
        "prompt_version": news_qa.NEWS_QA_PROMPT_VERSION,
    }
    validation = result["evidence_validation"]
    assert validation["mode"] == "CITATION_COVERAGE"
    assert validation["cited_evidence_ids"] == ["evidence-1"]
    assert validation["coverage_complete"] is True
    assert validation["entailment_status"] == "NOT_VERIFIED"
    assert len(validation["receipt_sha256"]) == 64
    assert calls[0]["purpose"] == "news-question-answer"
    assert calls[0]["model"] == "gemma-routed"
    assert calls[0]["payload"]["generationConfig"]["thinkingConfig"] == {
        "thinkingLevel": "high",
    }
    prompt = calls[0]["payload"]["contents"][0]["parts"][0]["text"]
    assert "this raw body" not in prompt
    assert prompt.count('"evidence_id"') == news_qa.MAX_RETRIEVED_EVIDENCE
    assert calls[0]["payload"]["generationConfig"]["responseSchema"][
        "required"
    ] == ["claims"]


def test_compact_evidence_packet_keeps_only_safe_public_source_links() -> None:
    secure, unsafe = news_qa.build_news_evidence_packet([
        _news("secure", link="https://example.com/evidence"),
        _news("unsafe", link="javascript:alert(1)"),
    ])

    assert secure["source_url"] == "https://example.com/evidence"
    assert "source_url" not in unsafe


def test_news_question_rejects_the_whole_answer_if_any_evidence_is_invented(
    monkeypatch,
) -> None:
    monkeypatch.setattr(news_qa, "generate_metered_json", lambda api_key, **kwargs: (
        {
            "claims": [{
                "text": "混合真实与虚构证据的回答",
                "evidence_ids": ["evidence-1", "invented"],
            }],
        },
        "gemma-test",
    ))
    with pytest.raises(ValueError, match="retrieved citations"):
        news_qa.answer_news_question(
            "今天市场关注什么？",
            [_news()],
            prompt_version=news_qa.NEWS_QA_PROMPT_VERSION,
            api_key="test-key",
            request_accountant=CallbackModelAccountant(lambda usage: True),
        )


def test_no_retrieval_evidence_returns_honest_insufficiency_without_model_call(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        news_qa,
        "generate_metered_json",
        lambda *args, **kwargs: pytest.fail("model must not run without evidence"),
    )
    result = news_qa.answer_news_question(
        "没有匹配资料吗？", [], prompt_version=news_qa.NEWS_QA_PROMPT_VERSION,
    )
    assert {key: value for key, value in result.items() if key != "evidence_validation"} == {
        "answer_status": "INSUFFICIENT_EVIDENCE",
        "answer": news_qa.INSUFFICIENT_EVIDENCE_ANSWER,
        "evidence_ids": [],
        "model_version": None,
        "prompt_version": news_qa.NEWS_QA_PROMPT_VERSION,
    }
    assert result["evidence_validation"]["mode"] == "INSUFFICIENT_EVIDENCE"
    assert result["evidence_validation"]["coverage_complete"] is False
    assert result["evidence_validation"]["entailment_status"] == "NOT_VERIFIED"


def test_every_news_claim_requires_a_retrieved_citation(monkeypatch) -> None:
    monkeypatch.setattr(news_qa, "generate_metered_json", lambda api_key, **kwargs: (
        {
            "claims": [
                {"text": "利率是当前主题。", "evidence_ids": ["evidence-1"]},
                {"text": "这是一个没有引用的额外事实。", "evidence_ids": []},
            ],
        },
        "gemma-test",
    ))
    with pytest.raises(ValueError, match="retrieved citations"):
        news_qa.answer_news_question(
            "今天市场关注什么？",
            [_news()],
            prompt_version=news_qa.NEWS_QA_PROMPT_VERSION,
            api_key="test-key",
            request_accountant=CallbackModelAccountant(lambda usage: True),
        )


def test_grounded_question_requires_unified_gateway_accounting(monkeypatch) -> None:
    monkeypatch.setattr(
        news_qa,
        "generate_metered_json",
        lambda *args, **kwargs: pytest.fail("validation must fail before transport"),
    )
    with pytest.raises(ValueError, match="credential and accountant"):
        news_qa.answer_news_question(
            "今天市场关注什么？", [_news()],
            prompt_version=news_qa.NEWS_QA_PROMPT_VERSION,
        )
    with pytest.raises(ValueError, match="Unsupported"):
        news_qa.answer_news_question(
            "今天市场关注什么？", [], prompt_version="news-qa-v1",
        )
