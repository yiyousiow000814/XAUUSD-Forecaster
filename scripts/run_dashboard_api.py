#!/usr/bin/env python
"""Read-only localhost API for the XAUUSD Forward dashboard."""

from __future__ import annotations

import argparse
import gzip
import json
import math
import sqlite3
import subprocess
import sys
import threading
import urllib.parse
from types import SimpleNamespace
from collections import Counter
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


MODULE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MODULE_ROOT))
DEFAULT_DATABASE = MODULE_ROOT / ".local" / "forward" / "forward-evidence.sqlite3"
UTC = timezone.utc
PAYLOAD_SCHEMA_VERSION = "xauusd-dashboard-v4-event-episode"
MARKET_DETAIL_CANDLE_LIMIT = 7 * 288
MARKET_OVERVIEW_CANDLE_LIMIT = 480
MARKET_HISTORY_PAGE_LIMIT = 500
_QUOTE_CANDLE_CACHE: dict[tuple[str, int, int], list[dict]] = {}

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
    completed_annotation_records,
    configured_gemini_api_keys,
    pending_annotation_records,
)
from xauusd_forecaster.gemini_quota import GeminiQuotaLedger  # noqa: E402
from xauusd_forecaster.training import MARKET_FEATURES  # noqa: E402
from xauusd_forecaster.learning_curves import learning_curve_payload  # noqa: E402
from xauusd_forecaster.execution_costs import net_shadow_log_return  # noqa: E402
from xauusd_forecaster.news_evidence import (  # noqa: E402
    EVIDENCE_POLICY_VERSION, event_evidence_rows_from_connection,
    resolve_event_clock,
)
from xauusd_forecaster.news_relevance import google_news_item_is_relevant  # noqa: E402
from xauusd_forecaster.news_features_v2 import SOURCE_RULES  # noqa: E402
from xauusd_forecaster.news_impact import (  # noqa: E402
    IMPACT_MODEL,
    IMPACT_PROMPT_VERSION,
    impact_is_actionable,
    impact_time_rule,
)
from xauusd_forecaster.storylines import STORYLINE_POLICY_VERSION, temporal_event_graph  # noqa: E402
from xauusd_forecaster.execution_learning import execution_learning_status  # noqa: E402
from xauusd_forecaster.market_session import expected_weekly_closure  # noqa: E402


def _deployment_status(
    runtime_sha: str | None, expected_sha: str | None, module_dirty: bool,
) -> str:
    """Keep unpublished local edits distinct from an actual deployed SHA drift."""
    if not runtime_sha or not expected_sha:
        return "PROVENANCE_UNKNOWN"
    if runtime_sha != expected_sha:
        return "DEPLOYMENT_DRIFT"
    if module_dirty:
        return "LOCAL_CHANGES"
    return "MATCHED"


def _deployment_provenance(generated_at: datetime, database_epoch: str | None) -> dict:
    """Expose code/data identity so a stale Sites mirror cannot look current."""
    # Git discovers the repository from the module directory in both the old
    # nested checkout and the current standalone checkout. A fixed parent
    # depth breaks as soon as the project is moved.
    repo = MODULE_ROOT
    def git(*args: str) -> str | None:
        try:
            result = subprocess.run(
                ("git", *args), cwd=repo, capture_output=True, text=True,
                timeout=5, check=True,
            )
            return result.stdout.strip() or None
        except (OSError, subprocess.SubprocessError):
            return None
    runtime_sha = git("rev-parse", "HEAD")
    upstream = git("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}")
    expected_sha = git("rev-parse", upstream or "origin/main")
    module_dirty = bool(git("status", "--porcelain", "--", "."))
    return {
        "runtime_git_sha": runtime_sha,
        "expected_git_sha": expected_sha,
        "runtime_dirty": module_dirty,
        "status": _deployment_status(runtime_sha, expected_sha, module_dirty),
        "storyline_policy_version": STORYLINE_POLICY_VERSION,
        "payload_schema_version": PAYLOAD_SCHEMA_VERSION,
        "payload_generated_at": generated_at.isoformat(),
        "source_database_epoch": database_epoch,
    }


DEPLOYMENT_PROVENANCE = _deployment_provenance(datetime.now(UTC), None)

_LEARNING_REVISION_TABLES = (
    "derived_outcomes",
    "model_updates_v2",
    "predictions_v2",
    "prediction_scores_v2",
    "execution_training_examples_v2",
    "execution_model_updates_v2",
    "execution_predictions_v2",
    "execution_position_scores_v2",
)
_LEARNING_CACHE_LOCK = threading.Lock()
_LEARNING_CACHE: dict[str, object] = {}


def _learning_revision(connection: sqlite3.Connection) -> tuple[object, ...]:
    database_row = connection.execute("PRAGMA database_list").fetchone()
    database_identity = database_row[2] if database_row and database_row[2] else id(connection)
    counts = tuple(
        int(connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0])
        for table in _LEARNING_REVISION_TABLES
    )
    return (database_identity, *counts)


def _learning_surfaces(connection: sqlite3.Connection) -> tuple[dict, dict]:
    """Rebuild learning surfaces only when append-only source counts change."""
    revision = _learning_revision(connection)
    with _LEARNING_CACHE_LOCK:
        if _LEARNING_CACHE.get("revision") != revision:
            _LEARNING_CACHE.update({
                "revision": revision,
                "learning": learning_curve_payload(connection),
                "execution": execution_learning_status(
                    SimpleNamespace(connection=connection)
                ),
            })
        return _LEARNING_CACHE["learning"], _LEARNING_CACHE["execution"]


