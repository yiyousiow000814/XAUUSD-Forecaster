"""Answer private news questions from the shared bounded retrieval packet."""

from __future__ import annotations

import json
import urllib.parse
from typing import Any

from xauusd_forecaster.news.annotation.product import DEFAULT_GEMMA_MODEL, generate_metered_json
from xauusd_forecaster.assistant_evidence import (
    MAX_CLAIM_CHARACTERS,
    MAX_EVIDENCE_CLAIMS,
    MAX_EVIDENCE_PER_CLAIM,
    insufficient_evidence_validation,
    validate_assistant_evidence_claims,
)
from xauusd_forecaster.assistant_routing import apply_provider_thinking_level
from xauusd_forecaster.ai.model_gateway import ModelRequestAccountant


NEWS_QA_PROMPT_VERSION = "news-qa-v3"
INSUFFICIENT_EVIDENCE_ANSWER = (
    "当前已收录且可追溯的新闻证据不足，无法可靠回答这个问题。"
)
MAX_RETRIEVED_EVIDENCE = 20
MAX_CITED_EVIDENCE = 12
MAX_ANSWER_CHARACTERS = 4_000
NEWS_QA_MAX_OUTPUT_TOKENS = 1_200


def _bounded_text(value: object, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


def _bounded_source_url(value: object) -> str | None:
    url = str(value or "").strip()[:2_048]
    if not url:
        return None
    parsed = urllib.parse.urlsplit(url)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
    ):
        return None
    return url


def build_news_evidence_packet(news: list[dict]) -> list[dict[str, object]]:
    """Reduce shared retrieval rows to the only fields the model may receive."""
    packet: list[dict[str, object]] = []
    seen: set[str] = set()
    for row in news[:MAX_RETRIEVED_EVIDENCE]:
        evidence_id = _bounded_text(
            row.get("evidence_id") or row.get("detail_key"), 128
        )
        headline = _bounded_text(row.get("headline"), 300)
        if not evidence_id or evidence_id in seen or not headline:
            continue
        seen.add(evidence_id)
        item: dict[str, object] = {
            "evidence_id": evidence_id,
            "published_at": _bounded_text(
                row.get("source_published_time") or row.get("published_time"), 64
            ),
            "received_at": _bounded_text(
                row.get("collector_first_seen_time"), 64
            ),
            "source": _bounded_text(row.get("source"), 100),
            "headline": headline,
            "summary": _bounded_text(
                row.get("summary_zh") or row.get("emerging_topic_zh"), 600
            ),
            "category": _bounded_text(row.get("category"), 80),
            "impact": _bounded_text(
                row.get("impact_reason_zh") or row.get("impact_status"), 600
            ),
        }
        source_url = _bounded_source_url(row.get("source_url") or row.get("link"))
        if source_url:
            item["source_url"] = source_url
        packet.append(item)
    return packet


def answer_news_question(
    question: str,
    news: list[dict],
    *,
    prompt_version: str,
    api_key: str | None = None,
    request_accountant: ModelRequestAccountant | None = None,
    model: str = DEFAULT_GEMMA_MODEL,
    thinking_level: str | None = None,
) -> dict[str, Any]:
    if prompt_version != NEWS_QA_PROMPT_VERSION:
        raise ValueError(f"Unsupported news Q&A prompt version: {prompt_version}")
    evidence = build_news_evidence_packet(news)
    if not evidence:
        validated = insufficient_evidence_validation(
            INSUFFICIENT_EVIDENCE_ANSWER,
        )
        return {
            "answer_status": "INSUFFICIENT_EVIDENCE",
            "answer": validated.answer,
            "evidence_ids": list(validated.evidence_ids),
            "evidence_validation": validated.receipt,
            "model_version": None,
            "prompt_version": prompt_version,
        }
    if not api_key or request_accountant is None:
        raise ValueError("news Q&A model credential and accountant are required")

    payload = {
        "systemInstruction": {"parts": [{"text": (
            "你只能根据 EVIDENCE 中的新闻证据回答。不得补充外部事实，不得提供交易建议。"
            "将回答拆成最多 12 个单行 claims；每个 claim 必须至少引用一个只来自"
            "本次 EVIDENCE 的 evidence_id。不得输出 claims 之外的字段。"
            "证据冲突时作为一个有引用的 claim 明确说明。使用简体中文，回答保持简洁。"
        )}]},
        "contents": [{"parts": [{"text": (
            f"问题：{_bounded_text(question, 200)}\nEVIDENCE\n"
            + json.dumps(evidence, ensure_ascii=False, separators=(",", ":"))
        )}]}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "temperature": 0,
            "maxOutputTokens": NEWS_QA_MAX_OUTPUT_TOKENS,
            "responseSchema": {
                "type": "object",
                "required": ["claims"],
                "properties": {
                    "claims": {
                        "type": "array",
                        "minItems": 1,
                        "maxItems": MAX_EVIDENCE_CLAIMS,
                        "items": {
                            "type": "object",
                            "required": ["text", "evidence_ids"],
                            "properties": {
                                "text": {
                                    "type": "string",
                                    "maxLength": MAX_CLAIM_CHARACTERS,
                                },
                                "evidence_ids": {
                                    "type": "array",
                                    "minItems": 1,
                                    "maxItems": MAX_EVIDENCE_PER_CLAIM,
                                    "items": {"type": "string"},
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
        purpose="news-question-answer",
        payload=apply_provider_thinking_level(payload, thinking_level),
        decode=decode,
        request_accountant=request_accountant,
    )
    validated = validate_assistant_evidence_claims(
        result,
        [str(row["evidence_id"]) for row in evidence],
        mode="CITATION_COVERAGE",
        max_cited_evidence=MAX_CITED_EVIDENCE,
    )
    if len(validated.answer) > MAX_ANSWER_CHARACTERS:
        raise ValueError("Gemma returned an invalid answer")
    return {
        "answer_status": "ANSWERED",
        "answer": validated.answer,
        "evidence_ids": list(validated.evidence_ids),
        "evidence_validation": validated.receipt,
        "model_version": model_version,
        "prompt_version": prompt_version,
    }
