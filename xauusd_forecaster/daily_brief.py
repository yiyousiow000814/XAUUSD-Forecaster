"""Bounded, display-only daily news briefs generated from frozen news receipts."""

from __future__ import annotations

import hashlib
import json
import urllib.request
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from .annotation import DEFAULT_GEMMA_MODEL, configured_gemini_api_keys
from .forward_ledger import ForwardLedger
from .gemini_quota import GeminiQuotaLedger


BRIEF_PROMPT_VERSION = "daily-news-brief-v1"
KUALA_LUMPUR = ZoneInfo("Asia/Kuala_Lumpur")


def _source_rows(ledger: ForwardLedger, day: str) -> list[dict]:
    start_local = datetime.fromisoformat(day).replace(tzinfo=KUALA_LUMPUR)
    end_local = start_local + timedelta(days=1)
    rows = ledger.connection.execute(
        """WITH latest AS (
               SELECT source,source_item_id,max(revision_number) revision_number
               FROM news_revisions GROUP BY source,source_item_id
           ), latest_annotation AS (
               SELECT *,row_number() OVER (
                   PARTITION BY source,source_item_id,revision_number
                   ORDER BY parsed_at DESC
               ) rank
               FROM news_annotations
           ), latest_translation AS (
               SELECT *,row_number() OVER (
                   PARTITION BY source,source_item_id,revision_number
                   ORDER BY parsed_at DESC
               ) rank
               FROM news_title_translations
           )
           SELECT n.source,n.source_item_id,n.revision_number,
                  n.source_published_time,n.collector_first_seen_time,
                  COALESCE(t.headline_zh,n.headline) headline,
                  json_extract(a.annotation_json,'$.summary_zh') summary,
                  json_extract(a.annotation_json,'$.primary_category') category
           FROM latest l
           JOIN news_revisions n USING(source,source_item_id,revision_number)
           JOIN latest_annotation a USING(source,source_item_id,revision_number)
           LEFT JOIN latest_translation t ON t.source=n.source
             AND t.source_item_id=n.source_item_id
             AND t.revision_number=n.revision_number AND t.rank=1
           WHERE a.rank=1 AND n.collector_first_seen_time>=? AND n.collector_first_seen_time<?
           ORDER BY n.collector_first_seen_time,n.source,n.source_item_id
           LIMIT 60""",
        (start_local.astimezone(UTC).isoformat(), end_local.astimezone(UTC).isoformat()),
    ).fetchall()
    return [dict(row) for row in rows]


def _source_hash(rows: list[dict]) -> str:
    encoded = json.dumps(rows, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _call_gemma(api_key: str, rows: list[dict]) -> tuple[dict, str]:
    evidence = [
        {
            "id": f"{row['source']}:{row['source_item_id']}:{row['revision_number']}",
            "headline": row["headline"], "summary": row["summary"],
            "category": row["category"], "published_at": row["source_published_time"],
            "received_at": row["collector_first_seen_time"],
        }
        for row in rows
    ]
    payload = {
        "systemInstruction": {"parts": [{"text": (
            "你是黄金市场新闻编辑。只可总结提供的资料，不作交易建议，不补充外部事实。"
            "合并重复报道，优先保留真正的新进展，使用简短自然的简体中文。"
        )}]},
        "contents": [{"parts": [{"text": (
            "生成当天新闻简报。返回标题和最多8条重点；每条必须列出支持它的evidence_ids。"
            "如果资料不足，宁可少写。只返回JSON。\nEVIDENCE\n" +
            json.dumps(evidence, ensure_ascii=False, separators=(",", ":"))
        )}]}],
        "generationConfig": {
            "responseMimeType": "application/json", "temperature": 0,
            "maxOutputTokens": 1600,
            "responseSchema": {
                "type": "object", "required": ["title", "items"],
                "properties": {
                    "title": {"type": "string"},
                    "items": {"type": "array", "maxItems": 8, "items": {
                        "type": "object", "required": ["headline", "summary", "evidence_ids"],
                        "properties": {
                            "headline": {"type": "string"}, "summary": {"type": "string"},
                            "evidence_ids": {"type": "array", "items": {"type": "string"}},
                        },
                    }},
                },
            },
        },
    }
    request = urllib.request.Request(
        f"https://generativelanguage.googleapis.com/v1beta/models/{DEFAULT_GEMMA_MODEL}:generateContent",
        data=json.dumps(payload, separators=(",", ":")).encode(),
        headers={"Content-Type": "application/json", "x-goog-api-key": api_key}, method="POST",
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        envelope = json.loads(response.read())
    result = json.loads(envelope["candidates"][0]["content"]["parts"][0]["text"])
    if not isinstance(result.get("items"), list) or not str(result.get("title") or "").strip():
        raise ValueError("Gemma daily brief returned an invalid result")
    allowed = {
        f"{row['source']}:{row['source_item_id']}:{row['revision_number']}" for row in rows
    }
    for item in result["items"][:8]:
        refs = item.get("evidence_ids") or []
        if not refs or any(ref not in allowed for ref in refs):
            raise ValueError("Gemma daily brief cited unknown evidence")
    result["items"] = result["items"][:8]
    return result, str(envelope.get("modelVersion") or DEFAULT_GEMMA_MODEL)


def update_daily_brief(ledger: ForwardLedger, now: datetime | None = None) -> dict:
    instant = (now or datetime.now(UTC)).astimezone(UTC)
    day = instant.astimezone(KUALA_LUMPUR).date().isoformat()
    rows = _source_rows(ledger, day)
    if not rows:
        return {"status": "NO_NEWS", "brief_date": day}
    source_hash = _source_hash(rows)
    existing = ledger.connection.execute(
        "SELECT 1 FROM daily_news_briefs WHERE brief_date=? AND source_hash=?", (day, source_hash)
    ).fetchone()
    if existing:
        return {"status": "UNCHANGED", "brief_date": day}
    keys = configured_gemini_api_keys()
    quota = GeminiQuotaLedger(ledger.path.parent / "gemma-4-31b-it-quota.json")
    key = next((candidate for candidate in keys if quota.reserve(candidate, instant)), None)
    if not key:
        return {"status": "DEFERRED", "brief_date": day, "reason": "NO_GEMMA_CAPACITY"}
    brief, model = _call_gemma(key, rows)
    revision = ledger.connection.execute(
        "SELECT COALESCE(max(revision_number),0)+1 FROM daily_news_briefs WHERE brief_date=?", (day,)
    ).fetchone()[0]
    with ledger.connection:
        ledger.connection.execute(
            "INSERT INTO daily_news_briefs VALUES (?,?,?,?,?,?,?,?)",
            (day, revision, source_hash, instant.isoformat(), instant.isoformat(), model,
             BRIEF_PROMPT_VERSION, json.dumps(brief, ensure_ascii=False, separators=(",", ":"))),
        )
    return {"status": "OK", "brief_date": day, "revision_number": revision, "items": len(brief["items"])}


def recent_daily_briefs(connection, limit: int = 3) -> list[dict]:
    rows = connection.execute(
        """WITH ranked AS (
               SELECT *,row_number() OVER(PARTITION BY brief_date ORDER BY revision_number DESC) rank
               FROM daily_news_briefs
           ) SELECT brief_date,revision_number,cutoff_at,generated_at,model_version,
                    prompt_version,brief_json FROM ranked WHERE rank=1
             ORDER BY brief_date DESC LIMIT ?""", (max(1, min(limit, 7)),)
    ).fetchall()
    return [{**dict(row), "brief": json.loads(row["brief_json"])} for row in rows]