NEWS_SOURCE_DEFINITIONS = {
    "federal_reserve_full_text": ("Federal Reserve", "发布源", 15, ("federal_reserve_monetary", "federal_reserve_press_all", "federal_reserve_speeches_testimony")),
    "us_treasury_press_releases": ("U.S. Treasury", "发布源", 45, ("us_treasury_press_releases",)),
    "bea_economic_releases": ("U.S. BEA", "发布源", 45, ("bea_economic_releases",)),
    "bls_employment_situation": ("BLS Employment Situation", "官方发布源", 15, ("bls_employment_situation",)),
    "bls_consumer_price_index": ("BLS Consumer Price Index", "官方发布源", 15, ("bls_consumer_price_index",)),
    "bls_job_openings": ("BLS Job Openings", "官方发布源", 15, ("bls_job_openings",)),
    "bls_public_api": ("BLS Public Data API", "数值与修订链路", 75, ()),
    "ecb_press_releases": ("European Central Bank", "发布源", 45, ("ecb_press_releases",)),
    "eia_press_releases": ("U.S. EIA Press", "发布源", 45, ("eia_press_releases",)),
    "eia_today_in_energy": ("U.S. EIA Energy", "发布源", 45, ("eia_today_in_energy",)),
    "gdelt_gold_geopolitics": ("GDELT", "发现源", 75, ("gdelt_gold_geopolitics",)),
    "google_news_gold_context": ("Google News Context", "发现源", 45, ("google_news_gold_context",)),
    "google_news_bls_official_releases": ("BLS Official Release Fallback", "官方域名备用发现源", 45, ("google_news_bls_official_releases",)),
    "google_news_us_employment": ("Google News U.S. Employment", "发现源", 45, ("google_news_us_employment",)),
    "google_news_us_inflation": ("Google News U.S. Inflation", "发现源", 45, ("google_news_us_inflation",)),
    "google_news_fed_rates": ("Google News Fed & Rates", "发现源", 45, ("google_news_fed_rates",)),
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
                      sum(status='OK' OR
                          (source='non_fed_full_text'
                           AND error_type='HydrationErrors')) ok_count,
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
        elif source == "bls_public_api":
            evidence = connection.execute(
                """SELECT count(*) revision_count,
                          count(DISTINCT series_id || ':' || observation_period) item_count,
                          max(collector_first_seen_time) latest_item_time
                   FROM macro_observations WHERE source='bls_public_api'"""
            ).fetchone()
            item_count = int(evidence["item_count"] or 0)
            revision_count = int(evidence["revision_count"] or 0)
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
        elif item_count == 0 and source.startswith("bls_"):
            health = "WARMING_UP"
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
            "recovery_mode": None, "fallback_label": None,
            "fallback_health": None, "next_retry_time": None,
            "semantic_status": "NO_RELEASE_CAPTURED" if health == "WARMING_UP" else "OK",
            "semantic_message": (
                "轮询正常，但本机尚未捕获该发布系列的正式条目"
                if health == "WARMING_UP" else None
            ),
        })
    by_source = {row["source"]: row for row in rows}
    bls_numeric = by_source.get("bls_public_api")
    if bls_numeric:
        numeric_seen = _parse_utc(bls_numeric.get("latest_item_time"))
        for source in (
            "bls_employment_situation", "bls_consumer_price_index", "bls_job_openings"
        ):
            release = by_source.get(source)
            release_seen = _parse_utc(release.get("latest_item_time")) if release else None
            if release and release_seen and (numeric_seen is None or numeric_seen < release_seen):
                release["health"] = "DEGRADED"
                release["semantic_status"] = "RELEASE_SEEN_NUMERIC_PENDING"
                release["semantic_message"] = "正式发布已捕获，但 BLS 数值/修订尚未同步"
    bls_fallback = by_source.get("google_news_bls_official_releases")
    if bls_fallback:
        for source in (
            "bls_employment_situation", "bls_consumer_price_index", "bls_job_openings"
        ):
            release = by_source.get(source)
            if (
                release
                and release.get("latest_status") == "ERROR"
                and release.get("last_error_type") == "HTTPError"
                and bls_fallback.get("health") in {"HEALTHY", "DEGRADED"}
            ):
                fallback_healthy = bls_fallback.get("health") == "HEALTHY"
                release["health"] = "FALLBACK_ACTIVE" if fallback_healthy else "DEGRADED"
                release["recovery_mode"] = "BLS_DIRECT_BLOCKED"
                release["fallback_label"] = bls_fallback["label"]
                release["fallback_health"] = bls_fallback["health"]
                release["semantic_status"] = (
                    "OFFICIAL_DOMAIN_FALLBACK_ACTIVE" if fallback_healthy
                    else "OFFICIAL_RELEASE_BODY_BLOCKED"
                )
                release["semantic_message"] = (
                    "BLS 直接 RSS 被本机网络拒绝；官方域名备用发现源与 BLS 数值 API 正常运行"
                    if fallback_healthy else
                    "已发现 BLS 官方发布，但本机仍无法读取正式正文；BLS 数值 API 独立运行"
                )
    gdelt = by_source.get("gdelt_gold_geopolitics")
    fallback = by_source.get("google_news_gold_context")
    if gdelt and fallback and "429" in str(gdelt.get("last_error") or ""):
        recent = connection.execute(
            """SELECT fetched_time,status,error FROM source_polls
               WHERE source='gdelt_gold_geopolitics'
               ORDER BY fetched_time DESC,poll_id DESC LIMIT 8"""
        ).fetchall()
        streak = 0
        for poll in recent:
            if poll["status"] == "ERROR" and "429" in str(poll["error"] or ""):
                streak += 1
            else:
                break
        latest_poll = _parse_utc(gdelt["latest_poll_time"])
        cooldown = min(360, 60 * (2 ** min(streak, 3))) if streak else 60
        gdelt["recovery_mode"] = "RATE_LIMIT_BACKOFF"
        gdelt["fallback_label"] = fallback["label"]
        gdelt["fallback_health"] = fallback["health"]
        gdelt["next_retry_time"] = (
            (latest_poll + timedelta(minutes=cooldown)).isoformat()
            if latest_poll else None
        )
        if fallback["health"] == "HEALTHY":
            gdelt["health"] = "FALLBACK_ACTIVE"
            gdelt["latest_status"] = "RATE_LIMITED"
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


CATEGORY_LABELS = {
    "rates_fed": "利率/Fed",
    "inflation_employment": "通胀/就业",
    "growth_economy": "增长/经济",
    "usd_liquidity": "美元/流动性",
    "oil_energy": "油价/能源",
    "war_geopolitics": "战争/地缘",
    "central_bank_gold": "央行购金",
    "risk_sentiment": "风险偏好",
    "regulation_other": "监管/其他",
}


def _news_category(item: dict) -> str:
    controlled = CATEGORY_LABELS.get(str(item.get("primary_category") or ""))
    if controlled:
        return controlled
    source = str(item.get("source") or "")
    searchable = " ".join(
        str(item.get(key) or "")
        for key in ("headline", "event_type", "summary_zh")
    ).lower()
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
        return "监管/其他"
    return "其他"


