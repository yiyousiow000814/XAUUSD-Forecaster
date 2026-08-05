#!/usr/bin/env python
"""Read-only localhost API for the XAUUSD Forward dashboard."""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
import sys
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


MODULE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MODULE_ROOT))
DEFAULT_DATABASE = MODULE_ROOT / ".local" / "forward" / "forward-evidence.sqlite3"
UTC = timezone.utc

from xauusd_forecaster.factors import factor_coverage  # noqa: E402
from xauusd_forecaster.annotation import (  # noqa: E402
    DEFAULT_GEMINI_MODEL,
    DEFAULT_GEMMA_MODEL,
    FALLBACK_GEMINI_MODEL,
    GEMMA_REQUESTS_PER_DAY_PER_KEY,
    GEMMA_SAFE_REQUESTS_PER_MINUTE_TOTAL,
    GEMINI_DAILY_PRIORITY_RESERVE,
    GEMINI_REQUESTS_PER_MINUTE_PER_KEY,
    INVALID_CHINESE_TITLE,
    configured_gemini_api_keys,
)
from xauusd_forecaster.gemini_quota import GeminiQuotaLedger  # noqa: E402
from xauusd_forecaster.training import MARKET_FEATURES  # noqa: E402
from xauusd_forecaster.learning_curves import learning_curve_payload  # noqa: E402


NEWS_SOURCE_DEFINITIONS = {
    "federal_reserve_full_text": ("Federal Reserve", "发布源", 15, ("federal_reserve_monetary", "federal_reserve_press_all", "federal_reserve_speeches_testimony")),
    "us_treasury_press_releases": ("U.S. Treasury", "发布源", 45, ("us_treasury_press_releases",)),
    "bea_economic_releases": ("U.S. BEA", "发布源", 45, ("bea_economic_releases",)),
    "ecb_press_releases": ("European Central Bank", "发布源", 45, ("ecb_press_releases",)),
    "eia_press_releases": ("U.S. EIA Press", "发布源", 45, ("eia_press_releases",)),
    "eia_today_in_energy": ("U.S. EIA Energy", "发布源", 45, ("eia_today_in_energy",)),
    "gdelt_gold_geopolitics": ("GDELT", "发布源", 75, ("gdelt_gold_geopolitics",)),
    "google_news_gold_context": ("Google News Context", "发布源", 45, ("google_news_gold_context",)),
    "google_news_gold_geopolitics": ("Google News Geopolitics", "备用发布源", 420, ("google_news_gold_geopolitics",)),
    "world_gold_council_central_banks": ("World Gold Council", "发布源", 420, ("world_gold_council_central_banks",)),
    "non_fed_full_text": ("非 Fed 正文解析器", "正文链路", 45, ()),
}


