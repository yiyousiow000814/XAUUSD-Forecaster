#!/usr/bin/env python
"""Local dashboard API plus the audited scheduler operator bridge."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import hmac
import json
import math
import os
import re
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.parse
from types import SimpleNamespace
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

MODULE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MODULE_ROOT))
from xauusd_forecaster.dashboard_payloads import (
    audit_status_payload,
    critical_status_payload,
)
from xauusd_forecaster.dashboard_read_models import (
    DashboardReadModelOwner,
    DashboardReadModelSnapshot,
    DashboardReadModelUnavailable,
    read_dashboard_read_model,
)
from xauusd_forecaster.critical_annotation_state import annotation_queue_snapshot
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
from xauusd_forecaster.news_projection import (
    NEWS_PROJECTION_CONTRACT_VERSION,
    NEWS_PROJECTION_MAX_ITEMS,
    NewsProjectionGeneration,
    build_news_projection_generation,
    canonicalize_news_projection_impact_clocks,
    receipt_digest,
)
from scripts.run_dashboard_sync import _learning_summary, market_chart_snapshot
from xauusd_forecaster.news_scheduler import (  # noqa: E402
    RetryScheduleConflict,
    apply_retry_schedule_override,
    install_scheduler_schema,
    list_retry_schedule_jobs,
)
from xauusd_forecaster.runtime_paths import (
    authoritative_runtime_root,
    logical_absolute_path,
    runtime_child_path,
)
from xauusd_forecaster.maintenance import (
    BACKUP_RECEIPT_SCHEMA,
    BACKUP_RETENTION_SCHEMA,
    BACKUP_RETENTION_STATE,
)
from xauusd_forecaster.sqlite_wal import (
    FORWARD_WAL_CHECKPOINT_SCHEMA,
    FORWARD_WAL_CHECKPOINT_STATE,
    open_forward_writer_connection,
)
UTC = timezone.utc
PAYLOAD_SCHEMA_VERSION = "xauusd-dashboard-v4-event-episode"
MARKET_DETAIL_CANDLE_LIMIT = 7 * 288
MARKET_OVERVIEW_CANDLE_LIMIT = 480
MARKET_HISTORY_PAGE_LIMIT = 500
NEWS_READER_WINDOW_DAYS = 60
NEWS_ARCHIVE_PAGE_LIMIT = 20
NEWS_EVIDENCE_PAGE_LIMIT = 50
NEWS_EVIDENCE_PAGE_LIMIT_BYTES = 350_000
U5_CONTEXT_SAMPLE_LIMIT = 2_016
STATUS_SNAPSHOT_TTL_SECONDS = 15.0
STATUS_SNAPSHOT_WAIT_SECONDS = 5.0
STATUS_SNAPSHOT_MAX_STALE_SECONDS = 90.0
SEMANTIC_SNAPSHOT_MAX_STALE_SECONDS = 420.0
COLLECTOR_HEARTBEAT_EXPECTED_SECONDS = 60.0
COLLECTOR_HEARTBEAT_FAILURE_SECONDS = 300.0
DECISION_OUTPUT_CADENCE_SECONDS = 300.0
DECISION_OUTPUT_STALLED_SECONDS = 420.0
DECISION_OUTPUT_GRACE_SECONDS = (
    DECISION_OUTPUT_STALLED_SECONDS - DECISION_OUTPUT_CADENCE_SECONDS
)
DECISION_HORIZON = timedelta(minutes=30)
_QUOTE_CANDLE_CACHE_LOCK = threading.Lock()
_QUOTE_CANDLE_CACHE: dict[str, dict] = {}
_NEWS_EVIDENCE_CACHE_LOCK = threading.Lock()
_NEWS_EVIDENCE_CACHE: dict[str, object] = {}
_NEWS_PROJECTION_CACHE_LOCK = threading.Lock()
_NEWS_PROJECTION_CACHE: dict[str, object] = {}
NEWS_PROJECTION_SOURCE_REFRESH_SECONDS = 300.0
NEWS_PROJECTION_SOURCE_RETRY_SECONDS = 30.0
NEWS_PROJECTION_GENERATION_FILE = "dashboard-news-projection-generation-v4.json.gz"
_NEWS_EVIDENCE_VOLATILE_FIELDS = frozenset({"economic_age_minutes"})
_NEWS_EVIDENCE_MANIFEST_VERSION = "local-news-evidence-generation-v2"


def _semantic_pipeline_component(latest, *, now: datetime) -> dict:
    if latest is None:
        return {
            "last_success": None,
            "age_seconds": None,
            "status": "STALE",
            "last_error": "尚无决策时点的新闻语义健康记录",
            "reason_codes": [],
            "actionable_failure_counts": {},
        }
    observed_at = datetime.fromisoformat(str(latest["observed_at"]))
    age_seconds = max(0.0, (now - observed_at).total_seconds())
    latest_keys = latest.keys()
    reason_codes = tuple(
        latest["reason_codes"]
        if "reason_codes" in latest_keys
        else json.loads(latest["reason_codes_json"])
    )
    freshness_failure = any(
        code.endswith("_STALE") or code.endswith("_MISSING")
        for code in reason_codes
        if code.startswith(("ANNOTATOR_HEARTBEAT_", "NEWS_COLLECTOR_POLL_"))
    )
    stale = age_seconds > SEMANTIC_SNAPSHOT_MAX_STALE_SECONDS or freshness_failure
    pending_only = bool(reason_codes) and all(
        code.endswith(("_PENDING", "_RECOVERING"))
        for code in reason_codes
    )
    return {
        "last_success": latest["heartbeat_at"],
        "age_seconds": age_seconds,
        "status": (
            "STALE" if stale else
            "OK" if latest["status"] == "HEALTHY" else
            "WARN" if pending_only else "ERROR"
        ),
        "last_error": None if not reason_codes else ", ".join(reason_codes),
        "reason_codes": list(reason_codes),
        "actionable_failure_counts": (
            latest["actionable_failure_counts"]
            if "actionable_failure_counts" in latest_keys
            else json.loads(latest["actionable_failure_counts_json"] or "{}")
            if "actionable_failure_counts_json" in latest_keys else {}
        ),
    }


def _materialized_semantic_health(
    connection: sqlite3.Connection, decision_id: str | None,
) -> dict | None:
    if not decision_id:
        return None
    row = connection.execute(
        """SELECT observed_at,status,reason_codes_json,heartbeat_at,
                  unresolved_items,oldest_unresolved_at,snapshot_hash
           FROM news_semantic_health_snapshots_v1
           WHERE source_decision_id=?""",
        (decision_id,),
    ).fetchone()
    if row is None:
        return None
    result = dict(row)
    result["reason_codes"] = json.loads(result.pop("reason_codes_json"))
    result["actionable_failure_counts"] = {}
    return result


def _collector_component(
    heartbeat: dict[str, object],
    *,
    latest_poll: str | None,
    now: datetime,
) -> dict[str, object]:
    """Use the supervised process pulse; source cadence has separate health."""
    heartbeat_at = None
    try:
        heartbeat_at = datetime.fromisoformat(str(heartbeat["last_success"]))
    except (KeyError, TypeError, ValueError):
        pass
    age_seconds = (
        max(0.0, (now - heartbeat_at).total_seconds())
        if heartbeat_at is not None else None
    )
    lifecycle_state = str(heartbeat.get("state") or "")
    running = lifecycle_state == "RUNNING"
    starting = lifecycle_state == "STARTING"
    if age_seconds is None:
        status = "STALE"
    elif starting:
        status = (
            "WARN"
            if age_seconds <= COLLECTOR_HEARTBEAT_FAILURE_SECONDS
            else "STALE"
        )
    elif not running:
        status = "STALE"
    elif age_seconds <= COLLECTOR_HEARTBEAT_EXPECTED_SECONDS:
        status = "OK"
    elif age_seconds <= COLLECTOR_HEARTBEAT_FAILURE_SECONDS:
        status = "WARN"
    else:
        status = "STALE"
    if starting:
        lifecycle_error = (
            "采集器启动中" if status == "WARN" else "采集器启动心跳已过期"
        )
    elif running:
        lifecycle_error = str(heartbeat.get("last_error") or "") or None
    else:
        lifecycle_error = "采集器运行心跳不可用"
    latest_poll_at = None
    try:
        latest_poll_at = datetime.fromisoformat(str(latest_poll))
    except (TypeError, ValueError):
        pass
    return {
        "last_success": heartbeat_at.isoformat() if heartbeat_at else None,
        "age_seconds": age_seconds,
        "status": status,
        "last_error": lifecycle_error,
        "latest_source_poll": latest_poll,
        "source_poll_age_seconds": (
            max(0.0, (now - latest_poll_at).total_seconds())
            if latest_poll_at is not None else None
        ),
    }


def _decision_collector_component(
    heartbeat: dict[str, object],
    *,
    latest_decision: str | None,
    decision_observation_start: str,
    broker_session: dict | None,
    quote_current: bool,
    now: datetime,
) -> dict[str, object]:
    """Separate supervised collector liveness from decision output cadence."""
    component = _collector_component(
        heartbeat, latest_poll=None, now=now,
    )
    component.pop("latest_source_poll", None)
    component.pop("source_poll_age_seconds", None)
    decision_at = None
    try:
        decision_at = datetime.fromisoformat(str(latest_decision))
    except (TypeError, ValueError):
        pass
    decision_age = (
        max(0.0, (now - decision_at).total_seconds())
        if decision_at is not None else None
    )
    observation_started_at = None
    try:
        observation_started_at = datetime.fromisoformat(
            str(decision_observation_start).replace("Z", "+00:00")
        )
    except (TypeError, ValueError):
        pass
    output_age = decision_age
    if output_age is None and observation_started_at is not None:
        output_age = max(0.0, (now - observation_started_at).total_seconds())
    output_reference = decision_at or observation_started_at
    eligible_grid = None
    post_reopen_stall_after = None
    try:
        opened_at = datetime.fromisoformat(
            str(broker_session["opened_at"]).replace("Z", "+00:00")
        )
        first_quote_after_open = datetime.fromisoformat(
            str(broker_session["first_quote_after_open_at"]).replace(
                "Z", "+00:00"
            )
        )
    except (KeyError, TypeError, ValueError):
        opened_at = None
        first_quote_after_open = None
    if (
        output_reference is not None
        and opened_at is not None
        and first_quote_after_open is not None
        and output_reference <= opened_at <= first_quote_after_open <= now
    ):
        eligible_grid = first_quote_after_open.replace(second=0, microsecond=0)
        minute_remainder = eligible_grid.minute % 5
        if minute_remainder or eligible_grid < first_quote_after_open:
            eligible_grid += timedelta(minutes=5 - minute_remainder)
        post_reopen_stall_after = eligible_grid + timedelta(
            seconds=DECISION_OUTPUT_GRACE_SECONDS
        )
    output_status = (
        "CURRENT"
        if decision_age is not None
        and decision_age <= DECISION_OUTPUT_STALLED_SECONDS
        else "NO_RECENT_DECISION"
    )
    output_reason = None
    output_message = None
    if broker_session is not None and not broker_session["is_open"]:
        output_status = "MARKET_CLOSED"
    elif broker_session is not None:
        try:
            closes_at = datetime.fromisoformat(
                str(broker_session["next_close_time"]).replace("Z", "+00:00")
            )
        except (KeyError, TypeError, ValueError):
            closes_at = None
        if closes_at is not None and closes_at <= now + DECISION_HORIZON:
            output_status = "EXPECTED_PAUSE"
            output_reason = "FIXED_HORIZON_CROSSES_BROKER_CLOSE"
            output_message = "等待下一个完整 30 分钟决策窗口"
        elif (
            closes_at is not None
            and quote_current
            and output_age is not None
            and (
                now > post_reopen_stall_after
                if post_reopen_stall_after is not None
                else output_age > DECISION_OUTPUT_STALLED_SECONDS
            )
            and output_status == "NO_RECENT_DECISION"
        ):
            output_status = "STALLED"
            output_reason = "DECISION_OUTPUT_CADENCE_EXCEEDED"
            output_message = "决策输出已超过正常 5 分钟节奏"
    component.update({
        "latest_decision": decision_at.isoformat() if decision_at else None,
        "decision_age_seconds": decision_age,
        "decision_observation_started_at": (
            observation_started_at.isoformat() if observation_started_at else None
        ),
        "decision_output_age_seconds": output_age,
        "decision_output_eligible_grid": (
            eligible_grid.isoformat() if eligible_grid else None
        ),
        "decision_output_stall_after": (
            post_reopen_stall_after.isoformat()
            if post_reopen_stall_after else None
        ),
        "collector_state": str(heartbeat.get("state") or ""),
        "decision_output_status": output_status,
        "decision_output_reason": output_reason,
        "decision_output_message": output_message,
        "decision_output_expected_cadence_seconds": (
            DECISION_OUTPUT_CADENCE_SECONDS
        ),
        "decision_output_stalled_after_seconds": (
            DECISION_OUTPUT_STALLED_SECONDS
        ),
        "market_closes_at": (
            broker_session.get("next_close_time") if broker_session else None
        ),
    })
    return component

from xauusd_forecaster.factors import (  # noqa: E402
    FACTOR_COVERAGE_MACRO_SERIES,
    FACTOR_COVERAGE_NEWS_SOURCES,
    factor_coverage,
)
from xauusd_forecaster.dashboard_payloads import bounded_evidence_window  # noqa: E402
from xauusd_forecaster.daily_brief import (  # noqa: E402
    daily_brief_summary,
    recent_daily_briefs,
)
from xauusd_forecaster.annotation import (  # noqa: E402
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
from xauusd_forecaster.gemini_quota import (  # noqa: E402
    GEMINI_REQUESTS_PER_DAY_PER_KEY, GeminiQuotaLedger,
)
from xauusd_forecaster.news_scheduler import (  # noqa: E402
    account_quota_snapshot, configured_api_credentials,
)
from xauusd_forecaster.ai_provider_registry import (  # noqa: E402
    AI_QUOTA_SURFACES,
    GEMINI_EMBEDDING_REQUESTS_PER_DAY_PER_ACCOUNT,
)
from xauusd_forecaster.model_limits import GEMMA_PROVIDER_LANES_PER_ACCOUNT  # noqa: E402
from xauusd_forecaster.learning_curves import learning_curve_payload  # noqa: E402
from xauusd_forecaster.execution_costs import net_shadow_log_return  # noqa: E402
from xauusd_forecaster.news_evidence import (  # noqa: E402
    EVIDENCE_POLICY_VERSION, event_evidence_rows_from_connection,
    resolve_event_clock,
)
from xauusd_forecaster.news_relevance import (  # noqa: E402
    GOOGLE_NEWS_MAX_AGE, google_news_item_is_relevant,
)
from xauusd_forecaster.news_time import assess_news_semantic_eligibility  # noqa: E402
from xauusd_forecaster.news_semantics import model_usable_annotation_predicate  # noqa: E402
from xauusd_forecaster.news_identity import preferred_cluster_peer_predicate  # noqa: E402
from xauusd_forecaster.news_contracts import CURRENT_NEWS_CONTRACT  # noqa: E402
from xauusd_forecaster.news_features_v2 import COLLECTION_SOURCES  # noqa: E402
from xauusd_forecaster.news_source_registry import NEWS_SOURCE_REGISTRY  # noqa: E402
from xauusd_forecaster.news_pipeline_health import (  # noqa: E402
    news_semantic_pipeline_health,
)
from xauusd_forecaster.source_polling import source_poll_recovery_state  # noqa: E402
from xauusd_forecaster.production_shape import production_contract_snapshot  # noqa: E402
from xauusd_forecaster.market_session import expected_weekly_closure  # noqa: E402
from xauusd_forecaster.operational_health import (  # noqa: E402
    extend_with_component_alerts,
    scheduler_health_snapshot,
)
from xauusd_forecaster.news_impact import (  # noqa: E402
    HANDOVER_IMPACT_PROMPT_VERSION,
    IMPACT_MODEL,
    IMPACT_PROMPT_VERSION,
    impact_is_actionable,
    impact_time_rule,
)
from xauusd_forecaster.storylines import STORYLINE_POLICY_VERSION, temporal_event_graph  # noqa: E402
from xauusd_forecaster.execution_learning import execution_learning_status  # noqa: E402


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


class StatusSnapshotUnavailable(RuntimeError):
    """Raised when no bounded-age dashboard snapshot can be served."""


class StatusSnapshotCache:
    """Serialize one dashboard snapshot at a time and fail closed while stale."""

    def __init__(
        self,
        *,
        ttl_seconds: float = STATUS_SNAPSHOT_TTL_SECONDS,
        wait_seconds: float = STATUS_SNAPSHOT_WAIT_SECONDS,
        max_stale_seconds: float = STATUS_SNAPSHOT_MAX_STALE_SECONDS,
        clock=time.monotonic,
    ) -> None:
        self.ttl_seconds = ttl_seconds
        self.wait_seconds = wait_seconds
        self.max_stale_seconds = max_stale_seconds
        self.clock = clock
        self._condition = threading.Condition()
        self._database: Path | None = None
        self._body: bytes | None = None
        self._built_at = 0.0
        self._refreshing = False
        self._last_error: str | None = None

    def _age(self) -> float | None:
        if self._body is None:
            return None
        return max(0.0, self.clock() - self._built_at)

    def health(self) -> tuple[int, dict]:
        with self._condition:
            age = self._age()
            if age is None:
                return 503, {
                    "status": "STARTING" if self._refreshing else "UNAVAILABLE",
                    "snapshot_age_seconds": None,
                    "last_error": self._last_error,
                }
            if self._last_error:
                return 503, {
                    "status": "ERROR",
                    "snapshot_age_seconds": age,
                    "last_error": self._last_error,
                }
            if age > self.max_stale_seconds:
                return 503, {
                    "status": "STALE",
                    "snapshot_age_seconds": age,
                    "last_error": self._last_error,
                }
            return 200, {
                "status": "OK",
                "snapshot_age_seconds": age,
                "refreshing": self._refreshing,
            }

    def _refresh(self, database: Path, builder) -> tuple[bytes, str, float]:
        try:
            payload = builder(database)
            body = json.dumps(
                payload, allow_nan=False, separators=(",", ":"),
            ).encode()
        except Exception as error:
            with self._condition:
                self._refreshing = False
                self._last_error = f"{type(error).__name__}: {str(error)[:400]}"
                self._condition.notify_all()
            raise
        with self._condition:
            self._body = body
            self._built_at = self.clock()
            self._refreshing = False
            self._last_error = None
            self._condition.notify_all()
        return body, "fresh", 0.0

    def _refresh_in_background(self, database: Path, builder) -> None:
        try:
            self._refresh(database, builder)
        except Exception:
            return

    def get(self, database: Path, builder) -> tuple[bytes, str, float]:
        database = logical_absolute_path(database)
        stale_result: tuple[bytes, str, float] | None = None
        start_background_refresh = False
        with self._condition:
            if self._database != database:
                self._database = database
                self._body = None
                self._built_at = 0.0
                self._last_error = None
            age = self._age()
            if self._body is not None and age is not None and age <= self.ttl_seconds:
                return self._body, "fresh", age
            if (
                self._body is not None
                and age is not None
                and age <= self.max_stale_seconds
            ):
                stale_result = (self._body, "stale", age)
                if not self._refreshing:
                    self._refreshing = True
                    start_background_refresh = True
                build_here = False
            else:
                build_here = not self._refreshing
                if build_here:
                    self._refreshing = True

        if stale_result is not None:
            if start_background_refresh:
                threading.Thread(
                    target=self._refresh_in_background,
                    args=(database, builder),
                    daemon=True,
                    name="dashboard-status-refresh",
                ).start()
            return stale_result

        if build_here:
            return self._refresh(database, builder)

        with self._condition:
            refresh_finished = self._condition.wait_for(
                lambda: not self._refreshing, timeout=self.wait_seconds,
            )
            if not refresh_finished:
                raise StatusSnapshotUnavailable(
                    "dashboard snapshot refresh is still running"
                )
            age = self._age()
            if self._last_error:
                raise StatusSnapshotUnavailable(self._last_error)
            if self._body is not None and age is not None:
                return self._body, "fresh", age
            raise StatusSnapshotUnavailable(
                "dashboard snapshot refresh completed without a result"
            )

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


def _latest_decision_created_at(
    database: Path, snapshot_connection: sqlite3.Connection | None = None,
) -> str | None:
    """Read cadence from the caller's snapshot when one owns the build."""
    owns_connection = snapshot_connection is None
    connection = snapshot_connection or sqlite3.connect(
        f"file:{database}?mode=ro", uri=True, timeout=5,
    )
    try:
        return connection.execute(
            """SELECT activity_time FROM dashboard_latest_activity_v1
               WHERE activity_name='decision_events'"""
        ).fetchone()[0]
    finally:
        if owns_connection:
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
    item["impact_event_at"] = event_at
    item["impact_clock_source"] = clock_source
    item["impact_expires_at"] = expires_at
    first_seen = datetime.fromisoformat(str(item["collector_first_seen_time"]))
    assessed_at = datetime.fromisoformat(str(item["impact_assessed_at"]))
    available_at = max(first_seen, assessed_at)
    item["impact_available_at"] = available_at
    canonicalize_news_projection_impact_clocks(item)
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
    # This is an in-process materialization bound, not an HTTP display or write
    # transport limit. Reusing the 20-row reader response bound here would
    # repeat the projection joins hundreds of times for one frozen generation.
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