def _not_required_reason(item: dict, forward_epoch: str) -> tuple[str, str]:
    """Explain the single reason a readable row will not consume AI quota."""
    published_raw = item.get("source_published_time")
    if not published_raw:
        return "HISTORICAL_MATERIAL", "历史资料：缺少可靠发布时间"
    published = datetime.fromisoformat(str(published_raw))
    epoch = datetime.fromisoformat(forward_epoch)
    if published < epoch:
        return "HISTORICAL_MATERIAL", "历史资料：发布时间早于系统开始记录"
    source = str(item.get("source") or "")
    if source.startswith("google_news_") or source.startswith("gdelt_"):
        return "SEARCH_LEAD", "搜索线索：来自聚合发现源，不是独立官方发布"
    return "DUPLICATE_CONTENT", "重复内容：同一事件已有正文更完整的版本"


def _apply_impact_status(item: dict, now: datetime) -> None:
    """Expose the current Gemma lifetime decision in plain, auditable states."""
    if not item.get("parsed_at"):
        item["impact_status"] = "PENDING_ANNOTATION"
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


def _quote_history_files(directory: Path) -> list[Path]:
    """Choose one authoritative file for each append-only quote date."""
    by_day: dict[str, Path] = {}
    for path in sorted(directory.glob("xauusd-quotes-*.jsonl*")):
        if path.name.endswith(".receipt.json"):
            continue
        day = path.name.split(".jsonl", 1)[0]
        current = by_day.get(day)
        replace_empty = (
            current is not None
            and current.stat().st_size == 0
            and path.stat().st_size > 0
        )
        prefer_live = (
            path.suffix == ".jsonl"
            and current is not None
            and current.suffix == ".gz"
            and path.stat().st_size > 0
        )
        if current is None or replace_empty or prefer_live:
            by_day[day] = path
    return [by_day[day] for day in sorted(by_day)]


def _quote_file_candles(path: Path) -> list[dict]:
    """Aggregate one daily quote file and cache immutable archive results."""
    try:
        stat = path.stat()
    except OSError:
        return []
    key = (str(path), stat.st_size, stat.st_mtime_ns)
    cached = _QUOTE_CANDLE_CACHE.get(key)
    if cached is not None:
        return cached
    for stale in [candidate for candidate in _QUOTE_CANDLE_CACHE if candidate[0] == str(path)]:
        del _QUOTE_CANDLE_CACHE[stale]
    buckets: dict[datetime, dict] = {}
    opener = gzip.open if path.suffix == ".gz" else open
    try:
        with opener(path, "rt", encoding="utf-8") as handle:
            for line in handle:
                try:
                    quote = json.loads(line)
                    observed = datetime.fromisoformat(
                        str(quote["received_time"]).replace("Z", "+00:00")
                    )
                    observed = (
                        observed.replace(tzinfo=UTC)
                        if observed.tzinfo is None else observed.astimezone(UTC)
                    )
                    bid = float(quote["bid"])
                    ask = float(quote["ask"])
                    midpoint = (bid + ask) / 2.0
                    minute = observed.replace(second=0, microsecond=0)
                    bucket = minute - timedelta(minutes=minute.minute % 5)
                except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                    continue
                candle = buckets.get(bucket)
                if candle is None:
                    buckets[bucket] = {
                        "time": bucket.isoformat(), "open": midpoint,
                        "high": midpoint, "low": midpoint, "close": midpoint,
                        "ticks": 1,
                    }
                else:
                    candle["high"] = max(candle["high"], midpoint)
                    candle["low"] = min(candle["low"], midpoint)
                    candle["close"] = midpoint
                    candle["ticks"] += 1
    except OSError:
        return []
    candles = [buckets[bucket] for bucket in sorted(buckets)]
    _QUOTE_CANDLE_CACHE[key] = candles
    return candles


def _downsample_candles(candles: list[dict], limit: int) -> list[dict]:
    """Preserve OHLC extremes while bounding the all-history overview."""
    if len(candles) <= limit:
        return candles
    chunk_size = math.ceil(len(candles) / limit)
    compacted = []
    for start in range(0, len(candles), chunk_size):
        rows = candles[start:start + chunk_size]
        compacted.append({
            "time": rows[0]["time"],
            "open": rows[0]["open"],
            "high": max(row["high"] for row in rows),
            "low": min(row["low"] for row in rows),
            "close": rows[-1]["close"],
            "ticks": sum(int(row.get("ticks") or 0) for row in rows),
            "source_candles": len(rows),
        })
    return compacted


def _all_market_candles(database: Path) -> list[dict]:
    history_by_time: dict[str, dict] = {}
    for path in _quote_history_files(database.parent / "quotes"):
        for candle in _quote_file_candles(path):
            history_by_time[candle["time"]] = candle
    return [history_by_time[key] for key in sorted(history_by_time)]


