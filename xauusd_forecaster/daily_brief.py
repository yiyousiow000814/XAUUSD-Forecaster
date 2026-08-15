"""Bounded, display-only daily news briefs generated from frozen news receipts."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from .annotation import DEFAULT_GEMMA_MODEL, generate_metered_json
from .forward_ledger import ForwardLedger
from .model_gateway import ModelGatewayCapacityExhausted, ModelRequestAccountant


BRIEF_PROMPT_VERSION = "daily-news-brief-v1"
BRIEF_EVIDENCE_LIMIT = 60
BRIEF_REGENERATION_DEBOUNCE = timedelta(minutes=10)
KUALA_LUMPUR = ZoneInfo("Asia/Kuala_Lumpur")


def _source_rows(
    ledger: ForwardLedger, day: str, *, cutoff: datetime,
) -> list[dict]:
    start_local = datetime.fromisoformat(day).replace(tzinfo=KUALA_LUMPUR)
    end_local = start_local + timedelta(days=1)
    cutoff_iso = cutoff.astimezone(UTC).isoformat()
    rows = ledger.connection.execute(
        """WITH latest AS (
               SELECT source,source_item_id,max(revision_number) revision_number
               FROM news_revisions
               WHERE collector_first_seen_time>=?
                 AND collector_first_seen_time<?
                 AND collector_first_seen_time<=?
               GROUP BY source,source_item_id
           ), latest_annotation AS (
               SELECT *,row_number() OVER (
                   PARTITION BY source,source_item_id,revision_number
                   ORDER BY parsed_at DESC
               ) rank
               FROM news_annotations
               WHERE parsed_at<=?
           ), latest_translation AS (
               SELECT *,row_number() OVER (
                   PARTITION BY source,source_item_id,revision_number
                   ORDER BY parsed_at DESC
               ) rank
               FROM news_title_translations
               WHERE parsed_at<=?
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
             AND t.raw_content_hash=n.content_hash
           WHERE a.rank=1 AND a.raw_content_hash=n.content_hash
           ORDER BY n.collector_first_seen_time,n.source,n.source_item_id,
                    n.revision_number""",
        (
            start_local.astimezone(UTC).isoformat(),
            end_local.astimezone(UTC).isoformat(),
            cutoff_iso,
            cutoff_iso,
            cutoff_iso,
        ),
    ).fetchall()
    return [dict(row) for row in rows]


def _source_hash(rows: list[dict]) -> str:
    encoded = json.dumps(
        rows,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _candidate_rows(rows: list[dict]) -> list[dict]:
    """Keep a deterministic bounded window in which later news can enter."""
    return rows[-BRIEF_EVIDENCE_LIMIT:]


def _bounded_text(value: object, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


def _evidence_packet(rows: list[dict]) -> list[dict[str, object]]:
    return [
        {
            "id": (
                f"{row['source']}:{row['source_item_id']}:"
                f"{row['revision_number']}"
            ),
            "headline": _bounded_text(row["headline"], 300),
            "summary": _bounded_text(row["summary"], 600),
            "category": _bounded_text(row["category"], 80),
            "published_at": row["source_published_time"],
            "received_at": row["collector_first_seen_time"],
        }
        for row in rows
    ]


def _brief_payload(evidence: list[dict[str, object]]) -> dict[str, object]:
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
                    "title": {"type": "string", "maxLength": 120},
                    "items": {"type": "array", "maxItems": 8, "items": {
                        "type": "object", "required": ["headline", "summary", "evidence_ids"],
                        "properties": {
                            "headline": {"type": "string", "maxLength": 240},
                            "summary": {"type": "string", "maxLength": 800},
                            "evidence_ids": {
                                "type": "array", "minItems": 1, "maxItems": 8,
                                "items": {"type": "string"},
                            },
                        },
                    }},
                },
            },
        },
    }
    return payload


def _decode_brief(
    envelope: dict[str, object], evidence: list[dict[str, object]],
) -> dict:
    result = json.loads(envelope["candidates"][0]["content"]["parts"][0]["text"])
    if not isinstance(result, dict):
        raise ValueError("Gemma daily brief returned a non-object result")
    title = str(result.get("title") or "").strip()
    items = result.get("items")
    if not isinstance(items, list) or not title or len(title) > 120:
        raise ValueError("Gemma daily brief returned an invalid result")
    allowed = {str(row["id"]) for row in evidence}
    canonical_items = []
    for item in items[:8]:
        if not isinstance(item, dict):
            raise ValueError("Gemma daily brief returned an invalid item")
        headline_value = item.get("headline")
        summary_value = item.get("summary")
        headline = headline_value.strip() if isinstance(headline_value, str) else ""
        summary = summary_value.strip() if isinstance(summary_value, str) else ""
        refs = item.get("evidence_ids")
        if (
            not headline
            or len(headline) > 240
            or not summary
            or len(summary) > 800
            or not isinstance(refs, list)
            or not 1 <= len(refs) <= 8
            or any(not isinstance(ref, str) or ref not in allowed for ref in refs)
        ):
            raise ValueError("Gemma daily brief cited unknown evidence")
        canonical_items.append({
            "headline": headline,
            "summary": summary,
            "evidence_ids": refs,
        })
    return {"title": title, "items": canonical_items}


def _settling_response(day: str, pending_since: datetime) -> dict[str, object]:
    retry_at = pending_since + BRIEF_REGENERATION_DEBOUNCE
    return {
        "status": "DEFERRED",
        "brief_date": day,
        "reason": "SOURCE_SETTLING",
        "retry_at": retry_at.isoformat(),
    }


def update_daily_brief(
    ledger: ForwardLedger,
    *,
    api_key: str,
    request_accountant: ModelRequestAccountant,
    now: datetime | None = None,
) -> dict:
    instant = (now or datetime.now(UTC)).astimezone(UTC)
    day = instant.astimezone(KUALA_LUMPUR).date().isoformat()
    rows = _source_rows(ledger, day, cutoff=instant)
    if not rows:
        return {"status": "NO_NEWS", "brief_date": day}
    source_hash = _source_hash(rows)
    candidates = _candidate_rows(rows)
    evidence = _evidence_packet(candidates)
    candidate_hash = _source_hash(evidence)
    existing = ledger.connection.execute(
        "SELECT 1 FROM daily_news_briefs WHERE brief_date=? AND source_hash=?",
        (day, source_hash),
    ).fetchone()
    if existing:
        with ledger.connection:
            ledger.connection.execute(
                """INSERT INTO daily_news_brief_refresh_state
                   (brief_date,last_generated_candidate_hash,pending_source_hash,
                    pending_candidate_hash,pending_since,last_observed_at)
                   VALUES (?,?,NULL,NULL,NULL,?)
                   ON CONFLICT(brief_date) DO UPDATE SET
                     last_generated_candidate_hash=excluded.last_generated_candidate_hash,
                     pending_source_hash=NULL,pending_candidate_hash=NULL,
                     pending_since=NULL,last_observed_at=excluded.last_observed_at""",
                (day, candidate_hash, instant.isoformat()),
            )
        return {"status": "UNCHANGED", "brief_date": day}

    latest = ledger.connection.execute(
        """SELECT 1 FROM daily_news_briefs
           WHERE brief_date=? ORDER BY revision_number DESC LIMIT 1""",
        (day,),
    ).fetchone()
    state = ledger.connection.execute(
        "SELECT * FROM daily_news_brief_refresh_state WHERE brief_date=?",
        (day,),
    ).fetchone()
    if (
        state is not None
        and state["last_generated_candidate_hash"] == candidate_hash
    ):
        if state["pending_candidate_hash"] is not None:
            with ledger.connection:
                ledger.connection.execute(
                    """UPDATE daily_news_brief_refresh_state
                       SET pending_source_hash=NULL,pending_candidate_hash=NULL,
                           pending_since=NULL,last_observed_at=?
                       WHERE brief_date=?""",
                    (instant.isoformat(), day),
                )
        return {
            "status": "UNCHANGED",
            "brief_date": day,
            "reason": "CANDIDATES_UNCHANGED",
        }

    if latest is not None:
        pending_matches = (
            state is not None
            and state["pending_candidate_hash"] == candidate_hash
            and state["pending_since"]
        )
        if not pending_matches:
            with ledger.connection:
                ledger.connection.execute(
                    """INSERT INTO daily_news_brief_refresh_state
                       (brief_date,last_generated_candidate_hash,
                        pending_source_hash,pending_candidate_hash,pending_since,
                        last_observed_at)
                       VALUES (?,NULL,?,?,?,?)
                       ON CONFLICT(brief_date) DO UPDATE SET
                         pending_source_hash=excluded.pending_source_hash,
                         pending_candidate_hash=excluded.pending_candidate_hash,
                         pending_since=excluded.pending_since,
                         last_observed_at=excluded.last_observed_at""",
                    (
                        day,
                        source_hash,
                        candidate_hash,
                        instant.isoformat(),
                        instant.isoformat(),
                    ),
                )
            return _settling_response(day, instant)
        pending_since = datetime.fromisoformat(str(state["pending_since"]))
        if state["pending_source_hash"] != source_hash:
            with ledger.connection:
                ledger.connection.execute(
                    """UPDATE daily_news_brief_refresh_state
                       SET pending_source_hash=?,last_observed_at=?
                       WHERE brief_date=?""",
                    (source_hash, instant.isoformat(), day),
                )
        if instant < pending_since + BRIEF_REGENERATION_DEBOUNCE:
            return _settling_response(day, pending_since)
    try:
        brief, model = generate_metered_json(
            api_key,
            model=DEFAULT_GEMMA_MODEL,
            purpose="daily-news-brief",
            payload=_brief_payload(evidence),
            decode=lambda envelope: _decode_brief(envelope, evidence),
            request_accountant=request_accountant,
        )
    except ModelGatewayCapacityExhausted:
        return {"status": "DEFERRED", "brief_date": day, "reason": "NO_GEMMA_CAPACITY"}
    revision = ledger.connection.execute(
        "SELECT COALESCE(max(revision_number),0)+1 FROM daily_news_briefs WHERE brief_date=?", (day,)
    ).fetchone()[0]
    with ledger.connection:
        ledger.connection.execute(
            "INSERT INTO daily_news_briefs VALUES (?,?,?,?,?,?,?,?)",
            (day, revision, source_hash, instant.isoformat(), instant.isoformat(), model,
             BRIEF_PROMPT_VERSION, json.dumps(brief, ensure_ascii=False, separators=(",", ":"))),
        )
        ledger.connection.execute(
            """INSERT INTO daily_news_brief_refresh_state
               (brief_date,last_generated_candidate_hash,pending_source_hash,
                pending_candidate_hash,pending_since,last_observed_at)
               VALUES (?,?,NULL,NULL,NULL,?)
               ON CONFLICT(brief_date) DO UPDATE SET
                 last_generated_candidate_hash=excluded.last_generated_candidate_hash,
                 pending_source_hash=NULL,pending_candidate_hash=NULL,
                 pending_since=NULL,last_observed_at=excluded.last_observed_at""",
            (day, candidate_hash, instant.isoformat()),
        )
    return {
        "status": "OK",
        "brief_date": day,
        "revision_number": revision,
        "items": len(brief["items"]),
        "eligible_items": len(rows),
        "candidate_items": len(candidates),
    }


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
