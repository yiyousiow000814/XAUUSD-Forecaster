from __future__ import annotations

import json

import pytest

from tests.model_accounting_fakes import CallbackModelAccountant
from xauusd_forecaster import assistant_compaction


def source_messages() -> list[dict[str, object]]:
    return [
        {
            "id": "message-1",
            "role": "USER",
            "content": "后续回答必须保留证据 ID。",
            "created_at": "2026-08-15T10:00:00.000Z",
            "provenance": {"evidence_ids": ["evidence:1"]},
        },
        {
            "id": "message-2",
            "role": "ASSISTANT",
            "content": "已根据证据解释利率预期。",
            "created_at": "2026-08-15T10:01:00.000Z",
            "provenance": {"model_version": "gemma-test"},
        },
    ]


def test_compaction_uses_only_prior_summary_and_next_chunk_through_metered_gateway(
    monkeypatch,
) -> None:
    calls = []

    def generate(api_key, **kwargs):
        calls.append((api_key, kwargs))
        return {
            "summary": "此前讨论了 CPI；新消息要求保留证据，并已完成利率解释。",
            "covered_message_ids": ["message-1", "message-2"],
            "pinned_entries": [{
                "kind": "CONSTRAINT",
                "content": "后续回答必须保留证据 ID。",
                "origin_message_ids": ["message-1"],
                "evidence_ids": ["evidence:1"],
                "source_refs": [],
                "important_timestamps": ["2026-08-15T10:00:00.000Z"],
                "tool_refs": [],
                "artifact_refs": [],
            }],
        }, "gemma-compaction-test"

    monkeypatch.setattr(assistant_compaction, "generate_metered_json", generate)
    result = assistant_compaction.compact_assistant_context(
        {
            "id": "summary-1",
            "version": 1,
            "content": "此前讨论了 CPI。",
            "anchors": {"evidence_ids": ["evidence:old"]},
        },
        [{
            "id": "pin-1",
            "kind": "UNRESOLVED",
            "content": "仍需核对价格反应。",
            "origin_message_ids": ["older-message"],
        }],
        source_messages(),
        prompt_version=assistant_compaction.ASSISTANT_COMPACTION_PROMPT_VERSION,
        context_profile_id="assistant-context-default-v1",
        api_key="test-key",
        request_accountant=CallbackModelAccountant(lambda usage: True),
        model="gemma-compaction-routed",
        thinking_level="minimal",
    )

    assert result["covered_message_ids"] == ["message-1", "message-2"]
    assert result["pinned_entries"][0]["evidence_ids"] == ["evidence:1"]
    assert result["model_version"] == "gemma-compaction-test"
    assert result["context_profile_id"] == "assistant-context-default-v1"
    assert calls[0][1]["purpose"] == "assistant-context-compaction"
    assert calls[0][1]["model"] == "gemma-compaction-routed"
    assert calls[0][1]["payload"]["generationConfig"]["thinkingConfig"] == {
        "thinkingLevel": "minimal",
    }
    prompt = calls[0][1]["payload"]["contents"][0]["parts"][0]["text"]
    inputs = json.loads(prompt)
    assert inputs["prior_summary"]["version"] == 1
    assert [row["id"] for row in inputs["newly_compactable_messages"]] == [
        "message-1", "message-2",
    ]
    assert inputs["required_covered_message_ids"] == ["message-1", "message-2"]
    assert "complete_history" not in inputs


@pytest.mark.parametrize(
    ("result", "match"),
    [
        ({
            "summary": "覆盖不完整。",
            "covered_message_ids": ["message-1"],
            "pinned_entries": [],
        }, "frozen message range"),
        ({
            "summary": "来源无效。",
            "covered_message_ids": ["message-1", "message-2"],
            "pinned_entries": [{
                "kind": "CONSTRAINT",
                "content": "伪造来源",
                "origin_message_ids": ["unknown-message"],
                "evidence_ids": [], "source_refs": [], "important_timestamps": [],
                "tool_refs": [], "artifact_refs": [],
            }],
        }, "pinned entry"),
    ],
)
def test_compaction_rejects_incomplete_coverage_or_unknown_pin_origins(
    monkeypatch, result, match,
) -> None:
    monkeypatch.setattr(
        assistant_compaction,
        "generate_metered_json",
        lambda *args, **kwargs: (result, "gemma-compaction-test"),
    )
    with pytest.raises(ValueError, match=match):
        assistant_compaction.compact_assistant_context(
            None,
            [],
            source_messages(),
            prompt_version=assistant_compaction.ASSISTANT_COMPACTION_PROMPT_VERSION,
            context_profile_id="assistant-context-default-v1",
            api_key="test-key",
            request_accountant=CallbackModelAccountant(lambda usage: True),
        )


def test_compaction_validates_frozen_rules_and_bounded_input_before_transport(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        assistant_compaction,
        "generate_metered_json",
        lambda *args, **kwargs: pytest.fail("validation must precede transport"),
    )
    accountant = CallbackModelAccountant(lambda usage: True)
    with pytest.raises(ValueError, match="Unsupported"):
        assistant_compaction.compact_assistant_context(
            None, [], source_messages(),
            prompt_version="assistant-compaction-v0",
            context_profile_id="assistant-context-default-v1",
            api_key="key", request_accountant=accountant,
        )
    with pytest.raises(ValueError, match="credential and accountant"):
        assistant_compaction.compact_assistant_context(
            None, [], source_messages(),
            prompt_version=assistant_compaction.ASSISTANT_COMPACTION_PROMPT_VERSION,
            context_profile_id="assistant-context-default-v1",
            api_key=None, request_accountant=accountant,
        )
    oversized = source_messages()
    oversized[0]["content"] = "甲" * assistant_compaction.MAX_INPUT_CHARACTERS
    with pytest.raises(ValueError, match="bounded prompt"):
        assistant_compaction.compact_assistant_context(
            None, [], oversized,
            prompt_version=assistant_compaction.ASSISTANT_COMPACTION_PROMPT_VERSION,
            context_profile_id="assistant-context-default-v1",
            api_key="key", request_accountant=accountant,
        )