def _market_decisions(
    connection: sqlite3.Connection, start_time: str, end_time: str | None = None,
) -> list[dict]:
    end_clause = " AND p.decision_time<?" if end_time else ""
    parameters: tuple[str, ...] = (
        (start_time, end_time) if end_time else (start_time,)
    )
    decision_rows = connection.execute(
        f"""WITH ranked AS (
             SELECT p.source_decision_id,p.decision_time,p.model_identity,
                    p.model_version,p.recommended_action,p.effective_action,
                    p.prediction_status,p.predicted_direction_u5,
                    p.ev_long_u5,p.ev_short_u5,p.lcb_long_u5,p.lcb_short_u5,
                    s.value_quote_return,
                    o.long_quote_return,o.short_quote_return,o.outcome_status,
                    o.reason_codes_json AS outcome_reason_codes_json,
                    row_number() OVER (
                      PARTITION BY p.source_decision_id,p.model_identity
                      ORDER BY u.created_at DESC,u.model_version DESC
                    ) AS version_rank
             FROM predictions_v2 p
             JOIN model_updates_v2 u USING(model_version)
             LEFT JOIN prediction_scores_v2 s
               USING(source_decision_id,model_version)
             LEFT JOIN derived_outcomes o
               ON o.source_decision_id=p.source_decision_id
             WHERE p.decision_time>=?{end_clause} AND p.decision_time>u.created_at
           )
           SELECT * FROM ranked WHERE version_rank=1
           ORDER BY decision_time,model_identity""",
        parameters,
    ).fetchall()
    decisions = []
    for row in decision_rows:
        recorded = row["recommended_action"]
        row_payload = {
            key: value for key, value in dict(row).items()
            if key != "outcome_reason_codes_json"
        }
        for key in ("long_quote_return", "short_quote_return"):
            gross = row_payload.get(key)
            row_payload[f"gross_{key}"] = gross
            if gross is not None:
                row_payload[key] = net_shadow_log_return(gross)
        gross_score = row_payload.get("value_quote_return")
        row_payload["gross_value_quote_return"] = gross_score
        if gross_score is not None:
            row_payload["value_quote_return"] = (
                0.0 if recorded == "WAIT" else net_shadow_log_return(gross_score)
            )
        ev_long = row["ev_long_u5"]
        ev_short = row["ev_short_u5"]
        lcb_long = row["lcb_long_u5"]
        lcb_short = row["lcb_short_u5"]
        expected = "WAIT"
        legacy_lcb_policy = row["prediction_status"] == "PROVISIONAL_LCB_GATED"
        if legacy_lcb_policy and lcb_long is not None and lcb_short is not None:
            if lcb_long > lcb_short and lcb_long > 0:
                expected = "LONG"
            elif lcb_short > lcb_long and lcb_short > 0:
                expected = "SHORT"
        elif not legacy_lcb_policy and ev_long is not None and ev_short is not None:
            if ev_long > ev_short and ev_long > 0:
                expected = "LONG"
            elif ev_short > ev_long and ev_short > 0:
                expected = "SHORT"
        decisions.append({
            **row_payload,
            "outcome_reason_codes": json.loads(row["outcome_reason_codes_json"] or "[]"),
            "exit_time": (
                datetime.fromisoformat(row["decision_time"]) + timedelta(minutes=30)
            ).isoformat(),
            "outcome_status": row["outcome_status"] or "PENDING",
            "policy_expected_action": expected,
            "policy_consistent": recorded == expected,
            "action_policy": (
                "POSITIVE_LCB_V1" if legacy_lcb_policy else "POSITIVE_POST_COST_EV_V2"
            ),
            "frozen_record": True,
        })
    return decisions


def _market_history_page(
    database: Path, connection: sqlite3.Connection, after: str | None, limit: int,
) -> dict:
    """Return an ordered, replay-safe page for incremental remote ingestion."""
    history = _all_market_candles(database)
    start_index = 0
    if after:
        while start_index < len(history) and history[start_index]["time"] <= after:
            start_index += 1
    candles = history[start_index:start_index + limit]
    if not candles:
        return {"candles": [], "decisions": [], "next_cursor": after, "has_more": False}
    end_index = start_index + len(candles)
    end_time = history[end_index]["time"] if end_index < len(history) else None
    return {
        "candles": candles,
        "decisions": _market_decisions(
            connection, candles[0]["time"], end_time,
        ),
        "next_cursor": candles[-1]["time"],
        "has_more": end_index < len(history),
        "history_start": history[0]["time"],
        "history_end": history[-1]["time"],
    }


def _recent_market_chart(
    database: Path, connection: sqlite3.Connection, now: datetime
) -> dict:
    """Build recorded quote history; weekends must not erase the last session."""
    history = _all_market_candles(database)
    candles = history[-MARKET_DETAIL_CANDLE_LIMIT:]
    overview_candles = (
        _downsample_candles(history, MARKET_OVERVIEW_CANDLE_LIMIT)
        if len(history) > len(candles) else []
    )
    first_time = candles[0]["time"] if candles else now.isoformat()
    decisions = _market_decisions(connection, first_time)
    marker_rows = connection.execute(
        """WITH grouped AS (
             SELECT model_identity,training_dataset_hash,min(created_at) created_at,
                    min(training_rows) training_rows,min(training_cutoff) training_cutoff,
                    count(*) artifact_count
             FROM model_updates_v2
             GROUP BY model_identity,training_dataset_hash
           )
           SELECT * FROM grouped WHERE created_at>=?
           ORDER BY created_at,model_identity""",
        (first_time,),
    ).fetchall()
    prediction_history_start: dict[str, str] = {}
    for row in decisions:
        identity = str(row.get("model_identity") or "")
        decision_time = str(row.get("decision_time") or "")
        if identity and decision_time and (
            identity not in prediction_history_start
            or decision_time < prediction_history_start[identity]
        ):
            prediction_history_start[identity] = decision_time
    return {
        "window_hours": None,
        "candle_minutes": 5,
        "candles": candles,
        "overview_candles": overview_candles,
        "history_start": history[0]["time"] if history else None,
        "history_end": history[-1]["time"] if history else None,
        "detail_start": candles[0]["time"] if candles else None,
        "source_candle_count": len(history),
        "overview_downsampled": bool(overview_candles),
        "prediction_history_start": prediction_history_start,
        "history_resource": "/api/market-history",
        "decisions": [dict(row) for row in decisions],
        "training_markers": [dict(row) for row in marker_rows],
    }


def _news_evidence_display_rows(
    connection: sqlite3.Connection, all_news_evidence: list[dict],
) -> list[dict]:
    """Return one audit row per independent event, not per frozen revision.

    Model visibility is immutable at the event-version level.  The dashboard,
    however, is an event audit.  Rendering every frozen version duplicated the
    same headline and also produced duplicate React keys when users switched
    between "used" and "never used".
    """
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
        visibility_rows = connection.execute(
            f"""SELECT event_key,
                      count(*) AS frozen_model_uses,
                      count(DISTINCT source_decision_id) AS frozen_decisions,
                      count(DISTINCT event_source_hash) AS frozen_versions,
                      min(decision_time) AS first_model_decision_time,
                      max(decision_time) AS last_model_decision_time,
                      group_concat(DISTINCT model_identity) AS model_identities,
                      group_concat(DISTINCT model_version) AS model_versions
               FROM {receipt_source}
               GROUP BY event_key"""
        ).fetchall()
        catalog_rows = connection.execute(
            """SELECT * FROM news_model_visibility_events_v1
               ORDER BY collector_first_seen_time DESC,event_key"""
        ).fetchall()
    except sqlite3.OperationalError:
        visibility_rows = []
        catalog_rows = []

    receipts = {row["event_key"]: dict(row) for row in visibility_rows}
    catalog_by_event: dict[str, dict] = {}
    for raw in catalog_rows:
        row = dict(raw)
        catalog_by_event.setdefault(row["event_key"], row)

    current_by_event: dict[str, dict] = {}
    for row in all_news_evidence:
        current_by_event[row["event_key"]] = row

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
        if row.get("prompt_version") != "news-json-v14-material-event-evidence":
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
                if row["broad_model_eligible"] else list(row["reason_codes"])
            ),
        })
        rows.append(display)
        displayed_events.add(event_key)

    rows.sort(
        key=lambda row: (
            int(row["model_seen"]), row["collector_first_seen_time"],
            row["event_key"],
        ),
        reverse=True,
    )
    return rows[:100]


