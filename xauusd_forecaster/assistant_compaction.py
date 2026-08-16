"""Incrementally compact canonical Assistant messages through the metered gateway."""

from __future__ import annotations

import json
from typing import Any

from .annotation import DEFAULT_GEMMA_MODEL, generate_metered_json
from .assistant_routing import apply_provider_thinking_level
from .model_gateway import ModelRequestAccountant


ASSISTANT_COMPACTION_PROMPT_VERSION = "assistant-compaction-v1"
ASSISTANT_PIN_KINDS = (
    "CONSTRAINT",
    "UNRESOLVED",
    "DECISION",
    "TASK_SCOPE",
    "EVIDENCE_REF",
    "TOOL_ARTIFACT",
    "IMPORTANT_TIMESTAMP",
    "TOPIC",
)
MAX_SOURCE_MESSAGES = 24
MAX_EXISTING_PINS = 64
MAX_GENERATED_PINS = 24
MAX_SUMMARY_CHARACTERS = 8_000
MAX_PIN_CONTENT_CHARACTERS = 1_200
MAX_REFERENCE_ITEMS = 64
MAX_INPUT_CHARACTERS = 40_000
ASSISTANT_COMPACTION_MAX_OUTPUT_TOKENS = 2_400


def _references(value: object, field: str) -> list[str]:
    if not isinstance(value, list) or len(value) > MAX_REFERENCE_ITEMS:
        raise ValueError(f"Assistant compaction {field} is invalid")
    result: list[str] = []
    for raw in value:
        item = str(raw or "").strip()
        if not item or len(item) > 256 or any(ord(char) < 32 for char in item):
            raise ValueError(f"Assistant compaction {field} is invalid")
        if item not in result:
            result.append(item)
    return result


def _source_packet(source_messages: list[dict[str, object]]) -> list[dict[str, object]]:
    if not source_messages or len(source_messages) > MAX_SOURCE_MESSAGES:
        raise ValueError("Assistant compaction source-message count is invalid")
    packet: list[dict[str, object]] = []
    seen: set[str] = set()
    for message in source_messages:
        message_id = str(message.get("id") or "").strip()
        role = str(message.get("role") or "").strip().upper()
        content = str(message.get("content") or "").strip()
        created_at = str(message.get("created_at") or "").strip()
        if (
            not message_id
            or message_id in seen
            or role not in {"USER", "ASSISTANT"}
            or not content
            or not created_at
        ):
            raise ValueError("Assistant compaction source message is invalid")
        seen.add(message_id)
        packet.append({
            "id": message_id,
            "role": role,
            "content": content,
            "created_at": created_at,
        })
    return packet


def _existing_pins(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list) or len(value) > MAX_EXISTING_PINS:
        raise ValueError("Assistant compaction pinned-state input is invalid")
    pins: list[dict[str, object]] = []
    for raw in value:
        if not isinstance(raw, dict):
            raise ValueError("Assistant compaction pinned-state input is invalid")
        pins.append(raw)
    return pins


def _validate_result(result: dict[str, object], source_ids: list[str]) -> dict[str, Any]:
    summary = str(result.get("summary") or "").strip()
    if not summary or len(summary) > MAX_SUMMARY_CHARACTERS:
        raise ValueError("Assistant compaction summary is invalid")
    covered = _references(result.get("covered_message_ids"), "coverage")
    if covered != source_ids:
        raise ValueError("Assistant compaction did not cover the frozen message range")
    raw_pins = result.get("pinned_entries")
    if not isinstance(raw_pins, list) or len(raw_pins) > MAX_GENERATED_PINS:
        raise ValueError("Assistant compaction pinned entries are invalid")
    pins: list[dict[str, object]] = []
    allowed_origins = set(source_ids)
    for raw in raw_pins:
        if not isinstance(raw, dict):
            raise ValueError("Assistant compaction pinned entry is invalid")
        kind = str(raw.get("kind") or "").strip().upper()
        content = str(raw.get("content") or "").strip()
        origins = _references(raw.get("origin_message_ids"), "origin_message_ids")
        if (
            kind not in ASSISTANT_PIN_KINDS
            or not content
            or len(content) > MAX_PIN_CONTENT_CHARACTERS
            or not origins
            or any(item not in allowed_origins for item in origins)
        ):
            raise ValueError("Assistant compaction pinned entry is invalid")
        pins.append({
            "kind": kind,
            "content": content,
            "origin_message_ids": origins,
            "evidence_ids": _references(raw.get("evidence_ids", []), "evidence_ids"),
            "source_refs": _references(raw.get("source_refs", []), "source_refs"),
            "important_timestamps": _references(
                raw.get("important_timestamps", []), "important_timestamps",
            ),
            "tool_refs": _references(raw.get("tool_refs", []), "tool_refs"),
            "artifact_refs": _references(raw.get("artifact_refs", []), "artifact_refs"),
        })
    return {
        "summary": summary,
        "covered_message_ids": covered,
        "pinned_entries": pins,
    }


