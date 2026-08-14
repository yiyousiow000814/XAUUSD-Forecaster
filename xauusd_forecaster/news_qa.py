"""Answer queued public news questions from bounded local evidence."""

from __future__ import annotations

import json

from .annotation import DEFAULT_GEMMA_MODEL, generate_metered_json
from .model_gateway import ModelRequestAccountant


def answer_news_question(
    question: str,
    news: list[dict],
    *,
    api_key: str,
    request_accountant: ModelRequestAccountant,
) -> dict:
    evidence = [{
        "id": f"{row.get('source')}:{row.get('source_item_id')}:{row.get('revision_number')}",
        "headline": row.get("headline"), "summary": row.get("summary_zh"),
        "published_at": row.get("source_published_time"), "received_at": row.get("collector_first_seen_time"),
    } for row in news[:200] if row.get("headline")]
    payload = {
        "systemInstruction": {"parts": [{"text": "你只能根据给出的新闻证据回答。不能提供交易建议；证据不足时明确说不知道。使用简体中文。"}]},
        "contents": [{"parts": [{"text": f"问题：{question}\nEVIDENCE\n{json.dumps(evidence, ensure_ascii=False, separators=(',', ':'))}"}]}],
        "generationConfig": {"responseMimeType": "application/json", "temperature": 0, "maxOutputTokens": 1200,
            "responseSchema": {"type": "object", "required": ["answer", "evidence_ids"], "properties": {
                "answer": {"type": "string"}, "evidence_ids": {"type": "array", "maxItems": 12, "items": {"type": "string"}},
            }}},
    }
    def decode(envelope: dict[str, object]) -> dict:
        return json.loads(envelope["candidates"][0]["content"]["parts"][0]["text"])

    result, model_version = generate_metered_json(
        api_key,
        model=DEFAULT_GEMMA_MODEL,
        purpose="news-question-answer",
        payload=payload,
        decode=decode,
        request_accountant=request_accountant,
    )
    allowed = {row["id"] for row in evidence}
    refs = [str(ref) for ref in result.get("evidence_ids", []) if str(ref) in allowed][:12]
    answer = str(result.get("answer") or "").strip()
    if not answer: raise ValueError("Gemma returned an empty answer")
    if not refs: raise ValueError("Gemma answer has no verified news evidence")
    return {
        "answer": answer,
        "evidence_ids": refs,
        "model_version": model_version,
    }
