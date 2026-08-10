"""Answer queued public news questions from bounded local evidence."""

from __future__ import annotations

import json
import urllib.request
from pathlib import Path

from .annotation import DEFAULT_GEMMA_MODEL, configured_gemini_api_keys
from .gemini_quota import GeminiQuotaLedger


def answer_news_question(question: str, news: list[dict], quota_path: Path) -> dict:
    evidence = [{
        "id": f"{row.get('source')}:{row.get('source_item_id')}:{row.get('revision_number')}",
        "headline": row.get("headline"), "summary": row.get("summary_zh"),
        "published_at": row.get("source_published_time"), "received_at": row.get("collector_first_seen_time"),
    } for row in news[:200] if row.get("headline")]
    keys = configured_gemini_api_keys()
    quota = GeminiQuotaLedger(quota_path)
    key = next((candidate for candidate in keys if quota.reserve(candidate)), None)
    if not key: raise RuntimeError("NO_GEMMA_CAPACITY")
    payload = {
        "systemInstruction": {"parts": [{"text": "你只能根据给出的新闻证据回答。不能提供交易建议；证据不足时明确说不知道。使用简体中文。"}]},
        "contents": [{"parts": [{"text": f"问题：{question}\nEVIDENCE\n{json.dumps(evidence, ensure_ascii=False, separators=(',', ':'))}"}]}],
        "generationConfig": {"responseMimeType": "application/json", "temperature": 0, "maxOutputTokens": 1200,
            "responseSchema": {"type": "object", "required": ["answer", "evidence_ids"], "properties": {
                "answer": {"type": "string"}, "evidence_ids": {"type": "array", "maxItems": 12, "items": {"type": "string"}},
            }}},
    }
    request = urllib.request.Request(
        f"https://generativelanguage.googleapis.com/v1beta/models/{DEFAULT_GEMMA_MODEL}:generateContent",
        data=json.dumps(payload, separators=(",", ":")).encode(),
        headers={"Content-Type": "application/json", "x-goog-api-key": key}, method="POST")
    with urllib.request.urlopen(request, timeout=120) as response: envelope = json.loads(response.read())
    result = json.loads(envelope["candidates"][0]["content"]["parts"][0]["text"])
    allowed = {row["id"] for row in evidence}
    refs = [str(ref) for ref in result.get("evidence_ids", []) if str(ref) in allowed][:12]
    answer = str(result.get("answer") or "").strip()
    if not answer: raise ValueError("Gemma returned an empty answer")
    return {"answer": answer, "evidence_ids": refs, "model_version": str(envelope.get("modelVersion") or DEFAULT_GEMMA_MODEL)}
