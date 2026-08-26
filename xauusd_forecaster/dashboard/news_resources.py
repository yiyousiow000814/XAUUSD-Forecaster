"""Dashboard-owned news archive and evidence read resources."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import threading
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from xauusd_forecaster.annotation import INVALID_CHINESE_TITLE, PROMPT_VERSION, pending_annotation_records
from xauusd_forecaster.dashboard.resource_contracts import NEWS_READER_WINDOW_DAYS
from xauusd_forecaster.dashboard.status_cache import StatusSnapshotUnavailable
from xauusd_forecaster.news_contracts import CURRENT_NEWS_CONTRACT
from xauusd_forecaster.news_evidence import event_evidence_rows_from_connection, resolve_event_clock
from xauusd_forecaster.news_features_v2 import COLLECTION_SOURCES
from xauusd_forecaster.news_identity import preferred_cluster_peer_predicate
from xauusd_forecaster.news_impact import (
    HANDOVER_IMPACT_PROMPT_VERSION,
    IMPACT_MODEL,
    IMPACT_PROMPT_VERSION,
    impact_is_actionable,
    impact_time_rule,
)
from xauusd_forecaster.news_semantics import model_usable_annotation_predicate
from xauusd_forecaster.news_time import assess_news_semantic_eligibility
from xauusd_forecaster.news_projection import (
    NEWS_PROJECTION_MAX_ITEMS,
    NewsProjectionGeneration,
    build_news_projection_generation,
)


UTC = timezone.utc

NEWS_ARCHIVE_PAGE_LIMIT = 20

NEWS_EVIDENCE_PAGE_LIMIT = 50

NEWS_EVIDENCE_PAGE_LIMIT_BYTES = 350_000

_NEWS_EVIDENCE_CACHE_LOCK = threading.Lock()

_NEWS_EVIDENCE_CACHE: dict[str, object] = {}

_NEWS_EVIDENCE_VOLATILE_FIELDS = frozenset({"economic_age_minutes"})

_NEWS_EVIDENCE_MANIFEST_VERSION = "local-news-evidence-generation-v2"

_NEWS_PROJECTION_CACHE_LOCK = threading.Lock()

_NEWS_PROJECTION_CACHE: dict[str, object] = {}

NEWS_PROJECTION_SOURCE_REFRESH_SECONDS = 300.0

NEWS_PROJECTION_SOURCE_RETRY_SECONDS = 30.0

NEWS_CATEGORY_LABELS = {
    "rates_fed": "利率/Fed",
    "inflation_employment": "通胀/就业",
    "growth_economy": "增长/经济",
    "usd_liquidity": "美元/流动性",
    "oil_energy": "油价/能源",
    "war_geopolitics": "战争/地缘",
    "central_bank_gold": "央行购金",
    "risk_sentiment": "风险情绪 / 避险",
    "regulation_other": "监管/其他",
}

OTHER_NEWS_CATEGORY_LABEL = "其他"

def _news_category_label(primary_category: object) -> str:
    """Map one completed semantic category without inferring from workflow state."""
    category = str(primary_category or "").strip()
    return NEWS_CATEGORY_LABELS.get(category, OTHER_NEWS_CATEGORY_LABEL)

def _not_required_reason(item: dict, forward_epoch: str) -> tuple[str, str]:
    """Explain the single reason a readable row will not consume AI quota."""
    published_raw = item.get("source_published_time")
    if not published_raw:
        return "HISTORICAL_MATERIAL", "历史资料：缺少可靠发布时间"
    published = datetime.fromisoformat(str(published_raw))
    epoch = datetime.fromisoformat(forward_epoch)
    if published < epoch:
        return "HISTORICAL_MATERIAL", "历史资料：发布时间早于系统开始记录"
    assessment = assess_news_semantic_eligibility(
        item, forward_epoch=epoch,
    )
    if assessment.reason_code == "PUBLISHED_AFTER_DECISION":
        return "INVALID_PUBLISHED_TIME", "发布时间晚于收到时间，时间证据无效"
    if assessment.reason_code == "PUBLISHED_TIME_MISSING":
        return "HISTORICAL_MATERIAL", "历史资料：缺少可靠发布时间"
    if assessment.reason_code in {
        "PRE_FORWARD_PUBLICATION", "PRE_FORWARD_RECEIPT",
    }:
        return "HISTORICAL_MATERIAL", "历史资料：时间早于系统开始记录"
    if item.get("has_canonical_content_peer"):
        return (
            "CANONICAL_COPY_HANDLES_ANNOTATION",
            "同一篇新闻已由另一采集入口的规范副本负责处理，不会重复消耗模型配额",
        )
    if assessment.eligible:
        return "QUEUE_INVARIANT_MISMATCH", "正文符合条件但未进入语义队列，需要检查"
    return "INTAKE_REJECTED", "未通过客观采集条件，不进入语义处理"

def _annotation_failure_reason(error: object, failure_code: object) -> str:
    """Explain a bounded model failure without exposing rejected output."""
    message = str(error or "")
    code = str(failure_code or "")
    if "supporting evidence is absent from source" in message:
        return "Gemini 返回的证据片段无法在来源正文中逐字找到。"
    if "supporting_evidence contains a long item" in message:
        return "Gemini 返回的证据片段超过允许长度。"
    if "display repair failed" in message:
        return "Gemini 的语义响应已收到，但中文展示字段修复仍未通过。"
    if code == "MODEL_OUTPUT_CONTRACT_FAILED":
        return "Gemini 响应未通过当前输出合同。"
    if code == "MODEL_OUTPUT_INVALID":
        return "Gemini 返回的内容无法解析为当前 JSON 合同。"
    if code == "PROVIDER_HTTP_ERROR":
        return "Gemini 服务返回 HTTP 错误。"
    return "Gemini 请求未成功完成；已保留有限诊断证据。"

def _apply_impact_status(item: dict, now: datetime) -> None:
    """Expose the current Gemma lifetime decision in plain, auditable states."""
    if not item.get("parsed_at"):
        item["impact_status"] = (
            "NOT_REQUIRED"
            if item.get("annotation_status") == "NOT_REQUIRED"
            else "PENDING_ANNOTATION"
        )
        return
    if not item.get("impact_assessed_at"):
        item["impact_status"] = "PENDING_IMPACT"
        return

    update_type = str(item.get("impact_update_type") or "")
    impact_class = str(item.get("impact_class") or "BACKGROUND")
    if not impact_is_actionable({
        "impact_class": impact_class,
        "event_state": item.get("impact_event_state"),
        "update_type": update_type,
    }):
        item["impact_status"] = {
            "DUPLICATE_REPORT": "DUPLICATE_REPORT",
            "COMMENTARY": "COMMENTARY_ONLY",
            "HISTORICAL_CONTEXT": "HISTORICAL_CONTEXT",
        }.get(update_type, "BACKGROUND")
        item["model_visibility"] = "MODEL_INELIGIBLE"
        return

    event_at, clock_source, _ = resolve_event_clock(item, primary_source=True)
    if event_at is None:
        item["impact_status"] = "MISSING_PUBLICATION_TIME"
        item["model_visibility"] = "MODEL_INELIGIBLE"
        return
    max_age, _ = impact_time_rule(impact_class)
    expires_at = event_at + max_age
    item["impact_event_at"] = event_at.isoformat(timespec="microseconds")
    item["impact_clock_source"] = clock_source
    item["impact_expires_at"] = expires_at.isoformat(timespec="microseconds")
    first_seen = datetime.fromisoformat(str(item["collector_first_seen_time"]))
    assessed_at = datetime.fromisoformat(str(item["impact_assessed_at"]))
    available_at = max(first_seen, assessed_at)
    item["impact_available_at"] = available_at.isoformat(timespec="microseconds")
    if first_seen >= expires_at:
        item["impact_status"] = "EXPIRED_ON_RECEIPT"
        item["model_visibility"] = "IMPACT_EXPIRED"
    elif available_at >= expires_at:
        item["impact_status"] = "EXPIRED_BEFORE_AVAILABLE"
        item["model_visibility"] = "IMPACT_EXPIRED"
    elif now >= expires_at:
        item["impact_status"] = "EXPIRED"
        item["model_visibility"] = "IMPACT_EXPIRED"
    else:
        item["impact_status"] = "ACTIVE"
        item["model_visibility"] = "MODEL_VISIBLE"

def _news_reader_rows(
    connection: sqlite3.Connection,
    now: datetime,
    *,
    after: str | None = None,
    limit: int = 200,
    candidate_keys: list[tuple[str, str, int, str]] | None = None,
) -> list[sqlite3.Row]:
    """Read one bounded page from the canonical 60-day reader archive."""
    cutoff = (now - timedelta(days=NEWS_READER_WINDOW_DAYS)).isoformat(
        timespec="microseconds"
    )
    if candidate_keys is None:
        candidate_keys = _news_mirror_candidate_keys(
            connection, cutoff=cutoff, after=after, limit=max(limit * 8, 160),
        )
    if not candidate_keys:
        return []
    candidate_clause = ",".join("(?,?,?,?)" for _ in candidate_keys)
    candidate_parameters = tuple(
        value for key in candidate_keys for value in key
    )
    return connection.execute(
        f"""WITH candidate_changes(
              source,source_item_id,revision_number,mirror_updated_at
            ) AS (VALUES {candidate_clause})
            SELECT n.source, n.source_item_id, n.revision_number,
                   n.cluster_id, n.source_published_time,
                   n.collector_first_seen_time, n.fetched_time,
                   candidate_changes.mirror_updated_at,
                   n.headline AS original_headline,
                   COALESCE(t.headline_zh, n.headline) AS headline,
                   length(COALESCE(n.body, '')) AS content_characters,
                   CASE WHEN n.body LIKE '[FULL_TEXT%' THEN 'FULL_TEXT'
                        WHEN length(trim(COALESCE(n.body, ''))) >= 240 THEN 'SOURCE_CONTENT'
                        ELSE 'HEADLINE_ONLY' END AS content_status,
                   CASE WHEN length(trim(COALESCE(n.body, ''))) >= 240 THEN 'AVAILABLE'
                        WHEN cf.is_terminal=1 THEN 'UNAVAILABLE'
                        WHEN cf.next_retry_at IS NOT NULL THEN 'RETRYING'
                        ELSE 'PENDING' END AS content_fetch_status,
                   cf.error_type AS content_error_type,
                   n.link, n.content_hash, n.body,
                   EXISTS (
                     SELECT 1 FROM news_annotation_display_checkpoints_v1 checkpoint
                     WHERE checkpoint.source=n.source
                       AND checkpoint.source_item_id=n.source_item_id
                       AND checkpoint.revision_number=n.revision_number
                       AND checkpoint.raw_content_hash=n.content_hash
                       AND checkpoint.prompt_version=?
                   ) AS has_display_checkpoint,
                   EXISTS (
                     SELECT 1 FROM news_revisions same_content
                     WHERE (same_content.content_hash=n.content_hash
                         OR (same_content.source<>n.source
                           AND same_content.source_item_id=n.source_item_id))
                       AND (same_content.source<>n.source
                         OR same_content.source_item_id<>n.source_item_id)
                       AND length(trim(COALESCE(same_content.body,'')))>=240
                       AND NOT EXISTS (
                         SELECT 1 FROM news_revisions same_content_newer
                         WHERE same_content_newer.source=same_content.source
                           AND same_content_newer.source_item_id=same_content.source_item_id
                           AND same_content_newer.revision_number>
                               same_content.revision_number)
                   ) AS has_canonical_content_peer,
                   json_extract(a.annotation_json, '$.summary_zh') AS summary_zh,
                   json_extract(a.annotation_json, '$.primary_category') AS primary_category,
                   json_extract(a.annotation_json, '$.secondary_categories') AS secondary_categories_json,
                   json_extract(a.annotation_json, '$.emerging_topic_zh') AS emerging_topic_zh,
                   json_extract(a.annotation_json, '$.xauusd_relevance') AS xauusd_relevance,
                   json_extract(a.annotation_json, '$.semantic_reason_zh') AS semantic_reason_zh,
                   json_extract(a.annotation_json, '$.event_time') AS event_time,
                   a.event_type, a.entities_json, a.hawkishness,
                   a.inflation_impulse, a.growth_impulse,
                   a.geopolitical_risk, a.usd_impulse, a.novelty,
                   a.confidence, a.llm_model_version, a.prompt_version,
                   a.parsed_at, i.impact_class,
                   CASE WHEN f.failure_id IS NOT NULL THEN COALESCE(
                     fe.failure_code,
                     CASE WHEN f.error_type='ValueError'
                       THEN 'MODEL_OUTPUT_CONTRACT_FAILED'
                       ELSE 'MODEL_REQUEST_FAILED' END)
                   END AS annotation_failure_code,
                   f.error AS annotation_failure,
                   i.event_state AS impact_event_state,
                   i.update_type AS impact_update_type,
                   i.confidence AS impact_confidence,
                   i.reason_zh AS impact_reason_zh,
                   i.assessed_at AS impact_assessed_at,
                   CASE WHEN n.source_published_time IS NOT NULL THEN
                     (julianday(n.collector_first_seen_time)-julianday(n.source_published_time))*86400
                   END AS collection_delay_seconds,
                   CASE WHEN a.parsed_at IS NOT NULL THEN
                     (julianday(a.parsed_at)-julianday(n.collector_first_seen_time))*86400
                   END AS processing_delay_seconds,
                   COALESCE(r.maximum_tier, 'COLLECT_ONLY') AS source_eligibility,
                   CASE WHEN r.maximum_tier='MODEL_ELIGIBLE'
                              AND length(trim(COALESCE(n.body,'')))>=r.minimum_body_characters
                              AND a.parsed_at IS NOT NULL THEN 'MODEL_VISIBLE'
                        WHEN r.maximum_tier='MODEL_ELIGIBLE'
                             AND length(trim(COALESCE(n.body,'')))>=r.minimum_body_characters
                             AND a.parsed_at IS NULL THEN 'NOT_YET_PARSED'
                        WHEN r.maximum_tier='MODEL_ELIGIBLE' AND cf.is_terminal=1
                             THEN 'CONTENT_UNAVAILABLE'
                        WHEN r.maximum_tier='MODEL_ELIGIBLE' THEN 'WAITING_CONTENT'
                        ELSE COALESCE(r.maximum_tier, 'COLLECT_ONLY') END AS model_visibility,
                   CASE WHEN a.annotation_id IS NOT NULL THEN 'READY'
                        WHEN cf.is_terminal=1 THEN 'CONTENT_UNAVAILABLE'
                        WHEN length(trim(COALESCE(n.body, ''))) < 240 THEN 'WAITING_CONTENT'
                        WHEN f.is_terminal=1 THEN 'DEAD_LETTER'
                        WHEN f.next_retry_at > ? THEN 'BACKING_OFF'
                        WHEN length(trim(COALESCE(n.body, ''))) >= 240 THEN 'QUEUED'
                        ELSE 'WAITING_CONTENT' END AS annotation_status
            FROM candidate_changes
            JOIN news_revisions n
              ON n.source=candidate_changes.source
             AND n.source_item_id=candidate_changes.source_item_id
             AND n.revision_number=candidate_changes.revision_number
            LEFT JOIN news_title_translations t ON t.translation_id=(
              SELECT latest_t.translation_id FROM news_title_translations latest_t
              WHERE latest_t.source=n.source
                AND latest_t.source_item_id=n.source_item_id
                AND latest_t.revision_number=n.revision_number
              ORDER BY (latest_t.headline_zh=?) ASC,
                       latest_t.parsed_at DESC, latest_t.translation_id DESC LIMIT 1)
            LEFT JOIN news_annotations a ON a.annotation_id=(
              SELECT preferred_a.annotation_id FROM news_annotations preferred_a
              WHERE preferred_a.source=n.source
                AND preferred_a.source_item_id=n.source_item_id
                AND preferred_a.revision_number=n.revision_number
                AND preferred_a.llm_model_version IN (
                  'gemini-3.5-flash-lite', 'gemini-3.1-flash-lite')
                AND preferred_a.prompt_version=?
                AND {model_usable_annotation_predicate('preferred_a')}
              ORDER BY CASE preferred_a.llm_model_version
                WHEN 'gemini-3.5-flash-lite' THEN 0 ELSE 1 END,
                preferred_a.parsed_at DESC LIMIT 1)
            LEFT JOIN news_impact_assessments_v1 i
              ON i.assessment_id=(
                SELECT selected_i.assessment_id
                FROM news_impact_assessments_v1 selected_i
                WHERE selected_i.annotation_id=a.annotation_id
                  AND selected_i.llm_model_version=?
                  AND selected_i.prompt_version IN (?,?)
                ORDER BY CASE selected_i.prompt_version WHEN ? THEN 0 ELSE 1 END,
                         selected_i.assessed_at DESC LIMIT 1)
            LEFT JOIN news_llm_failures f ON f.failure_id=(
              SELECT latest_f.failure_id FROM news_llm_failures latest_f
              WHERE latest_f.task_type='ANNOTATION'
                AND latest_f.source=n.source
                AND latest_f.source_item_id=n.source_item_id
                AND latest_f.revision_number=n.revision_number
                AND latest_f.llm_model_version IN (
                  'gemini-3.5-flash-lite', 'gemini-3.1-flash-lite')
                AND latest_f.prompt_version=?
                AND NOT (latest_f.error_type='RuntimeError'
                  AND latest_f.error='All configured Gemini keys unavailable for this batch')
              ORDER BY latest_f.failed_at DESC LIMIT 1)
            LEFT JOIN news_content_failures cf ON cf.failure_id=(
              SELECT latest_cf.failure_id FROM news_content_failures latest_cf
              WHERE latest_cf.source=n.source
                AND latest_cf.source_item_id=n.source_item_id
                AND latest_cf.revision_number=n.revision_number
              ORDER BY latest_cf.attempt_number DESC LIMIT 1)
            LEFT JOIN news_llm_failure_evidence_v1 fe
              ON fe.failure_id=f.failure_id
            LEFT JOIN source_eligibility_rules r
              ON r.eligibility_version='news-source-eligibility-v4-live-delay-materiality'
             AND r.source=n.source
            WHERE NOT EXISTS (
              SELECT 1 FROM news_revisions newer
              WHERE newer.source=n.source
                AND newer.source_item_id=n.source_item_id
                AND newer.revision_number>n.revision_number)
              AND NOT EXISTS (
                SELECT 1 FROM news_revisions peer
                WHERE peer.cluster_id=n.cluster_id
                  AND NOT EXISTS (
                    SELECT 1 FROM news_revisions peer_newer
                    WHERE peer_newer.source=peer.source
                      AND peer_newer.source_item_id=peer.source_item_id
                      AND peer_newer.revision_number>peer.revision_number)
                  AND {preferred_cluster_peer_predicate('peer', 'n')})
              AND length(trim(COALESCE(n.body, ''))) >= 240
              AND COALESCE(n.source_published_time,
                           n.collector_first_seen_time) >= ?
            ORDER BY candidate_changes.mirror_updated_at ASC,
                     n.source,n.source_item_id,n.revision_number
            LIMIT ?""",
        (
            *candidate_parameters,
            PROMPT_VERSION, now.isoformat(timespec="microseconds"),
            INVALID_CHINESE_TITLE, PROMPT_VERSION,
            IMPACT_MODEL, IMPACT_PROMPT_VERSION,
            HANDOVER_IMPACT_PROMPT_VERSION, IMPACT_PROMPT_VERSION,
            PROMPT_VERSION, cutoff, limit,
        ),
    ).fetchall()

def _news_mirror_candidate_keys(
    connection: sqlite3.Connection,
    *,
    cutoff: str,
    after: str | None,
    limit: int,
) -> list[tuple[str, str, int, str]]:
    """Find changed reader keys before running the expensive detail joins."""
    cursor_clause = ""
    cursor_parameters: tuple[object, ...] = ()
    if after:
        cursor = json.loads(after)
        if not isinstance(cursor, list) or len(cursor) != 4:
            raise ValueError("invalid news archive cursor")
        cursor_clause = (
            "HAVING (max(changed_at),changes.source,changes.source_item_id,"
            "changes.revision_number) "
            "> (?,?,?,?)"
        )
        cursor_parameters = tuple(cursor)
    rows = connection.execute(
        f"""WITH changes AS (
              SELECT source,source_item_id,revision_number,
                     fetched_time AS changed_at
              FROM news_revisions WHERE fetched_time>=?
              UNION ALL
              SELECT source,source_item_id,revision_number,parsed_at
              FROM news_title_translations WHERE parsed_at>=?
              UNION ALL
              SELECT source,source_item_id,revision_number,parsed_at
              FROM news_annotations WHERE parsed_at>=?
              UNION ALL
              SELECT source,source_item_id,revision_number,assessed_at
              FROM news_impact_assessments_v1 WHERE assessed_at>=?
              UNION ALL
              SELECT source,source_item_id,revision_number,failed_at
              FROM news_llm_failures WHERE failed_at>=?
              UNION ALL
              SELECT source,source_item_id,revision_number,failed_at
              FROM news_content_failures WHERE failed_at>=?
              UNION ALL
              SELECT f.source,f.source_item_id,f.revision_number,r.authorized_at
              FROM news_ai_failure_recoveries_v1 r
              JOIN news_llm_failures f ON f.failure_id=r.failure_id
              WHERE r.authorized_at>=?
            )
            SELECT changes.source,changes.source_item_id,
                   changes.revision_number,max(changed_at)
            FROM changes
            JOIN news_revisions n
              ON n.source=changes.source
             AND n.source_item_id=changes.source_item_id
             AND n.revision_number=changes.revision_number
            WHERE NOT EXISTS (
              SELECT 1 FROM news_revisions newer
              WHERE newer.source=n.source
                AND newer.source_item_id=n.source_item_id
                AND newer.revision_number>n.revision_number)
              AND NOT EXISTS (
                SELECT 1 FROM news_revisions peer
                WHERE peer.cluster_id=n.cluster_id
                  AND NOT EXISTS (
                    SELECT 1 FROM news_revisions peer_newer
                    WHERE peer_newer.source=peer.source
                      AND peer_newer.source_item_id=peer.source_item_id
                      AND peer_newer.revision_number>peer.revision_number)
                  AND {preferred_cluster_peer_predicate('peer', 'n')})
              AND length(trim(COALESCE(n.body,'')))>=240
              AND COALESCE(n.source_published_time,
                           n.collector_first_seen_time)>=?
            GROUP BY changes.source,changes.source_item_id,
                     changes.revision_number
            {cursor_clause}
            ORDER BY max(changed_at),changes.source,
                     changes.source_item_id,changes.revision_number
            LIMIT ?""",
        (
            cutoff, cutoff, cutoff, cutoff, cutoff, cutoff, cutoff,
            cutoff, *cursor_parameters, limit,
        ),
    ).fetchall()
    return [
        (str(row[0]), str(row[1]), int(row[2]), str(row[3]))
        for row in rows
    ]

def _serialize_news_rows(
    rows: list[sqlite3.Row], now: datetime, epoch: str,
    claimable_annotation_keys: set[tuple[str, str, int]],
) -> list[dict]:
    """Apply the shared reader-state contract without intake freshness gates."""
    news: list[dict] = []
    for row in rows:
        item = dict(row)
        listed_source = str(item.get("source") or "") in COLLECTION_SOURCES
        item["source_eligibility"] = (
            "SEMANTIC_CANDIDATE" if listed_source else "UNLISTED_CANDIDATE"
        )
        annotation_key = (
            str(item.get("source") or ""),
            str(item.get("source_item_id") or ""),
            int(item.get("revision_number") or 0),
        )
        annotation_failure_code = item.pop("annotation_failure_code", None)
        annotation_failure = item.pop("annotation_failure", None)
        has_display_checkpoint = bool(item.pop("has_display_checkpoint", False))
        has_canonical_content_peer = bool(
            item.pop("has_canonical_content_peer", False)
        )
        if item.get("parsed_at"):
            item["annotation_status"] = "READY"
        elif has_display_checkpoint:
            item["annotation_status"] = "REPAIRING_DISPLAY"
            item["annotation_reason_code"] = "DISPLAY_REPAIR_IN_PROGRESS"
            item["annotation_reason"] = (
                "语义复核已经完成，系统正在根据校验反馈修复中文显示"
            )
        elif annotation_key in claimable_annotation_keys:
            item["annotation_status"] = "QUEUED"
        elif item.get("annotation_status") == "QUEUED":
            item["annotation_status"] = "NOT_REQUIRED"
            reason_code, reason = _not_required_reason(
                {
                    **item,
                    "has_canonical_content_peer": has_canonical_content_peer,
                },
                epoch,
            )
            item["annotation_reason_code"] = reason_code
            item["annotation_reason"] = reason
        elif item.get("annotation_status") in {"BACKING_OFF", "DEAD_LETTER"}:
            item["annotation_reason_code"] = (
                annotation_failure_code or "MODEL_REQUEST_FAILED"
            )
            item["annotation_reason"] = _annotation_failure_reason(
                annotation_failure, annotation_failure_code
            )
        item["model_visibility"] = (
            "IMPACT_PENDING" if item.get("parsed_at")
            else "NOT_YET_PARSED" if item.get("annotation_status") == "QUEUED"
            else "MODEL_INELIGIBLE"
            if item.get("annotation_status") == "NOT_REQUIRED"
            else str(item.get("annotation_status") or "WAITING_CONTENT")
        )
        _apply_impact_status(item, now)
        item["entities"] = (
            json.loads(item.pop("entities_json"))
            if item.get("entities_json") else []
        )
        secondary = item.pop("secondary_categories_json", None)
        item["secondary_categories"] = json.loads(secondary) if secondary else []
        item["category"] = _news_category_label(item.get("primary_category"))
        item["eligibility_version"] = CURRENT_NEWS_CONTRACT.eligibility_version
        news.append(item)
    return news

def _news_archive_context(
    connection: sqlite3.Connection, now: datetime,
) -> tuple[str, set[tuple[str, str, int]]]:
    """Freeze reader metadata that is invariant across one paged generation."""
    epoch_row = connection.execute(
        "SELECT value FROM runtime_metadata WHERE key='FORWARD_EPOCH'"
    ).fetchone()
    epoch = str(epoch_row[0])
    claimable_keys = {
        (str(row["source"]), str(row["source_item_id"]), int(row["revision_number"]))
        for row in pending_annotation_records(
            connection, observed_at=now,
            received_from=now - timedelta(days=NEWS_READER_WINDOW_DAYS),
            limit=NEWS_PROJECTION_MAX_ITEMS,
        )
    }
    return epoch, claimable_keys


def _news_archive_page(
    connection: sqlite3.Connection, after: str | None, limit: int,
    *, now: datetime | None = None,
    context: tuple[str, set[tuple[str, str, int]]] | None = None,
) -> dict:
    now = now or datetime.now(UTC)
    epoch, claimable_keys = context or _news_archive_context(connection, now)
    rows = _news_reader_rows(connection, now, after=after, limit=limit + 1)
    has_more = len(rows) > limit
    serialized = _serialize_news_rows(rows[:limit], now, epoch, claimable_keys)
    withdrawals = [
        {
            "source": item["source"],
            "source_item_id": item["source_item_id"],
            "revision_number": item["revision_number"],
        }
        for item in serialized
        if item.get("xauusd_relevance") == "IRRELEVANT"
    ]
    news = [
        item for item in serialized
        if item.get("xauusd_relevance") != "IRRELEVANT"
    ]
    next_cursor = (
        json.dumps([
            serialized[-1]["mirror_updated_at"], serialized[-1]["source"],
            serialized[-1]["source_item_id"], serialized[-1]["revision_number"],
        ], ensure_ascii=False, separators=(",", ":"))
        if serialized else after
    )
    return {
        "items": news,
        "withdrawals": withdrawals,
        "next_cursor": next_cursor,
        "has_more": has_more,
        "window_days": NEWS_READER_WINDOW_DAYS,
        "window_start": (now - timedelta(days=NEWS_READER_WINDOW_DAYS)).isoformat(),
    }


def _build_news_projection_source(
    connection: sqlite3.Connection,
) -> NewsProjectionGeneration:
    """Freeze one complete, bounded 60-day source universe in reader order."""
    now = datetime.now(UTC)
    context = _news_archive_context(connection, now)
    source_page_items = min(1_000, NEWS_PROJECTION_MAX_ITEMS)
    cutoff = (now - timedelta(days=NEWS_READER_WINDOW_DAYS)).isoformat(
        timespec="microseconds"
    )
    candidate_keys = _news_mirror_candidate_keys(
        connection, cutoff=cutoff, after=None,
        limit=NEWS_PROJECTION_MAX_ITEMS + 1,
    )
    if len(candidate_keys) > NEWS_PROJECTION_MAX_ITEMS:
        raise ValueError("news source universe exceeds the 10,000-row bound")
    items: list[dict] = []
    withdrawals: list[dict] = []
    epoch, claimable_keys = context
    for start in range(0, len(candidate_keys), source_page_items):
        page_keys = candidate_keys[start:start + source_page_items]
        rows = _news_reader_rows(
            connection, now, limit=len(page_keys), candidate_keys=page_keys,
        )
        serialized = _serialize_news_rows(rows, now, epoch, claimable_keys)
        withdrawals.extend({
            "source": item["source"],
            "source_item_id": item["source_item_id"],
            "revision_number": item["revision_number"],
        } for item in serialized if item.get("xauusd_relevance") == "IRRELEVANT")
        items.extend(
            item for item in serialized
            if item.get("xauusd_relevance") != "IRRELEVANT"
        )
    return build_news_projection_generation(
        items, withdrawals,
        window_start=(now - timedelta(days=NEWS_READER_WINDOW_DAYS)).isoformat(),
        watermark=now.isoformat(),
    )


def _news_projection_source(
    connection: sqlite3.Connection, activated_snapshot_id: str | None,
) -> NewsProjectionGeneration:
    """Keep staging immutable; refresh only after its exact snapshot activates."""
    with _NEWS_PROJECTION_CACHE_LOCK:
        cached = _NEWS_PROJECTION_CACHE.get("generation")
        if isinstance(cached, NewsProjectionGeneration):
            snapshot_id = str(cached.manifest["snapshot_id"])
            if activated_snapshot_id != snapshot_id:
                return cached
        candidate = _build_news_projection_source(connection)
        if (
            isinstance(cached, NewsProjectionGeneration)
            and candidate.manifest["source_digest"] == cached.manifest["source_digest"]
        ):
            return cached
        _NEWS_PROJECTION_CACHE["generation"] = candidate
        _NEWS_PROJECTION_CACHE["built_at"] = time.monotonic()
        return candidate


class NewsProjectionSourcePending(RuntimeError):
    """The bounded local projection is building without blocking HTTP."""


def _build_news_projection_source_from_database(
    database: Path,
) -> NewsProjectionGeneration:
    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True, timeout=5)
    connection.row_factory = sqlite3.Row
    try:
        return _build_news_projection_source(connection)
    finally:
        connection.close()


def _finish_news_projection_source_build(database: Path) -> None:
    try:
        candidate = _build_news_projection_source_from_database(database)
    except Exception as error:
        with _NEWS_PROJECTION_CACHE_LOCK:
            _NEWS_PROJECTION_CACHE["error"] = type(error).__name__
            _NEWS_PROJECTION_CACHE["retry_at"] = (
                time.monotonic() + NEWS_PROJECTION_SOURCE_RETRY_SECONDS
            )
            _NEWS_PROJECTION_CACHE["building"] = False
        return
    with _NEWS_PROJECTION_CACHE_LOCK:
        cached = _NEWS_PROJECTION_CACHE.get("generation")
        if (
            not isinstance(cached, NewsProjectionGeneration)
            or candidate.manifest["source_digest"]
            != cached.manifest["source_digest"]
        ):
            _NEWS_PROJECTION_CACHE["generation"] = candidate
        _NEWS_PROJECTION_CACHE["built_at"] = time.monotonic()
        _NEWS_PROJECTION_CACHE.pop("error", None)
        _NEWS_PROJECTION_CACHE.pop("retry_at", None)
        _NEWS_PROJECTION_CACHE["building"] = False


def _news_projection_source_for_request(
    database: Path, activated_snapshot_id: str | None,
) -> NewsProjectionGeneration:
    """Return a frozen source or start one bounded background materialization."""
    fallback: NewsProjectionGeneration | None = None
    with _NEWS_PROJECTION_CACHE_LOCK:
        cached = _NEWS_PROJECTION_CACHE.get("generation")
        built_at = float(_NEWS_PROJECTION_CACHE.get("built_at") or 0.0)
        if isinstance(cached, NewsProjectionGeneration):
            snapshot_id = str(cached.manifest["snapshot_id"])
            refresh_due = (
                activated_snapshot_id == snapshot_id
                and time.monotonic() - built_at
                >= NEWS_PROJECTION_SOURCE_REFRESH_SECONDS
            )
            if not refresh_due:
                return cached
            fallback = cached
        if _NEWS_PROJECTION_CACHE.get("building") is True:
            if fallback is not None:
                return fallback
            raise NewsProjectionSourcePending("news projection source is building")
        retry_at = float(_NEWS_PROJECTION_CACHE.get("retry_at") or 0.0)
        if retry_at > time.monotonic():
            if fallback is not None:
                return fallback
            raise NewsProjectionSourcePending("news projection source retry is pending")
        _NEWS_PROJECTION_CACHE["building"] = True
    worker = threading.Thread(
        target=_finish_news_projection_source_build,
        args=(database,), name="news-projection-source", daemon=True,
    )
    worker.start()
    if fallback is not None:
        return fallback
    raise NewsProjectionSourcePending("news projection source is building")


def _news_projection_batch(
    generation: NewsProjectionGeneration, kind: str, offset: int,
) -> dict:
    batches = (
        generation.detail_batches if kind == "detail"
        else generation.index_batches if kind == "index"
        else None
    )
    if batches is None or offset < 0:
        raise ValueError("invalid news projection batch request")
    next_offset = 0
    for batch in batches:
        if next_offset == offset:
            items = list(batch)
            return {
                "generation_id": generation.manifest["generation_id"],
                "snapshot_id": generation.manifest["snapshot_id"],
                "kind": kind,
                "offset": offset,
                "items": items,
                "next_offset": offset + len(items),
            }
        next_offset += len(batch)
    expected = generation.manifest[
        "expected_detail_count" if kind == "detail" else "expected_index_count"
    ]
    if offset == expected:
        return {
            "generation_id": generation.manifest["generation_id"],
            "snapshot_id": generation.manifest["snapshot_id"],
            "kind": kind, "offset": offset, "items": [], "next_offset": offset,
        }
    raise ValueError("news projection offset is not a frozen batch boundary")

def _frozen_event_article_identity(row: dict) -> tuple[str, ...]:
    """Identify one frozen article across event-identity contract versions."""
    return tuple(str(row.get(field) or "").strip() for field in (
        "canonical_source", "canonical_headline", "source_published_time",
        "collector_first_seen_time",
    ))

def _news_evidence_display_rows(
    connection: sqlite3.Connection, all_news_evidence: list[dict],
) -> list[dict]:
    """Return one audit row per independent event, not per frozen revision.

    Model visibility is immutable at the event-version level.  The dashboard,
    however, is an event audit.  Rendering every frozen version duplicated the
    same headline and also produced duplicate React keys when users switched
    between "used" and "never used".
    """
    current_by_event = {
        str(row["event_key"]): row for row in all_news_evidence
    }
    current_keys_by_article: dict[tuple[str, ...], set[str]] = defaultdict(set)
    for event_key, row in current_by_event.items():
        current_keys_by_article[_frozen_event_article_identity(row)].add(event_key)

    try:
        aux_receipts = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' "
            "AND name='news_only_visibility_receipts_v1'"
        ).fetchone()
        receipt_source = (
            "(SELECT * FROM news_model_visibility_receipts_v1 UNION ALL "
            "SELECT * FROM news_only_visibility_receipts_v1)"
            if aux_receipts is not None
            else "news_model_visibility_receipts_v1"
        )
        catalog_rows = connection.execute(
            """SELECT * FROM news_model_visibility_events_v1
               ORDER BY collector_first_seen_time DESC,event_key"""
        ).fetchall()
        event_key_aliases: dict[str, str] = {}
        catalog_keys_by_article: dict[tuple[str, ...], set[str]] = defaultdict(set)
        for raw in catalog_rows:
            row = dict(raw)
            catalog_keys_by_article[
                _frozen_event_article_identity(row)
            ].add(str(row["event_key"]))
        for article_identity, catalog_keys in catalog_keys_by_article.items():
            current_keys = current_keys_by_article.get(article_identity, set())
            if len(current_keys) == 1:
                canonical_key = next(iter(current_keys))
            elif not current_keys and len(catalog_keys) > 1:
                canonical_key = min(catalog_keys)
            else:
                continue
            for event_key in catalog_keys:
                if event_key != canonical_key:
                    event_key_aliases[event_key] = canonical_key

        visibility_rows = connection.execute(
            f"""SELECT canonical_event_key AS event_key,
                      count(*) AS frozen_model_uses,
                      count(DISTINCT source_decision_id) AS frozen_decisions,
                      count(DISTINCT event_source_hash) AS frozen_versions,
                      min(decision_time) AS first_model_decision_time,
                      max(decision_time) AS last_model_decision_time,
                      group_concat(DISTINCT model_identity) AS model_identities,
                      group_concat(DISTINCT model_version) AS model_versions
               FROM (
                 SELECT receipt.*,
                        COALESCE(alias.value, receipt.event_key)
                          AS canonical_event_key
                 FROM {receipt_source} AS receipt
                 LEFT JOIN json_each(?) AS alias
                   ON alias.key=receipt.event_key
               )
               GROUP BY canonical_event_key""",
            (json.dumps(event_key_aliases, sort_keys=True),),
        ).fetchall()
    except sqlite3.OperationalError:
        visibility_rows = []
        catalog_rows = []
        event_key_aliases = {}

    receipts = {row["event_key"]: dict(row) for row in visibility_rows}
    catalog_by_event: dict[str, dict] = {}
    for raw in catalog_rows:
        row = dict(raw)
        event_key = event_key_aliases.get(str(row["event_key"]), row["event_key"])
        catalog_by_event.setdefault(str(event_key), row)

    display_fields = (
        "event_key", "source_hash", "canonical_headline", "canonical_source",
        "source_published_time", "collector_first_seen_time",
        "economic_age_minutes", "freshness_status", "topics",
        "evidence_grade", "broad_model_eligible", "model_permission",
        "member_count", "independent_publishers", "source_names",
        "publisher_domains", "source_identity_organizations", "reason_codes",
    )
    rows: list[dict] = []
    displayed_events: set[str] = set()

    for event_key, receipt in receipts.items():
        current = current_by_event.get(event_key)
        catalog = catalog_by_event.get(event_key)
        if current is None and catalog is None:
            continue
        if current is not None:
            display = {name: current.get(name) for name in display_fields}
        else:
            display = {
                "event_key": event_key,
                "source_hash": catalog["event_source_hash"],
                "canonical_headline": catalog["canonical_headline"],
                "canonical_source": catalog["canonical_source"],
                "source_published_time": catalog["source_published_time"],
                "collector_first_seen_time": catalog["collector_first_seen_time"],
                "economic_age_minutes": None,
                "freshness_status": "FROZEN_AT_DECISION",
                "topics": json.loads(catalog["topics_json"] or "[]"),
                "evidence_grade": catalog["evidence_grade"],
                "broad_model_eligible": False,
                "model_permission": "MODEL_USED",
                "member_count": 1,
                "independent_publishers": 1,
                "source_names": [catalog["canonical_source"]],
                "publisher_domains": [],
                "source_identity_organizations": [],
                "reason_codes": ["FROZEN_MODEL_VISIBILITY_RECEIPT"],
            }
        display.update({
            "model_seen": True,
            "frozen_model_uses": int(receipt["frozen_model_uses"]),
            "frozen_decisions": int(receipt["frozen_decisions"]),
            "frozen_versions": int(receipt["frozen_versions"]),
            "first_model_decision_time": receipt["first_model_decision_time"],
            "last_model_decision_time": receipt["last_model_decision_time"],
            "model_identities": sorted(filter(
                None, str(receipt["model_identities"] or "").split(","),
            )),
            "model_versions": sorted(filter(
                None, str(receipt["model_versions"] or "").split(","),
            )),
            "model_unseen_reason_codes": [],
        })
        rows.append(display)
        displayed_events.add(event_key)

    for row in reversed(all_news_evidence):
        event_key = row["event_key"]
        if event_key in displayed_events:
            continue
        current_eligible = bool(row.get("broad_model_eligible"))
        if not current_eligible:
            if row.get("prompt_version") != PROMPT_VERSION:
                continue
            if row.get("evidence_grade") not in {
                "PRIMARY", "CORROBORATED", "SINGLE_RELIABLE",
            }:
                continue
        display = {name: row.get(name) for name in display_fields}
        display.update({
            "model_seen": False,
            "frozen_model_uses": 0,
            "frozen_decisions": 0,
            "frozen_versions": 0,
            "first_model_decision_time": None,
            "last_model_decision_time": None,
            "model_identities": [],
            "model_versions": [],
            "model_unseen_reason_codes": (
                ["ELIGIBLE_AWAITING_FROZEN_PREDICTION"]
                if current_eligible else list(row["reason_codes"])
            ),
        })
        rows.append(display)
        displayed_events.add(event_key)

    rows.sort(
        key=lambda row: (
            row.get("source_published_time")
            or row.get("collector_first_seen_time") or "",
            row.get("collector_first_seen_time") or "",
            row["event_key"],
        ),
        reverse=True,
    )
    return rows

def _durable_news_evidence_rows(rows: list[dict]) -> list[dict]:
    """Remove wall-clock presentation fields from the durable page protocol."""
    return [
        {
            key: value for key, value in row.items()
            if key not in _NEWS_EVIDENCE_VOLATILE_FIELDS
        }
        for row in rows
    ]

def _publish_news_evidence_snapshot(rows: list[dict]) -> str:
    rows = _durable_news_evidence_rows(rows)
    encoded = json.dumps(
        rows, ensure_ascii=False, allow_nan=False, separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    snapshot_id = hashlib.sha256(encoded).hexdigest()
    with _NEWS_EVIDENCE_CACHE_LOCK:
        _NEWS_EVIDENCE_CACHE.clear()
        _NEWS_EVIDENCE_CACHE.update({
            "snapshot_id": snapshot_id,
            "items": tuple(rows),
        })
    return snapshot_id

def _materialize_news_evidence_generation(
    rows: list[dict], manifest_path: Path, *, activated_snapshot_id: str | None = None,
) -> tuple[str, list[dict]]:
    """Freeze a generation until its remote activation is acknowledged."""
    rows = _durable_news_evidence_rows(rows)
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        manifest = None
    if isinstance(manifest, dict):
        frozen_rows = manifest.get("items")
        frozen_snapshot = str(manifest.get("snapshot_id") or "")
        if (
            manifest.get("manifest_version") == _NEWS_EVIDENCE_MANIFEST_VERSION
            and re.fullmatch(r"[a-f0-9]{64}", frozen_snapshot)
            and isinstance(frozen_rows, list)
        ):
            encoded = json.dumps(
                frozen_rows, ensure_ascii=False, allow_nan=False,
                separators=(",", ":"), sort_keys=True,
            ).encode("utf-8")
            if (
                hashlib.sha256(encoded).hexdigest() == frozen_snapshot
                and frozen_snapshot != activated_snapshot_id
            ):
                return frozen_snapshot, frozen_rows

    encoded = json.dumps(
        rows, ensure_ascii=False, allow_nan=False, separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    snapshot_id = hashlib.sha256(encoded).hexdigest()
    manifest = {
        "manifest_version": _NEWS_EVIDENCE_MANIFEST_VERSION,
        "snapshot_id": snapshot_id,
        "items": rows,
    }
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = manifest_path.with_suffix(manifest_path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(manifest, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    temporary.replace(manifest_path)
    return snapshot_id, rows

def _build_news_evidence_resource(
    database: Path, *, clock=None, manifest_path: Path | None = None,
    activated_snapshot_id: str | None = None,
) -> dict:
    """Materialize the independently owned durable evidence generation."""
    now = (clock or (lambda: datetime.now(UTC)))()
    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True, timeout=5)
    connection.row_factory = sqlite3.Row
    connection.execute("BEGIN")
    try:
        evidence = event_evidence_rows_from_connection(connection, now)
        rows = _news_evidence_display_rows(connection, evidence)
    finally:
        connection.rollback()
        connection.close()
    snapshot_id, frozen_rows = _materialize_news_evidence_generation(
        rows,
        manifest_path or database.parent / "dashboard-news-evidence-generation-v2.json",
        activated_snapshot_id=activated_snapshot_id,
    )
    published_snapshot = _publish_news_evidence_snapshot(frozen_rows)
    if published_snapshot != snapshot_id:
        raise ValueError("news evidence manifest snapshot hash is invalid")
    return {"snapshot_id": snapshot_id, "record_count": len(frozen_rows)}

def _news_evidence_page(cursor: str | None, limit: int) -> dict:
    with _NEWS_EVIDENCE_CACHE_LOCK:
        snapshot_id = str(_NEWS_EVIDENCE_CACHE.get("snapshot_id") or "")
        items = tuple(_NEWS_EVIDENCE_CACHE.get("items") or ())
    if not snapshot_id:
        raise StatusSnapshotUnavailable("news evidence snapshot is not ready")
    offset = 0
    if cursor:
        cursor_snapshot, separator, raw_offset = cursor.partition(":")
        if separator != ":" or cursor_snapshot != snapshot_id:
            raise ValueError("news evidence cursor is stale")
        offset = int(raw_offset)
        if offset < 0 or offset > len(items):
            raise ValueError("news evidence cursor offset is invalid")
    page_items: list[dict] = []
    for row in items[offset:offset + limit]:
        candidate = [*page_items, row]
        candidate_payload = {
            "snapshot_id": snapshot_id,
            "items": candidate,
            "total": len(items),
        }
        encoded = json.dumps(
            candidate_payload, ensure_ascii=False, allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")
        if page_items and len(encoded) > NEWS_EVIDENCE_PAGE_LIMIT_BYTES:
            break
        if len(encoded) > NEWS_EVIDENCE_PAGE_LIMIT_BYTES:
            raise ValueError("one news evidence row exceeds the page byte limit")
        page_items = candidate
    next_offset = offset + len(page_items)
    has_more = next_offset < len(items)
    return {
        "snapshot_id": snapshot_id,
        "items": page_items,
        "total": len(items),
        "has_more": has_more,
        "next_cursor": f"{snapshot_id}:{next_offset}" if has_more else None,
    }

def _news_metrics(
    *,
    counts: dict[str, int],
    raw_article_revisions: int,
    distinct_articles: int,
    all_news_evidence: list[dict],
    auditable_events: list[dict],
    decision_event_exposures: int,
    learning: dict,
) -> dict:
    """Publish one named news-count contract for every public surface."""
    transition = learning.get("news_contract_transition", {})
    return {
        "schema_version": "news-metrics-v1",
        "articles": {
            "received": distinct_articles,
            "stored_revisions": raw_article_revisions,
            "readable": int(counts.get("readable_news_items", 0)),
            "semantic_reviews_complete": int(counts.get("parsed_news_items", 0)),
            "current_model_candidates": int(
                counts.get("model_candidate_news_items", 0)
            ),
        },
        "events": {
            "independent": len(all_news_evidence),
            "auditable": len(auditable_events),
            "currently_model_eligible": sum(
                int(row["broad_model_eligible"]) for row in all_news_evidence
            ),
            "used_in_predictions": sum(
                int(row["model_seen"]) for row in auditable_events
            ),
            "never_used": sum(
                int(not row["model_seen"]) for row in auditable_events
            ),
        },
        "prediction_usage": {
            "decision_event_exposures": decision_event_exposures,
            "frozen_model_uses": sum(
                int(row["frozen_model_uses"]) for row in auditable_events
            ),
        },
        "training": {
            "current_contract_rows": int(
                transition.get("current_contract_exposed_rows", 0)
            ),
            "distinct_events": int(
                transition.get("current_contract_distinct_events", 0)
            ),
        },
    }