def _parse_utc(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _news_source_health(connection: sqlite3.Connection, now: datetime) -> list[dict]:
    rows = []
    for source, (label, role, stale_minutes, revision_sources) in NEWS_SOURCE_DEFINITIONS.items():
        polls = connection.execute(
            """SELECT count(*) total,
                      sum(status='OK') ok_count,
                      sum(status='PARTIAL') partial_count,
                      sum(status='ERROR') error_count,
                      max(CASE WHEN status='OK' THEN fetched_time END) last_success
               FROM source_polls WHERE source=?""",
            (source,),
        ).fetchone()
        latest = connection.execute(
            """SELECT fetched_time, status, error_type, error
               FROM source_polls WHERE source=?
               ORDER BY fetched_time DESC, poll_id DESC LIMIT 1""",
            (source,),
        ).fetchone()
        latest_error = connection.execute(
            """SELECT fetched_time, error_type, error
               FROM source_polls WHERE source=? AND status<>'OK'
               ORDER BY fetched_time DESC, poll_id DESC LIMIT 1""",
            (source,),
        ).fetchone()
        item_count = revision_count = full_text_count = 0
        latest_item_time = None
        if revision_sources:
            placeholders = ",".join("?" for _ in revision_sources)
            evidence = connection.execute(
                f"""SELECT count(DISTINCT source || ':' || source_item_id) item_count,
                            count(*) revision_count,
                            count(DISTINCT CASE WHEN body LIKE '[FULL_TEXT%'
                              THEN source || ':' || source_item_id END) full_text_count,
                            max(collector_first_seen_time) latest_item_time
                     FROM news_revisions WHERE source IN ({placeholders})""",
                revision_sources,
            ).fetchone()
            item_count = int(evidence["item_count"] or 0)
            revision_count = int(evidence["revision_count"] or 0)
            full_text_count = int(evidence["full_text_count"] or 0)
            latest_item_time = evidence["latest_item_time"]
        latest_time = _parse_utc(latest["fetched_time"] if latest else None)
        age_seconds = max(0.0, (now - latest_time).total_seconds()) if latest_time else None
        latest_status = latest["status"] if latest else "NO_DATA"
        if latest_status == "ERROR":
            health = "DEGRADED" if role == "正文链路" else "ERROR"
        elif latest_status == "PARTIAL":
            health = "DEGRADED"
        elif age_seconds is None or age_seconds > stale_minutes * 60:
            health = "STALE"
        else:
            health = "HEALTHY"
        rows.append({
            "source": source, "label": label, "role": role, "health": health,
            "latest_status": latest_status,
            "latest_poll_time": latest["fetched_time"] if latest else None,
            "last_success": polls["last_success"] or (
                latest["fetched_time"] if role == "正文链路" and latest_status == "PARTIAL" else None
            ), "age_seconds": age_seconds,
            "last_error_time": latest_error["fetched_time"] if latest_error else None,
            "last_error_type": latest_error["error_type"] if latest_error else None,
            "last_error": latest_error["error"] if latest_error else None,
            "poll_count": int(polls["total"] or 0),
            "ok_count": int(polls["ok_count"] or 0),
            "partial_count": int(polls["partial_count"] or 0),
            "error_count": int(polls["error_count"] or 0),
            "item_count": item_count, "revision_count": revision_count,
            "full_text_count": full_text_count, "latest_item_time": latest_item_time,
        })
    return rows


def _latest_quote_received(database: Path) -> str | None:
    sources = sorted((database.parent / "quotes").glob("*.jsonl"))
    if not sources:
        return None
    with sources[-1].open("rb") as handle:
        handle.seek(0, 2)
        size = handle.tell()
        handle.seek(max(0, size - 65_536))
        lines = handle.read().splitlines()
    for line in reversed(lines):
        try:
            return str(json.loads(line)["received_time"]).replace("Z", "+00:00")
        except (KeyError, ValueError, json.JSONDecodeError):
            continue
    return None


def _news_category(item: dict) -> str:
    source = str(item.get("source") or "")
    searchable = " ".join(
        str(item.get(key) or "")
        for key in ("headline", "event_type", "summary_zh")
    ).lower()
    if source in {"gdelt_gold_geopolitics", "google_news_gold_geopolitics"}:
        return "战争/地缘"
    if source == "world_gold_council_central_banks":
        return "央行购金"
    if source in {"eia_today_in_energy", "eia_press_releases"}:
        return "油价/能源"
    if source == "ecb_press_releases":
        return "利率/Fed"
    if any(
        term in searchable
        for term in (
            "war", "conflict", "sanction", "iran", "russia", "ukraine",
            "middle east", "hormuz", "战争", "制裁", "伊朗", "俄罗斯", "乌克兰",
        )
    ):
        return "战争/地缘"
    if any(term in searchable for term in ("oil", "opec", "crude", "原油", "油价")):
        return "油价/能源"
    if any(
        term in searchable
        for term in (
            "inflation", "cpi", "pce", "payroll", "employment", "unemployment",
            "jobs", "wage", "通胀", "就业", "失业", "薪资",
        )
    ):
        return "通胀/就业"
    if any(term in searchable for term in ("dollar", "liquidity", "balance sheet", "美元", "流动性")):
        return "美元/流动性"
    if any(term in searchable for term in ("gdp", "gross domestic product", "personal income", "growth", "经济增长")):
        return "增长/经济"
    if source in {"federal_reserve_monetary", "federal_reserve_speeches_testimony"}:
        return "利率/Fed"
    if source == "federal_reserve_press_all":
        return "Fed监管/其他"
    return "其他"


def _dashboard_payload(database: Path) -> dict:
    now = datetime.now(UTC)
    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True, timeout=5)
    connection.row_factory = sqlite3.Row
    try:
        latest = connection.execute(
            """SELECT d.decision_time, d.effective_action, d.data_health,
                      d.reason_codes_json, s.source_event_time,
                      s.source_received_time, s.bid, s.ask, s.spread,
                      s.features_json, s.u5, s.u5_status
               FROM decision_events d
               JOIN market_snapshots s USING(snapshot_id)
               ORDER BY d.decision_time DESC LIMIT 1"""
        ).fetchone()
        recent = connection.execute(
            """SELECT d.decision_id, d.decision_time, d.effective_action, d.data_health,
                      s.bid, s.ask, s.spread, s.features_json,
                      o.outcome_status, o.long_return, o.short_return,
                      o.long_mfe, o.long_mae, o.short_mfe, o.short_mae,
                      o.maximum_spread
               FROM decision_events d
               JOIN market_snapshots s USING(snapshot_id)
               LEFT JOIN outcomes o USING(decision_id)
               ORDER BY d.decision_time DESC LIMIT 30"""
        ).fetchall()
        counts = {
            name: connection.execute(f"SELECT count(*) FROM {name}").fetchone()[0]
            for name in (
                "decision_events",
                "outcomes",
                "news_revisions",
                "news_annotations",
                "news_title_translations",
                "macro_observations",
                "training_eligibility",
                "model_updates",
                "shadow_trade_intents",
                "shadow_trade_results",
                "repair_batches",
                "derived_market_snapshots",
                "derived_news_feature_snapshots",
                "derived_outcomes",
                "training_eligibility_v2",
                "model_updates_v2",
                "predictions_v2",
                "prediction_scores_v2",
            )
        }
        decision_ids = [row["decision_id"] for row in recent]
        predictions_by_decision: dict[str, list[dict]] = {key: [] for key in decision_ids}
        if decision_ids:
            placeholders = ",".join("?" for _ in decision_ids)
            prediction_rows = connection.execute(
                f"""SELECT decision_id, model_identity, model_version,
                            predicted_direction_u5, predicted_news_residual_u5,
                            ev_long_u5, ev_short_u5, uncertainty_u5,
                            recommended_action, effective_action, prediction_status
                     FROM predictions WHERE decision_id IN ({placeholders})
                     ORDER BY decision_id, model_identity""",
                decision_ids,
            ).fetchall()
            for prediction in prediction_rows:
                item = dict(prediction)
                predictions_by_decision[item.pop("decision_id")].append(item)
        news_rows = connection.execute(
            """SELECT n.source, n.source_item_id, n.revision_number,
                       n.source_published_time, n.collector_first_seen_time,
                       n.fetched_time,
                      n.headline AS original_headline,
                      COALESCE(t.headline_zh, n.headline) AS headline,
                      length(COALESCE(n.body, '')) AS content_characters,
                      CASE WHEN n.body LIKE '[FULL_TEXT%' THEN 'FULL_TEXT'
                           WHEN length(trim(COALESCE(n.body, ''))) >= 240 THEN 'SOURCE_CONTENT'
                           ELSE 'HEADLINE_ONLY' END AS content_status,
                      n.link, n.content_hash,
                      json_extract(a.annotation_json, '$.summary_zh') AS summary_zh,
                      COALESCE(a.event_type, legacy.event_type, legacy_v3.event_type) AS event_type,
                      COALESCE(a.entities_json, legacy.entities_json, legacy_v3.entities_json) AS entities_json,
                      COALESCE(a.hawkishness, legacy.hawkishness, legacy_v3.hawkishness) AS hawkishness,
                      COALESCE(a.inflation_impulse, legacy.inflation_impulse, legacy_v3.inflation_impulse) AS inflation_impulse,
                      COALESCE(a.growth_impulse, legacy.growth_impulse, legacy_v3.growth_impulse) AS growth_impulse,
                      COALESCE(a.geopolitical_risk, legacy.geopolitical_risk, legacy_v3.geopolitical_risk) AS geopolitical_risk,
                      COALESCE(a.usd_impulse, legacy.usd_impulse, legacy_v3.usd_impulse) AS usd_impulse,
                      COALESCE(a.novelty, legacy.novelty, legacy_v3.novelty) AS novelty,
                      COALESCE(a.confidence, legacy.confidence, legacy_v3.confidence) AS confidence,
                      COALESCE(a.llm_model_version, legacy.llm_model_version, legacy_v3.llm_model_version) AS llm_model_version,
                      COALESCE(a.prompt_version, legacy.prompt_version, legacy_v3.prompt_version) AS prompt_version,
                       COALESCE(a.parsed_at, legacy.parsed_at, legacy_v3.parsed_at) AS parsed_at,
                       CASE WHEN n.source_published_time IS NOT NULL THEN
                         (julianday(n.collector_first_seen_time)-julianday(n.source_published_time))*86400
                       END AS collection_delay_seconds,
                       CASE WHEN COALESCE(a.parsed_at, legacy.parsed_at, legacy_v3.parsed_at) IS NOT NULL THEN
                         (julianday(COALESCE(a.parsed_at, legacy.parsed_at, legacy_v3.parsed_at))-
                          julianday(n.collector_first_seen_time))*86400
                       END AS processing_delay_seconds,
                       COALESCE(r.maximum_tier, 'COLLECT_ONLY') AS source_eligibility,
                       CASE WHEN r.maximum_tier='MODEL_ELIGIBLE'
                                  AND length(trim(COALESCE(n.body,'')))>=r.minimum_body_characters
                                  AND a.parsed_at IS NOT NULL THEN 'MODEL_VISIBLE'
                            WHEN r.maximum_tier='MODEL_ELIGIBLE'
                                 AND length(trim(COALESCE(n.body,'')))>=r.minimum_body_characters
                                 AND a.parsed_at IS NULL THEN 'NOT_YET_PARSED'
                            WHEN r.maximum_tier='MODEL_ELIGIBLE' THEN 'MODEL_INELIGIBLE'
                            ELSE COALESCE(r.maximum_tier, 'COLLECT_ONLY') END AS model_visibility,
                      CASE WHEN a.annotation_id IS NOT NULL THEN 'READY'
                           WHEN length(trim(COALESCE(n.body, ''))) < 240 THEN 'WAITING_CONTENT'
                           WHEN f.is_terminal=1 THEN 'DEAD_LETTER'
                           WHEN f.next_retry_at > ? THEN 'BACKING_OFF'
                           WHEN length(trim(COALESCE(n.body, ''))) >= 240 THEN 'QUEUED'
                           ELSE 'WAITING_CONTENT' END AS annotation_status
               FROM news_revisions n
               LEFT JOIN news_title_translations t
                 ON t.translation_id=(
                   SELECT latest_t.translation_id
                   FROM news_title_translations latest_t
                   WHERE latest_t.source=n.source
                     AND latest_t.source_item_id=n.source_item_id
                     AND latest_t.revision_number=n.revision_number
                   ORDER BY (latest_t.headline_zh=?) ASC,
                            latest_t.parsed_at DESC, latest_t.translation_id DESC
                   LIMIT 1)
               LEFT JOIN news_annotations a
                 ON a.annotation_id=(
                   SELECT preferred_a.annotation_id
                   FROM news_annotations preferred_a
                   WHERE preferred_a.source=n.source
                     AND preferred_a.source_item_id=n.source_item_id
                     AND preferred_a.revision_number=n.revision_number
                     AND preferred_a.llm_model_version IN (
                       'gemini-3.5-flash-lite', 'gemini-3.1-flash-lite')
                     AND preferred_a.prompt_version IN (
                       'news-json-v9-local-display-recovery',
                       'news-json-v8-strict-zh-source-number-lexemes')
                   ORDER BY CASE preferred_a.prompt_version
                     WHEN 'news-json-v9-local-display-recovery' THEN 0 ELSE 1 END,
                     CASE preferred_a.llm_model_version
                       WHEN 'gemini-3.5-flash-lite' THEN 0 ELSE 1 END,
                     preferred_a.parsed_at DESC LIMIT 1)
               LEFT JOIN news_annotations legacy
                 ON legacy.source=n.source
                AND legacy.source_item_id=n.source_item_id
                AND legacy.revision_number=n.revision_number
                AND legacy.llm_model_version='gemini-3.5-flash-lite'
                AND legacy.prompt_version='news-json-v7-strict-headline-and-summary-zh-verbatim-numbers'
               LEFT JOIN news_annotations legacy_v3
                 ON legacy_v3.source=n.source
                AND legacy_v3.source_item_id=n.source_item_id
                AND legacy_v3.revision_number=n.revision_number
                AND legacy_v3.llm_model_version='gemini-3.5-flash-lite'
                AND legacy_v3.prompt_version='news-json-v6-headline-and-summary-zh-verbatim-numbers'
               LEFT JOIN news_llm_failures f
                 ON f.failure_id=(
                   SELECT latest_f.failure_id
                   FROM news_llm_failures latest_f
                   WHERE latest_f.task_type='ANNOTATION'
                     AND latest_f.source=n.source
                     AND latest_f.source_item_id=n.source_item_id
                     AND latest_f.revision_number=n.revision_number
                     AND latest_f.llm_model_version IN (
                       'gemini-3.5-flash-lite', 'gemini-3.1-flash-lite')
                     AND latest_f.prompt_version='news-json-v9-local-display-recovery'
                     AND NOT (latest_f.error_type='RuntimeError'
                              AND latest_f.error='All configured Gemini keys unavailable for this batch')
                    ORDER BY latest_f.failed_at DESC LIMIT 1)
               LEFT JOIN source_eligibility_rules r
                 ON r.eligibility_version='news-source-eligibility-v1'
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
                     AND (length(COALESCE(peer.body, '')) > length(COALESCE(n.body, ''))
                          OR (length(COALESCE(peer.body, '')) = length(COALESCE(n.body, ''))
                              AND peer.source_item_id < n.source_item_id)))
               ORDER BY COALESCE(n.source_published_time,
                                 n.collector_first_seen_time) DESC,
                        n.source, n.source_item_id
               LIMIT 200""",
            (now.isoformat(timespec="microseconds"), INVALID_CHINESE_TITLE),
        ).fetchall()
        annotation_queue = connection.execute(
            """SELECT
                 sum(CASE WHEN length(trim(COALESCE(n.body, ''))) >= 240
                           AND a.annotation_id IS NOT NULL THEN 1 ELSE 0 END) AS ready,
                 sum(CASE WHEN length(trim(COALESCE(n.body, ''))) >= 240
                           AND a.annotation_id IS NULL
                           AND (f.failure_id IS NULL OR
                                (f.is_terminal=0 AND f.next_retry_at <= ?))
                          THEN 1 ELSE 0 END) AS queued,
                 sum(CASE WHEN a.annotation_id IS NULL
                           AND f.is_terminal=0 AND f.next_retry_at > ?
                          THEN 1 ELSE 0 END) AS backing_off,
                 sum(CASE WHEN a.annotation_id IS NULL
                           AND f.is_terminal=1 THEN 1 ELSE 0 END) AS dead_letter,
                 sum(CASE WHEN length(trim(COALESCE(n.body, ''))) < 240
                          THEN 1 ELSE 0 END) AS waiting_content
               FROM news_revisions n
               LEFT JOIN news_annotations a
                 ON a.annotation_id=(
                   SELECT preferred_a.annotation_id
                   FROM news_annotations preferred_a
                   WHERE preferred_a.source=n.source
                     AND preferred_a.source_item_id=n.source_item_id
                     AND preferred_a.revision_number=n.revision_number
                     AND preferred_a.llm_model_version IN (
                       'gemini-3.5-flash-lite', 'gemini-3.1-flash-lite')
                     AND preferred_a.prompt_version IN (
                       'news-json-v9-local-display-recovery',
                       'news-json-v8-strict-zh-source-number-lexemes')
                   ORDER BY CASE preferred_a.prompt_version
                     WHEN 'news-json-v9-local-display-recovery' THEN 0 ELSE 1 END,
                     CASE preferred_a.llm_model_version
                       WHEN 'gemini-3.5-flash-lite' THEN 0 ELSE 1 END,
                     preferred_a.parsed_at DESC LIMIT 1)
               LEFT JOIN news_llm_failures f
                 ON f.failure_id=(
                   SELECT latest_f.failure_id
                   FROM news_llm_failures latest_f
                   WHERE latest_f.task_type='ANNOTATION'
                     AND latest_f.source=n.source
                     AND latest_f.source_item_id=n.source_item_id
                     AND latest_f.revision_number=n.revision_number
                     AND latest_f.llm_model_version IN (
                       'gemini-3.5-flash-lite', 'gemini-3.1-flash-lite')
                     AND latest_f.prompt_version='news-json-v9-local-display-recovery'
                     AND NOT (latest_f.error_type='RuntimeError'
                              AND latest_f.error='All configured Gemini keys unavailable for this batch')
                   ORDER BY latest_f.failed_at DESC LIMIT 1)
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
                     AND (length(COALESCE(peer.body, '')) > length(COALESCE(n.body, ''))
                          OR (length(COALESCE(peer.body, '')) = length(COALESCE(n.body, ''))
                              AND peer.source_item_id < n.source_item_id)))""",
            (
                now.isoformat(timespec="microseconds"),
                now.isoformat(timespec="microseconds"),
            ),
        ).fetchone()
        model_rows = connection.execute(
            """SELECT model_identity, model_version, created_at,
                      training_cutoff, hyperparameters_json, artifact_hash
               FROM model_updates ORDER BY training_cutoff DESC,
                                           model_identity"""
        ).fetchall()
        valid = connection.execute(
            """SELECT count(*) AS samples,
                      avg(long_return) AS avg_long,
                      avg(short_return) AS avg_short,
                      avg(quote_coverage) AS avg_coverage
               FROM outcomes WHERE outcome_status='VALID'"""
        ).fetchone()
        epoch = connection.execute(
            "SELECT value FROM runtime_metadata WHERE key='FORWARD_EPOCH'"
        ).fetchone()[0]
        macro_rows = connection.execute(
            """SELECT m.series_id, m.observation_period, m.value, m.unit
               FROM macro_observations m
               WHERE m.revision_number=(
                 SELECT max(r.revision_number) FROM macro_observations r
                 WHERE r.source=m.source AND r.series_id=m.series_id
                   AND r.observation_period=m.observation_period)
                 AND m.observation_period=(
                   SELECT max(p.observation_period) FROM macro_observations p
                   WHERE p.series_id=m.series_id)
               ORDER BY m.series_id"""
        ).fetchall()
        latest_macro = {row["series_id"]: dict(row) for row in macro_rows}
        collected_news_sources = {
            row[0] for row in connection.execute("SELECT DISTINCT source FROM news_revisions")
        }
        complete_candidates = connection.execute(
            """SELECT s.features_json, s.u5
               FROM training_eligibility e
               JOIN decision_events d USING(decision_id)
               JOIN market_snapshots s USING(snapshot_id)
               WHERE e.eligible_at <= ? AND d.decision_time >= ?""",
            (now.isoformat(), epoch),
        ).fetchall()
        complete_rows = 0
        for candidate in complete_candidates:
            features = json.loads(candidate["features_json"])
            values = [features.get(name) for name in MARKET_FEATURES]
            if candidate["u5"] is None or any(value is None for value in values):
                continue
            numeric = [float(value) for value in values]
            if all(math.isfinite(value) for value in numeric) and math.isfinite(
                float(candidate["u5"])
            ):
                complete_rows += 1
        learning = learning_curve_payload(connection)
        component_times = {
            "quote_bridge": _latest_quote_received(database),
            "decision_collector": connection.execute("SELECT max(created_at) FROM decision_events").fetchone()[0],
            "outcome_settler": connection.execute("SELECT max(appended_at) FROM outcomes").fetchone()[0],
            "news_collector": connection.execute("SELECT max(fetched_time) FROM source_polls").fetchone()[0],
            "gemini_annotator": connection.execute("SELECT max(parsed_at) FROM news_annotations").fetchone()[0],
        }
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        news_source_health = _news_source_health(connection, now)
    finally:
        connection.close()

    latest_data = dict(latest) if latest else None
    age_seconds = None
    if component_times["quote_bridge"]:
        age_seconds = max(
            0.0,
            (now - datetime.fromisoformat(component_times["quote_bridge"])).total_seconds(),
        )
    decision_success = component_times.get("decision_collector")
    decision_age = ((now - datetime.fromisoformat(decision_success)).total_seconds()
                    if decision_success else None)
    online = bool(age_seconds is not None and age_seconds <= 30
                  and decision_age is not None and decision_age <= 420)

    def component(name: str, stale_after: int, last_error: str | None = None) -> dict:
        value = component_times.get(name)
        age = max(0.0, (now - datetime.fromisoformat(value)).total_seconds()) if value else None
        return {"last_success": value, "age_seconds": age,
                "status": "OK" if age is not None and age <= stale_after else "STALE",
                "last_error": last_error}

    sync_status_file = database.parent / "dashboard-sync-status.json"
    sync_status = {}
    if sync_status_file.exists():
        try:
            sync_status = json.loads(sync_status_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            sync_status = {"last_error": "Invalid synchronizer status file"}
    sync_time = sync_status.get("last_success")
    backup_files = sorted((database.parent / "backups").glob("*.sqlite3"), key=lambda p: p.stat().st_mtime)
    backup_time = datetime.fromtimestamp(backup_files[-1].stat().st_mtime, UTC).isoformat() if backup_files else None
    component_times["sites_synchronizer"] = sync_time
    component_times["sqlite_backup"] = backup_time

    def serialize_row(row: sqlite3.Row) -> dict:
        item = dict(row)
        item["features"] = json.loads(item.pop("features_json"))
        item["predictions"] = predictions_by_decision.get(item["decision_id"], [])
        return item

    news = []
    seen_news_links = set()
    for row in news_rows:
        item = dict(row)
        dedupe_key = item.get("link") or (
            item["source"],
            item["source_item_id"],
        )
        if dedupe_key in seen_news_links:
            continue
        seen_news_links.add(dedupe_key)
        item["entities"] = json.loads(item.pop("entities_json")) if item.get("entities_json") else []
        item["category"] = _news_category(item)
        item["eligibility_version"] = "news-source-eligibility-v1"
        news.append(item)
    counts["latest_news_items"] = len(news)
    models = []
    for row in model_rows:
        item = dict(row)
        item["hyperparameters"] = json.loads(item.pop("hyperparameters_json"))
        models.append(item)
    latest_market = next(
        (item for item in models if item["model_identity"] == "CHALLENGER_A"),
        None,
    )
    trained_rows = (
        int(latest_market["hyperparameters"].get("complete_rows", 0))
        if latest_market else 0
    )
    next_training_at = 200 if trained_rows == 0 else trained_rows + 50

    if latest_data:
        latest_data["features"] = json.loads(latest_data.pop("features_json"))
        latest_data["reason_codes"] = json.loads(latest_data.pop("reason_codes_json"))
    gemini_keys = configured_gemini_api_keys()
    gemini_quota = GeminiQuotaLedger(database.parent / "gemini-quota.json").snapshot(
        gemini_keys
    )
    gemini_31_quota = GeminiQuotaLedger(
        database.parent / "gemini-3.1-flash-lite-quota.json"
    ).snapshot(gemini_keys)
    gemma_quota = GeminiQuotaLedger(
        database.parent / "gemma-quota.json",
        daily_limit=GEMMA_REQUESTS_PER_DAY_PER_KEY,
    ).snapshot(gemini_keys)
    available_gemini_keys = sum(
        item["status"] == "AVAILABLE" for item in gemini_quota["keys"]
    )
    available_fallback_keys = sum(
        item["status"] == "AVAILABLE" for item in gemini_31_quota["keys"]
    )
    flash_routine_remaining = max(
        0, int(gemini_quota["total_remaining"]) - GEMINI_DAILY_PRIORITY_RESERVE
    )
    flash_priority_reserve = min(
        GEMINI_DAILY_PRIORITY_RESERVE, int(gemini_quota["total_remaining"])
    )
    return {
        "generated_at": now.isoformat(),
        "forward_epoch": epoch,
        "system": {
            "online": online,
            "quote_age_seconds": age_seconds,
            "mode": "SHADOW",
            "trading_enabled": False,
            "symbol": "XAUUSD",
            "source_of_truth": "Local append-only SQLite",
            "sites_mirror": "read-only materialized display mirror",
            "components": {
                "quote_bridge": component("quote_bridge", 30),
                "decision_collector": component("decision_collector", 420),
                "outcome_settler": component("outcome_settler", 420),
                "news_collector": component("news_collector", 300),
                "gemini_annotator": component("gemini_annotator", 900),
                "sites_synchronizer": component(
                    "sites_synchronizer", 120, sync_status.get("last_error")
                ),
                "sqlite_backup": component("sqlite_backup", 172800),
                "integrity_check": {"last_success": now.isoformat(), "age_seconds": 0,
                                    "status": "OK" if integrity == "ok" else "ERROR",
                                    "last_error": None if integrity == "ok" else integrity},
            },
        },
        "latest": latest_data,
        "counts": counts,
        "outcome_summary": dict(valid),
        "recent_decisions": [serialize_row(row) for row in recent],
        "recent_news": news,
        "news_source_health": news_source_health,
        "annotation_queue": {
            "ready": int(annotation_queue["ready"] or 0),
            "queued": int(annotation_queue["queued"] or 0),
            "backing_off": int(annotation_queue["backing_off"] or 0),
            "dead_letter": int(annotation_queue["dead_letter"] or 0),
            "waiting_content": int(annotation_queue["waiting_content"] or 0),
            "configured_key_count": len(gemini_keys),
            "available_key_count": available_gemini_keys,
            "fallback_available_key_count": available_fallback_keys,
            "requests_per_minute_per_key": GEMINI_REQUESTS_PER_MINUTE_PER_KEY,
            "requests_per_minute": (
                available_gemini_keys
                * GEMINI_REQUESTS_PER_MINUTE_PER_KEY
            ),
            "priority_reserve": flash_priority_reserve,
            "routine_remaining": flash_routine_remaining,
        },
        "gemini_quota": gemini_quota,
        "gemini_31_quota": gemini_31_quota,
        "gemma_quota": gemma_quota,
        "llm_routing": {
            "action_bearing": {
                "model": DEFAULT_GEMINI_MODEL,
                "fallback_model": FALLBACK_GEMINI_MODEL,
                "role": "3.5 优先；普通额度用尽后 3.1 接管完整正文与训练特征",
            },
            "display_only": {
                "model": DEFAULT_GEMMA_MODEL,
                "role": "标题中文翻译，不进入模型训练",
                "requests_per_minute": GEMMA_SAFE_REQUESTS_PER_MINUTE_TOTAL,
            },
            "antigravity": {
                "enabled": False,
                "reason": "每日额度仅 100，不用于批量新闻流水线",
            },
        },
        "training": {
            "automatic": True,
            "label": "LEARNING PROGRESS",
            "preview_rows": 96,
            "minimum_rows": 200,
            "retrain_interval": 50,
            "legacy_eligible_rows": counts["training_eligibility"],
            "eligible_rows": counts["training_eligibility_v2"],
            "complete_rows": counts["training_eligibility_v2"],
            "next_training_at": learning["next_training_threshold"],
            "champion_auto_promotion": False,
            "models": learning["models"],
        },
        "learning_curves": learning,
        "factor_coverage": factor_coverage(latest_macro, collected_news_sources),
        "sources": {
            "market": "cTrader CLI / Bid-Ask",
            "fed": "ONLINE",
            "bls": "ONLINE" if counts["macro_observations"] else "WARMING_UP",
            "llm": "ENABLED" if counts["news_annotations"] else "ANNOTATION_WARMUP",
        },
    }


class Handler(BaseHTTPRequestHandler):
    database: Path

    def do_GET(self) -> None:  # noqa: N802
        if self.path.rstrip("/") != "/api/status":
            self.send_error(404)
            return
        try:
            body = json.dumps(_dashboard_payload(self.database), allow_nan=False).encode()
            self.send_response(200)
        except Exception as error:
            body = json.dumps({"error": str(error)[:500]}).encode()
            self.send_response(500)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        return


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    Handler.database = args.database.resolve()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(
        json.dumps(
            {
                "event": "DASHBOARD_API_STARTED",
                "url": f"http://{args.host}:{args.port}/api/status",
                "database": str(Handler.database),
                "read_only": True,
            }
        ),
        flush=True,
    )
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
