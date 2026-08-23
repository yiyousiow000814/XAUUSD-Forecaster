from __future__ import annotations

import pytest

from tests.model_accounting_fakes import CallbackModelAccountant
from xauusd_forecaster.assistant import titles as assistant_titles


def test_title_generation_uses_bounded_context_and_the_metered_gateway(monkeypatch) -> None:
    calls = []

    def generate(api_key, **kwargs):
        calls.append((api_key, kwargs))
        return {"title": "CPI 后黄金反常上涨分析"}, "gemma-title-test"

    monkeypatch.setattr(assistant_titles, "generate_metered_json", generate)
    result = assistant_titles.generate_assistant_title(
        "用户问题 " + "甲" * 2_000,
        "回答 " + "乙" * 4_000,
        prompt_version=assistant_titles.ASSISTANT_TITLE_PROMPT_VERSION,
        api_key="test-key",
        request_accountant=CallbackModelAccountant(lambda usage: True),
        model="gemma-small-routed",
        thinking_level="minimal",
    )
    assert result == {
        "title": "CPI 后黄金反常上涨分析",
        "model_version": "gemma-title-test",
        "prompt_version": assistant_titles.ASSISTANT_TITLE_PROMPT_VERSION,
    }
    assert calls[0][1]["purpose"] == "assistant-conversation-title"
    assert calls[0][1]["model"] == "gemma-small-routed"
    assert calls[0][1]["payload"]["generationConfig"]["thinkingConfig"] == {
        "thinkingLevel": "minimal",
    }
    prompt = calls[0][1]["payload"]["contents"][0]["parts"][0]["text"]
    assert len(prompt) <= assistant_titles.MAX_TITLE_INPUT_CHARACTERS
    assert 900 <= prompt.count("甲") <= 1_000
    assert 1_500 <= prompt.count("乙") <= 2_000


def test_title_generation_requires_context_credential_and_accounting(monkeypatch) -> None:
    monkeypatch.setattr(
        assistant_titles,
        "generate_metered_json",
        lambda *args, **kwargs: pytest.fail("validation must precede transport"),
    )
    accountant = CallbackModelAccountant(lambda usage: True)
    with pytest.raises(ValueError, match="credential and accountant"):
        assistant_titles.generate_assistant_title(
            "问题", "回答",
            prompt_version=assistant_titles.ASSISTANT_TITLE_PROMPT_VERSION,
            api_key=None, request_accountant=accountant,
        )
    with pytest.raises(ValueError, match="context is incomplete"):
        assistant_titles.generate_assistant_title(
            "", "回答",
            prompt_version=assistant_titles.ASSISTANT_TITLE_PROMPT_VERSION,
            api_key="key", request_accountant=accountant,
        )
    with pytest.raises(ValueError, match="Unsupported"):
        assistant_titles.generate_assistant_title(
            "问题", "回答", prompt_version="assistant-title-v0",
            api_key="key", request_accountant=accountant,
        )


def test_title_generation_rejects_an_empty_model_result(monkeypatch) -> None:
    monkeypatch.setattr(
        assistant_titles,
        "generate_metered_json",
        lambda *args, **kwargs: ({"title": "  "}, "gemma-title-test"),
    )
    with pytest.raises(ValueError, match="invalid title"):
        assistant_titles.generate_assistant_title(
            "问题",
            "回答",
            prompt_version=assistant_titles.ASSISTANT_TITLE_PROMPT_VERSION,
            api_key="key",
            request_accountant=CallbackModelAccountant(lambda usage: True),
        )
