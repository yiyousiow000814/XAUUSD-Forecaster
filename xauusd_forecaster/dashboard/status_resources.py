"""Dashboard-owned current status and optional read resources."""

from __future__ import annotations

import json
import sqlite3
import subprocess
import threading
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from xauusd_forecaster.ai.provider_registry import (
    AI_QUOTA_SURFACES,
    GEMINI_EMBEDDING_REQUESTS_PER_DAY_PER_ACCOUNT,
)
from xauusd_forecaster.news.annotation.product import (
    DEFAULT_GEMINI_MODEL,
    DEFAULT_GEMMA_MODEL,
    FALLBACK_GEMINI_MODEL,
    GEMMA_REQUESTS_PER_DAY_PER_KEY,
    GEMMA_SAFE_INPUT_TOKENS_PER_MINUTE_TOTAL,
    GEMMA_SAFE_REQUESTS_PER_MINUTE_TOTAL,
    GEMINI_DAILY_PRIORITY_RESERVE,
    GEMINI_REQUESTS_PER_MINUTE_PER_KEY,
    GEMINI_SAFE_INPUT_TOKENS_PER_MINUTE_TOTAL,
    INVALID_CHINESE_TITLE,
    PROMPT_VERSION,
    pending_annotation_records,
)
from xauusd_forecaster.news.semantics.critical_state import annotation_queue_snapshot
from xauusd_forecaster.news.brief.product import daily_brief_summary, recent_daily_briefs
from xauusd_forecaster.dashboard.health_projection import (
    _collector_component,
    _decision_collector_component,
    _materialized_semantic_health,
    _semantic_pipeline_component,
)
from xauusd_forecaster.dashboard.market_resources import _recent_market_chart
from xauusd_forecaster.dashboard.news_resources import (
    _news_evidence_display_rows,
    _news_metrics,
    _serialize_news_rows,
)
from xauusd_forecaster.dashboard.resource_contracts import (
    _learning_summary,
    market_chart_snapshot,
)
from xauusd_forecaster.dashboard_payloads import audit_status_payload, bounded_evidence_window
from xauusd_forecaster.dashboard_summaries import (
    dashboard_collected_news_sources,
    dashboard_distinct_article_count,
    dashboard_latest_activity,
    dashboard_latest_macro,
    dashboard_macro_source_summary,
    dashboard_news_source_summary,
    dashboard_source_poll_summary,
    dashboard_table_counts,
    dashboard_total_brief_days,
    dashboard_valid_outcome_summary,
)
from xauusd_forecaster.execution_learning import execution_learning_status
from xauusd_forecaster.factors import (
    FACTOR_COVERAGE_MACRO_SERIES,
    FACTOR_COVERAGE_NEWS_SOURCES,
    factor_coverage,
)
from xauusd_forecaster.ai.quota import GeminiQuotaLedger
from xauusd_forecaster.learning_curves import learning_curve_payload
from xauusd_forecaster.market_session import expected_weekly_closure
from xauusd_forecaster.ai.model_limits import GEMMA_PROVIDER_LANES_PER_ACCOUNT
from xauusd_forecaster.news.semantics.model_contracts import CURRENT_NEWS_CONTRACT
from xauusd_forecaster.news.semantics.evidence import EVIDENCE_POLICY_VERSION, event_evidence_rows_from_connection
from xauusd_forecaster.news.semantics.features import COLLECTION_SOURCES
from xauusd_forecaster.news.retrieval.identity import preferred_cluster_peer_predicate
from xauusd_forecaster.news.annotation.impact import (
    HANDOVER_IMPACT_PROMPT_VERSION,
    IMPACT_MODEL,
    IMPACT_PROMPT_VERSION,
)
from xauusd_forecaster.news.scheduler.health import news_semantic_pipeline_health
from xauusd_forecaster.news.semantics.relevance import GOOGLE_NEWS_MAX_AGE
from xauusd_forecaster.news.scheduler.state import account_quota_snapshot, configured_api_credentials
from xauusd_forecaster.news.semantics.contracts import model_usable_annotation_predicate
from xauusd_forecaster.news.collection.source_registry import NEWS_SOURCE_REGISTRY
from xauusd_forecaster.operational_health import extend_with_component_alerts, scheduler_health_snapshot
from xauusd_forecaster.production_shape import production_contract_snapshot
from xauusd_forecaster.news.collection.source_polling import source_poll_recovery_state
from xauusd_forecaster.news.annotation.storylines import STORYLINE_POLICY_VERSION, temporal_event_graph


