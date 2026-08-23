"""Generate bounded conversation titles through the shared metered gateway."""

from __future__ import annotations

import json

from xauusd_forecaster.news.annotation.product import DEFAULT_GEMMA_MODEL, generate_metered_json
from xauusd_forecaster.assistant.routing import apply_provider_thinking_level
from xauusd_forecaster.ai.model_gateway import ModelRequestAccountant


ASSISTANT_TITLE_PROMPT_VERSION = "assistant-title-v1"
MAX_TITLE_INPUT_CHARACTERS = 3_000
MAX_TITLE_RESPONSE_CHARACTERS = 128
ASSISTANT_TITLE_MAX_OUTPUT_TOKENS = 80


def _bounded_text(value: object, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


def generate_assistant_title(
    first_user_message: str,
    latest_assistant_message: str,
    *,
    prompt_version: str,
    api_key: str | None,
    request_accountant: ModelRequestAccountant | None,
    model: str = DEFAULT_GEMMA_MODEL,
    thinking_level: str | None = None,
) -> dict[str, str]:
    """Return one candidate; the server remains the grapheme-limit authority."""
    if prompt_version != ASSISTANT_TITLE_PROMPT_VERSION:
        raise ValueError(f"Unsupported Assistant title prompt version: {prompt_version}")
    if not api_key or request_accountant is None:
        raise ValueError("Assistant title model credential and accountant are required")
    user_text = _bounded_text(first_user_message, 1_000)
    assistant_text = _bounded_text(latest_assistant_message, 2_000)
    if not user_text or not assistant_text:
        raise ValueError("Assistant title context is incomplete")

    prompt = (
        "请为这段 XAUUSD 研究对话生成一个准确、具体的简体中文标题。"
        "只能输出 JSON；标题必须单行，不要引号，不要使用泛泛的“关于黄金的对话”，"
        "只有日期能区分主题时才写日期，最多 32 个 Unicode 字素。\n"
        f"USER\n{user_text}\nASSISTANT\n{assistant_text}"
    )[:MAX_TITLE_INPUT_CHARACTERS]
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "temperature": 0,
            "maxOutputTokens": ASSISTANT_TITLE_MAX_OUTPUT_TOKENS,
            "responseSchema": {
                "type": "object",
                "required": ["title"],
                "properties": {
                    "title": {
                        "type": "string",
                        "maxLength": MAX_TITLE_RESPONSE_CHARACTERS,
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
        purpose="assistant-conversation-title",
        payload=apply_provider_thinking_level(payload, thinking_level),
        decode=decode,
        request_accountant=request_accountant,
    )
    title = " ".join(str(result.get("title") or "").split()).strip()
    if not title or len(title) > MAX_TITLE_RESPONSE_CHARACTERS:
        raise ValueError("Assistant title model returned an invalid title")
    return {
        "title": title,
        "model_version": model_version,
        "prompt_version": prompt_version,
    }