def _dashboard_payload(database: Path) -> dict:
    now = datetime.now(UTC)
    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True, timeout=5)
    connection.row_factory = sqlite3.Row
    try:
        latest = connection.execute(
            """SELECT d.decision_id, d.decision_time, d.effective_action, d.data_health,
                      d.reason_codes_json, s.source_event_time,
                      s.source_received_time, s.bid, s.ask, s.spread,
                      s.features_json, s.u5, s.u5_status
               FROM decision_events d
               JOIN market_snapshots s USING(snapshot_id)
               ORDER BY d.decision_time DESC LIMIT 1"""
        ).fetchone()
        latest_prediction = None
        if latest:
            latest_prediction = connection.execute(
                """SELECT p.model_identity,p.model_version,p.recommended_action,
                          p.prediction_status,p.ev_long_u5,p.ev_short_u5,
                          p.interval_width,p.decision_time
                   FROM predictions_v2 p
                   JOIN model_updates_v2 u USING(model_version)
                   WHERE p.source_decision_id=?
                     AND p.model_identity IN ('BROAD_FULL','FULL','MARKET_ONLY')
                   ORDER BY CASE p.model_identity
                              WHEN 'BROAD_FULL' THEN 0 WHEN 'FULL' THEN 1 ELSE 2 END,
                            u.created_at DESC
                   LIMIT 1""",
                (latest["decision_id"],),
            ).fetchone()
        u5_rows = connection.execute(
            """SELECT u5 FROM market_snapshots
               WHERE u5_status='READY' AND u5 IS NOT NULL
               ORDER BY decision_time"""
        ).fetchall()
        recent = connection.execute(
            """SELECT d.decision_id, d.decision_time, d.effective_action, d.data_health,
                      s.bid, s.ask, s.spread, s.features_json,
                      o.outcome_status,
                      o.reason_codes_json AS outcome_reason_codes_json,
                      o.long_return, o.short_return,
                      o.long_mfe, o.long_mae, o.short_mfe, o.short_mae,
                      o.maximum_spread,
                      (SELECT p.recommended_action FROM predictions_v2 p
                       JOIN model_updates_v2 u USING(model_version)
                       WHERE p.source_decision_id=d.decision_id
                         AND p.model_identity='BROAD_FULL'
                       ORDER BY u.created_at DESC LIMIT 1) AS research_action,
                      (SELECT p.prediction_status FROM predictions_v2 p
                       JOIN model_updates_v2 u USING(model_version)
                       WHERE p.source_decision_id=d.decision_id
                         AND p.model_identity='BROAD_FULL'
                       ORDER BY u.created_at DESC LIMIT 1) AS research_status
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
                f"""WITH ranked AS (
                       SELECT p.source_decision_id AS decision_id,
                              p.model_identity,p.model_version,
                              p.predicted_direction_u5,p.predicted_news_residual_u5,
                              p.ev_long_u5,p.ev_short_u5,
                              p.interval_width AS uncertainty_u5,
                              p.recommended_action,p.effective_action,p.prediction_status,
                              row_number() OVER (
                                PARTITION BY p.source_decision_id,p.model_identity
                                ORDER BY u.created_at DESC,u.model_version DESC
                              ) AS version_rank
                       FROM predictions_v2 p
                       JOIN model_updates_v2 u USING(model_version)
                       WHERE p.source_decision_id IN ({placeholders})
                         AND p.decision_time>u.created_at
                     )
                     SELECT decision_id,model_identity,model_version,
                            predicted_direction_u5,predicted_news_residual_u5,
                            ev_long_u5,ev_short_u5,uncertainty_u5,
                            recommended_action,effective_action,prediction_status
                     FROM ranked WHERE version_rank=1
                     ORDER BY decision_id,model_identity""",
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
                      CASE WHEN length(trim(COALESCE(n.body, ''))) >= 240 THEN 'AVAILABLE'
                           WHEN cf.is_terminal=1 THEN 'UNAVAILABLE'
                           WHEN cf.next_retry_at IS NOT NULL THEN 'RETRYING'
                           ELSE 'PENDING' END AS content_fetch_status,
                      cf.error_type AS content_error_type,
                      n.link, n.content_hash,
                      json_extract(a.annotation_json, '$.summary_zh') AS summary_zh,
                      json_extract(a.annotation_json, '$.primary_category') AS primary_category,
                      json_extract(a.annotation_json, '$.secondary_categories') AS secondary_categories_json,
                       json_extract(a.annotation_json, '$.emerging_topic_zh') AS emerging_topic_zh,
                       json_extract(a.annotation_json, '$.event_time') AS event_time,
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
                       i.impact_class,
                       i.event_state AS impact_event_state,
                       i.update_type AS impact_update_type,
                       i.confidence AS impact_confidence,
                       i.reason_zh AS impact_reason_zh,
                       i.assessed_at AS impact_assessed_at,
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
                       'news-json-v14-material-event-evidence',
                       'news-json-v13-event-claims',
                       'news-json-v12-gemini-story-identity',
                       'news-json-v11-gemini-story-subjects',
                       'news-json-v10-controlled-category-zh',
                       'news-json-v9-local-display-recovery')
                   ORDER BY CASE preferred_a.prompt_version
                     WHEN 'news-json-v14-material-event-evidence' THEN 0
                     WHEN 'news-json-v13-event-claims' THEN 1
                     WHEN 'news-json-v12-gemini-story-identity' THEN 2
                     WHEN 'news-json-v11-gemini-story-subjects' THEN 3
                     WHEN 'news-json-v10-controlled-category-zh' THEN 4 ELSE 5 END,
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
               LEFT JOIN news_impact_assessments_v1 i
                 ON i.annotation_id=a.annotation_id
                AND i.llm_model_version=?
                AND i.prompt_version=?
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
                     AND latest_f.prompt_version='news-json-v14-material-event-evidence'
                     AND NOT (latest_f.error_type='RuntimeError'
                              AND latest_f.error='All configured Gemini keys unavailable for this batch')
                    ORDER BY latest_f.failed_at DESC LIMIT 1)
               LEFT JOIN news_content_failures cf
                 ON cf.failure_id=(
                   SELECT latest_cf.failure_id
                   FROM news_content_failures latest_cf
                   WHERE latest_cf.source=n.source
                     AND latest_cf.source_item_id=n.source_item_id
                     AND latest_cf.revision_number=n.revision_number
                   ORDER BY latest_cf.attempt_number DESC LIMIT 1)
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
                     AND (length(COALESCE(peer.body, '')) > length(COALESCE(n.body, ''))
                          OR (length(COALESCE(peer.body, '')) = length(COALESCE(n.body, ''))
                              AND peer.source_item_id < n.source_item_id)))
                 -- The public reader is not the immutable intake ledger.  It
                 -- contains only readable evidence with a declared research
                 -- role; headline-only and COLLECT_ONLY intake candidates stay
                 -- out of the payload and therefore cannot accumulate online.
                 AND length(trim(COALESCE(n.body, ''))) >= 240
               -- Reader chronology follows the publisher clock.  First-seen
               -- remains the immutable point-in-time visibility clock.
               ORDER BY COALESCE(n.source_published_time,
                                 n.collector_first_seen_time) DESC,
                        n.collector_first_seen_time DESC,
                        n.source, n.source_item_id
               LIMIT 1000""",
            (
                now.isoformat(timespec="microseconds"), INVALID_CHINESE_TITLE,
                IMPACT_MODEL, IMPACT_PROMPT_VERSION,
            ),
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
                           AND (cf.failure_id IS NULL OR cf.is_terminal=0)
                          THEN 1 ELSE 0 END) AS waiting_content,
                 sum(CASE WHEN length(trim(COALESCE(n.body, ''))) < 240
                           AND cf.is_terminal=1
                          THEN 1 ELSE 0 END) AS unavailable_content
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
                       'news-json-v12-gemini-story-identity',
                       'news-json-v11-gemini-story-subjects',
                       'news-json-v10-controlled-category-zh',
                       'news-json-v9-local-display-recovery')
                   ORDER BY CASE preferred_a.prompt_version
                     WHEN 'news-json-v12-gemini-story-identity' THEN 0
                     WHEN 'news-json-v11-gemini-story-subjects' THEN 1
                     WHEN 'news-json-v10-controlled-category-zh' THEN 2 ELSE 3 END,
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
                     AND latest_f.prompt_version='news-json-v12-gemini-story-identity'
                     AND NOT (latest_f.error_type='RuntimeError'
                              AND latest_f.error='All configured Gemini keys unavailable for this batch')
                   ORDER BY latest_f.failed_at DESC LIMIT 1)
               LEFT JOIN news_content_failures cf
                 ON cf.failure_id=(
                   SELECT latest_cf.failure_id
                   FROM news_content_failures latest_cf
                   WHERE latest_cf.source=n.source
                     AND latest_cf.source_item_id=n.source_item_id
                     AND latest_cf.revision_number=n.revision_number
                   ORDER BY latest_cf.attempt_number DESC LIMIT 1)
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
        # The displayed queue is the worker's real claimable queue, not a
        # second approximation tied to an older prompt version.
        claimable_annotations = pending_annotation_records(connection, limit=100_000)
        claimable_annotation_count = len(claimable_annotations)
        claimable_annotation_keys = {
            (
                str(row["source"]),
                str(row["source_item_id"]),
                int(row["revision_number"]),
            )
            for row in claimable_annotations
        }
        completed_annotation_count = len(
            completed_annotation_records(connection, limit=100_000)
        )
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
        learning, execution_learning = _learning_surfaces(connection)
        market_chart = _recent_market_chart(database, connection, now)
        component_times = {
            "quote_bridge": _latest_quote_received(database),
            "decision_collector": connection.execute("SELECT max(created_at) FROM decision_events").fetchone()[0],
            "outcome_settler": connection.execute("SELECT max(appended_at) FROM outcomes").fetchone()[0],
            "news_collector": connection.execute("SELECT max(fetched_time) FROM source_polls").fetchone()[0],
            "gemini_annotator": connection.execute("SELECT max(parsed_at) FROM news_annotations").fetchone()[0],
        }
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        news_source_health = _news_source_health(connection, now)
        monitored_news_sources = {
            row["source"] for row in news_source_health if row["health"] == "HEALTHY"
        }
        all_news_evidence = event_evidence_rows_from_connection(connection, now)
        event_graph = temporal_event_graph(all_news_evidence)
        storylines = event_graph["stories"]
        evidence_grades = Counter(
            row["evidence_grade"] for row in all_news_evidence
        )
        evidence_topics = Counter(
            topic for row in all_news_evidence for topic in row["topics"]
        )
        news_evidence = _news_evidence_display_rows(connection, all_news_evidence)
        raw_article_revisions = connection.execute(
            "SELECT count(*) FROM news_revisions"
        ).fetchone()[0]
        distinct_articles = connection.execute(
            "SELECT count(*) FROM (SELECT DISTINCT source,source_item_id FROM news_revisions)"
        ).fetchone()[0]
        decision_event_exposures = connection.execute(
            "SELECT count(*) FROM news_decision_event_snapshots_v1"
        ).fetchone()[0]
    finally:
        connection.close()

    latest_data = dict(latest) if latest else None
    if latest_data:
        latest_data.pop("decision_id", None)
    research_forecast = dict(latest_prediction) if latest_prediction else None
    if research_forecast is not None:
        research_forecast["signal_expiry_seconds"] = 20
        research_forecast["forecast_horizon_seconds"] = 30 * 60
        ev_long = research_forecast.get("ev_long_u5")
        ev_short = research_forecast.get("ev_short_u5")
        research_forecast["directional_bias"] = (
            "LONG" if ev_long is not None and ev_short is not None and ev_long > ev_short
            else "SHORT" if ev_long is not None and ev_short is not None and ev_short > ev_long
            else "NEUTRAL"
        )
        research_forecast["frozen_record"] = True
    u5_values = sorted(float(row["u5"]) for row in u5_rows)
    current_u5 = float(latest["u5"]) if latest and latest["u5"] is not None else None
    u5_percentile = None
    if current_u5 is not None and u5_values:
        u5_percentile = round(
            100.0 * sum(value <= current_u5 for value in u5_values) / len(u5_values), 1
        )
    u5_context = {
        "percentile": u5_percentile,
        "samples": len(u5_values),
        "label": (
            "高波动" if u5_percentile is not None and u5_percentile >= 85 else
            "偏高" if u5_percentile is not None and u5_percentile >= 60 else
            "一般" if u5_percentile is not None and u5_percentile >= 25 else
            "低波动" if u5_percentile is not None else "等待样本"
        ),
    }
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
    market_session = (
        "OPEN" if online else
        "WEEKLY_CLOSED" if expected_weekly_closure(now) else
        "DATA_UNAVAILABLE"
    )
    clock_skew_seconds = None
    if latest and latest["source_event_time"] and latest["source_received_time"]:
        clock_skew_seconds = (
            datetime.fromisoformat(latest["source_event_time"])
            - datetime.fromisoformat(latest["source_received_time"])
        ).total_seconds()

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
    annotator_status_file = database.parent / "news-annotator-status.json"
    if annotator_status_file.exists():
        try:
            annotator_status = json.loads(
                annotator_status_file.read_text(encoding="utf-8")
            )
            component_times["gemini_annotator"] = annotator_status.get(
                "last_success"
            ) or component_times["gemini_annotator"]
        except (OSError, json.JSONDecodeError):
            pass
    backup_files = sorted((database.parent / "backups").glob("*.sqlite3"), key=lambda p: p.stat().st_mtime)
    backup_time = datetime.fromtimestamp(backup_files[-1].stat().st_mtime, UTC).isoformat() if backup_files else None
    component_times["sites_synchronizer"] = sync_time
    component_times["sqlite_backup"] = backup_time
    sites_sync_component = component(
        "sites_synchronizer", 120, sync_status.get("last_error")
    )
    degraded_resources = sync_status.get("degraded_resources") or []
    if (
        sites_sync_component["status"] == "OK"
        and sync_status.get("status") == "DEGRADED"
    ):
        sites_sync_component["status"] = "WARN"
        sites_sync_component["last_error"] = "; ".join(
            f"{row.get('resource')}: {row.get('error')}"
            for row in degraded_resources
            if isinstance(row, dict)
        )[:500]

    def serialize_row(row: sqlite3.Row) -> dict:
        item = dict(row)
        item["features"] = json.loads(item.pop("features_json"))
        item["outcome_reason_codes"] = json.loads(
            item.pop("outcome_reason_codes_json") or "[]"
        )
        item["predictions"] = predictions_by_decision.get(item["decision_id"], [])
        return item

    news = []
    seen_news_links = set()
    for row in news_rows:
        item = dict(row)
        source_tier, _, _, _ = SOURCE_RULES.get(
            str(item.get("source") or ""),
            ("COLLECT_ONLY", True, 200, "unlisted source"),
        )
        # SOURCE_RULES is the runtime authority.  The SQLite rule ledger is an
        # audit mirror and can legitimately lag a freshly deployed rule
        # version; treating a missing mirror row as COLLECT_ONLY made every
        # readable item disappear after a policy update.
        item["source_eligibility"] = source_tier
        if source_tier == "COLLECT_ONLY":
            continue
        item["model_visibility"] = (
            "IMPACT_PENDING"
            if source_tier == "MODEL_ELIGIBLE" and item.get("parsed_at")
            else "NOT_YET_PARSED"
            if source_tier == "MODEL_ELIGIBLE"
            else source_tier
        )
        annotation_key = (
            str(item.get("source") or ""),
            str(item.get("source_item_id") or ""),
            int(item.get("revision_number") or 0),
        )
        if item.get("parsed_at"):
            # Legacy-compatible annotations are also complete even when the
            # current-version LEFT JOIN did not populate a.annotation_id.
            item["annotation_status"] = "READY"
        elif (
            item.get("annotation_status") == "QUEUED"
            and annotation_key not in claimable_annotation_keys
        ):
            # A readable row can be retained for the reader without being a
            # job the current worker is allowed to claim (duplicate, archival,
            # or discovered too late). Calling it QUEUED is misleading.
            item["annotation_status"] = "NOT_REQUIRED"
            reason_code, reason = _not_required_reason(item, epoch)
            item["annotation_reason_code"] = reason_code
            item["annotation_reason"] = reason
        if source_tier == "MODEL_ELIGIBLE":
            _apply_impact_status(item, now)
        # The reader page shows every retained article with readable source
        # content. Parsing is a separate state: an unparsed article remains
        # visible for audit, but model_visibility prevents it from entering
        # any feature snapshot until the annotation has completed.
        if item.get("content_fetch_status") != "AVAILABLE":
            continue
        published_at = (
            datetime.fromisoformat(item["source_published_time"])
            if item.get("source_published_time") else None
        )
        allowed, _ = google_news_item_is_relevant(
            str(item["source"]), str(item.get("original_headline") or ""),
            published_at, now,
        )
        if not allowed:
            continue
        dedupe_key = item.get("link") or (
            item["source"],
            item["source_item_id"],
        )
        if dedupe_key in seen_news_links:
            continue
        seen_news_links.add(dedupe_key)
        item["entities"] = json.loads(item.pop("entities_json")) if item.get("entities_json") else []
        secondary_categories_json = item.pop("secondary_categories_json", None)
        item["secondary_categories"] = (
            json.loads(secondary_categories_json)
            if secondary_categories_json else []
        )
        item["category"] = _news_category(item)
        item["eligibility_version"] = "news-source-eligibility-v4-live-delay-materiality"
        news.append(item)
        if len(news) >= 200:
            break
    counts["latest_news_items"] = len(news)
    counts["readable_news_items"] = len(news)
    counts["parsed_news_items"] = sum(
        1 for item in news if item.get("parsed_at")
    )
    counts["model_candidate_news_items"] = sum(
        1 for item in news if item.get("model_visibility") == "MODEL_VISIBLE"
    )
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
    quote_component = component("quote_bridge", 30)
    decision_component = component("decision_collector", 420)
    outcome_component = component("outcome_settler", 420)
    if market_session == "WEEKLY_CLOSED":
        for market_component in (
            quote_component, decision_component, outcome_component,
        ):
            market_component["status"] = "MARKET_CLOSED"
            market_component["last_error"] = None

    return {
        "generated_at": now.isoformat(),
        "forward_epoch": epoch,
        "system": {
            "online": online,
            "market_session": market_session,
            "deployment": {
                **DEPLOYMENT_PROVENANCE,
                "payload_generated_at": now.isoformat(),
                "source_database_epoch": epoch,
            },
            "quote_age_seconds": age_seconds,
            "mode": "SHADOW",
            "trading_enabled": False,
            "symbol": "XAUUSD",
            "source_of_truth": "Local append-only SQLite",
            "sites_mirror": "read-only materialized display mirror",
            "components": {
                "quote_bridge": quote_component,
                "system_clock": {
                    "last_success": latest["source_received_time"] if latest else None,
                    "age_seconds": abs(clock_skew_seconds) if clock_skew_seconds is not None else None,
                    "status": (
                        "OK" if clock_skew_seconds is not None and abs(clock_skew_seconds) <= 5
                        else "WARN" if clock_skew_seconds is not None and abs(clock_skew_seconds) <= 20
                        else "ERROR"
                    ),
                    "last_error": (
                        None if clock_skew_seconds is not None and abs(clock_skew_seconds) <= 5
                        else f"偏差 {abs(clock_skew_seconds):.2f} 秒；仍在20秒样本隔离上限内，不影响当前评分。请用管理员 PowerShell 启动 Windows Time 并强制同步"
                        if clock_skew_seconds is not None else "尚无报价时钟样本"
                    ),
                },
                "decision_collector": decision_component,
                "outcome_settler": outcome_component,
                "news_collector": component("news_collector", 300),
                "gemini_annotator": component("gemini_annotator", 900),
                "sites_synchronizer": sites_sync_component,
                "sqlite_backup": component("sqlite_backup", 172800),
                "integrity_check": {"last_success": now.isoformat(), "age_seconds": 0,
                                    "status": "OK" if integrity == "ok" else "ERROR",
                                    "last_error": None if integrity == "ok" else integrity},
            },
        },
        "latest": latest_data,
        "research_forecast": research_forecast,
        "u5_context": u5_context,
        "counts": counts,
        "outcome_summary": dict(valid),
        "recent_decisions": [serialize_row(row) for row in recent],
        "recent_news": news,
        "news_evidence": news_evidence,
        "storylines": storylines[:20],
        "market_narrative_candidates": event_graph["market_narrative_candidates"][:20],
        "archived_storylines": event_graph["archived_storylines"][:20],
        "archived_story_event_candidates": event_graph["archived_event_candidates"][:50],
        "story_event_candidates": event_graph["event_candidates"][:50],
        "market_reaction_streams": event_graph["market_reaction_streams"],
        "theme_streams": event_graph["theme_streams"],
        "unassigned_story_events": event_graph["unassigned_events"][:50],
        "storyline_summary": {
            "policy_version": STORYLINE_POLICY_VERSION,
            "legacy_policy_status": event_graph["legacy_policy_status"],
            "total": len(storylines),
            "market_narrative_total": len(event_graph["market_narrative_candidates"]),
            "archived_total": len(event_graph["archived_storylines"]) + len(event_graph["archived_event_candidates"]),
            "candidate_total": len(event_graph["event_candidates"]),
            "market_stream_total": len(event_graph["market_reaction_streams"]),
            "theme_total": len(event_graph["theme_streams"]),
            "unassigned_total": len(event_graph["unassigned_events"]),
            "display_only": True,
        },
        "news_evidence_summary": {
            "policy_version": EVIDENCE_POLICY_VERSION,
            "raw_article_revisions": raw_article_revisions,
            "distinct_articles": distinct_articles,
            "decision_event_exposures": decision_event_exposures,
            "total_events": len(all_news_evidence),
            "displayed_events": len(news_evidence),
            "broad_model_eligible": sum(
                int(row["broad_model_eligible"]) for row in all_news_evidence
            ),
            "model_seen_events": sum(int(row["model_seen"]) for row in news_evidence),
            "model_unseen_events": sum(int(not row["model_seen"]) for row in news_evidence),
            "frozen_model_uses": sum(int(row["frozen_model_uses"]) for row in news_evidence),
            # Reuse the learning contract's sole row/event calculation. These
            # compact fields survive the PR preview bundle even when the heavy
            # learning-curve payload is intentionally removed.
            "current_contract_exposed_rows": int(
                learning.get("news_contract_transition", {}).get(
                    "current_contract_exposed_rows", 0
                )
            ),
            "current_contract_distinct_events": int(
                learning.get("news_contract_transition", {}).get(
                    "current_contract_distinct_events", 0
                )
            ),
            "grades": dict(evidence_grades),
            "topics": dict(evidence_topics),
        },
        "news_feature_policy": {
            "maximum_current_age_hours": 72,
            "freshness_half_life_hours": 6,
            "historical_training_rows_retained": True,
            "point_in_time_cutoff": True,
        },
        "news_source_health": news_source_health,
        "annotation_queue": {
            "ready": completed_annotation_count,
            "queued": claimable_annotation_count,
            "backing_off": int(annotation_queue["backing_off"] or 0),
            "dead_letter": int(annotation_queue["dead_letter"] or 0),
            "waiting_content": int(annotation_queue["waiting_content"] or 0),
            "unavailable_content": int(annotation_queue["unavailable_content"] or 0),
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
        "execution_learning": execution_learning,
        "market_chart": market_chart,
        "factor_coverage": factor_coverage(
            latest_macro, collected_news_sources, monitored_news_sources,
        ),
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
        parsed = urllib.parse.urlsplit(self.path)
        path = parsed.path.rstrip("/")
        if path == "/api/health":
            body = b'{"status":"OK"}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if path == "/api/market-history":
            query = urllib.parse.parse_qs(parsed.query)
            after = (query.get("after") or [None])[0]
            try:
                limit = min(
                    MARKET_HISTORY_PAGE_LIMIT,
                    max(1, int((query.get("limit") or [MARKET_HISTORY_PAGE_LIMIT])[0])),
                )
                connection = sqlite3.connect(
                    f"file:{self.database}?mode=ro", uri=True, timeout=5,
                )
                connection.row_factory = sqlite3.Row
                try:
                    payload = _market_history_page(
                        self.database, connection, after, limit,
                    )
                finally:
                    connection.close()
                body = json.dumps(payload, allow_nan=False).encode()
                self.send_response(200)
            except (OSError, sqlite3.Error, TypeError, ValueError) as error:
                body = json.dumps({"error": str(error)[:500]}).encode()
                self.send_response(400)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if path != "/api/status":
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
