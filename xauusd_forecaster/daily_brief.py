"""Per-date, rolling and append-only Daily Brief lifecycle."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from .annotation import (
    DEFAULT_GEMINI_MODEL, DEFAULT_GEMMA_MODEL, FALLBACK_GEMINI_MODEL,
    PROMPT_VERSION, conservative_input_token_estimate, generate_metered_json,
)
from .forward_ledger import ForwardLedger
from .model_gateway import (
    ModelGatewayCapacityExhausted, ModelGatewayResponseInvalid,
    ModelRequestAccountant,
)
from .news_semantics import validated_annotation_predicate


BRIEF_PROMPT_VERSION = "daily-news-brief-v4-concise-synthesis"
BRIEF_RECOVERY_VERSION = "daily-brief-recovery-v2-protected-lease"
BRIEF_EVIDENCE_LIMIT = 60
BRIEF_INPUT_TOKEN_BUDGET = 12_000
BRIEF_OUTPUT_TOKEN_BUDGET = 8_192
BRIEF_BACKLOG_LIMIT = 14
BRIEF_REGENERATION_DEBOUNCE = timedelta(minutes=10)
BRIEF_CAPACITY_RETRY = timedelta(minutes=1)
BRIEF_FAILURE_RETRY_MAX = timedelta(hours=1)
BRIEF_FAILURE_ATTEMPT_LIMIT = 5
KUALA_LUMPUR = ZoneInfo("Asia/Kuala_Lumpur")
FINAL_PHASES = frozenset({"FINAL", "DEGRADED"})
GENERATION_FAILURE_CODES = frozenset({
    "MODEL_OUTPUT_CONTRACT_FAILED", "MODEL_OUTPUT_INVALID",
    "PROVIDER_HTTP_ERROR", "MODEL_REQUEST_FAILED",
})


class DailyBriefEvidenceContractFailed(ValueError):
    """A brief referenced evidence outside its bounded input packet."""

    def __init__(self, result: dict[str, object], unknown_ids: list[str],
                 *, allowed_count: int) -> None:
        serialized = json.dumps(
            result, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
            default=str,
        )
        bounded_ids = [str(value)[:160] for value in unknown_ids[:8]]
        message = (
            "Gemma daily brief cited unknown evidence IDs: "
            + ", ".join(bounded_ids)
        )
        self.failure_evidence = {
            "failure_code": "MODEL_OUTPUT_CONTRACT_FAILED",
            "failure_stage": "DAILY_BRIEF_EVIDENCE_IDS",
            "response_hash": hashlib.sha256(serialized.encode("utf-8")).hexdigest(),
            "selected_output": {
                "unknown_evidence_ids": bounded_ids,
                "allowed_evidence_count": allowed_count,
            },
            "cause_type": type(self).__name__,
            "cause": message[:500],
        }
        super().__init__(message)


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="microseconds")


def _day_bounds(day: str) -> tuple[datetime, datetime]:
    parsed = date.fromisoformat(day)
    start = datetime.combine(parsed, datetime.min.time(), KUALA_LUMPUR)
    return start.astimezone(UTC), (start + timedelta(days=1)).astimezone(UTC)


def _population_rows(
    ledger: ForwardLedger, day: str, *, cutoff: datetime,
) -> list[dict]:
    """Return the exact latest, annotatable population for one receipt day."""
    start, end = _day_bounds(day)
    cutoff_iso = _iso(cutoff)
    valid = validated_annotation_predicate("candidate_a")
    parameters = (
        _iso(start), _iso(end), cutoff_iso,
        _iso(start), _iso(end), cutoff_iso,
        _iso(start), _iso(end), cutoff_iso,
        _iso(start), _iso(end), cutoff_iso,
        PROMPT_VERSION, DEFAULT_GEMINI_MODEL, FALLBACK_GEMINI_MODEL,
        PROMPT_VERSION, cutoff_iso, cutoff_iso, cutoff_iso, cutoff_iso,
    )
    rows = ledger.connection.execute(
        f"""WITH population AS (
               SELECT n.* FROM news_revisions n
               WHERE julianday(n.collector_first_seen_time)>=julianday(?)
                 AND julianday(n.collector_first_seen_time)<julianday(?)
                 AND julianday(n.collector_first_seen_time)<=julianday(?)
                 AND length(trim(COALESCE(n.body,'')))>=240
                 AND NOT EXISTS (
                   SELECT 1 FROM news_revisions newer
                   WHERE newer.source=n.source
                     AND newer.source_item_id=n.source_item_id
                     AND newer.revision_number>n.revision_number
                     AND julianday(newer.collector_first_seen_time)>=julianday(?)
                     AND julianday(newer.collector_first_seen_time)<julianday(?)
                     AND julianday(newer.collector_first_seen_time)<=julianday(?))
                 AND NOT EXISTS (
                   SELECT 1 FROM news_revisions peer
                   WHERE peer.cluster_id=n.cluster_id
                     AND julianday(peer.collector_first_seen_time)>=julianday(?)
                     AND julianday(peer.collector_first_seen_time)<julianday(?)
                     AND julianday(peer.collector_first_seen_time)<=julianday(?)
                     AND length(trim(COALESCE(peer.body,'')))>=240
                     AND NOT EXISTS (
                       SELECT 1 FROM news_revisions peer_newer
                       WHERE peer_newer.source=peer.source
                         AND peer_newer.source_item_id=peer.source_item_id
                         AND peer_newer.revision_number>peer.revision_number
                         AND julianday(peer_newer.collector_first_seen_time)>=julianday(?)
                         AND julianday(peer_newer.collector_first_seen_time)<julianday(?)
                         AND julianday(peer_newer.collector_first_seen_time)<=julianday(?))
                     AND (length(COALESCE(peer.body,''))>length(COALESCE(n.body,''))
                       OR (length(COALESCE(peer.body,''))=length(COALESCE(n.body,''))
                         AND (peer.source<n.source OR
                           (peer.source=n.source AND peer.source_item_id<n.source_item_id)))))
           )
           SELECT p.source,p.source_item_id,p.revision_number,p.content_hash,
                  p.cluster_id,p.source_published_time,p.collector_first_seen_time,
                  p.headline,a.annotation_id,a.annotation_json,a.novelty,
                  a.confidence,a.parsed_at,COALESCE(t.headline_zh,p.headline) headline_zh,
                  i.impact_class,i.event_state AS impact_event_state,
                  i.update_type AS impact_update_type,i.confidence AS impact_confidence,
                  er.canonical_event_id,er.canonical_episode_id,
                  CASE WHEN a.annotation_id IS NULL AND EXISTS (
                    SELECT 1 FROM news_ai_jobs_v1 j
                    WHERE j.task_type='ACTIVE_ANNOTATION'
                      AND j.source=p.source AND j.source_item_id=p.source_item_id
                      AND j.revision_number=p.revision_number
                      AND j.prompt_version=? AND j.state='DEAD_LETTER'
                  ) THEN 1 ELSE 0 END AS terminal_failure
           FROM population p
           LEFT JOIN news_annotations a ON a.annotation_id=(
             SELECT candidate_a.annotation_id FROM news_annotations candidate_a
             WHERE candidate_a.source=p.source
               AND candidate_a.source_item_id=p.source_item_id
               AND candidate_a.revision_number=p.revision_number
               AND candidate_a.raw_content_hash=p.content_hash
               AND candidate_a.llm_model_version IN (?,?)
               AND candidate_a.prompt_version=?
               AND julianday(candidate_a.parsed_at)<=julianday(?)
               AND {valid}
             ORDER BY julianday(candidate_a.parsed_at) DESC,
                      candidate_a.parsed_at DESC,candidate_a.annotation_id DESC LIMIT 1)
           LEFT JOIN news_title_translations t ON t.translation_id=(
             SELECT candidate_t.translation_id FROM news_title_translations candidate_t
             WHERE candidate_t.source=p.source
               AND candidate_t.source_item_id=p.source_item_id
               AND candidate_t.revision_number=p.revision_number
               AND candidate_t.raw_content_hash=p.content_hash
               AND julianday(candidate_t.parsed_at)<=julianday(?)
             ORDER BY julianday(candidate_t.parsed_at) DESC,
                      candidate_t.parsed_at DESC,candidate_t.translation_id DESC LIMIT 1)
           LEFT JOIN news_impact_assessments_v1 i ON i.assessment_id=(
             SELECT candidate_i.assessment_id FROM news_impact_assessments_v1 candidate_i
             WHERE candidate_i.annotation_id=a.annotation_id
               AND julianday(candidate_i.assessed_at)<=julianday(?)
             ORDER BY julianday(candidate_i.assessed_at) DESC,
                      candidate_i.assessed_at DESC,candidate_i.assessment_id DESC LIMIT 1)
           LEFT JOIN news_event_identity_resolutions_v1 er ON er.resolution_id=(
             SELECT candidate_er.resolution_id FROM news_event_identity_resolutions_v1 candidate_er
             WHERE candidate_er.assessment_id=i.assessment_id
               AND julianday(candidate_er.resolved_at)<=julianday(?)
             ORDER BY julianday(candidate_er.resolved_at) DESC,
                      candidate_er.resolved_at DESC,candidate_er.resolution_id DESC LIMIT 1)
           ORDER BY julianday(p.collector_first_seen_time),
                    p.collector_first_seen_time,p.source,p.source_item_id,
                    p.revision_number""",
        parameters,
    ).fetchall()
    return [dict(row) for row in rows]


def _reviewed_rows(rows: list[dict]) -> list[dict]:
    result = []
    for row in rows:
        if not row.get("annotation_id"):
            continue
        annotation = json.loads(str(row["annotation_json"]))
        result.append({
            **row,
            "summary": annotation.get("summary_zh"),
            "category": annotation.get("primary_category"),
            "review_priority": annotation.get("review_priority"),
            "materiality": annotation.get("materiality"),
            "material_event_key": annotation.get("material_event_key"),
        })
    return result


def _source_hash(rows: list[dict]) -> str:
    encoded = json.dumps(
        rows, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _instant_key(value: object) -> str:
    """Canonicalize an ISO timestamp before deterministic ordering."""
    return datetime.fromisoformat(str(value)).astimezone(UTC).isoformat(
        timespec="microseconds",
    )


def _population_hash(rows: list[dict]) -> str:
    return _source_hash([{
        "id": f"{row['source']}:{row['source_item_id']}:{row['revision_number']}",
        "content_hash": row["content_hash"],
        "annotation_id": row.get("annotation_id"),
        "terminal_failure": int(row.get("terminal_failure") or 0),
    } for row in rows])


def _importance(row: dict) -> tuple:
    priority = {"IMMEDIATE": 4, "FAST": 3, "NORMAL": 2, "BACKGROUND": 1}
    impact = {
        "POLICY_SHIFT": 6, "DATA_RELEASE": 5, "IMMEDIATE": 4,
        "SAME_DAY": 3, "ONGOING_EVENT": 2, "BACKGROUND": 1,
    }
    update = {"MATERIAL_UPDATE": 4, "NEW_EVENT": 3, "COMMENTARY": 1,
              "HISTORICAL_CONTEXT": 0, "DUPLICATE_REPORT": -1}
    category = str(row.get("category") or "").casefold()
    major = int(any(token in category for token in (
        "货币", "央行", "宏观", "经济数据", "通胀", "就业", "地缘", "政策",
        "rates_fed", "inflation", "employment", "growth_economy",
        "usd_liquidity", "war_geopolitics", "central_bank_gold",
    )))
    return (
        priority.get(str(row.get("review_priority") or "NORMAL").upper(), 0),
        impact.get(str(row.get("impact_class") or "BACKGROUND"), 0),
        update.get(str(row.get("impact_update_type") or ""), 0), major,
        float(row.get("materiality") or 0), float(row.get("novelty") or 0),
        float(row.get("impact_confidence") or row.get("confidence") or 0),
        _instant_key(row["collector_first_seen_time"]), str(row["source"]),
        str(row["source_item_id"]),
    )


def _event_identity(row: dict) -> str:
    return str(row.get("canonical_event_id") or row.get("canonical_episode_id")
               or row.get("material_event_key") or row.get("cluster_id")
               or f"{row['source']}:{row['source_item_id']}")


def _candidate_rows(rows: list[dict]) -> list[dict]:
    """Choose one best update per event before applying the hard packet bound."""
    by_event: dict[str, dict] = {}
    for row in rows:
        key = _event_identity(row)
        if key not in by_event or _importance(row) > _importance(by_event[key]):
            by_event[key] = row
    selected = sorted(by_event.values(), key=_importance, reverse=True)[:BRIEF_EVIDENCE_LIMIT]
    return sorted(selected, key=lambda row: (
        _instant_key(row["collector_first_seen_time"]), str(row["source"]),
        str(row["source_item_id"]), int(row["revision_number"]),
    ))


def _bounded_text(value: object, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


def _evidence_packet(rows: list[dict]) -> list[dict[str, object]]:
    return [{
        "id": f"{row['source']}:{row['source_item_id']}:{row['revision_number']}",
        "headline": _bounded_text(row["headline_zh"], 300),
        "summary": _bounded_text(row["summary"], 600),
        "category": _bounded_text(row["category"], 80),
        "impact_class": row.get("impact_class"),
        "update_type": row.get("impact_update_type"),
        "published_at": row["source_published_time"],
        "received_at": row["collector_first_seen_time"],
    } for row in rows]


def _budgeted_evidence_packet(
    day: str, rows: list[dict],
) -> list[dict[str, object]]:
    """Keep the strongest evidence that fits one Gemma TPM reservation."""
    selected = list(rows)
    while selected:
        packet = _evidence_packet(selected)
        serialized = json.dumps(
            _brief_payload(day, packet),
            ensure_ascii=False,
            separators=(",", ":"),
        )
        if conservative_input_token_estimate(serialized) <= BRIEF_INPUT_TOKEN_BUDGET:
            return packet
        selected.remove(min(selected, key=_importance))
    return []


def _brief_payload(day: str, evidence: list[dict[str, object]]) -> dict[str, object]:
    cited_evidence = [
        {"ref": f"E{index:02d}", **{
            key: value for key, value in row.items() if key != "id"
        }}
        for index, row in enumerate(evidence, start=1)
    ]
    citation_refs = [str(row["ref"]) for row in cited_evidence]
    return {
        "systemInstruction": {"parts": [{"text": (
            "你是黄金市场新闻编辑。只可总结提供的资料，不作交易建议，不补充外部事实。"
            "合并重复报道，先综合跨事件关系与共同市场背景，再列真正的新进展。"
            "使用简短自然的简体中文，不得把输入标题或摘要原样堆叠成简报。"
        )}]},
        "contents": [{"parts": [{"text": (
            f"生成 {day} 每日简报。返回标题、2至3句综合overview和最多5条重点；"
            "每条必须从资料中的ref原样选择支持它的evidence_ids；不得复制或猜测内部ID。"
            "如果资料不足，宁可少写。"
            "只返回JSON。\nEVIDENCE\n" +
            json.dumps(cited_evidence, ensure_ascii=False, separators=(",", ":"))
        )}]}],
        "generationConfig": {
            "responseMimeType": "application/json", "temperature": 0,
            "maxOutputTokens": BRIEF_OUTPUT_TOKEN_BUDGET,
            "thinkingConfig": {"thinkingLevel": "minimal"},
            "responseSchema": {
                "type": "object", "required": ["title", "overview", "items"],
                "properties": {
                    "title": {"type": "string", "maxLength": 120},
                    "overview": {"type": "string", "maxLength": 500},
                    "items": {"type": "array", "maxItems": 5, "items": {
                        "type": "object", "required": ["headline", "summary", "evidence_ids"],
                        "properties": {
                            "headline": {"type": "string", "maxLength": 90},
                            "summary": {"type": "string", "maxLength": 280},
                            "evidence_ids": {"type": "array", "minItems": 1,
                                "maxItems": 8, "items": {
                                    "type": "string", "enum": citation_refs,
                                }},
                        },
                    }},
                },
            },
        },
    }


def _decode_brief(envelope: dict[str, object], evidence: list[dict[str, object]]) -> dict:
    candidate = envelope["candidates"][0]
    finish_reason = candidate.get("finishReason")
    if finish_reason not in (None, "STOP"):
        raise ValueError(f"Gemma daily brief ended with {finish_reason!s}")
    result = json.loads(candidate["content"]["parts"][0]["text"])
    if not isinstance(result, dict):
        raise ValueError("Gemma daily brief returned a non-object result")
    title = str(result.get("title") or "").strip()
    overview = str(result.get("overview") or "").strip()
    items = result.get("items")
    if (not isinstance(items, list) or not title or len(title) > 120
            or not overview or len(overview) > 500 or len(items) > 5):
        raise ValueError("Gemma daily brief returned an invalid result")
    citation_map = {
        f"E{index:02d}": str(row["id"])
        for index, row in enumerate(evidence, start=1)
    }
    canonical = []
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("Gemma daily brief returned an invalid item")
        headline, summary, refs = item.get("headline"), item.get("summary"), item.get("evidence_ids")
        if (not isinstance(headline, str) or not headline.strip() or len(headline.strip()) > 90
                or not isinstance(summary, str) or not summary.strip() or len(summary.strip()) > 280
                or not isinstance(refs, list) or not 1 <= len(refs) <= 8
                or any(not isinstance(ref, str) for ref in refs)):
            raise ValueError("Gemma daily brief returned an invalid item")
        unknown = [ref for ref in refs if ref not in citation_map]
        if unknown:
            raise DailyBriefEvidenceContractFailed(
                result, unknown, allowed_count=len(citation_map),
            )
        canonical.append({"headline": headline.strip(), "summary": summary.strip(),
                          "evidence_ids": [citation_map[ref] for ref in refs]})
    return {"title": title, "overview": overview, "items": canonical}


def _counts(rows: list[dict]) -> dict[str, int]:
    reviewed = sum(bool(row.get("annotation_id")) for row in rows)
    terminal = sum(not row.get("annotation_id") and bool(row.get("terminal_failure")) for row in rows)
    return {"received_items": len(rows), "reviewed_items": reviewed,
            "pending_items": max(0, len(rows) - reviewed - terminal),
            "terminal_failure_items": terminal}


def _latest_brief(connection, day: str):
    return connection.execute(
        "SELECT revision_number,source_hash,generated_at,prompt_version "
        "FROM daily_news_briefs WHERE brief_date=? "
        "ORDER BY revision_number DESC LIMIT 1", (day,),
    ).fetchone()


def _effective_finalization(connection, day: str):
    correction = connection.execute(
        """SELECT * FROM daily_news_brief_finalization_corrections_v1
           WHERE brief_date=? AND recovery_version=?""",
        (day, BRIEF_RECOVERY_VERSION),
    ).fetchone()
    if correction:
        return correction
    original = connection.execute(
        "SELECT * FROM daily_news_brief_finalizations_v1 WHERE brief_date=?",
        (day,),
    ).fetchone()
    if original and str(original["final_status"]) == "DEGRADED":
        return None
    return original


def _write_state(
    ledger: ForwardLedger, day: str, *, instant: datetime, phase: str,
    counts: dict[str, int], latest_revision: int | None, last_generated_at: str | None,
    candidate_hash: str | None = None, pending_source_hash: str | None = None,
    pending_candidate_hash: str | None = None, pending_since: str | None = None,
    next_retry_at: str | None = None, finalized_at: str | None = None,
    failure_count: int = 0, failure_code: str | None = None,
    failure_at: str | None = None,
) -> None:
    values = (day, candidate_hash, pending_source_hash, pending_candidate_hash,
              pending_since, _iso(instant), phase, counts["received_items"],
              counts["reviewed_items"], counts["pending_items"],
              counts["terminal_failure_items"], latest_revision, last_generated_at,
              next_retry_at, finalized_at, failure_count, failure_code, failure_at)
    with ledger.connection:
        ledger.connection.execute(
            """INSERT INTO daily_news_brief_refresh_state
               (brief_date,last_generated_candidate_hash,pending_source_hash,
                pending_candidate_hash,pending_since,last_observed_at,phase,
                received_items,reviewed_items,pending_items,terminal_failure_items,
                latest_revision,last_generated_at,next_retry_at,finalized_at,
                generation_failure_count,last_failure_code,last_failure_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(brief_date) DO UPDATE SET
                 last_generated_candidate_hash=excluded.last_generated_candidate_hash,
                 pending_source_hash=excluded.pending_source_hash,
                 pending_candidate_hash=excluded.pending_candidate_hash,
                 pending_since=excluded.pending_since,last_observed_at=excluded.last_observed_at,
                 phase=excluded.phase,received_items=excluded.received_items,
                 reviewed_items=excluded.reviewed_items,pending_items=excluded.pending_items,
                 terminal_failure_items=excluded.terminal_failure_items,
                 latest_revision=excluded.latest_revision,last_generated_at=excluded.last_generated_at,
                 next_retry_at=excluded.next_retry_at,finalized_at=excluded.finalized_at,
                 generation_failure_count=excluded.generation_failure_count,
                 last_failure_code=excluded.last_failure_code,last_failure_at=excluded.last_failure_at""",
            values,
        )


def _finalize(ledger: ForwardLedger, day: str, *, instant: datetime,
              counts: dict[str, int], latest_revision: int,
              last_generated_at: str, candidate_hash: str,
              force_degraded: bool = False) -> str:
    phase = "DEGRADED" if counts["terminal_failure_items"] or force_degraded else "FINAL"
    with ledger.connection:
        original = ledger.connection.execute(
            "SELECT 1 FROM daily_news_brief_finalizations_v1 WHERE brief_date=?", (day,),
        ).fetchone()
        if original:
            correction_id = hashlib.sha256(
                f"{day}:{BRIEF_RECOVERY_VERSION}".encode("utf-8")
            ).hexdigest()
            ledger.connection.execute(
                """INSERT OR IGNORE INTO daily_news_brief_finalization_corrections_v1
                   (correction_id,brief_date,recovery_version,revision_number,
                    final_status,received_items,reviewed_items,terminal_failure_items,
                    cutoff_at,finalized_at) VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (correction_id, day, BRIEF_RECOVERY_VERSION, latest_revision, phase,
                 counts["received_items"], counts["reviewed_items"],
                 counts["terminal_failure_items"], _iso(instant), _iso(instant)),
            )
        else:
            ledger.connection.execute(
                """INSERT OR IGNORE INTO daily_news_brief_finalizations_v1
                   (brief_date,revision_number,final_status,received_items,reviewed_items,
                    terminal_failure_items,cutoff_at,finalized_at) VALUES (?,?,?,?,?,?,?,?)""",
                (day, latest_revision, phase, counts["received_items"],
                 counts["reviewed_items"], counts["terminal_failure_items"],
                 _iso(instant), _iso(instant)),
            )
    _write_state(ledger, day, instant=instant, phase=phase, counts=counts,
                 latest_revision=latest_revision, last_generated_at=last_generated_at,
                 candidate_hash=candidate_hash, finalized_at=_iso(instant))
    return phase