MODULE_ROOT = Path(__file__).resolve().parents[2]
UTC = timezone.utc
PAYLOAD_SCHEMA_VERSION = "xauusd-dashboard-v4-event-episode"
U5_CONTEXT_SAMPLE_LIMIT = 2_016


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


def _news_source_health(connection: sqlite3.Connection, now: datetime) -> list[dict]:
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


def _latest_decision_created_at(database: Path) -> str | None:
    """Read decision cadence from a new SQLite snapshot at classification time."""
    connection = sqlite3.connect(
        f"file:{database}?mode=ro", uri=True, timeout=5,
    )
    try:
        return connection.execute(
            """SELECT activity_time FROM dashboard_latest_activity_v1
               WHERE activity_name='decision_events'"""
        ).fetchone()[0]
    finally:
        connection.close()


def _runtime_heartbeat(path: Path, *, service: str) -> dict[str, object]:
    """Read one supervised loop heartbeat without treating output as liveness."""
    if not path.exists():
        return {}
    try:
        item = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(item, dict):
            return {}
        if item.get("service") != service:
            return {}
        return item
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return {}


def _broker_market_session(database: Path, now: datetime) -> dict | None:
    path = database.parent / "quotes" / "market-session.json"
    if not path.exists():
        return None
    try:
        item = json.loads(path.read_text(encoding="utf-8"))
        if item.get("schema") != "xauusd.forward.market-session.v1":
            return None
        if str(item.get("symbol", "")).casefold() != "xauusd":
            return None
        observed_at = datetime.fromisoformat(
            str(item["observed_at"]).replace("Z", "+00:00")
        )
        age = (now - observed_at).total_seconds()
        if age < -5 or age > 20:
            return None
        session = {
            "is_open": bool(item["is_open"]),
            "observed_at": observed_at.isoformat(),
            "next_open_time": item.get("next_open_time"),
            "next_close_time": item.get("next_close_time"),
        }
        for field in ("opened_at", "first_quote_after_open_at"):
            if item.get(field) is not None:
                session[field] = item[field]
        return session
    except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError):
        return None


def _market_session_status(
    broker_session: dict | None,
    *,
    online: bool,
    now: datetime,
) -> str:
    """Classify expected weekend silence without weakening open-market gates."""
    if broker_session is not None:
        if not broker_session["is_open"]:
            return "CLOSED"
        return "OPEN" if online else "DATA_UNAVAILABLE"
    if not online and expected_weekly_closure(now):
        return "WEEKLY_CLOSED"
    return "DATA_UNAVAILABLE"


def _market_session_observed_at(
    broker_session: dict | None,
    *,
    market_session: str,
    now: datetime,
) -> str | None:
    if broker_session is not None:
        return str(broker_session["observed_at"])
    if market_session == "WEEKLY_CLOSED":
        return now.isoformat()
    return None