def compact_assistant_context(
    prior_summary: dict[str, object] | None,
    pinned_state: list[dict[str, object]],
    source_messages: list[dict[str, object]],
    *,
    prompt_version: str,
    context_profile_id: str,
    api_key: str | None,
    request_accountant: ModelRequestAccountant | None,
    model: str = DEFAULT_GEMMA_MODEL,
    thinking_level: str | None = None,
) -> dict[str, Any]:
    """Summarize only the prior summary plus the next frozen message chunk."""
    if prompt_version != ASSISTANT_COMPACTION_PROMPT_VERSION:
        raise ValueError(f"Unsupported Assistant compaction prompt version: {prompt_version}")
    if not context_profile_id or len(context_profile_id) > 96:
        raise ValueError("Assistant compaction context profile is invalid")
    if not api_key or request_accountant is None:
        raise ValueError("Assistant compaction model credential and accountant are required")
    messages = _source_packet(source_messages)
    pins = _existing_pins(pinned_state)
    if prior_summary is not None and not isinstance(prior_summary, dict):
        raise ValueError("Assistant compaction prior summary is invalid")
    source_ids = [str(message["id"]) for message in messages]
    inputs = {
        "prior_summary": prior_summary,
        "existing_pinned_state": pins,
        "newly_compactable_messages": messages,
        "required_covered_message_ids": source_ids,
    }
    serialized = json.dumps(inputs, ensure_ascii=False, separators=(",", ":"))
    if len(serialized) > MAX_INPUT_CHARACTERS:
        raise ValueError("Assistant compaction input exceeds the bounded prompt")
    payload = {
        "systemInstruction": {"parts": [{"text": (
            "你负责增量压缩 XAUUSD Assistant 的旧上下文。只能处理 PRIOR_SUMMARY 与 "
            "NEWLY_COMPACTABLE_MESSAGES；不得假装看到完整历史。保留未解决工作、用户约束、"
            "决定、当前主题、证据 ID、来源、重要时间、工具和产物引用。EXISTING_PINNED_STATE "
            "由服务器独立保留，不要重复创建。不得新增事实、交易建议或证据。使用简体中文，"
            "只返回符合 schema 的 JSON。"
        )}]},
        "contents": [{"parts": [{"text": serialized}]}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "temperature": 0,
            "maxOutputTokens": ASSISTANT_COMPACTION_MAX_OUTPUT_TOKENS,
            "responseSchema": {
                "type": "object",
                "required": ["summary", "covered_message_ids", "pinned_entries"],
                "properties": {
                    "summary": {"type": "string", "maxLength": MAX_SUMMARY_CHARACTERS},
                    "covered_message_ids": {
                        "type": "array",
                        "maxItems": MAX_SOURCE_MESSAGES,
                        "items": {"type": "string"},
                    },
                    "pinned_entries": {
                        "type": "array",
                        "maxItems": MAX_GENERATED_PINS,
                        "items": {
                            "type": "object",
                            "required": [
                                "kind", "content", "origin_message_ids", "evidence_ids",
                                "source_refs", "important_timestamps", "tool_refs",
                                "artifact_refs",
                            ],
                            "properties": {
                                "kind": {"type": "string", "enum": list(ASSISTANT_PIN_KINDS)},
                                "content": {
                                    "type": "string", "maxLength": MAX_PIN_CONTENT_CHARACTERS,
                                },
                                "origin_message_ids": {
                                    "type": "array", "maxItems": MAX_REFERENCE_ITEMS,
                                    "items": {"type": "string"},
                                },
                                **{
                                    field: {
                                        "type": "array", "maxItems": MAX_REFERENCE_ITEMS,
                                        "items": {"type": "string"},
                                    }
                                    for field in (
                                        "evidence_ids", "source_refs", "important_timestamps",
                                        "tool_refs", "artifact_refs",
                                    )
                                },
                            },
                        },
                    },
                },
            },
        },
    }

    def decode(envelope: dict[str, object]) -> dict:
        return json.loads(envelope["candidates"][0]["content"]["parts"][0]["text"])

    result, model_version = generate_metered_json(
        api_key,
        model=model,
        purpose="assistant-context-compaction",
        payload=apply_provider_thinking_level(payload, thinking_level),
        decode=decode,
        request_accountant=request_accountant,
    )
    validated = _validate_result(result, source_ids)
    return {
        **validated,
        "model_version": model_version,
        "prompt_version": prompt_version,
        "context_profile_id": context_profile_id,
    }