def _news_projection_generation_path(database: Path) -> Path:
    return database.parent / NEWS_PROJECTION_GENERATION_FILE


def _news_projection_generation_payload(
    generation: NewsProjectionGeneration,
) -> dict:
    return {
        "manifest": generation.manifest,
        "index_rows": list(generation.index_rows),
        "detail_rows": list(generation.detail_rows),
        "index_batches": [list(batch) for batch in generation.index_batches],
        "detail_batches": [list(batch) for batch in generation.detail_batches],
    }


def _news_projection_payload_digest(payload: dict) -> str:
    return hashlib.sha256(json.dumps(
        payload, ensure_ascii=False, allow_nan=False, sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")).hexdigest()


def _news_projection_generation_from_payload(
    payload: object,
) -> NewsProjectionGeneration:
    if not isinstance(payload, dict):
        raise ValueError("persisted news generation payload is invalid")
    manifest = payload.get("manifest")
    index_rows = payload.get("index_rows")
    detail_rows = payload.get("detail_rows")
    index_batches = payload.get("index_batches")
    detail_batches = payload.get("detail_batches")
    if not isinstance(manifest, dict) or any(
        not isinstance(value, list)
        for value in (index_rows, detail_rows, index_batches, detail_batches)
    ):
        raise ValueError("persisted news generation shape is invalid")
    if any(
        not isinstance(batch, list)
        for batch in [*index_batches, *detail_batches]
    ):
        raise ValueError("persisted news generation batch shape is invalid")
    if manifest.get("contract_version") != NEWS_PROJECTION_CONTRACT_VERSION:
        raise ValueError("persisted news generation contract is incompatible")
    if (
        len(index_rows) != int(manifest.get("expected_index_count", -1))
        or len(detail_rows) != int(manifest.get("expected_detail_count", -1))
    ):
        raise ValueError("persisted news generation count is invalid")
    if (
        [item for batch in index_batches for item in batch] != index_rows
        or [item for batch in detail_batches for item in batch] != detail_rows
    ):
        raise ValueError("persisted news generation batches do not match rows")
    if receipt_digest(detail_batches, index_batches) != manifest.get(
        "expected_receipt_digest"
    ):
        raise ValueError("persisted news generation receipt is invalid")
    return NewsProjectionGeneration(
        manifest=dict(manifest),
        index_rows=tuple(index_rows),
        detail_rows=tuple(detail_rows),
        index_batches=tuple(tuple(batch) for batch in index_batches),
        detail_batches=tuple(tuple(batch) for batch in detail_batches),
    )


def _read_news_projection_generation_artifact(
    path: Path,
) -> NewsProjectionGeneration | None:
    if not path.exists():
        return None
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        envelope = json.load(handle)
    if not isinstance(envelope, dict) or envelope.get("schema_version") != 1:
        raise ValueError("persisted news generation envelope is invalid")
    payload = envelope.get("generation")
    if (
        not isinstance(payload, dict)
        or envelope.get("sha256") != _news_projection_payload_digest(payload)
    ):
        raise ValueError("persisted news generation digest is invalid")
    return _news_projection_generation_from_payload(payload)


def _read_persisted_news_projection_generation(
    database: Path,
) -> NewsProjectionGeneration | None:
    return _read_news_projection_generation_artifact(
        _news_projection_generation_path(database),
    )


def _write_news_projection_generation_artifact(
    path: Path, generation: NewsProjectionGeneration,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = _news_projection_generation_payload(generation)
    envelope = {
        "schema_version": 1,
        "sha256": _news_projection_payload_digest(payload),
        "generation": payload,
    }
    temporary = path.with_suffix(path.suffix + ".tmp")
    with gzip.open(temporary, "wt", encoding="utf-8", newline="") as handle:
        json.dump(
            envelope, handle, ensure_ascii=False, allow_nan=False,
            sort_keys=True, separators=(",", ":"),
        )
    os.replace(temporary, path)


def _write_persisted_news_projection_generation(
    database: Path, generation: NewsProjectionGeneration,
) -> None:
    _write_news_projection_generation_artifact(
        _news_projection_generation_path(database), generation,
    )


def _finish_news_projection_source_build(database: Path) -> None:
    try:
        candidate = _build_news_projection_source_from_database(database)
        with _NEWS_PROJECTION_CACHE_LOCK:
            cached = _NEWS_PROJECTION_CACHE.get("generation")
            replace_generation = (
                not isinstance(cached, NewsProjectionGeneration)
                or candidate.manifest["source_digest"]
                != cached.manifest["source_digest"]
            )
        if replace_generation:
            _write_persisted_news_projection_generation(database, candidate)
    except Exception as error:
        with _NEWS_PROJECTION_CACHE_LOCK:
            _NEWS_PROJECTION_CACHE["error"] = type(error).__name__
            _NEWS_PROJECTION_CACHE["retry_at"] = (
                time.monotonic() + NEWS_PROJECTION_SOURCE_RETRY_SECONDS
            )
            _NEWS_PROJECTION_CACHE["building"] = False
        return
    with _NEWS_PROJECTION_CACHE_LOCK:
        if replace_generation:
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
        if not isinstance(cached, NewsProjectionGeneration):
            cached = _read_persisted_news_projection_generation(database)
            if isinstance(cached, NewsProjectionGeneration):
                _NEWS_PROJECTION_CACHE["generation"] = cached
                _NEWS_PROJECTION_CACHE["built_at"] = time.monotonic()
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


def _append_quote_candle(buckets: dict[datetime, dict], raw: str | bytes) -> None:
    try:
        quote = json.loads(raw)
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
        return
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


def _quote_file_candles(path: Path) -> list[dict]:
    """Aggregate archives once and consume only new bytes from live quote files."""
    try:
        stat = path.stat()
    except OSError:
        return []
    key = str(path)
    with _QUOTE_CANDLE_CACHE_LOCK:
        cached = _QUOTE_CANDLE_CACHE.get(key)
        if path.suffix == ".gz":
            signature = (stat.st_size, stat.st_mtime_ns)
            if cached and cached.get("signature") == signature:
                return cached["candles"]
            buckets: dict[datetime, dict] = {}
            try:
                with gzip.open(path, "rt", encoding="utf-8") as handle:
                    for line in handle:
                        _append_quote_candle(buckets, line)
            except OSError:
                return []
            candles = [buckets[bucket] for bucket in sorted(buckets)]
            _QUOTE_CANDLE_CACHE[key] = {
                "signature": signature, "candles": candles,
            }
            return candles

        if cached and int(cached.get("offset", 0)) <= stat.st_size:
            buckets = cached["buckets"]
            offset = int(cached["offset"])
            remainder = bytes(cached.get("remainder", b""))
        else:
            buckets = {}
            offset = 0
            remainder = b""
        if offset == stat.st_size:
            return [dict(candle) for candle in cached["candles"]] if cached else []
        try:
            with path.open("rb") as handle:
                handle.seek(offset)
                chunk = handle.read()
                next_offset = handle.tell()
        except OSError:
            return []
        lines = (remainder + chunk).split(b"\n")
        remainder = lines.pop()
        for line in lines:
            if line:
                _append_quote_candle(buckets, line)
        candles = [buckets[bucket] for bucket in sorted(buckets)]
        _QUOTE_CANDLE_CACHE[key] = {
            "offset": next_offset,
            "remainder": remainder,
            "buckets": buckets,
            "candles": candles,
        }
        return [dict(candle) for candle in candles]


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


def _canonical_payload_digest(payload: dict) -> str:
    return hashlib.sha256(json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")).hexdigest()


def _backup_lifecycle_status(backup_root: Path) -> dict:
    state_path = backup_root / BACKUP_RETENTION_STATE
    result = {
        "status": "UNKNOWN",
        "last_success": None,
        "age_seconds": None,
        "last_verified_backup": None,
        "managed_count": 0,
        "managed_bytes": 0,
        "unknown_count": 0,
        "unknown_bytes": 0,
        "managed_gib_days": 0.0,
        "unknown_gib_days": 0.0,
        "disk_gib_days": 0.0,
        "proven_stale_reclaimed_count": 0,
        "proven_stale_reclaimed_bytes": 0,
        "policy": None,
        "last_error": "Backup retention state is not available",
    }
    if state_path.is_file():
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
            digest = str(state.pop("receipt_digest", ""))
            if not digest or digest != _canonical_payload_digest(state):
                raise ValueError("retention receipt digest")
            if state.get("schema") != BACKUP_RETENTION_SCHEMA:
                raise ValueError("retention receipt schema")
            retained = state.get("retained") or []
            verified = []
            for name in retained:
                target = backup_root / str(name)
                if (
                    target.parent.resolve() != backup_root.resolve()
                    or not re.fullmatch(
                        r"forward-evidence-\d{8}\.sqlite3", target.name,
                    )
                    or not target.is_file()
                ):
                    raise ValueError("retained backup identity")
                verified.append(target)
            if len(verified) != int(state.get("managed_count") or 0):
                raise ValueError("retained backup count")
            result.update({
                "status": "OK",
                "last_success": state.get("completed_at"),
                "age_seconds": max(0.0, (
                    datetime.now(UTC)
                    - datetime.fromisoformat(str(state["completed_at"]))
                ).total_seconds()),
                "last_verified_backup": (
                    datetime.fromtimestamp(
                        max(path.stat().st_mtime for path in verified), UTC,
                    ).isoformat()
                    if verified else None
                ),
                "managed_count": int(state.get("managed_count") or 0),
                "managed_bytes": int(state.get("managed_bytes") or 0),
                "unknown_count": int(state.get("unknown_count") or 0),
                "unknown_bytes": int(state.get("unknown_bytes") or 0),
                "managed_gib_days": float(
                    state.get("managed_gib_days") or 0.0
                ),
                "unknown_gib_days": float(
                    state.get("unknown_gib_days") or 0.0
                ),
                "disk_gib_days": float(state.get("disk_gib_days") or 0.0),
                "proven_stale_reclaimed_count": int(
                    state.get("proven_stale_reclaimed_count") or 0
                ),
                "proven_stale_reclaimed_bytes": int(
                    state.get("proven_stale_reclaimed_bytes") or 0
                ),
                "policy": state.get("policy"),
                "last_error": None,
            })
            return result
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            result["last_error"] = f"Invalid backup retention state: {exc}"
            return result

    verified = []
    for receipt_path in backup_root.glob("forward-evidence-*.sqlite3.receipt.json"):
        try:
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            digest = str(receipt.pop("receipt_digest", ""))
            if (
                receipt.get("schema") != BACKUP_RECEIPT_SCHEMA
                or not digest
                or digest != _canonical_payload_digest(receipt)
            ):
                continue
            target = Path(str((receipt.get("snapshot") or {}).get("path") or ""))
            if target.parent.resolve() == backup_root.resolve() and target.is_file():
                verified.append(target)
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            continue
    if verified:
        result.update({
            "status": "PENDING_RETENTION",
            "last_verified_backup": datetime.fromtimestamp(
                max(path.stat().st_mtime for path in verified), UTC,
            ).isoformat(),
            "managed_count": len(verified),
            "managed_bytes": sum(path.stat().st_size for path in verified),
            "last_error": "Backup retention inventory has not completed",
        })
    return result


def _wal_checkpoint_status(state_root: Path) -> dict:
    state_path = state_root / FORWARD_WAL_CHECKPOINT_STATE
    result = {
        "status": "UNKNOWN",
        "last_success": None,
        "age_seconds": None,
        "pending_frames": None,
        "wal_bytes": None,
        "journal_size_limit_bytes": None,
        "last_error": "WAL checkpoint state is not available",
    }
    if not state_path.is_file():
        return result
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
        digest = str(state.pop("receipt_digest", ""))
        if not digest or digest != _canonical_payload_digest(state):
            raise ValueError("WAL checkpoint receipt digest")
        if state.get("schema") != FORWARD_WAL_CHECKPOINT_SCHEMA:
            raise ValueError("WAL checkpoint receipt schema")
        recorded_at = datetime.fromisoformat(str(state["recorded_at"]))
        age_seconds = max(0.0, (datetime.now(UTC) - recorded_at).total_seconds())
        checkpoint_status = str(state.get("status") or "UNKNOWN")
        if checkpoint_status in {"CHECKPOINTED", "TRUNCATED"}:
            component_status = "OK"
        elif checkpoint_status in {
            "CHECKPOINT_BUSY", "READER_PINNED", "TRUNCATE_BUSY",
            "TRUNCATE_INCOMPLETE",
        }:
            component_status = "WARN"
        else:
            component_status = "ERROR"
        if age_seconds > 300:
            component_status = "ERROR"
        result.update({
            "status": component_status,
            "checkpoint_status": checkpoint_status,
            "last_success": state.get("recorded_at"),
            "age_seconds": age_seconds,
            "pending_frames": int(state.get("pending_frames") or 0),
            "wal_bytes": int(state.get("wal_bytes_after") or 0),
            "journal_size_limit_bytes": int(
                state.get("journal_size_limit_bytes") or 0
            ),
            "last_error": state.get("error"),
        })
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        result["status"] = "ERROR"
        result["last_error"] = f"Invalid WAL checkpoint state: {exc}"
    return result


def _dashboard_payload(
    database: Path, *, clock=None, include_optional: bool = True,
    optional_resources: frozenset[str] | None = None,
    snapshot_connection: sqlite3.Connection | None = None,
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
    owns_connection = snapshot_connection is None
    connection = snapshot_connection or sqlite3.connect(
        f"file:{database}?mode=ro", uri=True, timeout=5,
    )
    connection.row_factory = sqlite3.Row
    if owns_connection:
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
        if owns_connection:
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
    latest_decision_time = _latest_decision_created_at(
        database, snapshot_connection,
    )
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
    backup_lifecycle = _backup_lifecycle_status(database.parent / "backups")
    wal_checkpoint = _wal_checkpoint_status(database.parent)
    backup_time = backup_lifecycle["last_verified_backup"]
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
        "backup_retention": backup_lifecycle,
        "sqlite_wal_checkpoint": wal_checkpoint,
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


def _optional_resource_payload(
    snapshot: DashboardReadModelSnapshot, resource: str,
) -> dict:
    """Build one optional resource without evaluating sibling producers."""
    payload = _dashboard_payload(
        snapshot.database,
        clock=lambda: snapshot.started_at,
        optional_resources=frozenset({resource}),
        snapshot_connection=snapshot.connection,
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


class Handler(BaseHTTPRequestHandler):
    database: Path
    status_cache = StatusSnapshotCache()
    critical_status_cache = StatusSnapshotCache()
    audit_cache = StatusSnapshotCache()
    learning_cache = StatusSnapshotCache()
    market_chart_cache = StatusSnapshotCache()
    news_evidence_cache = StatusSnapshotCache()

    def _operator_bridge_auth_error(self) -> tuple[int, bytes] | None:
        if self.client_address[0] not in {"127.0.0.1", "::1"}:
            return 403, b'{"error":"localhost operator bridge only"}'
        # Browser-origin requests have no reason to reach this machine bridge.
        if self.headers.get("Origin") or self.headers.get("Sec-Fetch-Mode"):
            return 403, b'{"error":"browser origin is not permitted"}'
        expected = os.environ.get("DASHBOARD_OPERATOR_BRIDGE_TOKEN", "").strip()
        supplied = self.headers.get("X-Aurum-Operator-Bridge-Token", "").strip()
        if not 32 <= len(expected) <= 512:
            return 503, b'{"error":"operator bridge credential is not configured"}'
        if not supplied or not hmac.compare_digest(supplied, expected):
            return 401, b'{"error":"operator bridge authorization failed"}'
        return None

    def _write_json(self, status: int, body: bytes, **headers: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        for name, value in headers.items():
            self.send_header(name.replace("_", "-"), value)
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
            pass

    def do_GET(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlsplit(self.path)
        path = parsed.path.rstrip("/")
        if path == "/api/health":
            try:
                self.critical_status_cache.get(
                    self.database,
                    lambda database: critical_status_payload(
                        _dashboard_payload(database, include_optional=False)
                    ),
                )
            except Exception:
                pass
            status, critical = self.critical_status_cache.health()
            payload = {
                **critical,
                "readiness_scope": "PROCESS_AND_CRITICAL_STATUS",
                "optional_resources": "SEPARATE_DEGRADATION",
            }
            body = json.dumps(payload, separators=(",", ":")).encode()
            self._write_json(status, body)
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
                status = 200
            except (OSError, sqlite3.Error, TypeError, ValueError) as error:
                body = json.dumps({"error": str(error)[:500]}).encode()
                status = 400
            self._write_json(status, body)
            return
        if path == "/api/news-archive":
            auth_error = self._operator_bridge_auth_error()
            if auth_error is not None:
                self._write_json(*auth_error)
                return
            query = urllib.parse.parse_qs(parsed.query)
            mode = (query.get("mode") or ["manifest"])[0]
            activated_snapshot_id = (
                query.get("activated_snapshot_id") or [None]
            )[0]
            try:
                if activated_snapshot_id and not re.fullmatch(
                    r"[a-f0-9]{64}", activated_snapshot_id,
                ):
                    raise ValueError("invalid activated news snapshot identity")
                generation = _news_projection_source_for_request(
                    self.database, activated_snapshot_id,
                )
                if mode == "manifest":
                    payload = {"manifest": generation.manifest}
                elif mode == "batch":
                    snapshot_id = (query.get("snapshot_id") or [""])[0]
                    if snapshot_id != generation.manifest["snapshot_id"]:
                        raise ValueError("news projection snapshot is no longer available")
                    kind = (query.get("kind") or [""])[0]
                    offset = int((query.get("offset") or ["0"])[0])
                    payload = _news_projection_batch(generation, kind, offset)
                else:
                    raise ValueError("invalid news projection source mode")
                body = json.dumps(payload, allow_nan=False).encode()
                status = 200
            except NewsProjectionSourcePending as error:
                body = json.dumps({
                    "error": str(error),
                    "error_code": "NEWS_PROJECTION_SOURCE_BUILDING",
                    "projection_state": "REPLAYING",
                }).encode()
                self._write_json(503, body, Retry_After="30")
                return
            except (OSError, sqlite3.Error, TypeError, ValueError) as error:
                body = json.dumps({"error": str(error)[:500]}).encode()
                status = 400
            self._write_json(status, body)
            return
        if path == "/api/news-evidence":
            query = urllib.parse.parse_qs(parsed.query)
            cursor = (query.get("cursor") or [None])[0]
            activated_snapshot_id = (
                query.get("activated_snapshot_id") or [None]
            )[0]
            if activated_snapshot_id and not re.fullmatch(
                r"[a-f0-9]{64}", activated_snapshot_id,
            ):
                self._write_json(400, b'{"error":"invalid activated snapshot id"}')
                return
            try:
                self.news_evidence_cache.get(
                    self.database,
                    lambda database: _build_news_evidence_resource(
                        database, activated_snapshot_id=activated_snapshot_id,
                    ),
                )
                limit = min(
                    NEWS_EVIDENCE_PAGE_LIMIT,
                    max(1, int((query.get("limit") or [NEWS_EVIDENCE_PAGE_LIMIT])[0])),
                )
                payload = _news_evidence_page(cursor, limit)
                body = json.dumps(
                    payload, ensure_ascii=False, allow_nan=False,
                    separators=(",", ":"),
                ).encode("utf-8")
                status = 200
            except StatusSnapshotUnavailable as error:
                body = json.dumps({"error": str(error)[:500]}).encode()
                status = 503
            except (TypeError, ValueError) as error:
                body = json.dumps({"error": str(error)[:500]}).encode()
                status = 409
            self._write_json(status, body)
            return
        read_model_resources = {
            "/api/audit": "audit",
            "/api/learning": "learning",
            "/api/market-chart": "market_chart",
        }
        if path in read_model_resources:
            try:
                body, metadata = read_dashboard_read_model(
                    self.database, read_model_resources[path],
                )
                status = 200
                headers = {
                    "X_Dashboard_Read_Model": str(metadata["state"]),
                    "X_Dashboard_Snapshot_Age": f"{metadata['age_seconds']:.3f}",
                    "X_Dashboard_Source_Revision": str(metadata["source_revision"]),
                }
            except DashboardReadModelUnavailable as error:
                body = json.dumps({"error": str(error)[:500]}).encode()
                status = 503
                headers = {}
            except Exception as error:
                body = json.dumps({"error": str(error)[:500]}).encode()
                status = 500
                headers = {}
            self._write_json(status, body, **headers)
            return
        if path == "/api/retry-jobs":
            auth_error = self._operator_bridge_auth_error()
            if auth_error:
                self._write_json(*auth_error)
                return
            try:
                connection = sqlite3.connect(
                    f"file:{self.database}?mode=ro", uri=True, timeout=5,
                )
                connection.row_factory = sqlite3.Row
                try:
                    payload = {"items": list_retry_schedule_jobs(connection)}
                finally:
                    connection.close()
                body = json.dumps(payload, allow_nan=False).encode()
                status = 200
            except (OSError, sqlite3.Error, TypeError, ValueError) as error:
                body = json.dumps({"error": str(error)[:500]}).encode()
                status = 400
            self._write_json(status, body)
            return
        if path not in {"/api/status", "/api/critical-status"}:
            self.send_error(404)
            return
        try:
            # /api/status is the canonical bounded first-paint contract. Heavy
            # audit, learning, and market detail have independent lazy/paged
            # owners and may never inflate this request path again.
            body, snapshot_state, snapshot_age = self.critical_status_cache.get(
                self.database,
                lambda database: critical_status_payload(
                    _dashboard_payload(database, include_optional=False)
                ),
            )
            status = 200
        except StatusSnapshotUnavailable as error:
            body = json.dumps({"error": str(error)[:500]}).encode()
            status = 503
        except Exception as error:
            body = json.dumps({"error": str(error)[:500]}).encode()
            status = 500
        headers = {}
        if status == 200:
            headers = {
                "X_Dashboard_Snapshot_State": snapshot_state,
                "X_Dashboard_Snapshot_Age": f"{snapshot_age:.3f}",
            }
        self._write_json(status, body, **headers)

    def do_POST(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlsplit(self.path)
        if parsed.path.rstrip("/") != "/api/retry-overrides":
            self.send_error(404)
            return
        auth_error = self._operator_bridge_auth_error()
        if auth_error:
            self._write_json(*auth_error)
            return
        content_type = self.headers.get("Content-Type", "").partition(";")[0].strip().lower()
        if content_type != "application/json":
            self._write_json(415, b'{"error":"application/json content type required"}')
            return
        try:
            content_length = int(self.headers.get("Content-Length", "0"))
            if content_length < 2 or content_length > 100_000:
                raise ValueError("retry override payload size is invalid")
            payload = json.loads(self.rfile.read(content_length))
            if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
                raise ValueError("retry override items are required")
            items = payload["items"]
            if not 1 <= len(items) <= 100:
                raise ValueError("retry override batch size is invalid")
            operator_id = str(payload.get("operator_id") or "").strip()
            if not operator_id.startswith("cloudflare-access:") or len(operator_id) > 500:
                raise ValueError("retry override operator identity is invalid")
            connection = open_forward_writer_connection(
                self.database, timeout=10, row_factory=sqlite3.Row,
            )
            try:
                install_scheduler_schema(connection)
                results = []
                for item in items:
                    if not isinstance(item, dict):
                        results.append({"status": "REJECTED", "code": "INVALID_ITEM"})
                        continue
                    try:
                        requested_at = item.get("requested_available_at")
                        custom_time = (
                            datetime.fromisoformat(str(requested_at))
                            if requested_at else None
                        )
                        current = apply_retry_schedule_override(
                            connection,
                            request_id=str(item.get("request_id") or ""),
                            job_id=str(item.get("job_id") or ""),
                            operator_id=operator_id,
                            mode=str(item.get("mode") or ""),
                            reason=str(item.get("reason") or ""),
                            expected_state=str(item.get("expected_state") or ""),
                            expected_available_at=str(
                                item.get("expected_available_at") or ""
                            ),
                            requested_available_at=custom_time,
                        )
                        results.append({
                            "request_id": item.get("request_id"),
                            "job_id": item.get("job_id"),
                            "status": "APPLIED",
                            "current": current,
                        })
                    except RetryScheduleConflict as error:
                        results.append({
                            "request_id": item.get("request_id"),
                            "job_id": item.get("job_id"),
                            "status": "CONFLICT",
                            "code": error.code,
                            "current": error.current,
                        })
                    except (TypeError, ValueError) as error:
                        results.append({
                            "request_id": item.get("request_id"),
                            "job_id": item.get("job_id"),
                            "status": "REJECTED",
                            "code": "INVALID_REQUEST",
                            "error": str(error)[:500],
                        })
            finally:
                connection.close()
            status = 200 if all(item["status"] == "APPLIED" for item in results) else 207
            body = json.dumps({"results": results}, allow_nan=False).encode()
        except (json.JSONDecodeError, OSError, sqlite3.Error, TypeError, ValueError) as error:
            status = 400
            body = json.dumps({"error": str(error)[:500]}).encode()
        self._write_json(status, body)

    def log_message(self, format: str, *args: object) -> None:
        return


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-root", type=Path, required=True)
    parser.add_argument(
        "--runtime-role", choices=("production", "preflight"),
        default="production",
    )
    parser.add_argument("--database", type=Path)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    state_root = authoritative_runtime_root(args.state_root, role=args.runtime_role)
    Handler.database = runtime_child_path(
        state_root, args.database, name="forward-evidence.sqlite3",
    )
    read_model_owner = DashboardReadModelOwner(
        Handler.database,
        {
            resource: (
                lambda snapshot, resource=resource: _optional_resource_payload(
                    snapshot, resource,
                )
            )
            for resource in ("audit", "learning", "market_chart")
        },
    )
    read_model_owner.start()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(
        json.dumps(
            {
                "event": "DASHBOARD_API_STARTED",
                "url": f"http://{args.host}:{args.port}/api/status",
                "database": str(Handler.database),
                "read_only": False,
                "writes": ["audited retry schedule overrides"],
            }
        ),
        flush=True,
    )
    try:
        server.serve_forever()
    finally:
        read_model_owner.stop()
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