def _defer(ledger: ForwardLedger, day: str, *, instant: datetime,
           counts: dict[str, int], latest, candidate_hash: str | None,
           reason: str, retry_at: datetime) -> dict[str, object]:
    state = ledger.connection.execute(
        """SELECT pending_source_hash,pending_since
           FROM daily_news_brief_refresh_state WHERE brief_date=?""", (day,),
    ).fetchone()
    _write_state(ledger, day, instant=instant, phase="DEFERRED", counts=counts,
                 latest_revision=int(latest["revision_number"]) if latest else None,
                 last_generated_at=str(latest["generated_at"]) if latest else None,
                 candidate_hash=str(latest["source_hash"]) if latest else None,
                 pending_source_hash=(str(state["pending_source_hash"])
                                      if state and state["pending_source_hash"] else None),
                 pending_candidate_hash=candidate_hash,
                 pending_since=(str(state["pending_since"])
                                if state and state["pending_since"] else None),
                 next_retry_at=_iso(retry_at),
                 failure_code=reason)
    return {"status": "DEFERRED", "phase": "DEFERRED", "brief_date": day,
            "reason": reason, "next_retry_at": _iso(retry_at), **counts}


def _record_generation_failure(ledger: ForwardLedger, day: str, *, instant: datetime,
                               error: Exception, counts: dict[str, int], latest,
                               candidate_hash: str) -> dict[str, object]:
    previous = ledger.connection.execute(
        """SELECT generation_failure_count,pending_source_hash,pending_candidate_hash,
                  pending_since,last_failure_code
           FROM daily_news_brief_refresh_state WHERE brief_date=?""",
        (day,),
    ).fetchone()
    sequence = ledger.connection.execute(
        "SELECT COALESCE(MAX(attempt_number),0) FROM daily_news_brief_failures_v1 WHERE brief_date=?",
        (day,),
    ).fetchone()
    attempt = int(sequence[0]) + 1
    consecutive = (
        int(previous["generation_failure_count"]) + 1
        if previous and previous["pending_candidate_hash"] == candidate_hash
        and previous["last_failure_code"] in GENERATION_FAILURE_CODES
        else 1
    )
    minutes = min(2 ** min(consecutive - 1, 6), int(BRIEF_FAILURE_RETRY_MAX.total_seconds() / 60))
    retry_at = instant + timedelta(minutes=minutes)
    evidence = getattr(error, "failure_evidence", None)
    if isinstance(evidence, dict):
        code = str(evidence.get("failure_code") or "MODEL_OUTPUT_INVALID")
        error_type = str(evidence.get("cause_type") or type(error).__name__)[:120]
        message = str(evidence.get("cause") or str(error))[:500]
    elif isinstance(error, ModelGatewayResponseInvalid):
        code = "MODEL_OUTPUT_INVALID"
        error_type = str(error.cause_type)[:120]
        message = str(error.cause_message)[:500]
    elif isinstance(error, (ValueError, KeyError, json.JSONDecodeError)):
        code = "MODEL_OUTPUT_INVALID"
        error_type = type(error).__name__
        message = str(error)[:500]
    elif isinstance(getattr(error, "code", None), int):
        code = "PROVIDER_HTTP_ERROR"
        error_type = type(error).__name__
        message = str(error)[:500]
    else:
        code = "MODEL_REQUEST_FAILED"
        error_type = type(error).__name__
        message = str(error)[:500]
    signature = hashlib.sha256(f"{error_type}:{message}".encode()).hexdigest()
    failure_id = hashlib.sha256(f"{day}:{attempt}:{_iso(instant)}:{signature}".encode()).hexdigest()
    evidence_json = (
        json.dumps(evidence, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        if isinstance(evidence, dict) else None
    )
    with ledger.connection:
        ledger.connection.execute(
            """INSERT INTO daily_news_brief_failures_v1
               (failure_id,brief_date,attempt_number,failure_code,error_type,
                error_signature,error,failed_at,next_retry_at,failure_evidence_json)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (failure_id, day, attempt, code, error_type, signature,
             message, _iso(instant), _iso(retry_at), evidence_json),
        )
    _write_state(ledger, day, instant=instant, phase="DEFERRED", counts=counts,
                 latest_revision=int(latest["revision_number"]) if latest else None,
                 last_generated_at=str(latest["generated_at"]) if latest else None,
                 candidate_hash=str(latest["source_hash"]) if latest else None,
                 pending_source_hash=(str(previous["pending_source_hash"])
                                      if previous and previous["pending_source_hash"] else None),
                 pending_candidate_hash=candidate_hash,
                 pending_since=(str(previous["pending_since"])
                                if previous and previous["pending_since"] else None),
                 next_retry_at=_iso(retry_at),
                 failure_count=consecutive, failure_code=code, failure_at=_iso(instant))
    return {"status": "DEFERRED", "phase": "DEFERRED", "brief_date": day,
            "reason": code, "failure_count": consecutive,
            "next_retry_at": _iso(retry_at), **counts}


def _finalize_generation_fallback(
    ledger: ForwardLedger, day: str, *, instant: datetime,
    counts: dict[str, int], latest, candidate_hash: str,
    candidates: list[dict],
) -> dict[str, object]:
    """Close an irrecoverable historical synthesis with cited reviewed facts."""
    revision = int(latest["revision_number"]) + 1 if latest else 1
    important = sorted(candidates, key=_importance, reverse=True)[:8]
    brief = {
        "title": f"{day} 每日简报（自动整理）",
        "overview": "Gemma 汇总未完成；以下内容由系统从已复核资料中按重要性整理。",
        "items": [{
            "headline": _bounded_text(row["headline_zh"], 240),
            "summary": _bounded_text(row["summary"], 800),
            "evidence_ids": [
                f"{row['source']}:{row['source_item_id']}:{row['revision_number']}"
            ],
        } for row in important],
    }
    generated_at = _iso(instant)
    with ledger.connection:
        ledger.connection.execute(
            """INSERT INTO daily_news_briefs
               (brief_date,revision_number,source_hash,cutoff_at,generated_at,
                model_version,prompt_version,brief_json) VALUES (?,?,?,?,?,?,?,?)""",
            (day, revision, candidate_hash, generated_at, generated_at,
             "system-degraded-fallback", BRIEF_PROMPT_VERSION,
             json.dumps(brief, ensure_ascii=False, sort_keys=True,
                        separators=(",", ":"))),
        )
    phase = _finalize(
        ledger, day, instant=instant, counts=counts, latest_revision=revision,
        last_generated_at=generated_at, candidate_hash=candidate_hash,
        force_degraded=True,
    )
    return {"status": "OK", "phase": phase, "brief_date": day,
            "reason": "GENERATION_FAILURE_TERMINAL_FALLBACK",
            "revision_number": revision, **counts}


def brief_dates_to_process(
    connection: sqlite3.Connection, *, now: datetime | None = None,
    limit: int = BRIEF_BACKLOG_LIMIT,
) -> list[str]:
    """Return current receipt day plus a bounded newest-first unfinished backlog."""
    instant = now or datetime.now(UTC)
    current_day = instant.astimezone(KUALA_LUMPUR).date().isoformat()
    rows = connection.execute(
        """WITH receipt_days AS (
             SELECT DISTINCT substr(datetime(collector_first_seen_time,'+8 hours'),1,10) AS day
             FROM news_revisions
             WHERE julianday(collector_first_seen_time)<=julianday(?)
           )
           SELECT d.day FROM receipt_days d
           LEFT JOIN daily_news_brief_finalizations_v1 f ON f.brief_date=d.day
           LEFT JOIN daily_news_brief_finalization_corrections_v1 c
             ON c.brief_date=d.day AND c.recovery_version=?
           WHERE f.brief_date IS NULL
              OR (f.final_status='DEGRADED' AND c.brief_date IS NULL)
           ORDER BY d.day DESC LIMIT ?""",
        (_iso(instant), BRIEF_RECOVERY_VERSION, max(1, int(limit))),
    ).fetchall()
    available = [str(row[0]) for row in rows if row[0]]
    backlog = [day for day in available if day != current_day]
    return [current_day, *backlog[: max(0, limit - 1)]]


def update_daily_brief(
    ledger: ForwardLedger, *, api_key: str | None,
    request_accountant: ModelRequestAccountant | None,
    now: datetime | None = None, brief_date: str | None = None,
) -> dict[str, object]:
    """Advance one date without mutating any prior brief revision."""
    instant = now or datetime.now(UTC)
    current_day = instant.astimezone(KUALA_LUMPUR).date().isoformat()
    day = brief_date or current_day
    date.fromisoformat(day)

    original_finalization = ledger.connection.execute(
        "SELECT * FROM daily_news_brief_finalizations_v1 WHERE brief_date=?", (day,),
    ).fetchone()
    finalized = _effective_finalization(ledger.connection, day)
    recovering = bool(
        finalized is None and original_finalization
        and str(original_finalization["final_status"]) == "DEGRADED"
    )
    if finalized:
        latest = _latest_brief(ledger.connection, day)
        counts = {
            "received_items": int(finalized["received_items"]),
            "reviewed_items": int(finalized["reviewed_items"]),
            "pending_items": 0,
            "terminal_failure_items": int(finalized["terminal_failure_items"]),
        }
        _write_state(
            ledger, day, instant=instant, phase=str(finalized["final_status"]),
            counts=counts,
            latest_revision=int(latest["revision_number"]) if latest else None,
            last_generated_at=str(latest["generated_at"]) if latest else None,
            candidate_hash=str(latest["source_hash"]) if latest else None,
            finalized_at=str(finalized["finalized_at"]),
        )
        return {"status": "UNCHANGED", "phase": str(finalized["final_status"]),
                "brief_date": day, "reason": "FINALIZED", **counts}

    rows = _population_rows(ledger, day, cutoff=instant)
    counts = _counts(rows)
    reviewed = _reviewed_rows(rows)
    candidates = _candidate_rows(reviewed)
    packet = _budgeted_evidence_packet(day, candidates)
    population_hash = _population_hash(rows)
    candidate_hash = _source_hash([
        {"prompt_version": BRIEF_PROMPT_VERSION},
        *([{"recovery_version": BRIEF_RECOVERY_VERSION}] if recovering else []),
        *packet,
    ]) if packet else None
    latest = _latest_brief(ledger.connection, day)
    state = ledger.connection.execute(
        "SELECT * FROM daily_news_brief_refresh_state WHERE brief_date=?", (day,),
    ).fetchone()
    if recovering and state and str(state["phase"]) == "DEGRADED":
        _write_state(
            ledger, day, instant=instant, phase="UPDATING", counts=counts,
            latest_revision=int(latest["revision_number"]) if latest else None,
            last_generated_at=str(latest["generated_at"]) if latest else None,
            pending_source_hash=population_hash,
        )
        state = ledger.connection.execute(
            "SELECT * FROM daily_news_brief_refresh_state WHERE brief_date=?", (day,),
        ).fetchone()

    if not rows:
        phase = "WAITING" if day == current_day else "EMPTY"
        if phase == "EMPTY":
            with ledger.connection:
                ledger.connection.execute(
                    """INSERT OR IGNORE INTO daily_news_brief_finalizations_v1
                       (brief_date,revision_number,final_status,received_items,
                        reviewed_items,terminal_failure_items,cutoff_at,finalized_at)
                       VALUES (?,NULL,'EMPTY',0,0,0,?,?)""",
                    (day, _iso(instant), _iso(instant)),
                )
        _write_state(ledger, day, instant=instant, phase=phase, counts=counts,
                     latest_revision=None, last_generated_at=None,
                     finalized_at=_iso(instant) if phase == "EMPTY" else None)
        return {"status": "NO_NEWS", "phase": phase, "brief_date": day, **counts}

    if not packet:
        if (day != current_day and counts["pending_items"] == 0
                and counts["terminal_failure_items"] > 0):
            if not latest:
                generated_at = _iso(instant)
                with ledger.connection:
                    ledger.connection.execute(
                        """INSERT INTO daily_news_briefs
                           (brief_date,revision_number,source_hash,cutoff_at,generated_at,
                            model_version,prompt_version,brief_json)
                           VALUES (?,?,?,?,?,?,?,?)""",
                        (day, 1, population_hash, generated_at, generated_at,
                         "system-terminal-settlement", BRIEF_PROMPT_VERSION,
                         json.dumps({"title": f"{day} 每日简报", "items": []},
                                    ensure_ascii=False, separators=(",", ":"))),
                    )
                latest = _latest_brief(ledger.connection, day)
            phase = _finalize(
                ledger, day, instant=instant, counts=counts,
                latest_revision=int(latest["revision_number"]),
                last_generated_at=str(latest["generated_at"]),
                candidate_hash=population_hash,
            )
            return {"status": "OK", "phase": phase, "brief_date": day,
                    "revision_number": int(latest["revision_number"]), **counts}
        phase = "WAITING" if day == current_day else "UPDATING"
        _write_state(ledger, day, instant=instant, phase=phase, counts=counts,
                     latest_revision=int(latest["revision_number"]) if latest else None,
                     last_generated_at=str(latest["generated_at"]) if latest else None,
                     pending_source_hash=population_hash)
        return {"status": "NO_REVIEWED_NEWS", "phase": phase,
                "brief_date": day, **counts}

    generated_hash = (
        state["last_generated_candidate_hash"]
        if state and state["last_generated_candidate_hash"]
        else latest["source_hash"] if latest else None
    )
    if (not recovering and latest and generated_hash == candidate_hash
            and latest["prompt_version"] == BRIEF_PROMPT_VERSION):
        if day != current_day and counts["pending_items"] == 0:
            phase = _finalize(
                ledger, day, instant=instant, counts=counts,
                latest_revision=int(latest["revision_number"]),
                last_generated_at=str(latest["generated_at"]),
                candidate_hash=str(candidate_hash),
            )
        else:
            phase = "UPDATING"
            _write_state(
                ledger, day, instant=instant, phase=phase, counts=counts,
                latest_revision=int(latest["revision_number"]),
                last_generated_at=str(latest["generated_at"]),
                candidate_hash=candidate_hash, pending_source_hash=population_hash,
            )
        return {"status": "UNCHANGED", "phase": phase, "brief_date": day,
                "reason": "CANDIDATES_UNCHANGED", "eligible_items": len(reviewed),
                "candidate_items": len(candidates), **counts}

    if latest and (day == current_day or counts["pending_items"] > 0):
        pending_since = str(state["pending_since"]) if state and state["pending_since"] else _iso(instant)
        ready_at = datetime.fromisoformat(pending_since) + BRIEF_REGENERATION_DEBOUNCE
        if instant < ready_at:
            _write_state(
                ledger, day, instant=instant, phase="UPDATING", counts=counts,
                latest_revision=int(latest["revision_number"]),
                last_generated_at=str(latest["generated_at"]),
                candidate_hash=str(state["last_generated_candidate_hash"] or "") if state else None,
                pending_source_hash=population_hash, pending_candidate_hash=candidate_hash,
                pending_since=pending_since, next_retry_at=_iso(ready_at),
            )
            return {"status": "DEFERRED", "phase": "UPDATING", "brief_date": day,
                    "reason": "SOURCE_SETTLING", "next_retry_at": _iso(ready_at), **counts}

    if state and state["next_retry_at"]:
        retry_at = datetime.fromisoformat(str(state["next_retry_at"]))
        if instant < retry_at and state["last_failure_code"] not in (None, "SOURCE_SETTLING"):
            return {"status": "DEFERRED", "phase": "DEFERRED", "brief_date": day,
                    "reason": str(state["last_failure_code"]),
                    "next_retry_at": str(state["next_retry_at"]), **counts}

    if (day != current_day and state
            and int(state["generation_failure_count"] or 0) >= BRIEF_FAILURE_ATTEMPT_LIMIT
            and state["pending_candidate_hash"] == candidate_hash):
        return _finalize_generation_fallback(
            ledger, day, instant=instant, counts=counts, latest=latest,
            candidate_hash=str(candidate_hash), candidates=candidates,
        )

    if not api_key or request_accountant is None:
        return _defer(
            ledger, day, instant=instant, counts=counts, latest=latest,
            candidate_hash=candidate_hash, reason="NO_COMPATIBLE_ROUTINE_ACCOUNT",
            retry_at=instant + BRIEF_CAPACITY_RETRY,
        )

    try:
        brief, model = generate_metered_json(
            api_key,
            model=DEFAULT_GEMMA_MODEL,
            payload=_brief_payload(day, packet),
            decode=lambda envelope: _decode_brief(envelope, packet),
            request_accountant=request_accountant,
            purpose="daily-news-brief",
        )
    except ModelGatewayCapacityExhausted:
        return _defer(
            ledger, day, instant=instant, counts=counts, latest=latest,
            candidate_hash=candidate_hash, reason="NO_GEMMA_CAPACITY",
            retry_at=instant + BRIEF_CAPACITY_RETRY,
        )
    except sqlite3.Error:
        raise
    except Exception as error:
        failure = _record_generation_failure(
            ledger, day, instant=instant, error=error, counts=counts,
            latest=latest, candidate_hash=str(candidate_hash),
        )
        if (day != current_day
                and int(failure["failure_count"]) >= BRIEF_FAILURE_ATTEMPT_LIMIT):
            return _finalize_generation_fallback(
                ledger, day, instant=instant, counts=counts, latest=latest,
                candidate_hash=str(candidate_hash), candidates=candidates,
            )
        return failure

    revision = int(latest["revision_number"]) + 1 if latest else 1
    with ledger.connection:
        ledger.connection.execute(
            """INSERT INTO daily_news_briefs
               (brief_date,revision_number,source_hash,cutoff_at,generated_at,
                model_version,prompt_version,brief_json) VALUES (?,?,?,?,?,?,?,?)""",
            (day, revision, str(candidate_hash), _iso(instant), _iso(instant), model,
             BRIEF_PROMPT_VERSION,
             json.dumps(brief, ensure_ascii=False, sort_keys=True, separators=(",", ":"))),
        )
    generated_at = _iso(instant)
    if day != current_day and counts["pending_items"] == 0:
        phase = _finalize(
            ledger, day, instant=instant, counts=counts, latest_revision=revision,
            last_generated_at=generated_at, candidate_hash=str(candidate_hash),
        )
    else:
        phase = "UPDATING"
        _write_state(
            ledger, day, instant=instant, phase=phase, counts=counts,
            latest_revision=revision, last_generated_at=generated_at,
            candidate_hash=candidate_hash, pending_source_hash=population_hash,
        )
    return {"status": "OK", "phase": phase, "brief_date": day,
            "revision_number": revision, "eligible_items": len(reviewed),
            "candidate_items": len(candidates), **counts}


def recent_daily_briefs(connection: sqlite3.Connection, *, limit: int = 14) -> list[dict]:
    rows = connection.execute(
        """SELECT b.*,COALESCE(s.phase,c.final_status,f.final_status,'UPDATING') AS phase,
                  COALESCE(s.received_items,c.received_items,f.received_items,0) AS received_items,
                  COALESCE(s.reviewed_items,c.reviewed_items,f.reviewed_items,0) AS reviewed_items,
                  COALESCE(s.pending_items,0) AS pending_items,
                  COALESCE(s.terminal_failure_items,c.terminal_failure_items,
                           f.terminal_failure_items,0)
                    AS terminal_failure_items,
                  s.next_retry_at,COALESCE(s.finalized_at,c.finalized_at,f.finalized_at)
                    AS finalized_at
           FROM daily_news_briefs b
           JOIN (SELECT brief_date,MAX(revision_number) revision_number
                 FROM daily_news_briefs GROUP BY brief_date) latest
             ON latest.brief_date=b.brief_date AND latest.revision_number=b.revision_number
           LEFT JOIN daily_news_brief_refresh_state s ON s.brief_date=b.brief_date
           LEFT JOIN daily_news_brief_finalizations_v1 f ON f.brief_date=b.brief_date
           LEFT JOIN daily_news_brief_finalization_corrections_v1 c
             ON c.brief_date=b.brief_date AND c.recovery_version=?
           ORDER BY b.brief_date DESC LIMIT ?""", (BRIEF_RECOVERY_VERSION, limit),
    ).fetchall()
    return [{**dict(row), "is_final": row["phase"] in FINAL_PHASES,
             "brief": json.loads(str(row["brief_json"]))} for row in rows]


def daily_brief_summary(
    connection: sqlite3.Connection, *, now: datetime | None = None,
) -> dict[str, object]:
    instant = now or datetime.now(UTC)
    day = instant.astimezone(KUALA_LUMPUR).date().isoformat()
    row = connection.execute(
        "SELECT * FROM daily_news_brief_refresh_state WHERE brief_date=?", (day,),
    ).fetchone()
    total = int(connection.execute(
        "SELECT COUNT(DISTINCT brief_date) FROM daily_news_briefs"
    ).fetchone()[0])
    if not row:
        latest = _latest_brief(connection, day)
        return {"brief_date": day, "phase": "UPDATING" if latest else "WAITING",
                "received_items": 0,
                "reviewed_items": 0, "pending_items": 0,
                "terminal_failure_items": 0,
                "last_generated_at": str(latest["generated_at"]) if latest else None,
                "latest_revision": int(latest["revision_number"]) if latest else None,
                "next_retry_at": None,
                "is_final": False, "total_brief_days": total}
    result = dict(row)
    latest_failure = (
        connection.execute(
            """SELECT failure_evidence_json FROM daily_news_brief_failures_v1
               WHERE brief_date=? AND failed_at=? AND failure_code=?
               ORDER BY attempt_number DESC LIMIT 1""",
            (day, result.get("last_failure_at"), result.get("last_failure_code")),
        ).fetchone()
        if result.get("last_failure_at") and result.get("last_failure_code")
        else None
    )
    result["last_failure_evidence"] = (
        json.loads(str(latest_failure["failure_evidence_json"]))
        if latest_failure and latest_failure["failure_evidence_json"] else None
    )
    result["is_final"] = result["phase"] in FINAL_PHASES
    result["total_brief_days"] = total
    return result