def _dashboard_payload(
    database: Path, *, clock=None, include_optional: bool = True,
    optional_resources: frozenset[str] | None = None,
) -> dict:
    clock = clock or (lambda: datetime.now(UTC))
    now = clock()
    def wants(resource: str) -> bool:
        return include_optional and (
            optional_resources is None or resource in optional_resources
        )

    include_audit = wants("audit")
    include_learning = wants("learning") or include_audit
    include_market_chart = wants("market_chart")
    include_news_evidence = wants("news_evidence") or include_audit
    credentials = configured_api_credentials()
    gemini_keys = tuple(credential.api_key for credential in credentials)
    gemini_account_count = len({
        credential.account_id for credential in credentials
    })
    scheduler_quotas = None
    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True, timeout=5)
    connection.row_factory = sqlite3.Row
    connection.execute("BEGIN")
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
        # The annotator heartbeat is a mutable runtime file outside this
        # SQLite snapshot. Sample semantic health at the same boundary as the
        # fixed observation clock, before optional evidence aggregation can
        # make a later heartbeat look like future-dated evidence.
        current_semantic_health = (
            news_semantic_pipeline_health(
                SimpleNamespace(connection=connection, path=database),
                observed_at=now,
            )
            if include_optional else _materialized_semantic_health(
                connection, str(latest["decision_id"]) if latest else None,
            )
        )
        latest_prediction = None
        latest_news_input_coverage = None
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
            coverage_table = connection.execute(
                """SELECT 1 FROM sqlite_master
                   WHERE type='table' AND name='news_input_coverage_snapshots_v1'"""
            ).fetchone()
            if coverage_table is not None:
                latest_news_input_coverage = connection.execute(
                    """SELECT state,usable_core_event_count,
                              usable_broad_event_count,
                              unresolved_annotation_count,
                              unresolved_impact_count,recovering_count,
                              terminal_or_overdue_count,
                              operational_reason_codes_json,
                              coverage_reason_codes_json,
                              source_observability_json,source_evidence_hash,
                              snapshot_hash,observed_at
                       FROM news_input_coverage_snapshots_v1
                       WHERE source_decision_id=?""",
                    (latest["decision_id"],),
                ).fetchone()
        u5_rows = connection.execute(
            """SELECT u5 FROM market_snapshots
               WHERE u5_status='READY' AND u5 IS NOT NULL
               ORDER BY decision_time DESC LIMIT ?""",
            (U5_CONTEXT_SAMPLE_LIMIT,),
        ).fetchall()
        recent = connection.execute(
            """SELECT d.decision_id, d.decision_time, d.effective_action, d.data_health,
                      s.bid, s.ask, s.spread,
                      o.outcome_status,
                      o.reason_codes_json AS outcome_reason_codes_json,
                      o.long_return, o.short_return,
                      (SELECT p.recommended_action FROM predictions_v2 p
                       JOIN model_updates_v2 u USING(model_version)
                       WHERE p.source_decision_id=d.decision_id
                         -- Prevent the low-cardinality identity/time index from
                         -- turning this fixed 18-row lookup into a historical
                         -- BROAD_FULL scan. The primary key owns decision-local
                         -- prediction lookup; identity remains a filter.
                         AND +p.model_identity='BROAD_FULL'
                       ORDER BY u.created_at DESC LIMIT 1) AS research_action,
                      (SELECT p.prediction_status FROM predictions_v2 p
                       JOIN model_updates_v2 u USING(model_version)
                       WHERE p.source_decision_id=d.decision_id
                         AND +p.model_identity='BROAD_FULL'
                       ORDER BY u.created_at DESC LIMIT 1) AS research_status
               FROM decision_events d
               JOIN market_snapshots s USING(snapshot_id)
               LEFT JOIN outcomes o USING(decision_id)
               ORDER BY d.decision_time DESC LIMIT 18"""
        ).fetchall() if not include_audit else connection.execute(
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
        counts = dashboard_table_counts(connection)
        decision_ids = [row["decision_id"] for row in recent] if include_audit else []
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
                f"""SELECT n.source, n.source_item_id, n.revision_number,
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
                       json_extract(a.annotation_json, '$.xauusd_relevance') AS xauusd_relevance,
                       json_extract(a.annotation_json, '$.event_time') AS event_time,
                      a.event_type, a.entities_json, a.hawkishness,
                      a.inflation_impulse, a.growth_impulse,
                      a.geopolitical_risk, a.usd_impulse, a.novelty,
                      a.confidence, a.llm_model_version, a.prompt_version,
                       a.parsed_at,
                       CASE WHEN f.failure_id IS NOT NULL THEN COALESCE(
                         fe.failure_code,
                         CASE WHEN f.error_type='ValueError'
                           THEN 'MODEL_OUTPUT_CONTRACT_FAILED'
                           ELSE 'MODEL_REQUEST_FAILED' END)
                       END AS annotation_failure_code,
                       f.error AS annotation_failure,
                       i.impact_class,
                       i.event_state AS impact_event_state,
                       i.update_type AS impact_update_type,
                       i.confidence AS impact_confidence,
                       i.reason_zh AS impact_reason_zh,
                       i.assessed_at AS impact_assessed_at,
                       CASE WHEN n.source_published_time IS NOT NULL THEN
                         (julianday(n.collector_first_seen_time)-julianday(n.source_published_time))*86400
                       END AS collection_delay_seconds,
                       CASE WHEN a.parsed_at IS NOT NULL THEN
                         (julianday(a.parsed_at)-
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
                     AND latest_f.prompt_version=?
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
                 -- The public reader is not the immutable intake ledger.  It
                 -- contains only readable evidence with a declared research
                 -- role; headline-only and COLLECT_ONLY intake candidates stay
                 -- out of the payload and therefore cannot accumulate online.
                 AND length(trim(COALESCE(n.body, ''))) >= 240
                 AND COALESCE(
                       json_extract(a.annotation_json, '$.xauusd_relevance'), ''
                     ) <> 'IRRELEVANT'
               -- Reader chronology follows the publisher clock.  First-seen
               -- remains the immutable point-in-time visibility clock.
               ORDER BY COALESCE(n.source_published_time,
                                 n.collector_first_seen_time) DESC,
                        n.collector_first_seen_time DESC,
                        n.source, n.source_item_id
               LIMIT 1000""",
            (
                now.isoformat(timespec="microseconds"), INVALID_CHINESE_TITLE,
                PROMPT_VERSION,
                IMPACT_MODEL, IMPACT_PROMPT_VERSION,
                HANDOVER_IMPACT_PROMPT_VERSION, IMPACT_PROMPT_VERSION,
                PROMPT_VERSION,
            ),
        ).fetchall() if include_audit else []
        annotation_queue = annotation_queue_snapshot(
            connection,
            prompt_version=PROMPT_VERSION,
            observed_at=now.isoformat(timespec="microseconds"),
        )
        if include_audit:
            claimable_annotations = pending_annotation_records(
                connection, limit=100_000,
            )
            claimable_annotation_keys = {
                (
                    str(row["source"]),
                    str(row["source_item_id"]),
                    int(row["revision_number"]),
                )
                for row in claimable_annotations
            }
        else:
            claimable_annotation_keys = set()
        model_rows = connection.execute(
            """SELECT model_identity, model_version, created_at,
                      training_cutoff, hyperparameters_json, artifact_hash
               FROM model_updates ORDER BY training_cutoff DESC,
                                           model_identity"""
        ).fetchall() if include_learning else []
        valid = dashboard_valid_outcome_summary(connection)
        epoch = connection.execute(
            "SELECT value FROM runtime_metadata WHERE key='FORWARD_EPOCH'"
        ).fetchone()[0]
        latest_macro = dashboard_latest_macro(
            connection, tuple(sorted(FACTOR_COVERAGE_MACRO_SERIES)),
        )
        collected_news_sources = dashboard_collected_news_sources(
            connection, tuple(sorted(FACTOR_COVERAGE_NEWS_SOURCES)),
        )
        if include_learning:
            learning, execution_learning = _learning_surfaces(connection)
            counts["live_oos_model_groups"] = len({
                str(row.get("model_identity") or "")
                for row in learning.get("models", [])
                if row.get("active_rank") is not None and row.get("model_identity")
            })
        else:
            active_generation = connection.execute(
                """SELECT generation_id
                   FROM news_model_generation_activations_v1
                   ORDER BY activated_at DESC,activation_id DESC LIMIT 1"""
            ).fetchone()
            if active_generation is not None:
                counts["live_oos_model_groups"] = int(connection.execute(
                    """SELECT count(DISTINCT model_identity) FROM (
                         SELECT model_identity
                         FROM news_model_generation_members_v1
                         WHERE generation_id=?
                         UNION ALL
                         SELECT model_identity
                         FROM news_model_generation_aux_members_v1
                         WHERE generation_id=?
                       )""",
                    (active_generation["generation_id"], active_generation["generation_id"]),
                ).fetchone()[0])
            else:
                counts["live_oos_model_groups"] = int(connection.execute(
                    "SELECT count(DISTINCT model_identity) FROM model_updates_v2"
                ).fetchone()[0])
            complete = int(counts["training_eligibility_v2"])
            learning = {
                "models": [],
                "next_training_threshold": (
                    96 if complete < 96 else 200 if complete < 200
                    else ((complete // 50) + 1) * 50
                ),
                "news_contract_transition": {},
            }
            execution_learning = {}
        market_chart = (
            _recent_market_chart(database, connection, now)
            if include_market_chart else {}
        )
        latest_activity = dashboard_latest_activity(connection)
        latest_news_poll = latest_activity.get("source_polls")
        latest_decision_time = latest_activity.get("decision_events")
        component_times = {
            "quote_bridge": _latest_quote_received(database),
            # The collector invokes the settler on every successful loop. No
            # newly appended outcome is expected until a decision reaches its
            # 30-minute horizon, so output recency is not worker health.
            "outcome_settler": latest_activity.get("outcomes"),
            "news_collector": None,
            "gemini_annotator": latest_activity.get("news_annotations"),
        }
        news_source_health = _news_source_health(connection, now)
        monitored_news_sources = {
            row["source"] for row in news_source_health if row["health"] == "HEALTHY"
        }
        if include_news_evidence:
            all_news_evidence = event_evidence_rows_from_connection(connection, now)
            event_graph = temporal_event_graph(all_news_evidence)
            storylines = event_graph["stories"]
            evidence_grades = Counter(
                row["evidence_grade"] for row in all_news_evidence
            )
            evidence_topics = Counter(
                topic for row in all_news_evidence for topic in row["topics"]
            )
            auditable_news_events = _news_evidence_display_rows(
                connection, all_news_evidence
            )
        else:
            all_news_evidence = []
            event_graph = {
                "stories": [], "market_narrative_candidates": [],
                "archived_storylines": [], "archived_event_candidates": [],
                "event_candidates": [], "market_reaction_streams": [],
                "theme_streams": [], "unassigned_events": [],
                "legacy_policy_status": "OPTIONAL_RESOURCE",
            }
            storylines = []
            evidence_grades = Counter()
            evidence_topics = Counter()
            auditable_news_events = []
        news_evidence = bounded_evidence_window(auditable_news_events, 60)
        raw_article_revisions = counts["news_revisions"]
        distinct_articles = dashboard_distinct_article_count(connection)
        decision_event_exposures = counts["news_decision_event_snapshots_v1"]
        daily_news_briefs = (
            recent_daily_briefs(connection) if include_audit else []
        )
        daily_news_brief_summary = daily_brief_summary(
            connection,
            now=now,
            total_brief_days=(
                None if include_audit else dashboard_total_brief_days(connection)
            ),
        )
        scheduler_ledger_available = connection.execute(
            """SELECT 1 FROM sqlite_master
               WHERE type='table' AND name='news_ai_account_daily_usage_v1'"""
        ).fetchone() is not None
        if scheduler_ledger_available:
            scheduler_quotas = {
                surface.payload_key: account_quota_snapshot(
                    connection, credentials,
                    model_families=surface.model_families,
                    daily_limit=surface.daily_limit,
                    quota_authority=surface.payload_key,
                    now=now,
                )
                for surface in AI_QUOTA_SURFACES
            }
        production_contract = production_contract_snapshot(
            connection,
            now=now,
            account_ids=frozenset(
                credential.account_id for credential in credentials
            ),
            materialized_latest_decision_time=latest_activity.get("decision_time"),
            use_materialized_latest_decision=True,
        )
        operational_health = scheduler_health_snapshot(connection, now=now)
    finally:
        connection.rollback()
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
    news_input_coverage = (
        dict(latest_news_input_coverage)
        if latest_news_input_coverage is not None else None
    )
    if news_input_coverage is not None:
        for field in (
            "operational_reason_codes_json", "coverage_reason_codes_json",
            "source_observability_json",
        ):
            value = news_input_coverage.pop(field)
            news_input_coverage[field.removesuffix("_json")] = json.loads(value)
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
        "sample_limit": U5_CONTEXT_SAMPLE_LIMIT,
        "scope": "RECENT_READY_WINDOW",
        "label": (
            "高波动" if u5_percentile is not None and u5_percentile >= 85 else
            "偏高" if u5_percentile is not None and u5_percentile >= 60 else
            "一般" if u5_percentile is not None and u5_percentile >= 25 else
            "低波动" if u5_percentile is not None else "等待样本"
        ),
    }
    # Snapshot construction performs bounded but potentially blocking SQLite
    # and evidence work. Refresh the wall clock before validating continuously
    # published runtime heartbeats so a current broker receipt cannot appear to
    # come from the future relative to the snapshot's initial query boundary.
    now = clock()
    # Runtime files are outside the SQLite snapshot. Read them at the current
    # classification boundary, and open a fresh SQLite snapshot for output
    # cadence, so a long status build or hot reload cannot publish false
    # liveness or decision-output incidents.
    collector_heartbeat = _runtime_heartbeat(
        database.parent / "collector-status.json", service="collector",
    )
    component_times["quote_bridge"] = _latest_quote_received(database)
    latest_decision_time = _latest_decision_created_at(database)
    component_times["news_collector"] = collector_heartbeat.get("last_success")
    component_times["outcome_settler"] = (
        collector_heartbeat.get("last_success")
        or component_times["outcome_settler"]
    )
    age_seconds = None
    if component_times["quote_bridge"]:
        age_seconds = max(
            0.0,
            (now - datetime.fromisoformat(component_times["quote_bridge"])).total_seconds(),
        )
    broker_session = _broker_market_session(database, now)
    decision_component = _decision_collector_component(
        collector_heartbeat,
        latest_decision=latest_decision_time,
        decision_observation_start=epoch,
        broker_session=broker_session,
        quote_current=age_seconds is not None and age_seconds <= 30,
        now=now,
    )
    collector_available = (
        collector_heartbeat.get("state") == "RUNNING"
        and decision_component["status"] in {"OK", "WARN"}
    )
    online = bool(
        age_seconds is not None
        and age_seconds <= 30
        and collector_available
        and broker_session is not None
        and broker_session["is_open"]
    )
    market_session = _market_session_status(
        broker_session,
        online=online,
        now=now,
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
    backup_integrity_component = component(
        "sqlite_backup", 172800,
        None if backup_time else "No verified daily SQLite backup is available",
    )
    sites_sync_component = component(
        "sites_synchronizer", 120, sync_status.get("last_error")
    )
    semantic_pipeline_component = _semantic_pipeline_component(
        current_semantic_health, now=now,
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
        if "features_json" in item:
            item["features"] = json.loads(item.pop("features_json"))
        item["outcome_reason_codes"] = json.loads(
            item.pop("outcome_reason_codes_json") or "[]"
        )
        if include_audit:
            item["predictions"] = predictions_by_decision.get(item["decision_id"], [])
        return item

    # The status snapshot remains a small recent page. The complete bounded
    # reader archive is exposed separately by /api/news-archive.
    news = _serialize_news_rows(
        news_rows[:200], now, epoch, claimable_annotation_keys,
    )
    if include_audit:
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
    if latest_data:
        latest_data["features"] = json.loads(latest_data.pop("features_json"))
        latest_data["reason_codes"] = json.loads(latest_data.pop("reason_codes_json"))
    if scheduler_quotas is not None:
        gemini_quota = scheduler_quotas["gemini_quota"]
        gemini_31_quota = scheduler_quotas["gemini_31_quota"]
        gemma_quota = scheduler_quotas["gemma_quota"]
        gemini_embedding_quota = scheduler_quotas["gemini_embedding_quota"]
    else:
        gemini_quota = GeminiQuotaLedger(
            database.parent / "gemini-quota.json"
        ).snapshot(gemini_keys)
        gemini_31_quota = GeminiQuotaLedger(
            database.parent / "gemini-3.1-flash-lite-quota.json"
        ).snapshot(gemini_keys)
        gemma_quota = GeminiQuotaLedger(
            database.parent / "gemma-quota.json",
            daily_limit=GEMMA_REQUESTS_PER_DAY_PER_KEY,
        ).snapshot(gemini_keys)
        gemini_embedding_quota = GeminiQuotaLedger(
            database.parent / "gemini-embedding-2-quota.json",
            daily_limit=GEMINI_EMBEDDING_REQUESTS_PER_DAY_PER_ACCOUNT,
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
    outcome_component = component(
        "outcome_settler", 420,
        str(collector_heartbeat.get("last_error") or "") or None,
    )
    runtime_update_failure = None
    runtime_update_path = database.parent / "runtime-update-state.json"
    if runtime_update_path.exists():
        try:
            runtime_update = json.loads(runtime_update_path.read_text(encoding="utf-8-sig"))
            if runtime_update.get("user_visible_failure") is True:
                runtime_update_failure = {
                    "status": runtime_update.get("update_status"),
                    "failed_at": runtime_update.get("failed_at"),
                }
        except (OSError, ValueError):
            pass

    system_components = {
        "quote_bridge": quote_component,
        "system_clock": {
            "last_success": latest["source_received_time"] if latest else None,
            "age_seconds": (
                abs(clock_skew_seconds)
                if clock_skew_seconds is not None else None
            ),
            "status": (
                "OK" if clock_skew_seconds is not None
                and abs(clock_skew_seconds) <= 5
                else "WARN" if clock_skew_seconds is not None
                and abs(clock_skew_seconds) <= 20
                else "ERROR"
            ),
            "last_error": (
                None if clock_skew_seconds is not None
                and abs(clock_skew_seconds) <= 5
                else f"偏差 {abs(clock_skew_seconds):.2f} 秒；仍在20秒样本隔离上限内，不影响当前评分。请用管理员 PowerShell 启动 Windows Time 并强制同步"
                if clock_skew_seconds is not None
                else "尚无报价时钟样本"
            ),
        },
        "decision_collector": decision_component,
        "outcome_settler": outcome_component,
        "news_collector": _collector_component(
            collector_heartbeat, latest_poll=latest_news_poll, now=now,
        ),
        "gemini_annotator": component("gemini_annotator", 900),
        "news_semantic_pipeline": semantic_pipeline_component,
        "sites_synchronizer": sites_sync_component,
        "sqlite_backup": component("sqlite_backup", 172800),
        # Daily online backups are published only after the complete SQLite
        # integrity check succeeds. Reuse that durable proof.
        "integrity_check": backup_integrity_component,
    }
    if market_session in {"CLOSED", "WEEKLY_CLOSED"}:
        for component_name in (
            "quote_bridge",
            "decision_collector",
            "outcome_settler",
            "news_semantic_pipeline",
        ):
            system_components[component_name]["status"] = "MARKET_CLOSED"
            system_components[component_name]["last_error"] = None
    operational_health = extend_with_component_alerts(
        operational_health,
        components=system_components,
        news_sources=news_source_health,
        runtime_update_failure=runtime_update_failure,
        daily_news_brief=daily_news_brief_summary,
        sync_degraded_resources=[
            row for row in degraded_resources if isinstance(row, dict)
        ],
    )

    return {
        "generated_at": now.isoformat(),
        "production_contract": production_contract,
        "dashboard_sync": sync_status,
        "forward_epoch": epoch,
        "system": {
            "online": online,
            "market_session": market_session,
            "market_session_observed_at": _market_session_observed_at(
                broker_session,
                market_session=market_session,
                now=now,
            ),
            "market_reopens_at": (
                broker_session["next_open_time"] if broker_session else None
            ),
            "market_closes_at": (
                broker_session["next_close_time"] if broker_session else None
            ),
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
            "runtime_update_failure": runtime_update_failure,
            "components": system_components,
        },
        "operational_health": operational_health,
        "latest": latest_data,
        "research_forecast": research_forecast,
        "u5_context": u5_context,
        "counts": counts,
        "outcome_summary": dict(valid),
        "recent_decisions": [serialize_row(row) for row in recent],
        "recent_news": news if include_audit else [],
        "daily_news_briefs": daily_news_briefs,
        "daily_news_brief_summary": daily_news_brief_summary,
        "news_evidence": news_evidence if include_audit else [],
        "storylines": storylines[:20] if include_audit else [],
        "market_narrative_candidates": (
            event_graph["market_narrative_candidates"][:20]
            if include_audit else []
        ),
        "archived_storylines": (
            event_graph["archived_storylines"][:20] if include_audit else []
        ),
        "archived_story_event_candidates": (
            event_graph["archived_event_candidates"][:50]
            if include_audit else []
        ),
        "story_event_candidates": (
            event_graph["event_candidates"][:50] if include_audit else []
        ),
        "market_reaction_streams": (
            event_graph["market_reaction_streams"] if include_audit else []
        ),
        "theme_streams": event_graph["theme_streams"] if include_audit else [],
        "unassigned_story_events": (
            event_graph["unassigned_events"][:50] if include_audit else []
        ),
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
        "news_metrics": _news_metrics(
            counts=counts,
            raw_article_revisions=raw_article_revisions,
            distinct_articles=distinct_articles,
            all_news_evidence=all_news_evidence,
            auditable_events=auditable_news_events,
            decision_event_exposures=decision_event_exposures,
            learning=learning,
        ),
        "news_evidence_summary": {
            "policy_version": EVIDENCE_POLICY_VERSION,
            "raw_article_revisions": raw_article_revisions,
            "distinct_articles": distinct_articles,
            "decision_event_exposures": decision_event_exposures,
            "total_events": len(all_news_evidence),
            "displayed_events": len(auditable_news_events),
            "broad_model_eligible": sum(
                int(row["broad_model_eligible"]) for row in all_news_evidence
            ),
            "model_seen_events": sum(
                int(row["model_seen"]) for row in auditable_news_events
            ),
            "model_unseen_events": sum(
                int(not row["model_seen"]) for row in auditable_news_events
            ),
            "frozen_model_uses": sum(
                int(row["frozen_model_uses"]) for row in auditable_news_events
            ),
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
        "news_input_coverage": news_input_coverage,
        "annotation_queue": {
            "ready": int(annotation_queue["ready"]),
            "semantic_pending": int(annotation_queue["semantic_pending"]),
            "queued": int(annotation_queue["queued"]),
            "backing_off": int(annotation_queue["backing_off"] or 0),
            "dead_letter": int(annotation_queue["dead_letter"] or 0),
            "contract_backfill_queued": int(
                annotation_queue["contract_backfill_queued"] or 0
            ),
            "unclassified_annotation_jobs": int(
                annotation_queue["unclassified_annotation_jobs"] or 0
            ),
            "waiting_content": int(annotation_queue["waiting_content"] or 0),
            "unavailable_content": int(annotation_queue["unavailable_content"] or 0),
            "configured_key_count": len(gemini_keys),
            "configured_account_count": gemini_account_count,
            "available_key_count": available_gemini_keys,
            "fallback_available_key_count": available_fallback_keys,
            "requests_per_minute_per_key": GEMINI_REQUESTS_PER_MINUTE_PER_KEY,
            "requests_per_minute_per_account": GEMINI_REQUESTS_PER_MINUTE_PER_KEY,
            "requests_per_minute": (
                GEMINI_REQUESTS_PER_MINUTE_PER_KEY * gemini_account_count
            ),
            "input_tokens_per_minute": (
                GEMINI_SAFE_INPUT_TOKENS_PER_MINUTE_TOTAL * gemini_account_count
            ),
            "minute_scope": "ACCOUNT",
            "priority_reserve": flash_priority_reserve,
            "routine_remaining": flash_routine_remaining,
        },
        "gemini_quota": gemini_quota,
        "gemini_31_quota": gemini_31_quota,
        "gemma_quota": gemma_quota,
        "gemini_embedding_quota": gemini_embedding_quota,
        "llm_routing": {
            "action_bearing": {
                "model": DEFAULT_GEMINI_MODEL,
                "fallback_model": FALLBACK_GEMINI_MODEL,
                "role": "3.5 优先；普通额度用尽后 3.1 接管完整正文与训练特征",
            },
            "display_only": {
                "model": DEFAULT_GEMMA_MODEL,
                "role": "事件归并与影响说明；低优先级时兼顾中文标题展示",
                "configured_account_count": gemini_account_count,
                "requests_per_minute_per_account": (
                    GEMMA_SAFE_REQUESTS_PER_MINUTE_TOTAL
                ),
                "requests_per_minute": (
                    GEMMA_SAFE_REQUESTS_PER_MINUTE_TOTAL
                    * gemini_account_count
                ),
                "input_tokens_per_minute_per_account": (
                    GEMMA_SAFE_INPUT_TOKENS_PER_MINUTE_TOTAL
                ),
                "input_tokens_per_minute": (
                    GEMMA_SAFE_INPUT_TOKENS_PER_MINUTE_TOTAL
                    * gemini_account_count
                ),
                "provider_lanes_per_account": GEMMA_PROVIDER_LANES_PER_ACCOUNT,
                "maximum_concurrent_requests": (
                    GEMMA_PROVIDER_LANES_PER_ACCOUNT * gemini_account_count
                ),
                "minute_scope": "ACCOUNT",
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


def _optional_resource_payload(database: Path, resource: str) -> dict:
    """Build one optional resource without evaluating sibling producers."""
    payload = _dashboard_payload(
        database, optional_resources=frozenset({resource}),
    )
    if resource == "audit":
        return audit_status_payload(payload)
    if resource == "learning":
        summary = {
            key: payload[key] for key in (
                "generated_at", "counts", "training",
            ) if key in payload
        }
        if isinstance(summary.get("training"), dict):
            summary["training"] = {
                key: value for key, value in summary["training"].items()
                if key != "models"
            }
        summary.update(_learning_summary(payload))
        return summary
    if resource == "market_chart":
        return {
            "generated_at": payload.get("generated_at"),
            "market_chart": json.loads(market_chart_snapshot(payload)),
        }
    raise ValueError(f"unknown optional dashboard resource: {resource}")
