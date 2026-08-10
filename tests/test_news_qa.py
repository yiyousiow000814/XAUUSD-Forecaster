from __future__ import annotations

import io
import json

from xauusd_forecaster import news_qa


class _Response:
    def __enter__(self): return self
    def __exit__(self, *args): return None
    def read(self):
        return json.dumps({
            "modelVersion": "gemma-test",
            "candidates": [{"content": {"parts": [{"text": json.dumps({
                "answer": "根据现有新闻，市场正在关注利率。",
                "evidence_ids": ["Reuters:1:1", "invented:2:1"],
            })}]}}],
        }).encode()


def test_news_question_keeps_only_real_evidence(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(news_qa, "configured_gemini_api_keys", lambda: ("key",))
    monkeypatch.setattr(news_qa.GeminiQuotaLedger, "reserve", lambda self, key: True)
    monkeypatch.setattr(news_qa.urllib.request, "urlopen", lambda request, timeout: _Response())
    result = news_qa.answer_news_question("今天市场关注什么？", [{
        "source": "Reuters", "source_item_id": "1", "revision_number": 1,
        "headline": "市场关注利率", "summary_zh": "利率预期变化",
        "source_published_time": "2026-08-10T00:00:00+00:00",
        "collector_first_seen_time": "2026-08-10T00:01:00+00:00",
    }], tmp_path / "quota.json")
    assert result["evidence_ids"] == ["Reuters:1:1"]
    assert result["model_version"] == "gemma-test"
