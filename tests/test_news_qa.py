from __future__ import annotations

import pytest

from xauusd_forecaster import news_qa
from tests.model_accounting_fakes import CallbackModelAccountant


def test_news_question_keeps_only_real_evidence(tmp_path, monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(news_qa, "generate_metered_json", lambda api_key, **kwargs: (
        calls.append(kwargs) or {
            "answer": "根据现有新闻，市场正在关注利率。",
            "evidence_ids": ["Reuters:1:1", "invented:2:1"],
        },
        "gemma-test",
    ))
    result = news_qa.answer_news_question("今天市场关注什么？", [{
        "source": "Reuters", "source_item_id": "1", "revision_number": 1,
        "headline": "市场关注利率", "summary_zh": "利率预期变化",
        "source_published_time": "2026-08-10T00:00:00+00:00",
        "collector_first_seen_time": "2026-08-10T00:01:00+00:00",
    }], api_key="test-key", request_accountant=CallbackModelAccountant(lambda usage: True))
    assert result["evidence_ids"] == ["Reuters:1:1"]
    assert result["model_version"] == "gemma-test"
    assert calls[0]["purpose"] == "news-question-answer"


def test_news_question_rejects_answer_without_verified_evidence(monkeypatch) -> None:
    monkeypatch.setattr(news_qa, "generate_metered_json", lambda api_key, **kwargs: (
        {"answer": "没有真实依据的回答", "evidence_ids": ["invented:2:1"]},
        "gemma-test",
    ))
    with pytest.raises(ValueError, match="no verified news evidence"):
        news_qa.answer_news_question(
            "今天市场关注什么？",
            [{
                "source": "Reuters", "source_item_id": "1", "revision_number": 1,
                "headline": "市场关注利率", "summary_zh": "利率预期变化",
            }],
            api_key="test-key",
            request_accountant=CallbackModelAccountant(lambda usage: True),
        )
