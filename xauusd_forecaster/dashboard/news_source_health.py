"""Read-only health projection for monitored news sources."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

from xauusd_forecaster.dashboard_summaries import (
    dashboard_macro_source_summary,
    dashboard_news_source_summary,
    dashboard_source_poll_summary,
)
from xauusd_forecaster.news_relevance import GOOGLE_NEWS_MAX_AGE
from xauusd_forecaster.news_source_registry import NEWS_SOURCE_REGISTRY
from xauusd_forecaster.source_polling import source_poll_recovery_state


UTC = timezone.utc


def _parse_utc(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _has_recent_evidence(latest_item_time: str | None, now: datetime) -> bool:
    latest_item = _parse_utc(latest_item_time)
    if latest_item is None:
        return False
    age = now - latest_item
    return timedelta(0) <= age <= GOOGLE_NEWS_MAX_AGE


def news_source_health(
    connection: sqlite3.Connection, now: datetime,
) -> list[dict]:
    """Project bounded source-poll evidence into dashboard health rows."""
    rows = []
    for spec in NEWS_SOURCE_REGISTRY:
        source = spec.source
        label = spec.label
        role = spec.role
        stale_minutes = spec.stale_minutes
        revision_sources = spec.revision_sources
        polls = dashboard_source_poll_summary(connection, source)
        freshness_reference = connection.execute(
            """SELECT fetched_time,status FROM source_polls
               WHERE source=? AND status IN ('OK','PARTIAL')
               ORDER BY fetched_time DESC,poll_id DESC LIMIT 1""",
            (source,),
        ).fetchone()
        latest = connection.execute(
            """SELECT fetched_time, status, error_type, error
               FROM source_polls WHERE source=?
               ORDER BY fetched_time DESC, poll_id DESC LIMIT 1""",
            (source,),
        ).fetchone()
        latest_error = connection.execute(
            """SELECT fetched_time,error_type,error,provider_http_status,
                      retry_after_seconds
               FROM source_polls WHERE source=? AND status<>'OK'
               ORDER BY fetched_time DESC, poll_id DESC LIMIT 1""",
            (source,),
        ).fetchone()
        item_count = revision_count = full_text_count = 0
        latest_item_time = None
        if revision_sources:
            evidence = dashboard_news_source_summary(connection, revision_sources)
            item_count = int(evidence["item_count"] or 0)
            revision_count = int(evidence["revision_count"] or 0)
            full_text_count = int(evidence["full_text_count"] or 0)
            latest_item_time = evidence["latest_item_time"]
        elif source == "bls_public_api":
            evidence = dashboard_macro_source_summary(connection, source)
            item_count = int(evidence["item_count"] or 0)
            revision_count = int(evidence["revision_count"] or 0)
            latest_item_time = evidence["latest_item_time"]
        latest_status = latest["status"] if latest else "NO_DATA"
        freshness_reference_time = (
            freshness_reference["fetched_time"] if freshness_reference else None
        )
        freshness_time = _parse_utc(freshness_reference_time)
        age_seconds = (
            max(0.0, (now - freshness_time).total_seconds())
            if freshness_time else None
        )
        recovery = source_poll_recovery_state(
            connection, source, observed_at=now,
        )
        freshness_is_fresh = (
            age_seconds is not None and age_seconds <= stale_minutes * 60
        )
        recovery_mode = recovery["recovery_mode"] if recovery else None
        if recovery_mode == "OPERATOR_ACTION_REQUIRED":
            health = "ERROR"
        elif latest_status in {"ERROR", "PARTIAL"}:
            health = (
                "DEGRADED" if freshness_is_fresh
                else "STALE" if freshness_time is not None
                else "ERROR"
            )
        elif age_seconds is None or not freshness_is_fresh:
            health = "STALE"
        elif revision_sources and item_count == 0:
            health = "WARMING_UP"
        else:
            health = "HEALTHY"
        recent_evidence = _has_recent_evidence(latest_item_time, now)
        if recovery_mode == "OPERATOR_ACTION_REQUIRED":
            semantic_status = "OPERATOR_ACTION_REQUIRED"
            semantic_message = "来源鉴权或配置失败；需要操作员检查凭据与权限"
        elif health == "DEGRADED" and recovery is not None:
            semantic_status = "AUTO_RECOVERING"
            semantic_message = "最新可用数据仍新鲜；传输失败正在按有界退避自动重试"
        elif health in {"ERROR", "STALE"}:
            semantic_status = "SOURCE_ERROR"
            semantic_message = "来源当前轮询失败；请查看最近错误与后备链路状态"
        elif health == "WARMING_UP":
            semantic_status = "NO_RELEASE_CAPTURED"
            semantic_message = "轮询正常，但本机尚未捕获该发布系列的正式条目"
        elif revision_sources and not recent_evidence:
            semantic_status = "NO_RECENT_EVIDENCE"
            semantic_message = "来源轮询正常，但最近 72 小时没有捕获可用资料"
        else:
            semantic_status = "OK"
            semantic_message = None
        rows.append({
            "source": source, "label": label, "role": role, "health": health,
            "latest_status": latest_status,
            "latest_poll_time": latest["fetched_time"] if latest else None,
            "last_success": polls["last_success"],
            "freshness_reference_time": freshness_reference_time,
            "freshness_reference_status": (
                freshness_reference["status"] if freshness_reference else None
            ),
            "age_seconds": age_seconds,
            "last_error_time": latest_error["fetched_time"] if latest_error else None,
            "last_error_type": latest_error["error_type"] if latest_error else None,
            "last_error": latest_error["error"] if latest_error else None,
            "provider_http_status": (
                latest_error["provider_http_status"] if latest_error else None
            ),
            "retry_after_seconds": (
                latest_error["retry_after_seconds"] if latest_error else None
            ),
            "poll_count": int(polls["total"] or 0),
            "ok_count": int(polls["ok_count"] or 0),
            "partial_count": int(polls["partial_count"] or 0),
            "error_count": int(polls["error_count"] or 0),
            "item_count": item_count, "revision_count": revision_count,
            "full_text_count": full_text_count, "latest_item_time": latest_item_time,
            "recent_evidence": recent_evidence,
            "recovery_mode": recovery_mode, "fallback_label": None,
            "fallback_health": None,
            "next_retry_time": (
                recovery["next_retry_at"].isoformat() if recovery else None
            ),
            "semantic_status": semantic_status,
            "semantic_message": semantic_message,
        })
    by_source = {row["source"]: row for row in rows}
    gdelt = by_source.get("gdelt_gold_geopolitics")
    fallback = by_source.get("google_news_gold_context")
    # Historical failures remain visible for audit, but only the current poll
    # may activate a recovery mode. A later successful GKG poll clears the old
    # DOC API 429 instead of leaving the source permanently rate-limited.
    if (
        gdelt and fallback
        and gdelt.get("latest_status") == "ERROR"
        and gdelt.get("recovery_mode") == "RATE_LIMITED"
    ):
        gdelt["fallback_label"] = fallback["label"]
        fallback_ready = bool(
            fallback["health"] == "HEALTHY" and fallback.get("recent_evidence")
        )
        gdelt["fallback_health"] = (
            fallback["health"]
            if fallback.get("recent_evidence") else "NO_RECENT_EVIDENCE"
        )
        if fallback_ready:
            gdelt["health"] = "FALLBACK_ACTIVE"
            gdelt["latest_status"] = "RATE_LIMITED"
    return rows
