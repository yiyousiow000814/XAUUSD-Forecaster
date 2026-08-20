"""Persistent operational scheduling for versioned news AI work.

The scheduler tables are deliberately mutable operational state.  Model inputs,
annotations, assessments, failures, and predictions remain append-only evidence.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from .credential_identity import derived_credential_id

PACIFIC = ZoneInfo("America/Los_Angeles")
ROUTINE_POOL = "ROUTINE"
PREEMPTIBLE_POOL = "PREEMPTIBLE"
URGENT_PRIORITIES = frozenset({"IMMEDIATE", "FAST"})
PRIORITIES = ("IMMEDIATE", "FAST", "NORMAL", "BACKGROUND")
LIVE_LANE = "LIVE"
CONTRACT_BACKFILL_LANE = "CONTRACT_BACKFILL"
WORK_LANES = (LIVE_LANE, CONTRACT_BACKFILL_LANE)
CONTRACT_BACKFILL_PAGE_SIZE = 50
EXISTING_JOB_LANE_PAGE_SIZE = 100
DOWNSTREAM_PROVENANCE_PAGE_SIZE = 100
WORK_PROVENANCE_VERSION = "ai-work-provenance-v1"
LIVE_OPERATIONAL_WORKLOAD = "LIVE_OPERATIONAL"
CONTRACT_BACKFILL_WORKLOAD = "CONTRACT_BACKFILL"
REQUEST_WORKLOADS = (LIVE_OPERATIONAL_WORKLOAD, CONTRACT_BACKFILL_WORKLOAD)
BACKFILL_FORECAST_DAYS = 14
BACKFILL_MIN_COMPLETE_DAYS = 7
BACKFILL_RESERVE_RATIO = 0.08
BACKFILL_SAFETY_RATIO = 0.08
BACKFILL_MAX_SHARE_DENOMINATOR = 10
BACKFILL_LIVE_SLA = timedelta(minutes=15)
PRIORITY_HEAD_START = timedelta(minutes=1)
IDLE_CAPACITY_MAX_WAIT = timedelta(minutes=30)
RETRY_OVERRIDE_MODES = (
    "KEEP_ORIGINAL",
    "IMMEDIATE",
    "DELAY_15_MIN",
    "DELAY_1_HOUR",
    "IDLE_CAPACITY",
    "CUSTOM_TIME",
)
TASKS = (
    "ACTIVE_ANNOTATION",
    "ACTIVE_IMPACT",
    "TITLE_TRANSLATION",
)
TOKEN_CALIBRATION_ALPHA = 0.05
TOKEN_CALIBRATION_RECENT_LIMIT = 128
TOKEN_CALIBRATION_P99_MARGIN = 1.05
TOKEN_CALIBRATION_MIN_RATIO = 0.50
TOKEN_CALIBRATION_MAX_RATIO = 8.00
TOKEN_CALIBRATION_MAX_DOWNWARD_STEP = 0.01
SCHEDULER_DEFERRAL_RETENTION = timedelta(hours=24)
EFFECTIVE_INPUT_TOKENS_SQL = """CASE
  WHEN provider_outcome='PROVIDER_SUCCEEDED'
   AND provider_prompt_token_count>0
    THEN provider_prompt_token_count
  ELSE COALESCE(admitted_input_tokens,input_token_count)
END"""
SCHEDULER_SCHEMA = """
CREATE TABLE IF NOT EXISTS news_ai_jobs_v1 (
    job_id TEXT PRIMARY KEY,
    task_type TEXT NOT NULL CHECK(task_type IN (
        'ACTIVE_ANNOTATION','ACTIVE_IMPACT','TITLE_TRANSLATION')),
    source TEXT NOT NULL,
    source_item_id TEXT NOT NULL,
    revision_number INTEGER NOT NULL,
    annotation_id TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    priority TEXT NOT NULL CHECK(priority IN (
        'IMMEDIATE','FAST','NORMAL','BACKGROUND')),
    state TEXT NOT NULL CHECK(state IN (
        'QUEUED','LEASED','BACKING_OFF','COMPLETED','DEAD_LETTER')),
    available_at TEXT NOT NULL,
    lease_owner TEXT,
    lease_expires_at TEXT,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    completed_at TEXT,
    work_lane TEXT NOT NULL DEFAULT 'LIVE' CHECK(work_lane IN (
        'LIVE','CONTRACT_BACKFILL')),
    lane_classified INTEGER NOT NULL DEFAULT 0 CHECK(lane_classified IN (0,1)),
    provenance_resolved INTEGER NOT NULL DEFAULT 0 CHECK(
        provenance_resolved IN (0,1)),
    provenance_version TEXT,
    provenance_origin_task TEXT,
    provenance_origin_ref TEXT,
    UNIQUE(task_type,source,source_item_id,revision_number,annotation_id,prompt_version)
);

CREATE INDEX IF NOT EXISTS news_ai_jobs_claim_v1
ON news_ai_jobs_v1(state,available_at,priority,task_type,created_at);

CREATE TABLE IF NOT EXISTS news_ai_job_attempts_v1 (
    attempt_id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL,
    attempt_number INTEGER NOT NULL CHECK(attempt_number >= 1),
    account_id TEXT NOT NULL,
    credential_id TEXT NOT NULL,
    outcome TEXT NOT NULL,
    failure_code TEXT,
    error_type TEXT,
    provider_http_status INTEGER,
    error_detail TEXT,
    attempted_at TEXT NOT NULL,
    next_retry_at TEXT,
    FOREIGN KEY(job_id) REFERENCES news_ai_jobs_v1(job_id),
    UNIQUE(job_id,attempt_number,account_id,credential_id)
);

CREATE INDEX IF NOT EXISTS news_ai_job_attempts_lookup_v1
ON news_ai_job_attempts_v1(job_id,attempt_number,attempted_at);

CREATE INDEX IF NOT EXISTS news_ai_job_attempts_recent_v1
ON news_ai_job_attempts_v1(attempted_at,job_id,outcome,failure_code);

CREATE TABLE IF NOT EXISTS news_ai_retry_schedule_overrides_v1 (
    override_id TEXT PRIMARY KEY,
    request_id TEXT NOT NULL UNIQUE,
    job_id TEXT NOT NULL,
    task_type TEXT NOT NULL,
    operator_id TEXT NOT NULL,
    mode TEXT NOT NULL CHECK(mode IN (
        'KEEP_ORIGINAL','IMMEDIATE','DELAY_15_MIN','DELAY_1_HOUR',
        'IDLE_CAPACITY','CUSTOM_TIME')),
    requested_at TEXT NOT NULL,
    original_available_at TEXT NOT NULL,
    previous_available_at TEXT NOT NULL,
    requested_available_at TEXT,
    effective_available_at TEXT NOT NULL,
    reason TEXT NOT NULL,
    observed_state TEXT NOT NULL,
    resulting_state TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),
    FOREIGN KEY(job_id) REFERENCES news_ai_jobs_v1(job_id)
);

CREATE INDEX IF NOT EXISTS news_ai_retry_schedule_overrides_active_v1
ON news_ai_retry_schedule_overrides_v1(job_id,active,requested_at);

CREATE TABLE IF NOT EXISTS news_ai_scheduler_deferrals_v1 (
    deferral_id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL,
    task_type TEXT NOT NULL,
    account_id TEXT NOT NULL,
    failure_code TEXT NOT NULL,
    evidence_json TEXT,
    deferred_at TEXT NOT NULL,
    next_retry_at TEXT,
    FOREIGN KEY(job_id) REFERENCES news_ai_jobs_v1(job_id)
);

CREATE INDEX IF NOT EXISTS news_ai_scheduler_deferrals_lookup_v1
ON news_ai_scheduler_deferrals_v1(task_type,deferred_at,failure_code);

CREATE INDEX IF NOT EXISTS news_ai_scheduler_deferrals_retention_v1
ON news_ai_scheduler_deferrals_v1(deferred_at);

CREATE TABLE IF NOT EXISTS news_ai_account_daily_usage_v1 (
    quota_day TEXT NOT NULL,
    account_id TEXT NOT NULL,
    model_family TEXT NOT NULL,
    request_count INTEGER NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(quota_day,account_id,model_family)
);

CREATE TABLE IF NOT EXISTS news_ai_account_minute_usage_v1 (
    minute_bucket TEXT NOT NULL,
    account_id TEXT NOT NULL,
    model_family TEXT NOT NULL,
    request_count INTEGER NOT NULL,
    input_token_count INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(minute_bucket,account_id,model_family)
);

CREATE TABLE IF NOT EXISTS news_ai_account_request_usage_v1 (
    usage_id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL,
    model_family TEXT NOT NULL,
    request_count INTEGER NOT NULL CHECK(request_count > 0),
    input_token_count INTEGER NOT NULL CHECK(input_token_count >= 0),
    reserved_at TEXT NOT NULL,
    attempted_at TEXT,
    provider_outcome TEXT CHECK(provider_outcome IN (
        'PROVIDER_SUCCEEDED','PROVIDER_THROTTLED','PROVIDER_FAILED')),
    provider_http_status INTEGER,
    provider_completed_at TEXT,
    vectors_committed_at TEXT,
    provider_prompt_token_count INTEGER,
    provider_candidates_token_count INTEGER,
    provider_total_token_count INTEGER,
    requested_model TEXT,
    purpose TEXT,
    prompt_contract TEXT,
    estimator_version TEXT,
    base_estimated_input_tokens INTEGER,
    admitted_input_tokens INTEGER,
    calibration_provider_model_version TEXT,
    calibration_safe_ratio REAL,
    provider_model_version TEXT
);

CREATE INDEX IF NOT EXISTS news_ai_account_request_usage_window_v1
ON news_ai_account_request_usage_v1(account_id,reserved_at,model_family);

CREATE TABLE IF NOT EXISTS news_ai_quota_day_workload_v1 (
    quota_day TEXT NOT NULL,
    hour_bucket INTEGER NOT NULL CHECK(hour_bucket BETWEEN 0 AND 23),
    account_id TEXT NOT NULL,
    quota_authority TEXT NOT NULL,
    workload_class TEXT NOT NULL CHECK(workload_class IN (
        'LIVE_OPERATIONAL','CONTRACT_BACKFILL')),
    request_count INTEGER NOT NULL CHECK(request_count >= 0),
    updated_at TEXT NOT NULL,
    PRIMARY KEY(quota_day,hour_bucket,account_id,quota_authority,workload_class)
);

CREATE INDEX IF NOT EXISTS news_ai_quota_day_workload_retention_v1
ON news_ai_quota_day_workload_v1(quota_day);

CREATE TABLE IF NOT EXISTS news_ai_token_calibration_v1 (
    bucket_id TEXT PRIMARY KEY,
    requested_model TEXT NOT NULL,
    purpose TEXT NOT NULL,
    prompt_contract TEXT NOT NULL,
    estimator_version TEXT NOT NULL,
    provider_model_version TEXT NOT NULL,
    lifetime_sample_count INTEGER NOT NULL,
    effective_sample_count INTEGER NOT NULL,
    ewma_ratio REAL NOT NULL,
    ewma_absolute_error REAL NOT NULL,
    recent_ratio_window_json TEXT NOT NULL,
    recent_p99_ratio REAL NOT NULL,
    safe_ratio REAL NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(requested_model,purpose,prompt_contract,estimator_version,
           provider_model_version)
);

CREATE TABLE IF NOT EXISTS news_ai_token_calibration_route_v1 (
    requested_model TEXT NOT NULL,
    purpose TEXT NOT NULL,
    prompt_contract TEXT NOT NULL,
    estimator_version TEXT NOT NULL,
    last_provider_model_version TEXT NOT NULL,
    last_request_reserved_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(requested_model,purpose,prompt_contract,estimator_version)
);

CREATE TABLE IF NOT EXISTS news_ai_provider_dispatch_state_v1 (
    provider_scope TEXT PRIMARY KEY,
    next_eligible_at TEXT NOT NULL,
    interval_ms INTEGER NOT NULL CHECK(interval_ms BETWEEN 120 AND 5000),
    success_streak INTEGER NOT NULL DEFAULT 0,
    throttle_count INTEGER NOT NULL DEFAULT 0,
    cooldown_until TEXT,
    last_outcome TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS news_ai_provider_dispatch_task_state_v1 (
    task_class TEXT PRIMARY KEY CHECK(task_class IN (
        'ANNOTATION','IMPACT','TITLE_REPAIR','EMBEDDING','DAILY_BRIEF')),
    last_requested_at TEXT NOT NULL,
    demand_until TEXT NOT NULL,
    last_dispatched_at TEXT,
    last_pressure_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS news_ai_retrieval_mode_state_v1 (
    state_id TEXT PRIMARY KEY CHECK(state_id='NEWS_IDENTITY'),
    mode TEXT NOT NULL CHECK(mode IN (
        'WAIT_FOR_HYBRID','DETERMINISTIC_FALLBACK','HYBRID')),
    reason TEXT NOT NULL,
    mode_since TEXT NOT NULL,
    recovery_observed_at TEXT,
    pressure_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS news_ai_scheduler_migrations_v1 (
    migration_id TEXT PRIMARY KEY,
    applied_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS news_ai_failure_recoveries_v1 (
    failure_id TEXT NOT NULL,
    recovery_version TEXT NOT NULL,
    source TEXT NOT NULL,
    source_item_id TEXT NOT NULL,
    revision_number INTEGER NOT NULL,
    llm_model_version TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    authorized_at TEXT NOT NULL,
    PRIMARY KEY(
      recovery_version,source,source_item_id,revision_number,
      llm_model_version,prompt_version),
    UNIQUE(failure_id,recovery_version),
    FOREIGN KEY(failure_id) REFERENCES news_llm_failures(failure_id)
);

CREATE TABLE IF NOT EXISTS news_ai_impact_failure_recoveries_v1 (
    failure_id TEXT NOT NULL,
    recovery_version TEXT NOT NULL,
    annotation_id TEXT NOT NULL,
    llm_model_version TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    authorized_at TEXT NOT NULL,
    PRIMARY KEY(
      recovery_version,annotation_id,llm_model_version,prompt_version),
    UNIQUE(failure_id,recovery_version),
    FOREIGN KEY(failure_id) REFERENCES news_impact_failures_v1(failure_id)
);
"""


@dataclass(frozen=True)
class ApiCredential:
    account_id: str
    pool: str
    api_key: str = field(repr=False)
    credential_id: str


@dataclass(frozen=True)
class WorkProvenance:
    """Authoritative operational ownership inherited by derived AI work."""

    work_lane: str
    origin_task: str
    origin_ref: str

    def __post_init__(self) -> None:
        if self.work_lane not in WORK_LANES:
            raise ValueError("work provenance lane is not controlled")
        if not self.origin_task.strip() or not self.origin_ref.strip():
            raise ValueError("work provenance origin is required")

    @property
    def workload_class(self) -> str:
        return (
            CONTRACT_BACKFILL_WORKLOAD
            if self.work_lane == CONTRACT_BACKFILL_LANE
            else LIVE_OPERATIONAL_WORKLOAD
        )


@dataclass(frozen=True)
class ScheduledJob:
    job_id: str
    task_type: str
    source: str
    source_item_id: str
    revision_number: int
    annotation_id: str
    prompt_version: str
    priority: str
    work_lane: str
    provenance_resolved: bool
    provenance_version: str | None
    provenance_origin_task: str | None
    provenance_origin_ref: str | None
    state: str
    available_at: datetime
    lease_owner: str | None
    lease_expires_at: datetime | None
    attempt_count: int


class RetryScheduleConflict(ValueError):
    """The requested override no longer matches mutable scheduler state."""

    def __init__(self, code: str, current: dict[str, object] | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.current = current


def install_scheduler_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(SCHEDULER_SCHEMA)
    installed_at = datetime.now(UTC)
    job_columns = {
        str(row["name"])
        for row in connection.execute("PRAGMA table_info(news_ai_jobs_v1)").fetchall()
    }
    if "work_lane" not in job_columns:
        connection.execute(
            "ALTER TABLE news_ai_jobs_v1 ADD COLUMN work_lane TEXT NOT NULL "
            "DEFAULT 'LIVE' CHECK(work_lane IN ('LIVE','CONTRACT_BACKFILL'))"
        )
    if "lane_classified" not in job_columns:
        connection.execute(
            "ALTER TABLE news_ai_jobs_v1 ADD COLUMN lane_classified INTEGER "
            "NOT NULL DEFAULT 0 CHECK(lane_classified IN (0,1))"
        )
    if "provenance_resolved" not in job_columns:
        connection.execute(
            "ALTER TABLE news_ai_jobs_v1 ADD COLUMN provenance_resolved INTEGER "
            "NOT NULL DEFAULT 0 CHECK(provenance_resolved IN (0,1))"
        )
    if "provenance_version" not in job_columns:
        connection.execute(
            "ALTER TABLE news_ai_jobs_v1 ADD COLUMN provenance_version TEXT"
        )
    if "provenance_origin_task" not in job_columns:
        connection.execute(
            "ALTER TABLE news_ai_jobs_v1 ADD COLUMN provenance_origin_task TEXT"
        )
    if "provenance_origin_ref" not in job_columns:
        connection.execute(
            "ALTER TABLE news_ai_jobs_v1 ADD COLUMN provenance_origin_ref TEXT"
        )
    lane_index = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type='index' "
        "AND name='news_ai_jobs_lane_claim_v1'"
    ).fetchone()
    if lane_index is not None and "lane_classified" not in str(lane_index[0]):
        connection.execute("DROP INDEX news_ai_jobs_lane_claim_v1")
    connection.executescript(
        """
        CREATE INDEX IF NOT EXISTS news_ai_jobs_lane_claim_v1
        ON news_ai_jobs_v1(state,available_at,lane_classified,work_lane,
                           priority,task_type,created_at);
        CREATE INDEX IF NOT EXISTS news_ai_jobs_lane_migration_v1
        ON news_ai_jobs_v1(prompt_version,task_type,lane_classified,
                           created_at,job_id);
        CREATE INDEX IF NOT EXISTS news_ai_jobs_lane_health_v1
        ON news_ai_jobs_v1(task_type,lane_classified,work_lane,state,
                           available_at,created_at);
        CREATE INDEX IF NOT EXISTS news_ai_jobs_lane_oldest_v1
        ON news_ai_jobs_v1(task_type,lane_classified,work_lane,state,created_at);
        CREATE INDEX IF NOT EXISTS news_ai_jobs_provenance_migration_v1
        ON news_ai_jobs_v1(task_type,provenance_resolved,created_at,job_id);
        CREATE TABLE IF NOT EXISTS news_annotation_contract_backfill_v1 (
          prompt_version TEXT PRIMARY KEY,
          activated_at TEXT NOT NULL,
          cursor_first_seen TEXT,
          cursor_source TEXT,
          cursor_source_item_id TEXT,
          cursor_revision_number INTEGER,
          state TEXT NOT NULL CHECK(state IN ('ACTIVE','COMPLETE')),
          updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS news_annotation_work_lane_migrations_v1 (
          prompt_version TEXT PRIMARY KEY,
          activated_at TEXT NOT NULL,
          cursor_created_at TEXT,
          cursor_job_id TEXT,
          processed_count INTEGER NOT NULL DEFAULT 0,
          state TEXT NOT NULL CHECK(state IN ('ACTIVE','COMPLETE')),
          updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS news_downstream_provenance_migrations_v1 (
          provenance_version TEXT PRIMARY KEY,
          cursor_created_at TEXT,
          cursor_job_id TEXT,
          processed_count INTEGER NOT NULL DEFAULT 0,
          resolved_count INTEGER NOT NULL DEFAULT 0,
          unresolved_count INTEGER NOT NULL DEFAULT 0,
          leased_skipped_count INTEGER NOT NULL DEFAULT 0,
          pass_count INTEGER NOT NULL DEFAULT 0,
          state TEXT NOT NULL CHECK(state IN ('ACTIVE','WAITING_INPUT','COMPLETE')),
          updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS news_annotation_transition_state_v1 (
          from_prompt_version TEXT NOT NULL,
          to_prompt_version TEXT NOT NULL,
          transition_kind TEXT NOT NULL CHECK(transition_kind IN (
            'REUSE_COMPATIBLE','DETERMINISTIC_MIGRATION','MODEL_REVIEW_REQUIRED')),
          transition_fingerprint TEXT NOT NULL,
          transition_contract_json TEXT NOT NULL,
          cursor_parsed_at TEXT,
          cursor_annotation_id TEXT,
          processed_count INTEGER NOT NULL DEFAULT 0,
          state TEXT NOT NULL CHECK(state IN ('ACTIVE','COMPLETE')),
          updated_at TEXT NOT NULL,
          PRIMARY KEY(from_prompt_version,to_prompt_version)
        );
        CREATE TABLE IF NOT EXISTS news_annotation_transition_projections_v1 (
          target_annotation_id TEXT PRIMARY KEY,
          source_annotation_id TEXT NOT NULL,
          from_prompt_version TEXT NOT NULL,
          to_prompt_version TEXT NOT NULL,
          transition_kind TEXT NOT NULL,
          migrator_version TEXT,
          transition_fingerprint TEXT NOT NULL,
          source_annotation_hash TEXT NOT NULL,
          projected_annotation_hash TEXT NOT NULL,
          applied_at TEXT NOT NULL,
          UNIQUE(source_annotation_id,to_prompt_version),
          FOREIGN KEY(target_annotation_id) REFERENCES news_annotations(annotation_id),
          FOREIGN KEY(source_annotation_id) REFERENCES news_annotations(annotation_id)
        );
        CREATE TABLE IF NOT EXISTS news_annotation_transition_failures_v1 (
          source_annotation_id TEXT NOT NULL,
          to_prompt_version TEXT NOT NULL,
          transition_kind TEXT NOT NULL,
          failure_code TEXT NOT NULL,
          failure_detail TEXT NOT NULL,
          failed_at TEXT NOT NULL,
          PRIMARY KEY(source_annotation_id,to_prompt_version)
        );
        CREATE TABLE IF NOT EXISTS
          news_annotation_transition_contract_failures_v1 (
            from_prompt_version TEXT NOT NULL,
            to_prompt_version TEXT NOT NULL,
            persisted_fingerprint TEXT,
            current_fingerprint TEXT NOT NULL,
            failure_code TEXT NOT NULL,
            failure_detail TEXT NOT NULL,
            failed_at TEXT NOT NULL,
            PRIMARY KEY(from_prompt_version,to_prompt_version)
        );
        """
    )
    transition_state_columns = {
        str(row["name"])
        for row in connection.execute(
            "PRAGMA table_info(news_annotation_transition_state_v1)"
        ).fetchall()
    }
    if "transition_fingerprint" not in transition_state_columns:
        connection.execute(
            "ALTER TABLE news_annotation_transition_state_v1 "
            "ADD COLUMN transition_fingerprint TEXT"
        )
    if "transition_contract_json" not in transition_state_columns:
        connection.execute(
            "ALTER TABLE news_annotation_transition_state_v1 "
            "ADD COLUMN transition_contract_json TEXT"
        )
    projection_columns = {
        str(row["name"])
        for row in connection.execute(
            "PRAGMA table_info(news_annotation_transition_projections_v1)"
        ).fetchall()
    }
    if "transition_fingerprint" not in projection_columns:
        connection.execute(
            "ALTER TABLE news_annotation_transition_projections_v1 "
            "ADD COLUMN transition_fingerprint TEXT"
        )
    provider_task_sql = str(connection.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' "
        "AND name='news_ai_provider_dispatch_task_state_v1'"
    ).fetchone()[0])
    if "DAILY_BRIEF" not in provider_task_sql:
        # Provider task state is mutable scheduling evidence. Rebuild the old
        # checked table in place so existing pressure/rotation history survives.
        connection.executescript(
            """BEGIN IMMEDIATE;
               ALTER TABLE news_ai_provider_dispatch_task_state_v1
                 RENAME TO news_ai_provider_dispatch_task_state_v1_legacy;
               CREATE TABLE news_ai_provider_dispatch_task_state_v1 (
                 task_class TEXT PRIMARY KEY CHECK(task_class IN (
                   'ANNOTATION','IMPACT','TITLE_REPAIR','EMBEDDING','DAILY_BRIEF')),
                 last_requested_at TEXT NOT NULL,
                 demand_until TEXT NOT NULL,
                 last_dispatched_at TEXT,
                 last_pressure_json TEXT NOT NULL
               );
               INSERT INTO news_ai_provider_dispatch_task_state_v1
                 SELECT * FROM news_ai_provider_dispatch_task_state_v1_legacy;
               DROP TABLE news_ai_provider_dispatch_task_state_v1_legacy;
               COMMIT;"""
        )
    minute_columns = {
        str(row["name"])
        for row in connection.execute(
            "PRAGMA table_info(news_ai_account_minute_usage_v1)"
        ).fetchall()
    }
    if "input_token_count" not in minute_columns:
        connection.execute(
            "ALTER TABLE news_ai_account_minute_usage_v1 "
            "ADD COLUMN input_token_count INTEGER NOT NULL DEFAULT 0"
        )
    request_usage_columns = {
        str(row["name"])
        for row in connection.execute(
            "PRAGMA table_info(news_ai_account_request_usage_v1)"
        ).fetchall()
    }
    request_usage_additions = {
        "attempted_at": "TEXT",
        "provider_outcome": "TEXT",
        "provider_http_status": "INTEGER",
        "provider_completed_at": "TEXT",
        "vectors_committed_at": "TEXT",
        "provider_prompt_token_count": "INTEGER",
        "provider_candidates_token_count": "INTEGER",
        "provider_total_token_count": "INTEGER",
        "requested_model": "TEXT",
        "purpose": "TEXT",
        "prompt_contract": "TEXT",
        "estimator_version": "TEXT",
        "base_estimated_input_tokens": "INTEGER",
        "admitted_input_tokens": "INTEGER",
        "calibration_provider_model_version": "TEXT",
        "calibration_safe_ratio": "REAL",
        "provider_model_version": "TEXT",
        "workload_class": "TEXT NOT NULL DEFAULT 'LIVE_OPERATIONAL'",
    }
    for name, declaration in request_usage_additions.items():
        if name not in request_usage_columns:
            connection.execute(
                "ALTER TABLE news_ai_account_request_usage_v1 "
                f"ADD COLUMN {name} {declaration}"
            )
    migration_id = "exact-rolling-capacity-v1"
    migrated = connection.execute(
        "SELECT 1 FROM news_ai_scheduler_migrations_v1 WHERE migration_id=?",
        (migration_id,),
    ).fetchone()
    if migrated is None:
        cutoff = _iso(installed_at - timedelta(seconds=60))
        rows = connection.execute(
            """SELECT minute_bucket,account_id,model_family,request_count,
                      input_token_count,updated_at
               FROM news_ai_account_minute_usage_v1
               WHERE updated_at>?""",
            (cutoff,),
        ).fetchall()
        for row in rows:
            (
                legacy_minute, legacy_account, legacy_model,
                legacy_requests, legacy_tokens, legacy_updated_at,
            ) = tuple(row)
            identity = "\x1f".join(str(value) for value in (
                legacy_minute, legacy_account, legacy_model,
            ))
            usage_id = "migration-" + hashlib.sha256(
                identity.encode("utf-8"),
            ).hexdigest()[:24]
            connection.execute(
                """INSERT OR IGNORE INTO news_ai_account_request_usage_v1
                   (usage_id,account_id,model_family,request_count,
                    input_token_count,reserved_at)
                   VALUES (?,?,?,?,?,?)""",
                (
                    usage_id, legacy_account, legacy_model,
                    legacy_requests, legacy_tokens, legacy_updated_at,
                ),
            )
        connection.execute(
            "INSERT OR IGNORE INTO news_ai_scheduler_migrations_v1 VALUES (?,?)",
            (migration_id, _iso(installed_at)),
        )
    connection.execute(
        "DELETE FROM news_ai_account_request_usage_v1 WHERE reserved_at<=?",
        (_iso(installed_at - timedelta(days=1)),),
    )
    connection.execute(
        "DELETE FROM news_ai_scheduler_deferrals_v1 WHERE deferred_at<=?",
        (_iso(installed_at - SCHEDULER_DEFERRAL_RETENTION),),
    )
    oldest_quota_day = quota_day(installed_at - timedelta(days=BACKFILL_FORECAST_DAYS))
    connection.execute(
        "DELETE FROM news_ai_quota_day_workload_v1 WHERE quota_day<?",
        (oldest_quota_day,),
    )
    connection.commit()
    from .critical_annotation_state import install_annotation_job_count_schema
    install_annotation_job_count_schema(connection)


def rolling_account_usage(
    connection: sqlite3.Connection,
    *,
    account_id: str,
    model_families: tuple[str, ...],
    now: datetime,
    share_across_accounts: bool = False,
) -> tuple[int, int]:
    """Return requests and tokens reserved during the exact trailing 60 seconds."""
    families = tuple(dict.fromkeys(model_families))
    placeholders = ",".join("?" for _ in families)
    account_clause = "" if share_across_accounts else "AND account_id=?"
    parameters: tuple[object, ...] = (
        _iso(now - timedelta(seconds=60)),
        _iso(now),
        *((account_id,) if not share_across_accounts else ()),
        *families,
    )
    row = connection.execute(
        f"""SELECT COALESCE(sum(request_count),0) AS requests,
                   COALESCE(sum({EFFECTIVE_INPUT_TOKENS_SQL}),0) AS tokens
            FROM news_ai_account_request_usage_v1
            WHERE reserved_at>? AND reserved_at<=? {account_clause}
              AND model_family IN ({placeholders})""",
        parameters,
    ).fetchone()
    return int(row["requests"]), int(row["tokens"])


def _calibration_identity(
    requested_model: str,
    purpose: str,
    prompt_contract: str,
    estimator_version: str,
    provider_model_version: str,
) -> str:
    payload = "\x1f".join((
        requested_model, purpose, prompt_contract, estimator_version,
        provider_model_version,
    ))
    return "token-calibration-" + hashlib.sha256(
        payload.encode("utf-8"),
    ).hexdigest()[:32]


def _controlled_calibration_key(value: str, label: str) -> str:
    normalized = value.strip()
    if not normalized or len(normalized) > 200:
        raise ValueError(f"{label} is not a bounded calibration key")
    return normalized


def calibrated_input_tokens(
    connection: sqlite3.Connection,
    *,
    requested_model: str,
    purpose: str,
    prompt_contract: str,
    estimator_version: str,
    base_estimated_input_tokens: int,
) -> tuple[int, str | None, float]:
    """Resolve one bounded, durable calibration bucket for admission."""
    base_tokens = max(0, int(base_estimated_input_tokens))
    keys = tuple(_controlled_calibration_key(value, label) for value, label in (
        (requested_model, "requested model"),
        (purpose, "purpose"),
        (prompt_contract, "prompt contract"),
        (estimator_version, "estimator version"),
    ))
    row = connection.execute(
        """SELECT r.last_provider_model_version,c.safe_ratio
           FROM news_ai_token_calibration_route_v1 r
           JOIN news_ai_token_calibration_v1 c
             ON c.requested_model=r.requested_model
            AND c.purpose=r.purpose
            AND c.prompt_contract=r.prompt_contract
            AND c.estimator_version=r.estimator_version
            AND c.provider_model_version=r.last_provider_model_version
           WHERE r.requested_model=? AND r.purpose=?
             AND r.prompt_contract=? AND r.estimator_version=?""",
        keys,
    ).fetchone()
    if row is None:
        return base_tokens, None, 1.0
    try:
        safe_ratio = float(row["safe_ratio"])
    except (TypeError, ValueError):
        return base_tokens, None, 1.0
    if not math.isfinite(safe_ratio) or not (
        TOKEN_CALIBRATION_MIN_RATIO <= safe_ratio <= TOKEN_CALIBRATION_MAX_RATIO
    ):
        return base_tokens, None, 1.0
    admitted = math.ceil(base_tokens * safe_ratio)
    return admitted, str(row["last_provider_model_version"]), safe_ratio


def _recent_p99(values: list[float]) -> float:
    ordered = sorted(values)
    index = max(0, math.ceil(0.99 * len(ordered)) - 1)
    return ordered[index]


def _calibration_floor(sample_count: int) -> float:
    if sample_count < 5:
        return 1.0
    if sample_count < 20:
        return 0.9
    return TOKEN_CALIBRATION_MIN_RATIO


def _record_token_calibration_sample_locked(
    connection: sqlite3.Connection,
    *,
    usage_row: sqlite3.Row,
    provider_prompt_token_count: int,
    provider_model_version: str,
    updated_at: str,
) -> None:
    base_tokens = int(usage_row["base_estimated_input_tokens"] or 0)
    actual_tokens = int(provider_prompt_token_count)
    if base_tokens <= 0 or actual_tokens <= 0:
        return
    ratio = actual_tokens / base_tokens
    if not math.isfinite(ratio) or not (
        TOKEN_CALIBRATION_MIN_RATIO / 2
        <= ratio <= TOKEN_CALIBRATION_MAX_RATIO
    ):
        return
    requested_model, purpose, prompt_contract, estimator_version = (
        _controlled_calibration_key(str(usage_row[name] or ""), name)
        for name in (
            "requested_model", "purpose", "prompt_contract", "estimator_version",
        )
    )
    exact_model = _controlled_calibration_key(
        provider_model_version, "provider model version",
    )
    bucket_id = _calibration_identity(
        requested_model, purpose, prompt_contract, estimator_version, exact_model,
    )
    existing = connection.execute(
        "SELECT * FROM news_ai_token_calibration_v1 WHERE bucket_id=?",
        (bucket_id,),
    ).fetchone()
    recent: list[float] = []
    lifetime_count = 0
    old_ewma = ratio
    old_error = 0.0
    old_safe: float | None = None
    if existing is not None:
        try:
            loaded = json.loads(str(existing["recent_ratio_window_json"]))
            if not isinstance(loaded, list):
                raise ValueError("calibration window is not a list")
            recent = [float(item) for item in loaded]
            if any(
                not math.isfinite(item)
                or not (TOKEN_CALIBRATION_MIN_RATIO / 2
                        <= item <= TOKEN_CALIBRATION_MAX_RATIO)
                for item in recent
            ):
                raise ValueError("calibration window contains invalid ratios")
            recent = recent[-TOKEN_CALIBRATION_RECENT_LIMIT:]
            lifetime_count = max(0, int(existing["lifetime_sample_count"]))
            old_ewma = float(existing["ewma_ratio"])
            old_error = max(0.0, float(existing["ewma_absolute_error"]))
            old_safe = float(existing["safe_ratio"])
            if not all(math.isfinite(item) for item in (
                old_ewma, old_error, old_safe,
            )):
                raise ValueError("calibration scalar is invalid")
        except (TypeError, ValueError, json.JSONDecodeError):
            recent = []
            lifetime_count = 0
            old_ewma = ratio
            old_error = 0.0
            old_safe = None
    new_count = lifetime_count + 1
    if lifetime_count == 0:
        ewma = ratio
        ewma_error = 0.0
    else:
        ewma = (
            TOKEN_CALIBRATION_ALPHA * ratio
            + (1 - TOKEN_CALIBRATION_ALPHA) * old_ewma
        )
        ewma_error = (
            TOKEN_CALIBRATION_ALPHA * abs(ratio - old_ewma)
            + (1 - TOKEN_CALIBRATION_ALPHA) * old_error
        )
    recent.append(ratio)
    recent = recent[-TOKEN_CALIBRATION_RECENT_LIMIT:]
    recent_p99 = _recent_p99(recent)
    measured_safe = max(
        ratio * TOKEN_CALIBRATION_P99_MARGIN,
        recent_p99 * TOKEN_CALIBRATION_P99_MARGIN,
        ewma + 3 * ewma_error,
    )
    safe_ratio = max(_calibration_floor(new_count), measured_safe)
    if old_safe is not None:
        safe_ratio = max(
            safe_ratio,
            old_safe * (1 - TOKEN_CALIBRATION_MAX_DOWNWARD_STEP),
        )
    safe_ratio = min(TOKEN_CALIBRATION_MAX_RATIO, safe_ratio)
    connection.execute(
        """INSERT INTO news_ai_token_calibration_v1
           (bucket_id,requested_model,purpose,prompt_contract,estimator_version,
            provider_model_version,lifetime_sample_count,effective_sample_count,
            ewma_ratio,ewma_absolute_error,recent_ratio_window_json,
            recent_p99_ratio,safe_ratio,updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(bucket_id) DO UPDATE SET
             lifetime_sample_count=excluded.lifetime_sample_count,
             effective_sample_count=excluded.effective_sample_count,
             ewma_ratio=excluded.ewma_ratio,
             ewma_absolute_error=excluded.ewma_absolute_error,
             recent_ratio_window_json=excluded.recent_ratio_window_json,
             recent_p99_ratio=excluded.recent_p99_ratio,
             safe_ratio=excluded.safe_ratio,
             updated_at=excluded.updated_at""",
        (
            bucket_id, requested_model, purpose, prompt_contract,
            estimator_version, exact_model, new_count, len(recent), ewma,
            ewma_error, json.dumps(recent, separators=(",", ":")),
            recent_p99, safe_ratio, updated_at,
        ),
    )
    reserved_at = str(usage_row["reserved_at"])
    connection.execute(
        """INSERT INTO news_ai_token_calibration_route_v1
           (requested_model,purpose,prompt_contract,estimator_version,
            last_provider_model_version,last_request_reserved_at,updated_at)
           VALUES (?,?,?,?,?,?,?)
           ON CONFLICT(requested_model,purpose,prompt_contract,estimator_version)
           DO UPDATE SET
             last_provider_model_version=CASE
               WHEN excluded.last_request_reserved_at>=last_request_reserved_at
                 THEN excluded.last_provider_model_version
               ELSE last_provider_model_version END,
             last_request_reserved_at=max(
               last_request_reserved_at,excluded.last_request_reserved_at),
             updated_at=CASE
               WHEN excluded.last_request_reserved_at>=last_request_reserved_at
                 THEN excluded.updated_at ELSE updated_at END""",
        (
            requested_model, purpose, prompt_contract, estimator_version,
            exact_model, reserved_at, updated_at,
        ),
    )


def _minute_capacity_next_eligible(
    connection: sqlite3.Connection,
    *,
    account_id: str,
    model_families: tuple[str, ...],
    now: datetime,
    share_across_accounts: bool,
    requested_requests: int,
    requested_tokens: int,
    requests_per_minute: int,
    input_tokens_per_minute: int | None,
) -> str | None:
    """Return the first exact rolling-window expiry that admits the request."""
    placeholders = ",".join("?" for _ in model_families)
    account_clause = "" if share_across_accounts else "AND account_id=?"
    parameters: tuple[object, ...] = (
        _iso(now - timedelta(seconds=60)),
        *((account_id,) if not share_across_accounts else ()),
        *model_families,
    )
    rows = connection.execute(
        f"""SELECT request_count,reserved_at,
                   {EFFECTIVE_INPUT_TOKENS_SQL} AS effective_input_tokens
            FROM news_ai_account_request_usage_v1
            WHERE reserved_at>? {account_clause}
              AND model_family IN ({placeholders})
            ORDER BY reserved_at""",
        parameters,
    ).fetchall()
    for row in rows:
        candidate = datetime.fromisoformat(str(row["reserved_at"])) + timedelta(
            seconds=60,
        )
        remaining = [
            item for item in rows
            if datetime.fromisoformat(str(item["reserved_at"])) > candidate - timedelta(
                seconds=60,
            )
        ]
        requests = sum(int(item["request_count"]) for item in remaining)
        tokens = sum(int(item["effective_input_tokens"]) for item in remaining)
        if requests + requested_requests > requests_per_minute:
            continue
        if (
            input_tokens_per_minute is not None
            and tokens + requested_tokens > input_tokens_per_minute
        ):
            continue
        return _iso(candidate)
    return None


def _iso(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("scheduler timestamps must be timezone-aware")
    return value.astimezone(UTC).isoformat(timespec="microseconds")


def _credential_id(api_key: str) -> str:
    return derived_credential_id(api_key)


def _contains_raw_key(identifier: str, api_key: str) -> bool:
    return api_key in identifier


def _runtime_environment_value(name: str) -> str:
    """Read mutable user configuration instead of a stale process snapshot."""
    if os.name == "nt":
        try:
            import winreg

            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
                value, _ = winreg.QueryValueEx(key, name)
                return str(value or "")
        except FileNotFoundError:
            # Non-interactive deployments may inject secrets only into the
            # process environment instead of the interactive user's registry.
            return os.environ.get(name, "")
        except OSError:
            # A restricted service account may not have a readable HKCU hive.
            # Its explicitly injected process environment remains authoritative.
            pass
    return os.environ.get(name, "")


def configured_api_credentials(
    *,
    raw_accounts: str | None = None,
    legacy_keys: tuple[str, ...] | None = None,
) -> tuple[ApiCredential, ...]:
    """Load account-aware pools without exposing secret key material.

    ``GEMINI_API_ACCOUNTS`` is a JSON list of objects with ``account_id``,
    ``pool``, and ``api_keys``. An optional parallel ``credential_ids`` list
    pins stable non-secret identifiers across identity-scheme migrations.
    Legacy key variables remain routine-only compatibility inputs.
    """
    raw = (
        raw_accounts
        if raw_accounts is not None
        else _runtime_environment_value("GEMINI_API_ACCOUNTS")
    )
    entries: list[dict[str, object]] = []
    if raw.strip():
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as error:
            raise ValueError("GEMINI_API_ACCOUNTS is not valid JSON") from error
        if not isinstance(parsed, list):
            raise ValueError("GEMINI_API_ACCOUNTS must be a JSON list")
        entries = parsed
    else:
        keys = list(legacy_keys or ())
        if legacy_keys is None:
            keys.extend(_runtime_environment_value("GEMINI_API_KEYS").split(";"))
            keys.append(_runtime_environment_value("GEMINI_API_KEY"))
        entries = [
            {
                "account_id": f"legacy-{_credential_id(key.strip())}",
                "pool": ROUTINE_POOL,
                "api_keys": [key.strip()],
            }
            for key in keys if key.strip()
        ]

    normalized_entries: list[tuple[dict[str, object], tuple[str, ...]]] = []
    configured_keys: list[str] = []
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("each Gemini account must be an object")
        raw_keys = entry.get("api_keys")
        if not isinstance(raw_keys, list) or not raw_keys:
            raise ValueError("Gemini account api_keys must be a non-empty list")
        normalized_keys = tuple(str(raw_key or "").strip() for raw_key in raw_keys)
        if any(not key for key in normalized_keys):
            raise ValueError("Gemini account contains an empty API key")
        normalized_entries.append((entry, normalized_keys))
        configured_keys.extend(
            key for key in normalized_keys if key not in configured_keys
        )

    credentials: list[ApiCredential] = []
    account_pools: dict[str, str] = {}
    key_accounts: dict[str, str] = {}
    credential_id_keys: dict[str, str] = {}
    for entry, api_keys in normalized_entries:
        account_id = str(entry.get("account_id") or "").strip()
        pool = str(entry.get("pool") or "").strip().upper()
        credential_ids = entry.get("credential_ids")
        if not account_id:
            raise ValueError("Gemini account_id is required")
        if any(_contains_raw_key(account_id, key) for key in configured_keys):
            raise ValueError("Gemini account_id must not contain an API key")
        if pool not in {ROUTINE_POOL, PREEMPTIBLE_POOL}:
            raise ValueError("Gemini account pool is not controlled")
        if credential_ids is not None and (
            not isinstance(credential_ids, list)
            or len(credential_ids) != len(api_keys)
        ):
            raise ValueError(
                "Gemini account credential_ids must match api_keys"
            )
        prior_pool = account_pools.setdefault(account_id, pool)
        if prior_pool != pool:
            raise ValueError("one Gemini account cannot belong to two pools")
        for key_index, api_key in enumerate(api_keys):
            prior_account = key_accounts.setdefault(api_key, account_id)
            if prior_account != account_id:
                raise ValueError("one Gemini API key cannot belong to two accounts")
            credential_id = _credential_id(api_key)
            if credential_ids is not None:
                credential_id = str(credential_ids[key_index] or "").strip()
                if not credential_id:
                    raise ValueError("Gemini credential_id is required")
                if any(
                    _contains_raw_key(credential_id, key)
                    for key in configured_keys
                ):
                    raise ValueError(
                        "Gemini credential_id must not contain an API key"
                    )
            prior_key = credential_id_keys.setdefault(credential_id, api_key)
            if prior_key != api_key:
                raise ValueError("Gemini credential_id must be unique")
            credential = ApiCredential(
                account_id=account_id,
                pool=pool,
                api_key=api_key,
                credential_id=credential_id,
            )
            if credential not in credentials:
                credentials.append(credential)
    return tuple(credentials)


def _job_id(
    task_type: str,
    source: str,
    source_item_id: str,
    revision_number: int,
    annotation_id: str,
    prompt_version: str,
) -> str:
    identity = "|".join((
        task_type, source, source_item_id, str(revision_number),
        annotation_id, prompt_version,
    ))
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def resolve_work_provenance(
    connection: sqlite3.Connection,
    *,
    source: str,
    source_item_id: str,
    revision_number: int,
    annotation_id: str = "",
    current_prompt_version: str,
) -> WorkProvenance | None:
    """Resolve derived ownership from the classified current annotation job."""
    projection_filter = ""
    projection_parameters: list[object] = [
        source, source_item_id, revision_number, current_prompt_version,
    ]
    if annotation_id:
        projection_filter = "AND a.annotation_id=?"
        projection_parameters.append(annotation_id)
    projection = connection.execute(
        f"""SELECT p.transition_fingerprint,p.source_annotation_id
            FROM news_annotation_transition_projections_v1 p
            JOIN news_annotations a ON a.annotation_id=p.target_annotation_id
            WHERE a.source=? AND a.source_item_id=? AND a.revision_number=?
              AND a.prompt_version=? {projection_filter}
            ORDER BY a.parsed_at DESC,a.annotation_id DESC LIMIT 2""",
        tuple(projection_parameters),
    ).fetchall()
    annotation_filter = ""
    parameters: list[object] = [
        source, source_item_id, revision_number, current_prompt_version,
    ]
    if annotation_id:
        annotation_filter = "AND a.annotation_id=?"
        parameters.append(annotation_id)
    row = connection.execute(
        f"""SELECT j.job_id,j.work_lane
            FROM news_ai_jobs_v1 j
            WHERE j.task_type='ACTIVE_ANNOTATION'
              AND j.source=? AND j.source_item_id=? AND j.revision_number=?
              AND j.prompt_version=? AND j.lane_classified=1
              AND EXISTS (
                SELECT 1 FROM news_annotations a
                WHERE a.source=j.source AND a.source_item_id=j.source_item_id
                  AND a.revision_number=j.revision_number
                  AND a.prompt_version=j.prompt_version {annotation_filter})
            LIMIT 2""",
        tuple(parameters),
    ).fetchall()
    if len(row) != 1:
        return None
    if len(projection) > 1:
        return None
    if projection:
        return WorkProvenance(
            CONTRACT_BACKFILL_LANE,
            "SEMANTIC_TRANSITION",
            f"{projection[0]['transition_fingerprint']}:"
            f"{projection[0]['source_annotation_id']}",
        )
    lane = str(row[0]["work_lane"])
    if lane not in WORK_LANES:
        return None
    return WorkProvenance(lane, "ACTIVE_ANNOTATION", str(row[0]["job_id"]))


def enqueue_job(
    connection: sqlite3.Connection,
    *,
    task_type: str,
    source: str,
    source_item_id: str,
    revision_number: int,
    prompt_version: str,
    priority: str,
    work_lane: str = LIVE_LANE,
    provenance: WorkProvenance | None = None,
    annotation_id: str = "",
    reopen_completed: bool = False,
    now: datetime | None = None,
) -> str:
    if task_type not in TASKS:
        raise ValueError("scheduler task type is not controlled")
    if priority not in PRIORITIES:
        raise ValueError("scheduler priority is not controlled")
    if work_lane not in WORK_LANES:
        raise ValueError("scheduler work lane is not controlled")
    created = now or datetime.now(UTC)
    job_id = _job_id(
        task_type, source, source_item_id, revision_number,
        annotation_id, prompt_version,
    )
    resolved_provenance = provenance or WorkProvenance(
        work_lane, task_type, job_id,
    )
    if resolved_provenance.work_lane != work_lane:
        raise ValueError("scheduler job lane disagrees with workload provenance")
    if resolved_provenance.work_lane not in WORK_LANES:
        raise ValueError("scheduler provenance lane is not controlled")
    timestamp = _iso(created)
    with connection:
        connection.execute(
            """INSERT OR IGNORE INTO news_ai_jobs_v1
            (job_id,task_type,source,source_item_id,revision_number,annotation_id,
             prompt_version,priority,state,available_at,lease_owner,
             lease_expires_at,attempt_count,last_error,created_at,updated_at,
             completed_at,work_lane,lane_classified,provenance_resolved,
             provenance_version,provenance_origin_task,provenance_origin_ref)
             VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                job_id, task_type, source, source_item_id, revision_number,
                annotation_id, prompt_version, priority, "QUEUED", timestamp,
                None, None, 0, None, timestamp, timestamp, None, work_lane, 1,
                1, WORK_PROVENANCE_VERSION,
                resolved_provenance.origin_task,
                resolved_provenance.origin_ref,
            ),
        )
        connection.execute(
            """UPDATE news_ai_jobs_v1
               SET work_lane=?,priority=?,lane_classified=1,
                   provenance_resolved=1,provenance_version=?,
                   provenance_origin_task=?,provenance_origin_ref=?
               WHERE job_id=? AND state IN ('QUEUED','BACKING_OFF')""",
            (
                work_lane, priority, WORK_PROVENANCE_VERSION,
                resolved_provenance.origin_task,
                resolved_provenance.origin_ref, job_id,
            ),
        )
        if reopen_completed:
            connection.execute(
                """UPDATE news_ai_jobs_v1
                   SET state='QUEUED',available_at=?,lease_owner=NULL,
                       lease_expires_at=NULL,last_error=NULL,
                       updated_at=?,completed_at=NULL
                   WHERE job_id=? AND state='COMPLETED'""",
                (timestamp, timestamp, job_id),
            )
    return job_id


def enqueue_derived_ai_job(
    connection: sqlite3.Connection,
    *,
    provenance: WorkProvenance,
    task_type: str,
    source: str,
    source_item_id: str,
    revision_number: int,
    prompt_version: str,
    priority: str,
    annotation_id: str = "",
    now: datetime | None = None,
) -> str:
    """Enqueue history-sensitive work without an implicit LIVE fallback."""
    effective_priority = (
        "BACKGROUND"
        if provenance.work_lane == CONTRACT_BACKFILL_LANE
        else priority
    )
    return enqueue_job(
        connection,
        task_type=task_type,
        source=source,
        source_item_id=source_item_id,
        revision_number=revision_number,
        annotation_id=annotation_id,
        prompt_version=prompt_version,
        priority=effective_priority,
        work_lane=provenance.work_lane,
        provenance=provenance,
        now=now,
    )


def record_job_attempt(
    connection: sqlite3.Connection,
    *,
    job: ScheduledJob,
    credential: ApiCredential,
    status: dict[str, object],
    attempted_at: datetime,
) -> None:
    """Persist one sanitized scheduler outcome for diagnosis and recovery."""
    identity = "|".join((
        job.job_id, str(job.attempt_count), credential.account_id,
        credential.credential_id,
    ))
    provider_status = status.get("provider_http_status")
    failure_evidence = status.get("failure_evidence")
    error_detail = (
        json.dumps(
            failure_evidence, ensure_ascii=False, sort_keys=True,
            separators=(",", ":"),
        )
        if isinstance(failure_evidence, dict)
        else str(status.get("error") or status.get("reason") or "")
    )
    with connection:
        connection.execute(
            """INSERT OR IGNORE INTO news_ai_job_attempts_v1 VALUES
            (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                hashlib.sha256(identity.encode("utf-8")).hexdigest(),
                job.job_id, job.attempt_count, credential.account_id,
                credential.credential_id, str(status.get("status") or "ERROR"),
                str(status.get("failure_code") or "") or None,
                str(status.get("error_type") or "") or None,
                int(provider_status) if isinstance(provider_status, int) else None,
                error_detail[:500] or None,
                _iso(attempted_at),
                str(status.get("next_retry_at") or "") or None,
            ),
        )


def record_scheduler_deferral(
    connection: sqlite3.Connection,
    *,
    job: ScheduledJob,
    credential: ApiCredential,
    status: dict[str, object],
    deferred_at: datetime,
) -> None:
    """Persist a non-attempt maintenance/pacing deferral for operations."""
    failure_code = str(status.get("failure_code") or "")
    evidence = status.get("failure_evidence")
    serialized = (
        json.dumps(evidence, ensure_ascii=False, sort_keys=True,
                   separators=(",", ":"))[:500]
        if isinstance(evidence, dict) else None
    )
    coalesced = failure_code == "BACKFILL_BUDGET_DEFERRED"
    identity = "|".join((
        job.job_id, credential.account_id, failure_code,
        "coalesced" if coalesced else _iso(deferred_at),
    ))
    deferral_id = hashlib.sha256(identity.encode("utf-8")).hexdigest()
    with connection:
        connection.execute(
            "DELETE FROM news_ai_scheduler_deferrals_v1 WHERE deferred_at<=?",
            (_iso(deferred_at - SCHEDULER_DEFERRAL_RETENTION),),
        )
        if coalesced:
            existing = connection.execute(
                "SELECT deferred_at FROM news_ai_scheduler_deferrals_v1 "
                "WHERE deferral_id=?",
                (deferral_id,),
            ).fetchone()
            if existing is not None and datetime.fromisoformat(
                str(existing["deferred_at"])
            ) > deferred_at - timedelta(minutes=5):
                return
        connection.execute(
            """INSERT INTO news_ai_scheduler_deferrals_v1
               VALUES (?,?,?,?,?,?,?,?)
               ON CONFLICT(deferral_id) DO UPDATE SET
                 evidence_json=excluded.evidence_json,
                 deferred_at=excluded.deferred_at,
                 next_retry_at=excluded.next_retry_at""",
            (
                deferral_id, job.job_id, job.task_type, credential.account_id,
                failure_code, serialized, _iso(deferred_at),
                str(status.get("next_retry_at") or "") or None,
            ),
        )


def _job_from_row(row: sqlite3.Row) -> ScheduledJob:
    return ScheduledJob(
        job_id=str(row["job_id"]),
        task_type=str(row["task_type"]),
        source=str(row["source"]),
        source_item_id=str(row["source_item_id"]),
        revision_number=int(row["revision_number"]),
        annotation_id=str(row["annotation_id"]),
        prompt_version=str(row["prompt_version"]),
        priority=str(row["priority"]),
        work_lane=str(row["work_lane"]),
        provenance_resolved=bool(row["provenance_resolved"]),
        provenance_version=(
            str(row["provenance_version"])
            if row["provenance_version"] else None
        ),
        provenance_origin_task=(
            str(row["provenance_origin_task"])
            if row["provenance_origin_task"] else None
        ),
        provenance_origin_ref=(
            str(row["provenance_origin_ref"])
            if row["provenance_origin_ref"] else None
        ),
        state=str(row["state"]),
        available_at=datetime.fromisoformat(str(row["available_at"])),
        lease_owner=str(row["lease_owner"]) if row["lease_owner"] else None,
        lease_expires_at=(
            datetime.fromisoformat(str(row["lease_expires_at"]))
            if row["lease_expires_at"] else None
        ),
        attempt_count=int(row["attempt_count"]),
    )


def _retry_job_snapshot(row: sqlite3.Row) -> dict[str, object]:
    keys = set(row.keys())
    return {
        "job_id": str(row["job_id"]),
        "task_type": str(row["task_type"]),
        "source": str(row["source"]),
        "source_item_id": str(row["source_item_id"]),
        "state": str(row["state"]),
        "priority": str(row["priority"]),
        "available_at": str(row["available_at"]),
        "attempt_count": int(row["attempt_count"]),
        "last_error": str(row["last_error"]) if row["last_error"] else None,
        "last_failure_at": (
            str(row["last_failure_at"])
            if "last_failure_at" in keys and row["last_failure_at"] else None
        ),
        "title": (
            str(row["title"])
            if "title" in keys and row["title"] else str(row["source_item_id"])
        ),
        "lease_owner": str(row["lease_owner"]) if row["lease_owner"] else None,
        "lease_expires_at": (
            str(row["lease_expires_at"]) if row["lease_expires_at"] else None
        ),
        "override_mode": (
            str(row["override_mode"])
            if "override_mode" in keys and row["override_mode"] else None
        ),
        "override_requested_at": (
            str(row["override_requested_at"])
            if "override_requested_at" in keys and row["override_requested_at"]
            else None
        ),
        "original_available_at": (
            str(row["original_available_at"])
            if "original_available_at" in keys and row["original_available_at"]
            else str(row["available_at"])
        ),
    }


def list_retry_schedule_jobs(
    connection: sqlite3.Connection,
    *,
    limit: int = 200,
) -> list[dict[str, object]]:
    """Return bounded scheduler state without provider or credential material."""
    bounded = max(1, min(500, int(limit)))
    has_revisions = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='news_revisions'",
    ).fetchone() is not None
    has_translations = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='news_title_translations'",
    ).fetchone() is not None
    title_sql = (
        f"""COALESCE({
          "(SELECT t.headline_zh FROM news_title_translations t "
          "WHERE t.source=j.source AND t.source_item_id=j.source_item_id "
          "AND t.revision_number=j.revision_number ORDER BY t.parsed_at DESC LIMIT 1),"
          if has_translations else ""
        }(SELECT n.headline FROM news_revisions n
                       WHERE n.source=j.source AND n.source_item_id=j.source_item_id
                         AND n.revision_number=j.revision_number LIMIT 1),
                    j.source_item_id)"""
        if has_revisions else "j.source_item_id"
    )
    rows = connection.execute(
        f"""SELECT j.*,
                  {title_sql} AS title,
                  (SELECT max(a.attempted_at) FROM news_ai_job_attempts_v1 a
                   WHERE a.job_id=j.job_id) AS last_failure_at,
                  active.mode AS override_mode,
                  active.requested_at AS override_requested_at,
                  active.original_available_at AS original_available_at
           FROM news_ai_jobs_v1 j
           LEFT JOIN news_ai_retry_schedule_overrides_v1 active
             ON active.job_id=j.job_id AND active.active=1
           WHERE j.state IN ('QUEUED','BACKING_OFF','LEASED')
           ORDER BY CASE j.state WHEN 'BACKING_OFF' THEN 0 WHEN 'QUEUED' THEN 1 ELSE 2 END,
                    j.available_at,j.created_at,j.job_id
           LIMIT ?""",
        (bounded,),
    ).fetchall()
    return [_retry_job_snapshot(row) for row in rows]


def apply_retry_schedule_override(
    connection: sqlite3.Connection,
    *,
    request_id: str,
    job_id: str,
    operator_id: str,
    mode: str,
    reason: str,
    expected_state: str,
    expected_available_at: str,
    requested_available_at: datetime | None = None,
    now: datetime | None = None,
) -> dict[str, object]:
    """Append an audited override and conditionally change current availability."""
    request_id = request_id.strip()
    operator_id = operator_id.strip()
    normalized_mode = mode.strip().upper()
    normalized_reason = " ".join(reason.split())
    if not request_id or len(request_id) > 128:
        raise ValueError("retry override request_id is invalid")
    if not operator_id or len(operator_id) > 256:
        raise ValueError("retry override operator is invalid")
    if normalized_mode not in RETRY_OVERRIDE_MODES:
        raise ValueError("retry override mode is invalid")
    if not normalized_reason or len(normalized_reason) > 500:
        raise ValueError("retry override reason is invalid")
    instant = now or datetime.now(UTC)
    timestamp = _iso(instant)
    custom = _iso(requested_available_at) if requested_available_at else None
    if normalized_mode == "CUSTOM_TIME" and custom is None:
        raise ValueError("custom retry time is required")
    if normalized_mode != "CUSTOM_TIME" and custom is not None:
        raise ValueError("requested retry time is only valid for custom mode")
    if custom and datetime.fromisoformat(custom) < instant - timedelta(minutes=5):
        raise ValueError("custom retry time is too far in the past")

    connection.execute("BEGIN IMMEDIATE")
    try:
        existing = connection.execute(
            """SELECT j.*,
                      CASE WHEN o.active=1 THEN o.mode END AS override_mode,
                      CASE WHEN o.active=1 THEN o.requested_at END AS override_requested_at,
                      CASE WHEN o.active=1 THEN o.original_available_at
                           ELSE j.available_at END AS original_available_at
               FROM news_ai_retry_schedule_overrides_v1 o
               JOIN news_ai_jobs_v1 j ON j.job_id=o.job_id
               WHERE o.request_id=?""",
            (request_id,),
        ).fetchone()
        if existing is not None:
            if str(existing["job_id"]) != job_id:
                raise RetryScheduleConflict("IDEMPOTENCY_CONFLICT")
            connection.commit()
            return _retry_job_snapshot(existing)

        row = connection.execute(
            "SELECT * FROM news_ai_jobs_v1 WHERE job_id=?", (job_id,),
        ).fetchone()
        if row is None:
            raise RetryScheduleConflict("JOB_NOT_FOUND")
        current = _retry_job_snapshot(row)
        if str(row["state"]) not in {"QUEUED", "BACKING_OFF"}:
            raise RetryScheduleConflict("JOB_NOT_MUTABLE", current)
        if (
            str(row["state"]) != expected_state
            or str(row["available_at"]) != expected_available_at
        ):
            raise RetryScheduleConflict("JOB_STATE_CHANGED", current)

        first = connection.execute(
            """SELECT original_available_at
               FROM news_ai_retry_schedule_overrides_v1
               WHERE job_id=? AND active=1 LIMIT 1""",
            (job_id,),
        ).fetchone()
        original = str(first[0]) if first else str(row["available_at"])
        effective = {
            "KEEP_ORIGINAL": original,
            "IMMEDIATE": timestamp,
            "DELAY_15_MIN": _iso(instant + timedelta(minutes=15)),
            "DELAY_1_HOUR": _iso(instant + timedelta(hours=1)),
            "IDLE_CAPACITY": timestamp,
            "CUSTOM_TIME": custom,
        }[normalized_mode]
        assert effective is not None

        if normalized_mode == "KEEP_ORIGINAL" and first is None:
            connection.execute(
                """INSERT INTO news_ai_retry_schedule_overrides_v1 (
                   override_id,request_id,job_id,task_type,operator_id,mode,
                   requested_at,original_available_at,previous_available_at,
                   requested_available_at,effective_available_at,reason,
                   observed_state,resulting_state,active)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,0)""",
                (
                    str(uuid.uuid4()), request_id, job_id, str(row["task_type"]),
                    operator_id, normalized_mode, timestamp, original,
                    str(row["available_at"]), custom, effective, normalized_reason,
                    str(row["state"]), str(row["state"]),
                ),
            )
            connection.commit()
            return current

        connection.execute(
            "UPDATE news_ai_retry_schedule_overrides_v1 SET active=0 WHERE job_id=? AND active=1",
            (job_id,),
        )
        updated = connection.execute(
            """UPDATE news_ai_jobs_v1
               SET available_at=?,updated_at=?
               WHERE job_id=? AND state=? AND available_at=?
                 AND state IN ('QUEUED','BACKING_OFF')""",
            (effective, timestamp, job_id, expected_state, expected_available_at),
        )
        if updated.rowcount != 1:
            current_row = connection.execute(
                "SELECT * FROM news_ai_jobs_v1 WHERE job_id=?", (job_id,),
            ).fetchone()
            raise RetryScheduleConflict(
                "JOB_STATE_CHANGED",
                _retry_job_snapshot(current_row) if current_row else None,
            )
        connection.execute(
            """INSERT INTO news_ai_retry_schedule_overrides_v1 (
               override_id,request_id,job_id,task_type,operator_id,mode,
               requested_at,original_available_at,previous_available_at,
               requested_available_at,effective_available_at,reason,
               observed_state,resulting_state,active)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,1)""",
            (
                str(uuid.uuid4()), request_id, job_id, str(row["task_type"]),
                operator_id, normalized_mode, timestamp, original,
                str(row["available_at"]), custom, effective, normalized_reason,
                str(row["state"]), str(row["state"]),
            ),
        )
        result = connection.execute(
            """SELECT j.*,o.mode AS override_mode,
                      o.requested_at AS override_requested_at,
                      o.original_available_at AS original_available_at
               FROM news_ai_jobs_v1 j
               JOIN news_ai_retry_schedule_overrides_v1 o
                 ON o.job_id=j.job_id AND o.active=1
               WHERE j.job_id=?""",
            (job_id,),
        ).fetchone()
        connection.commit()
        return _retry_job_snapshot(result)
    except Exception:
        connection.rollback()
        raise


def claim_job(
    connection: sqlite3.Connection,
    *,
    worker_id: str,
    pool: str,
    task_types: tuple[str, ...] | None = None,
    excluded_task_types: frozenset[str] = frozenset(),
    work_lanes: tuple[str, ...] = WORK_LANES,
    now: datetime | None = None,
    lease_seconds: int = 180,
) -> ScheduledJob | None:
    if pool not in {ROUTINE_POOL, PREEMPTIBLE_POOL}:
        raise ValueError("scheduler pool is not controlled")
    if not worker_id.strip():
        raise ValueError("scheduler worker_id is required")
    claimable_tasks = tuple(dict.fromkeys(TASKS if task_types is None else task_types))
    if any(task_type not in TASKS for task_type in claimable_tasks):
        raise ValueError("scheduler task type is not controlled")
    if not excluded_task_types.issubset(TASKS):
        raise ValueError("scheduler task exclusion is not controlled")
    claimable_lanes = tuple(dict.fromkeys(work_lanes))
    if not claimable_lanes or any(lane not in WORK_LANES for lane in claimable_lanes):
        raise ValueError("scheduler work lane is not controlled")
    claimable_tasks = tuple(
        task_type for task_type in claimable_tasks
        if task_type not in excluded_task_types
    )
    if not claimable_tasks:
        return None
    instant = now or datetime.now(UTC)
    timestamp = _iso(instant)
    aged_before = _iso(instant - PRIORITY_HEAD_START)
    lease_expires = _iso(instant + timedelta(seconds=max(30, lease_seconds)))
    priority_filter = (
        "AND j.priority IN ('IMMEDIATE','FAST')"
        if pool == PREEMPTIBLE_POOL else ""
    )
    task_placeholders = ",".join("?" for _ in claimable_tasks)
    lane_placeholders = ",".join("?" for _ in claimable_lanes)
    connection.execute("BEGIN IMMEDIATE")
    try:
        connection.execute(
            """UPDATE news_ai_jobs_v1
               SET state='QUEUED',lease_owner=NULL,lease_expires_at=NULL,
                   updated_at=?
               WHERE state='LEASED' AND lease_expires_at<=?""",
            (timestamp, timestamp),
        )
        row = connection.execute(
            f"""SELECT j.* FROM news_ai_jobs_v1 j
                LEFT JOIN news_ai_retry_schedule_overrides_v1 retry_override
                  ON retry_override.job_id=j.job_id AND retry_override.active=1
                 WHERE j.state IN ('QUEUED','BACKING_OFF')
                   AND j.task_type IN ({task_placeholders})
                   AND j.lane_classified=1
                   AND (j.task_type='ACTIVE_ANNOTATION' OR
                        (j.provenance_resolved=1 AND j.provenance_version=?))
                   AND j.work_lane IN ({lane_placeholders})
                   AND j.available_at<=? {priority_filter}
                 ORDER BY
                   CASE WHEN retry_override.mode='IDLE_CAPACITY'
                              AND retry_override.requested_at<=? THEN 0 ELSE 1 END,
                   CASE j.work_lane WHEN 'LIVE' THEN 0 ELSE 1 END,
                   CASE WHEN j.task_type='ACTIVE_ANNOTATION'
                              AND j.work_lane='LIVE'
                              AND j.priority IN ('IMMEDIATE','FAST')
                        THEN 0 ELSE 1 END,
                  CASE WHEN retry_override.mode='IDLE_CAPACITY'
                             AND retry_override.requested_at>? THEN 1 ELSE 0 END,
                  CASE WHEN j.created_at<=? THEN 0 ELSE 1 END,
                  CASE WHEN j.created_at<=? THEN j.created_at ELSE NULL END,
                  CASE j.priority WHEN 'IMMEDIATE' THEN 0 WHEN 'FAST' THEN 1
                                WHEN 'NORMAL' THEN 2 ELSE 3 END,
                  CASE j.task_type WHEN 'ACTIVE_IMPACT' THEN 0
                                 WHEN 'ACTIVE_ANNOTATION' THEN 1 ELSE 2 END,
                  j.created_at,j.job_id
                LIMIT 1""",
            (
                *claimable_tasks, WORK_PROVENANCE_VERSION,
                *claimable_lanes, timestamp,
                _iso(instant - IDLE_CAPACITY_MAX_WAIT),
                _iso(instant - IDLE_CAPACITY_MAX_WAIT),
                aged_before, aged_before,
            ),
        ).fetchone()
        if row is None:
            connection.commit()
            return None
        updated = connection.execute(
            """UPDATE news_ai_jobs_v1
               SET state='LEASED',lease_owner=?,lease_expires_at=?,
                   attempt_count=attempt_count+1,updated_at=?
               WHERE job_id=? AND state IN ('QUEUED','BACKING_OFF')""",
            (worker_id, lease_expires, timestamp, row["job_id"]),
        )
        if updated.rowcount != 1:
            connection.rollback()
            return None
        connection.execute(
            """UPDATE news_ai_retry_schedule_overrides_v1
               SET active=0 WHERE job_id=? AND active=1""",
            (row["job_id"],),
        )
        claimed = connection.execute(
            "SELECT * FROM news_ai_jobs_v1 WHERE job_id=?",
            (row["job_id"],),
        ).fetchone()
        connection.commit()
        return _job_from_row(claimed)
    except Exception:
        connection.rollback()
        raise


def _transition_leased_job(
    connection: sqlite3.Connection,
    job_id: str,
    worker_id: str,
    *,
    state: str,
    available_at: datetime,
    error: str | None,
    completed_at: datetime | None,
) -> None:
    now = datetime.now(UTC)
    with connection:
        result = connection.execute(
            """UPDATE news_ai_jobs_v1
               SET state=?,available_at=?,lease_owner=NULL,
                   lease_expires_at=NULL,last_error=?,updated_at=?,completed_at=?
               WHERE job_id=? AND state='LEASED' AND lease_owner=?""",
            (
                state, _iso(available_at), error, _iso(now),
                _iso(completed_at) if completed_at else None,
                job_id, worker_id,
            ),
        )
        if result.rowcount != 1:
            raise ValueError("scheduler lease is not owned by this worker")


def complete_job(
    connection: sqlite3.Connection,
    job_id: str,
    worker_id: str,
    *,
    now: datetime | None = None,
) -> None:
    completed = now or datetime.now(UTC)
    _transition_leased_job(
        connection, job_id, worker_id, state="COMPLETED",
        available_at=completed, error=None, completed_at=completed,
    )


def release_job(
    connection: sqlite3.Connection,
    job_id: str,
    worker_id: str,
    *,
    available_at: datetime,
    error: str | None = None,
) -> None:
    _transition_leased_job(
        connection, job_id, worker_id, state="QUEUED",
        available_at=available_at, error=error, completed_at=None,
    )


def defer_job_for_maintenance(
    connection: sqlite3.Connection,
    job_id: str,
    worker_id: str,
    *,
    available_at: datetime,
    reason: str,
) -> None:
    """Release a shared-prerequisite wait without counting a model attempt."""
    now = datetime.now(UTC)
    with connection:
        result = connection.execute(
            """UPDATE news_ai_jobs_v1
               SET state='QUEUED',available_at=?,lease_owner=NULL,
                   lease_expires_at=NULL,last_error=?,updated_at=?,
                   attempt_count=attempt_count-1
               WHERE job_id=? AND state='LEASED' AND lease_owner=?
                 AND attempt_count>0""",
            (
                _iso(available_at), reason, _iso(now), job_id, worker_id,
            ),
        )
        if result.rowcount != 1:
            raise ValueError("scheduler lease is not owned by this worker")


def backoff_job(
    connection: sqlite3.Connection,
    job_id: str,
    worker_id: str,
    *,
    available_at: datetime,
    error: str,
    terminal: bool = False,
) -> None:
    _transition_leased_job(
        connection, job_id, worker_id,
        state="DEAD_LETTER" if terminal else "BACKING_OFF",
        available_at=available_at, error=error,
        completed_at=available_at if terminal else None,
    )


def quota_day(now: datetime) -> str:
    return now.astimezone(PACIFIC).date().isoformat()


def minute_bucket(now: datetime) -> str:
    return now.astimezone(UTC).replace(second=0, microsecond=0).isoformat()


def account_quota_snapshot(
    connection: sqlite3.Connection,
    credentials: tuple[ApiCredential, ...],
    *,
    model_families: tuple[str, ...],
    daily_limit: int,
    quota_authority: str | None = None,
    now: datetime | None = None,
) -> dict[str, object]:
    """Read the scheduler's account ledger without exposing credentials."""
    instant = now or datetime.now(UTC)
    day = quota_day(instant)
    families = tuple(dict.fromkeys(model_families))
    usage: dict[str, int] = {}
    if families:
        placeholders = ",".join("?" for _ in families)
        rows = connection.execute(
            f"""SELECT account_id,sum(request_count) AS request_count
                FROM news_ai_account_daily_usage_v1
                WHERE quota_day=? AND model_family IN ({placeholders})
                GROUP BY account_id""",
            (day, *families),
        ).fetchall()
        usage = {
            str(row["account_id"]): int(row["request_count"])
            for row in rows
        }

    accounts: dict[str, list[ApiCredential]] = {}
    for credential in credentials:
        accounts.setdefault(credential.account_id, []).append(credential)
    keys = []
    for slot, (account_id, account_credentials) in enumerate(accounts.items(), 1):
        sent = usage.get(account_id, 0)
        fingerprint = (
            account_credentials[0].credential_id
            if len(account_credentials) == 1
            else hashlib.sha256(account_id.encode("utf-8")).hexdigest()[:12]
        )
        key_snapshot: dict[str, object] = {
            "slot": slot,
            "fingerprint": fingerprint,
            "sent": sent,
            "remaining": max(0, daily_limit - sent),
            "status": "AVAILABLE" if sent < daily_limit else "DAILY_LIMIT",
        }
        if quota_authority in {"gemini_quota", "gemini_31_quota"}:
            forecast = _backfill_admission_locked(
                connection,
                account_id=account_id,
                quota_authority=quota_authority,
                model_families=families,
                daily_limit=daily_limit,
                current_daily_count=sent,
                requested_requests=1,
                now=instant,
            )
            backfill_today = int(connection.execute(
                """SELECT COALESCE(sum(request_count),0)
                   FROM news_ai_quota_day_workload_v1
                   WHERE quota_day=? AND account_id=? AND quota_authority=?
                     AND workload_class='CONTRACT_BACKFILL'""",
                (day, account_id, quota_authority),
            ).fetchone()[0])
            key_snapshot["contract_backfill"] = {
                "predicted_remaining_live_demand": forecast["predicted_live_requests"],
                "operational_retry_reserve": forecast["operational_retry_reserve"],
                "safety_buffer": forecast["safety_buffer"],
                "spendable_remaining": forecast["safe_backfill_budget"],
                "requests_today": backfill_today,
                "dispatch_allowed": bool(forecast["admitted"]),
                "deferred_reason": (
                    None if forecast["admitted"] else forecast["reason"]
                ),
                "forecast_window_days": BACKFILL_FORECAST_DAYS,
            }
        keys.append(key_snapshot)
    next_midnight = (instant.astimezone(PACIFIC) + timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0,
    ).astimezone(UTC)
    return {
        "quota_day_pacific": day,
        "daily_limit_per_key": daily_limit,
        "next_reset_at": next_midnight.isoformat(),
        "keys": keys,
        "total_sent": sum(item["sent"] for item in keys),
        "total_remaining": sum(item["remaining"] for item in keys),
        "accounting_source": "SCHEDULER_DB",
    }


def rank_accounts_for_models(
    connection: sqlite3.Connection,
    credentials: tuple[ApiCredential, ...],
    *,
    models: tuple[str, ...],
    priority_reserve_models: tuple[str, ...] = (),
    urgent: bool,
    now: datetime | None = None,
) -> tuple[str, ...]:
    """Rank configured accounts by live quota headroom for a model route.

    Keys belonging to one account intentionally share one score.  Adding an
    independent account therefore increases capacity immediately, while adding
    another key to an existing account only adds transport redundancy.
    """
    from .ai_provider_registry import quota_surface_for_model

    instant = now or datetime.now(UTC)
    day = quota_day(instant)
    account_order = tuple(dict.fromkeys(item.account_id for item in credentials))
    route = tuple(dict.fromkeys(models))

    def model_headroom(account_id: str, model: str) -> float:
        policy = quota_surface_for_model(model)
        families = policy.model_families
        placeholders = ",".join("?" for _ in families)
        reserve = 0
        if not urgent and model in priority_reserve_models:
            from .annotation import GEMINI_DAILY_PRIORITY_RESERVE
            reserve = GEMINI_DAILY_PRIORITY_RESERVE
        daily_limit = max(0, policy.daily_limit - reserve)
        daily = connection.execute(
            f"""SELECT COALESCE(sum(request_count),0) AS requests
                FROM news_ai_account_daily_usage_v1
                WHERE quota_day=? AND account_id=?
                  AND model_family IN ({placeholders})""",
            (day, account_id, *families),
        ).fetchone()
        minute_requests, minute_tokens = rolling_account_usage(
            connection,
            account_id=account_id,
            model_families=families,
            now=instant,
            share_across_accounts=policy.share_minute_across_accounts,
        )
        ratios = (
            max(0.0, (daily_limit - int(daily["requests"])) / max(1, daily_limit)),
            max(0.0, (policy.requests_per_minute - minute_requests)
                / max(1, policy.requests_per_minute)),
            max(0.0, (policy.input_tokens_per_minute - minute_tokens)
                / max(1, policy.input_tokens_per_minute)),
        )
        return min(ratios)

    scores = {
        account_id: max(
            (model_headroom(account_id, model) for model in route),
            default=0.0,
        )
        for account_id in account_order
    }
    order_index = {account_id: index for index, account_id in enumerate(account_order)}
    return tuple(sorted(
        account_order,
        key=lambda account_id: (-scores[account_id], order_index[account_id]),
    ))


def credentials_for_background_task(
    connection: sqlite3.Connection,
    credentials: tuple[ApiCredential, ...],
    *, task_type: str, now: datetime | None = None,
) -> tuple[ApiCredential, ...]:
    """Resolve a declared background route to ordered ROUTINE credentials."""
    from .ai_task_registry import route_for_task

    eligible = tuple(item for item in credentials if item.pool == ROUTINE_POOL)
    if not eligible:
        return ()
    route = route_for_task(task_type)
    accounts = rank_accounts_for_models(
        connection, eligible, models=route.models,
        priority_reserve_models=route.priority_reserve_models,
        urgent=False, now=now,
    )
    by_account: dict[str, list[ApiCredential]] = {}
    for credential in eligible:
        by_account.setdefault(credential.account_id, []).append(credential)
    return tuple(
        credential
        for account in accounts
        for credential in sorted(
            by_account[account], key=lambda item: item.credential_id,
        )
    )


def _nearest_rank_p95(values: list[int]) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    return ordered[max(0, math.ceil(0.95 * len(ordered)) - 1)]


def forecast_safe_backfill_budget(
    *, remaining_capacity: int, predicted_live: int,
    operational_retry_reserve: int, safety_buffer: int,
) -> int:
    return max(
        0,
        int(remaining_capacity) - int(predicted_live)
        - int(operational_retry_reserve) - int(safety_buffer),
    )


def _live_pressure_tasks_for_quota(quota_authority: str) -> tuple[str, ...]:
    """Resolve persisted LIVE demand independently from the requested model."""
    from .ai_provider_registry import quota_surface_for_model
    from .ai_task_registry import AI_TASK_ROUTES

    tasks: list[str] = []
    for route in AI_TASK_ROUTES:
        if any(
            quota_surface_for_model(model).payload_key == quota_authority
            for model in route.models
        ):
            tasks.extend(route.quota_pressure_tasks)
    return tuple(dict.fromkeys(tasks))


def _backfill_admission_locked(
    connection: sqlite3.Connection,
    *,
    account_id: str,
    quota_authority: str,
    model_families: tuple[str, ...],
    daily_limit: int,
    current_daily_count: int,
    requested_requests: int,
    now: datetime,
) -> dict[str, object]:
    """Return one atomic, forecast-safe grant for contract backfill.

    The caller owns ``BEGIN IMMEDIATE``. History contains only admitted requests
    and is aggregated by quota day/hour, so retention and forecasting stay O(1)
    with respect to news history size.
    """
    pacific = now.astimezone(PACIFIC)
    current_day = pacific.date().isoformat()
    complete_start = (pacific.date() - timedelta(days=BACKFILL_FORECAST_DAYS)).isoformat()
    history = connection.execute(
        """WITH complete_days AS (
             SELECT DISTINCT quota_day FROM news_ai_quota_day_workload_v1
             WHERE account_id=? AND quota_authority=?
               AND quota_day>=? AND quota_day<?
           )
           SELECT d.quota_day,COALESCE(sum(CASE
                    WHEN w.workload_class='LIVE_OPERATIONAL'
                     AND w.hour_bucket>=? THEN w.request_count ELSE 0 END),0)
                  AS remaining_live
           FROM complete_days d
           LEFT JOIN news_ai_quota_day_workload_v1 w
             ON w.quota_day=d.quota_day AND w.account_id=?
            AND w.quota_authority=?
           GROUP BY d.quota_day""",
        (
            account_id, quota_authority, complete_start, current_day,
            pacific.hour, account_id, quota_authority,
        ),
    ).fetchall()
    values = [int(row["remaining_live"]) for row in history]
    history_days = len(values)
    predicted_live = _nearest_rank_p95(values)
    operational_reserve = math.ceil(daily_limit * BACKFILL_RESERVE_RATIO)
    safety = math.ceil(daily_limit * BACKFILL_SAFETY_RATIO)
    remaining_capacity = max(0, daily_limit - current_daily_count)
    safe_budget = forecast_safe_backfill_budget(
        remaining_capacity=remaining_capacity,
        predicted_live=predicted_live,
        operational_retry_reserve=operational_reserve,
        safety_buffer=safety,
    )
    next_reset = (pacific + timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0,
    ).isoformat(timespec="microseconds")
    evidence: dict[str, object] = {
        "reason": "SAFE_BUDGET_AVAILABLE",
        "quota_authority": quota_authority,
        "history_days": history_days,
        "forecast_method": "P95_REMAINING_QUOTA_DAY_HOURLY",
        "predicted_live_requests": predicted_live,
        "operational_retry_reserve": operational_reserve,
        "safety_buffer": safety,
        "remaining_daily_capacity": remaining_capacity,
        "safe_backfill_budget": safe_budget,
        "next_retry_at": (now + timedelta(minutes=1)).isoformat(timespec="microseconds"),
    }
    if history_days < BACKFILL_MIN_COMPLETE_DAYS:
        return {**evidence, "admitted": False, "reason": "COLD_START_HISTORY"}

    placeholders = ",".join("?" for _ in model_families)
    pressure_tasks = _live_pressure_tasks_for_quota(quota_authority)
    if not pressure_tasks:
        return {
            **evidence, "admitted": False,
            "reason": "UNCLASSIFIED_LIVE_PRESSURE_ROUTE",
        }
    task_placeholders = ",".join("?" for _ in pressure_tasks)
    pressure_cutoff = (now - timedelta(minutes=15)).isoformat(timespec="microseconds")
    live = connection.execute(
        f"""SELECT
             sum(CASE WHEN state='LEASED' OR available_at<=? THEN 1 ELSE 0 END) claimable,
             min(CASE WHEN state='LEASED' OR available_at<=? THEN created_at END) oldest,
             sum(CASE WHEN created_at>=? THEN 1 ELSE 0 END) arrivals,
             sum(CASE WHEN completed_at>=? THEN 1 ELSE 0 END) completions
           FROM news_ai_jobs_v1
           WHERE task_type IN ({task_placeholders}) AND lane_classified=1
             AND work_lane='LIVE'
             AND (task_type='ACTIVE_ANNOTATION' OR
                  (provenance_resolved=1 AND provenance_version=?))
             AND state IN ('QUEUED','LEASED','BACKING_OFF','COMPLETED')""",
        (
            now.isoformat(timespec="microseconds"),
            now.isoformat(timespec="microseconds"), pressure_cutoff, pressure_cutoff,
            *pressure_tasks, WORK_PROVENANCE_VERSION,
        ),
    ).fetchone()
    claimable = int(live["claimable"] or 0)
    oldest = datetime.fromisoformat(str(live["oldest"])) if live["oldest"] else None
    if oldest is not None and oldest.tzinfo is None:
        oldest = oldest.replace(tzinfo=UTC)
    live_overdue = bool(oldest and now - oldest >= BACKFILL_LIVE_SLA)
    backlog_increasing = int(live["arrivals"] or 0) > int(live["completions"] or 0)
    throttled = int(connection.execute(
        f"""SELECT count(*) FROM news_ai_account_request_usage_v1
            WHERE account_id=? AND reserved_at>=?
              AND model_family IN ({placeholders})
              AND provider_outcome='PROVIDER_THROTTLED'""",
        (account_id, pressure_cutoff, *model_families),
    ).fetchone()[0])
    capacity_deferred = int(connection.execute(
        f"""SELECT count(*) FROM news_ai_scheduler_deferrals_v1 d
           JOIN news_ai_jobs_v1 j ON j.job_id=d.job_id
           WHERE d.deferred_at>=? AND j.lane_classified=1
             AND j.work_lane='LIVE'
             AND j.task_type IN ({task_placeholders})
             AND (j.task_type='ACTIVE_ANNOTATION' OR
                  (j.provenance_resolved=1 AND j.provenance_version=?))
             AND d.failure_code IN ('MODEL_CAPACITY_DEFERRED',
                                    'PROVIDER_DISPATCH_DEFERRED')""",
        (pressure_cutoff, *pressure_tasks, WORK_PROVENANCE_VERSION),
    ).fetchone()[0])
    recent = connection.execute(
        f"""SELECT COALESCE(sum(request_count),0) AS total,
                   COALESCE(sum(CASE WHEN workload_class='CONTRACT_BACKFILL'
                                     THEN request_count ELSE 0 END),0) AS backfill
            FROM news_ai_account_request_usage_v1
            WHERE account_id=? AND reserved_at>?
              AND model_family IN ({placeholders})""",
        (
            account_id, (now - timedelta(seconds=60)).isoformat(timespec="microseconds"),
            *model_families,
        ),
    ).fetchone()
    total_60s = int(recent["total"])
    backfill_60s = int(recent["backfill"])
    share_blocked = backfill_60s >= max(
        1, (total_60s + 1) // BACKFILL_MAX_SHARE_DENOMINATOR,
    )
    evidence.update({
        "live_claimable": claimable,
        "live_sla_overdue": live_overdue,
        "live_backlog_increasing": backlog_increasing,
        "provider_throttles_15m": throttled,
        "live_capacity_deferrals_15m": capacity_deferred,
        "backfill_requests_60s": backfill_60s,
        "total_requests_60s": total_60s,
    })
    blockers = (
        (claimable > 0, "LIVE_CLAIMABLE"),
        (live_overdue, "LIVE_SLA_PRESSURE"),
        (backlog_increasing, "LIVE_BACKLOG_INCREASING"),
        (throttled > 0, "PROVIDER_THROTTLED"),
        (capacity_deferred > 0, "LIVE_CAPACITY_DEFERRED"),
        (share_blocked, "INSTANTANEOUS_SHARE"),
        (safe_budget < requested_requests, "NO_SAFE_DAILY_BUDGET"),
    )
    reason = next((name for blocked, name in blockers if blocked), None)
    if reason is not None:
        return {
            **evidence, "admitted": False, "reason": reason,
            "next_retry_at": next_reset if reason == "NO_SAFE_DAILY_BUDGET"
            else evidence["next_retry_at"],
        }
    return {**evidence, "admitted": True}


def reserve_account_request(
    connection: sqlite3.Connection,
    *,
    account_id: str,
    model_family: str,
    daily_limit: int,
    requests_per_minute: int,
    request_count: int = 1,
    input_tokens: int = 0,
    input_tokens_per_minute: int | None = None,
    shared_model_families: tuple[str, ...] | None = None,
    share_minute_across_accounts: bool = False,
    reserve_total: int = 0,
    urgent: bool = False,
    now: datetime | None = None,
    usage_id: str | None = None,
    provider_task: str | None = None,
    requested_model: str | None = None,
    purpose: str | None = None,
    prompt_contract: str | None = None,
    estimator_version: str | None = None,
    base_estimated_input_tokens: int | None = None,
    calibration_provider_model_version: str | None = None,
    calibration_safe_ratio: float | None = None,
    workload_class: str = LIVE_OPERATIONAL_WORKLOAD,
    quota_authority: str | None = None,
    decision: dict[str, object] | None = None,
) -> bool:
    """Atomically count attempted provider requests against one account.

    Batch APIs may carry several independently quota-counted requests in one
    HTTP envelope. ``request_count`` represents that provider-visible unit.
    """
    instant = now or datetime.now(UTC)
    day = quota_day(instant)
    minute = minute_bucket(instant)
    timestamp = _iso(instant)
    estimated_tokens = max(0, int(input_tokens))
    base_tokens = max(0, int(
        estimated_tokens
        if base_estimated_input_tokens is None
        else base_estimated_input_tokens
    ))
    attempted_requests = int(request_count)
    if attempted_requests < 1:
        raise ValueError("request_count must be positive")
    families = tuple(dict.fromkeys(shared_model_families or (model_family,)))
    if workload_class not in REQUEST_WORKLOADS:
        raise ValueError("unknown request workload class")
    authority = str(quota_authority or model_family)
    placeholders = ",".join("?" for _ in families)
    usable_daily_limit = daily_limit if urgent else max(0, daily_limit - reserve_total)
    connection.execute("BEGIN IMMEDIATE")
    try:
        daily = connection.execute(
            f"""SELECT COALESCE(sum(request_count),0) AS request_count
                FROM news_ai_account_daily_usage_v1
                WHERE quota_day=? AND account_id=?
                  AND model_family IN ({placeholders})""",
            (day, account_id, *families),
        ).fetchone()
        daily_count = int(daily["request_count"])
        if workload_class == CONTRACT_BACKFILL_WORKLOAD:
            backfill = _backfill_admission_locked(
                connection,
                account_id=account_id,
                quota_authority=authority,
                model_families=families,
                daily_limit=daily_limit,
                current_daily_count=daily_count,
                requested_requests=attempted_requests,
                now=instant,
            )
            if not bool(backfill["admitted"]):
                connection.rollback()
                if decision is not None:
                    decision.update(
                        failure_code="BACKFILL_BUDGET_DEFERRED",
                        next_retry_at=backfill["next_retry_at"],
                        **{key: value for key, value in backfill.items()
                           if key not in {"admitted", "next_retry_at"}},
                    )
                return False
        minute_count, minute_tokens = rolling_account_usage(
            connection,
            account_id=account_id,
            model_families=families,
            now=instant,
            share_across_accounts=share_minute_across_accounts,
        )
        daily_exhausted = daily_count + attempted_requests > usable_daily_limit
        rpm_exhausted = minute_count + attempted_requests > requests_per_minute
        token_exhausted = (
            input_tokens_per_minute is not None
            and minute_tokens + estimated_tokens > input_tokens_per_minute
        )
        if daily_exhausted or rpm_exhausted or token_exhausted:
            connection.rollback()
            if decision is not None:
                dimensions = [
                    name for name, exhausted in (
                        ("RPD", daily_exhausted),
                        ("RPM", rpm_exhausted),
                        ("TPM", token_exhausted),
                    )
                    if exhausted
                ]
                next_retry_at = None
                if daily_exhausted:
                    next_retry_at = _iso(
                        (instant.astimezone(PACIFIC) + timedelta(days=1)).replace(
                            hour=0, minute=0, second=0, microsecond=0,
                        )
                    )
                elif not (
                    attempted_requests > requests_per_minute
                    or (
                        input_tokens_per_minute is not None
                        and estimated_tokens > input_tokens_per_minute
                    )
                ):
                    next_retry_at = _minute_capacity_next_eligible(
                        connection,
                        account_id=account_id,
                        model_families=families,
                        now=instant,
                        share_across_accounts=share_minute_across_accounts,
                        requested_requests=attempted_requests,
                        requested_tokens=estimated_tokens,
                        requests_per_minute=requests_per_minute,
                        input_tokens_per_minute=input_tokens_per_minute,
                    )
                primary = dimensions[0]
                decision.update(
                    failure_code="MODEL_CAPACITY_DEFERRED",
                    dimension=primary,
                    dimensions=dimensions,
                    current=(
                        daily_count if primary == "RPD"
                        else minute_count if primary == "RPM"
                        else minute_tokens
                    ),
                    requested=(
                        attempted_requests if primary in {"RPD", "RPM"}
                        else estimated_tokens
                    ),
                    limit=(
                        usable_daily_limit if primary == "RPD"
                        else requests_per_minute if primary == "RPM"
                        else input_tokens_per_minute
                    ),
                    current_requests_60s=minute_count,
                    current_tokens_60s=minute_tokens,
                    current_requests_day=daily_count,
                    base_estimated_input_tokens=base_tokens,
                    admitted_input_tokens=estimated_tokens,
                    next_retry_at=next_retry_at,
                )
            return False
        if provider_task is not None:
            dispatch = _reserve_provider_dispatch_locked(
                connection, provider_task=provider_task, now=instant,
            )
            if not dispatch[0]:
                # A denied slot must not reserve account quota, but the shared
                # governor may have advanced a durable low-priority deferral.
                connection.commit()
                if decision is not None:
                    decision.update(
                        failure_code="PROVIDER_DISPATCH_DEFERRED",
                        next_retry_at=dispatch[1],
                    )
                return False
        connection.execute(
            """INSERT INTO news_ai_account_daily_usage_v1 VALUES (?,?,?,?,?)
               ON CONFLICT(quota_day,account_id,model_family) DO UPDATE SET
                 request_count=request_count+excluded.request_count,
                 updated_at=excluded.updated_at""",
            (day, account_id, model_family, attempted_requests, timestamp),
        )
        connection.execute(
            """INSERT INTO news_ai_account_minute_usage_v1
               (minute_bucket,account_id,model_family,request_count,
                input_token_count,updated_at) VALUES (?,?,?,?,?,?)
               ON CONFLICT(minute_bucket,account_id,model_family) DO UPDATE SET
                 request_count=request_count+excluded.request_count,
                 input_token_count=input_token_count+excluded.input_token_count,
                 updated_at=excluded.updated_at""",
            (
                minute, account_id, model_family, attempted_requests,
                estimated_tokens, timestamp,
            ),
        )
        connection.execute(
            """INSERT INTO news_ai_account_request_usage_v1
               (usage_id,account_id,model_family,request_count,
                input_token_count,reserved_at,requested_model,purpose,
                prompt_contract,estimator_version,base_estimated_input_tokens,
                admitted_input_tokens,calibration_provider_model_version,
                calibration_safe_ratio,workload_class)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                usage_id or str(uuid.uuid4()), account_id, model_family,
                attempted_requests,
                estimated_tokens, timestamp, requested_model or model_family,
                purpose, prompt_contract, estimator_version, base_tokens,
                estimated_tokens, calibration_provider_model_version,
                calibration_safe_ratio,
                workload_class,
            ),
        )
        pacific = instant.astimezone(PACIFIC)
        connection.execute(
            """INSERT INTO news_ai_quota_day_workload_v1
               (quota_day,hour_bucket,account_id,quota_authority,
                workload_class,request_count,updated_at)
               VALUES (?,?,?,?,?,?,?)
               ON CONFLICT(quota_day,hour_bucket,account_id,quota_authority,
                           workload_class) DO UPDATE SET
                 request_count=request_count+excluded.request_count,
                 updated_at=excluded.updated_at""",
            (
                day, pacific.hour, account_id, authority, workload_class,
                attempted_requests, timestamp,
            ),
        )
        connection.commit()
        if decision is not None:
            decision.update(failure_code=None, next_retry_at=None)
        return True
    except Exception:
        connection.rollback()
        raise


GOOGLE_PROVIDER_SCOPE = "GOOGLE_GENERATIVE_LANGUAGE"
PROVIDER_DISPATCH_INITIAL_INTERVAL_MS = 250
PROVIDER_DISPATCH_MIN_INTERVAL_MS = 120
PROVIDER_DISPATCH_MAX_INTERVAL_MS = 5_000
PROVIDER_DISPATCH_DEMAND_TTL = timedelta(seconds=2)


def _provider_task_class(provider_task: str) -> str:
    normalized = provider_task.strip().upper().replace("-", "_")
    if "DAILY" in normalized and "BRIEF" in normalized:
        return "DAILY_BRIEF"
    if "EMBEDDING" in normalized:
        return "EMBEDDING"
    if "IMPACT" in normalized:
        return "IMPACT"
    if "TITLE" in normalized or "TRANSLATION" in normalized:
        return "TITLE_REPAIR"
    return "ANNOTATION"


def _provider_pressure(
    connection: sqlite3.Connection,
    *,
    task_class: str,
    now: datetime,
) -> dict[str, int]:
    task_types = {
        "ANNOTATION": ("ACTIVE_ANNOTATION",),
        "IMPACT": ("ACTIVE_IMPACT",),
        "TITLE_REPAIR": ("TITLE_TRANSLATION",),
    }
    if task_class == "DAILY_BRIEF":
        row = connection.execute(
            """SELECT last_pressure_json
               FROM news_ai_provider_dispatch_task_state_v1
               WHERE task_class='DAILY_BRIEF'"""
        ).fetchone()
        stored = json.loads(str(row[0])) if row and row[0] else {}
        return {
            "dependency_fanout": max(0, int(stored.get("dependency_fanout", 0))),
            "oldest_age_ms": max(0, int(stored.get("oldest_age_ms", 0))),
            "retry_overdue_ms": max(0, int(stored.get("retry_overdue_ms", 0))),
            "backlog": max(1, int(stored.get("backlog", 0))),
            "drain_gap": max(0, int(stored.get("drain_gap", 0))),
        }
    if task_class == "EMBEDDING":
        row = connection.execute(
            """SELECT count(*) AS backlog,min(created_at) AS oldest,
                      min(available_at) AS earliest_retry
               FROM news_ai_jobs_v1
               WHERE task_type='ACTIVE_IMPACT'
                 AND state IN ('QUEUED','LEASED','BACKING_OFF')
                 AND (state='LEASED' OR available_at<=?)
                 AND (last_error LIKE 'NEWS_EMBEDDING_%'
                      OR last_error='PROVIDER_DISPATCH_DEFERRED')""",
            (_iso(now),),
        ).fetchone()
        dependency_fanout = int(row["backlog"])
        backlog = max(1, dependency_fanout)
        completion_task = "ACTIVE_IMPACT"
    else:
        controlled = task_types[task_class]
        placeholders = ",".join("?" for _ in controlled)
        row = connection.execute(
            f"""SELECT count(*) AS backlog,min(created_at) AS oldest,
                       min(available_at) AS earliest_retry
                FROM news_ai_jobs_v1
                WHERE task_type IN ({placeholders})
                  AND state IN ('QUEUED','LEASED','BACKING_OFF')
                  AND (state='LEASED' OR available_at<=?)""",
            (*controlled, _iso(now)),
        ).fetchone()
        dependency_fanout = 0
        backlog = max(1, int(row["backlog"]))
        completion_task = controlled[0]
    completed = connection.execute(
        """SELECT count(*) FROM news_ai_jobs_v1
           WHERE task_type=? AND state='COMPLETED' AND completed_at>=?""",
        (completion_task, _iso(now - timedelta(minutes=15))),
    ).fetchone()[0]
    oldest = datetime.fromisoformat(str(row["oldest"])) if row["oldest"] else now
    earliest = (
        datetime.fromisoformat(str(row["earliest_retry"]))
        if row["earliest_retry"] else now
    )
    return {
        "dependency_fanout": dependency_fanout,
        "oldest_age_ms": max(0, int((now - oldest).total_seconds() * 1000)),
        "retry_overdue_ms": max(0, int((now - earliest).total_seconds() * 1000)),
        "backlog": backlog,
        "drain_gap": max(0, backlog - int(completed)),
    }


def _register_provider_demand(
    connection: sqlite3.Connection,
    *,
    task_class: str,
    now: datetime,
    pressure: dict[str, int] | None = None,
) -> dict[str, int]:
    pressure = pressure or _provider_pressure(
        connection, task_class=task_class, now=now,
    )
    connection.execute(
        """INSERT INTO news_ai_provider_dispatch_task_state_v1
           (task_class,last_requested_at,demand_until,last_dispatched_at,
            last_pressure_json) VALUES (?,?,?,NULL,?)
           ON CONFLICT(task_class) DO UPDATE SET
             last_requested_at=excluded.last_requested_at,
             demand_until=excluded.demand_until,
             last_pressure_json=excluded.last_pressure_json""",
        (
            task_class, _iso(now),
            _iso(now + PROVIDER_DISPATCH_DEMAND_TTL),
            json.dumps(pressure, sort_keys=True, separators=(",", ":")),
        ),
    )
    return pressure


def _selected_provider_task(
    connection: sqlite3.Connection, *, now: datetime,
) -> str:
    rows = connection.execute(
        """SELECT task_class,last_dispatched_at,last_pressure_json
           FROM news_ai_provider_dispatch_task_state_v1
           WHERE demand_until>=?""",
        (_iso(now),),
    ).fetchall()
    candidates = []
    for row in rows:
        task_class = str(row["task_class"])
        pressure = _provider_pressure(
            connection, task_class=task_class, now=now,
        )
        connection.execute(
            """UPDATE news_ai_provider_dispatch_task_state_v1
               SET last_pressure_json=? WHERE task_class=?""",
            (
                json.dumps(pressure, sort_keys=True, separators=(",", ":")),
                task_class,
            ),
        )
        candidates.append({
            "task_class": task_class,
            "last_dispatched_at": row["last_dispatched_at"],
            "pressure": pressure,
        })

    def dominates(left: dict[str, object], right: dict[str, object]) -> bool:
        left_pressure = left["pressure"]
        right_pressure = right["pressure"]
        keys = tuple(left_pressure)
        return all(
            int(left_pressure[key]) >= int(right_pressure[key]) for key in keys
        ) and any(
            int(left_pressure[key]) > int(right_pressure[key]) for key in keys
        )

    frontier = [
        candidate for candidate in candidates
        if not any(
            dominates(other, candidate)
            for other in candidates if other is not candidate
        )
    ]
    selected = min(
        frontier,
        key=lambda item: (
            item["last_dispatched_at"] is not None,
            str(item["last_dispatched_at"] or ""),
            str(item["task_class"]),
        ),
    )
    return str(selected["task_class"])


def _provider_dispatch_row(
    connection: sqlite3.Connection,
) -> sqlite3.Row | None:
    return connection.execute(
        """SELECT * FROM news_ai_provider_dispatch_state_v1
           WHERE provider_scope=?""",
        (GOOGLE_PROVIDER_SCOPE,),
    ).fetchone()


def _reserve_provider_dispatch_locked(
    connection: sqlite3.Connection,
    *,
    provider_task: str,
    now: datetime,
) -> tuple[bool, str]:
    task_class = _provider_task_class(provider_task)
    _register_provider_demand(
        connection, task_class=task_class, now=now,
    )
    row = _provider_dispatch_row(connection)
    interval_ms = (
        int(row["interval_ms"])
        if row is not None else PROVIDER_DISPATCH_INITIAL_INTERVAL_MS
    )
    next_eligible = (
        datetime.fromisoformat(str(row["next_eligible_at"]))
        if row is not None else now
    )
    cooldown = (
        datetime.fromisoformat(str(row["cooldown_until"]))
        if row is not None and row["cooldown_until"] else None
    )
    eligible_at = max(
        next_eligible,
        cooldown if cooldown is not None else now,
    )
    if eligible_at > now:
        return False, _iso(eligible_at)
    selected_task = _selected_provider_task(connection, now=now)
    if selected_task != task_class:
        return False, _iso(now + timedelta(milliseconds=interval_ms))
    next_dispatch = now + timedelta(milliseconds=interval_ms)
    timestamp = _iso(now)
    connection.execute(
        """INSERT INTO news_ai_provider_dispatch_state_v1
           (provider_scope,next_eligible_at,interval_ms,success_streak,
            throttle_count,cooldown_until,last_outcome,updated_at)
           VALUES (?,?,?,0,0,NULL,'DISPATCHED',?)
           ON CONFLICT(provider_scope) DO UPDATE SET
             next_eligible_at=excluded.next_eligible_at,
             last_outcome='DISPATCHED',updated_at=excluded.updated_at""",
        (
            GOOGLE_PROVIDER_SCOPE, _iso(next_dispatch), interval_ms,
            timestamp,
        ),
    )
    connection.execute(
        """UPDATE news_ai_provider_dispatch_task_state_v1
           SET last_dispatched_at=? WHERE task_class=?""",
        (timestamp, task_class),
    )
    return True, _iso(next_dispatch)


def reserve_provider_dispatch(
    connection: sqlite3.Connection,
    *,
    provider_task: str,
    now: datetime | None = None,
) -> tuple[bool, str]:
    instant = now or datetime.now(UTC)
    connection.execute("BEGIN IMMEDIATE")
    try:
        decision = _reserve_provider_dispatch_locked(
            connection, provider_task=provider_task, now=instant,
        )
        connection.commit()
        return decision
    except Exception:
        connection.rollback()
        raise


def register_provider_dispatch_demand(
    connection: sqlite3.Connection,
    *,
    provider_task: str,
    now: datetime | None = None,
    pressure: dict[str, int] | None = None,
) -> None:
    """Publish short-lived demand without consuming a provider slot."""
    instant = now or datetime.now(UTC)
    connection.execute("BEGIN IMMEDIATE")
    try:
        _register_provider_demand(
            connection,
            task_class=_provider_task_class(provider_task),
            now=instant,
            pressure=pressure,
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise


def provider_dispatch_next_eligible(
    connection: sqlite3.Connection,
) -> str | None:
    row = _provider_dispatch_row(connection)
    return str(row["next_eligible_at"]) if row is not None else None


def record_provider_dispatch_outcome(
    connection: sqlite3.Connection,
    *,
    outcome: str,
    retry_after_seconds: int | None = None,
    now: datetime | None = None,
) -> None:
    if outcome not in {
        "PROVIDER_SUCCEEDED", "PROVIDER_THROTTLED", "PROVIDER_FAILED",
    }:
        raise ValueError("provider dispatch outcome is not controlled")
    instant = now or datetime.now(UTC)
    connection.execute("BEGIN IMMEDIATE")
    try:
        row = _provider_dispatch_row(connection)
        if row is None:
            connection.rollback()
            return
        interval_ms = int(row["interval_ms"])
        success_streak = int(row["success_streak"])
        throttle_count = int(row["throttle_count"])
        cooldown = (
            datetime.fromisoformat(str(row["cooldown_until"]))
            if row["cooldown_until"] else None
        )
        next_eligible = datetime.fromisoformat(str(row["next_eligible_at"]))
        if outcome == "PROVIDER_THROTTLED":
            interval_ms = min(
                PROVIDER_DISPATCH_MAX_INTERVAL_MS,
                max(PROVIDER_DISPATCH_INITIAL_INTERVAL_MS, interval_ms * 2),
            )
            success_streak = 0
            throttle_count += 1
            wait_seconds = (
                max(1, min(86_400, int(retry_after_seconds)))
                if retry_after_seconds is not None
                else interval_ms / 1000
            )
            cooldown = instant + timedelta(seconds=wait_seconds)
            next_eligible = max(next_eligible, cooldown)
        elif outcome == "PROVIDER_SUCCEEDED" and (
            cooldown is None or cooldown <= instant
        ):
            success_streak += 1
            interval_ms = max(
                PROVIDER_DISPATCH_MIN_INTERVAL_MS,
                round(interval_ms * 0.9),
            )
            cooldown = None
        else:
            success_streak = 0
        connection.execute(
            """UPDATE news_ai_provider_dispatch_state_v1
               SET next_eligible_at=?,interval_ms=?,success_streak=?,
                   throttle_count=?,cooldown_until=?,last_outcome=?,updated_at=?
               WHERE provider_scope=?""",
            (
                _iso(next_eligible), interval_ms, success_streak,
                throttle_count, _iso(cooldown) if cooldown else None,
                outcome, _iso(instant), GOOGLE_PROVIDER_SCOPE,
            ),
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise


def mark_account_request_attempted(
    connection: sqlite3.Connection,
    usage_id: str,
    *,
    now: datetime | None = None,
) -> None:
    timestamp = _iso(now or datetime.now(UTC))
    with connection:
        updated = connection.execute(
            """UPDATE news_ai_account_request_usage_v1
               SET attempted_at=COALESCE(attempted_at,?)
               WHERE usage_id=?""",
            (timestamp, usage_id),
        )
        if updated.rowcount != 1:
            raise ValueError("model request admission is missing")


def record_account_request_outcome(
    connection: sqlite3.Connection,
    usage_id: str,
    *,
    outcome: str,
    provider_http_status: int | None = None,
    retry_after_seconds: int | None = None,
    usage_metadata: dict[str, int] | None = None,
    provider_model_version: str | None = None,
    now: datetime | None = None,
) -> None:
    if outcome not in {
        "PROVIDER_SUCCEEDED", "PROVIDER_THROTTLED", "PROVIDER_FAILED",
    }:
        raise ValueError("provider request outcome is not controlled")
    timestamp = _iso(now or datetime.now(UTC))
    with connection:
        usage_row = connection.execute(
            "SELECT * FROM news_ai_account_request_usage_v1 WHERE usage_id=?",
            (usage_id,),
        ).fetchone()
        if usage_row is None:
            raise ValueError("model request admission is missing")
        updated = connection.execute(
            """UPDATE news_ai_account_request_usage_v1
               SET provider_outcome=?,provider_http_status=?,
                   provider_completed_at=?,provider_prompt_token_count=?,
                   provider_candidates_token_count=?,provider_total_token_count=?,
                   provider_model_version=?
               WHERE usage_id=? AND attempted_at IS NOT NULL
                 AND provider_outcome IS NULL""",
            (
                outcome, provider_http_status, timestamp,
                (usage_metadata or {}).get("prompt_token_count"),
                (usage_metadata or {}).get("candidates_token_count"),
                (usage_metadata or {}).get("total_token_count"),
                provider_model_version,
                usage_id,
            ),
        )
        if updated.rowcount != 1:
            raise ValueError("model request outcome is not claimable")
        prompt_tokens = (usage_metadata or {}).get("prompt_token_count")
        if (
            outcome == "PROVIDER_SUCCEEDED"
            and isinstance(prompt_tokens, int)
            and prompt_tokens > 0
            and provider_model_version
        ):
            _record_token_calibration_sample_locked(
                connection,
                usage_row=usage_row,
                provider_prompt_token_count=prompt_tokens,
                provider_model_version=provider_model_version,
                updated_at=timestamp,
            )
    record_provider_dispatch_outcome(
        connection,
        outcome=outcome,
        retry_after_seconds=retry_after_seconds,
        now=datetime.fromisoformat(timestamp),
    )


def record_account_vectors_committed(
    connection: sqlite3.Connection,
    usage_ids: tuple[str, ...],
    *,
    now: datetime | None = None,
) -> None:
    if not usage_ids:
        return
    timestamp = _iso(now or datetime.now(UTC))
    placeholders = ",".join("?" for _ in usage_ids)
    with connection:
        updated = connection.execute(
            f"""UPDATE news_ai_account_request_usage_v1
                SET vectors_committed_at=?
                WHERE usage_id IN ({placeholders})
                  AND provider_outcome='PROVIDER_SUCCEEDED'
                  AND vectors_committed_at IS NULL""",
            (timestamp, *usage_ids),
        )
        if updated.rowcount != len(usage_ids):
            raise ValueError("successful embedding admissions are incomplete")


def scheduler_counts(connection: sqlite3.Connection) -> dict[str, int]:
    rows = connection.execute(
        """SELECT state,count(*) AS total FROM news_ai_jobs_v1
        WHERE task_type IN ('ACTIVE_ANNOTATION','ACTIVE_IMPACT','TITLE_TRANSLATION')
          AND lane_classified=1
          AND (task_type='ACTIVE_ANNOTATION' OR
               (provenance_resolved=1 AND provenance_version=?))
        GROUP BY state""",
        (WORK_PROVENANCE_VERSION,),
    ).fetchall()
    counts = {str(row["state"]): int(row["total"]) for row in rows}
    result = {state.lower(): counts.get(state, 0) for state in (
        "QUEUED", "LEASED", "BACKING_OFF", "COMPLETED", "DEAD_LETTER",
    )}
    obsolete = int(connection.execute(
        """SELECT count(*) FROM news_ai_jobs_v1
        WHERE state='DEAD_LETTER'
          AND lane_classified=1
          AND (task_type='ACTIVE_ANNOTATION' OR
               (provenance_resolved=1 AND provenance_version=?))
          AND last_error='CURRENT_EVIDENCE_NO_LONGER_ELIGIBLE'""",
        (WORK_PROVENANCE_VERSION,),
    ).fetchone()[0])
    result["obsolete"] = obsolete
    result["dead_letter"] = max(0, result["dead_letter"] - obsolete)
    lane_rows = connection.execute(
        """SELECT task_type,work_lane,state,count(*) AS total
           FROM news_ai_jobs_v1
           WHERE task_type IN ('ACTIVE_ANNOTATION','ACTIVE_IMPACT',
                               'TITLE_TRANSLATION')
             AND lane_classified=1
             AND (task_type='ACTIVE_ANNOTATION' OR
                  (provenance_resolved=1 AND provenance_version=?))
           GROUP BY task_type,work_lane,state""",
        (WORK_PROVENANCE_VERSION,),
    ).fetchall()
    for task_type in TASKS:
        task_prefix = task_type.lower()
        for lane in WORK_LANES:
            lane_prefix = "live" if lane == LIVE_LANE else "backfill"
            for state in (
                "QUEUED", "LEASED", "BACKING_OFF", "COMPLETED", "DEAD_LETTER",
            ):
                result[f"{task_prefix}_{lane_prefix}_{state.lower()}"] = sum(
                    int(row["total"]) for row in lane_rows
                    if str(row["task_type"]) == task_type
                    and str(row["work_lane"]) == lane
                    and str(row["state"]) == state
                )
    result["active_annotation_unclassified"] = int(connection.execute(
        """SELECT count(*) FROM news_ai_jobs_v1
           WHERE task_type='ACTIVE_ANNOTATION' AND lane_classified=0"""
    ).fetchone()[0])
    result["downstream_provenance_unresolved"] = int(connection.execute(
        """SELECT count(*) FROM news_ai_jobs_v1
           WHERE task_type IN ('ACTIVE_IMPACT','TITLE_TRANSLATION')
             AND (lane_classified=0 OR provenance_resolved=0
                  OR COALESCE(provenance_version,'')<>?)""",
        (WORK_PROVENANCE_VERSION,),
    ).fetchone()[0])
    return result


def _annotation_priority(row: dict[str, object]) -> str:
    annotation = row.get("annotation")
    if not isinstance(annotation, dict):
        return "NORMAL"
    candidate = str(annotation.get("review_priority") or "NORMAL").upper()
    return candidate if candidate in PRIORITIES else "NORMAL"


def authorize_repairable_annotation_failures(
    connection: sqlite3.Connection,
    *,
    prompt_version: str,
    recovery_version: str,
    now: datetime | None = None,
) -> int:
    """Grant one auditable retry to failures fixed by this recovery version."""
    timestamp = _iso(now or datetime.now(UTC))
    with connection:
        inserted = connection.execute(
            """INSERT OR IGNORE INTO news_ai_failure_recoveries_v1
               (failure_id,recovery_version,source,source_item_id,
                revision_number,llm_model_version,prompt_version,authorized_at)
               SELECT f.failure_id,?,f.source,f.source_item_id,
                      f.revision_number,f.llm_model_version,f.prompt_version,?
               FROM news_llm_failures f
               LEFT JOIN news_llm_failure_evidence_v1 e
                 ON e.failure_id=f.failure_id
               WHERE f.task_type='ANNOTATION' AND f.prompt_version=?
                 AND f.is_terminal=1
                 AND (
                   e.failure_stage IN (
                     'DISPLAY_REPAIR','EVIDENCE_ANCHOR_REPAIR')
                   OR (e.failure_stage='SEMANTIC_CONTRACT'
                     AND e.cause='annotation supporting evidence is absent from source')
                   OR EXISTS (
                     SELECT 1
                     FROM news_annotation_display_checkpoints_v1 c
                     WHERE c.source=f.source
                       AND c.source_item_id=f.source_item_id
                       AND c.revision_number=f.revision_number
                       AND c.prompt_version=f.prompt_version))
                 AND f.attempt_number=(
                   SELECT max(f2.attempt_number) FROM news_llm_failures f2
                   WHERE f2.task_type=f.task_type AND f2.source=f.source
                     AND f2.source_item_id=f.source_item_id
                     AND f2.revision_number=f.revision_number
                     AND f2.llm_model_version=f.llm_model_version
                     AND f2.prompt_version=f.prompt_version)""",
            (recovery_version, timestamp, prompt_version),
        ).rowcount
        connection.execute(
            """UPDATE news_ai_jobs_v1 AS j
               SET state='QUEUED',available_at=?,lease_owner=NULL,
                   lease_expires_at=NULL,last_error=NULL,updated_at=?,
                   completed_at=NULL
               WHERE j.task_type='ACTIVE_ANNOTATION'
                 AND j.prompt_version=? AND j.state='DEAD_LETTER'
                 AND EXISTS (
                   SELECT 1 FROM news_llm_failures f
                   JOIN news_ai_failure_recoveries_v1 r
                     ON r.failure_id=f.failure_id AND r.recovery_version=?
                   WHERE f.source=j.source
                     AND f.source_item_id=j.source_item_id
                     AND f.revision_number=j.revision_number
                     AND f.prompt_version=j.prompt_version
                     AND j.updated_at < r.authorized_at
                     AND f.attempt_number=(
                       SELECT max(f2.attempt_number)
                       FROM news_llm_failures f2
                       WHERE f2.task_type=f.task_type
                         AND f2.source=f.source
                         AND f2.source_item_id=f.source_item_id
                         AND f2.revision_number=f.revision_number
                         AND f2.llm_model_version=f.llm_model_version
                         AND f2.prompt_version=f.prompt_version))""",
            (timestamp, timestamp, prompt_version, recovery_version),
        )
    return max(0, inserted)


def authorize_repairable_impact_failures(
    connection: sqlite3.Connection,
    *,
    prompt_version: str,
    recovery_version: str,
    now: datetime | None = None,
) -> int:
    """Grant one auditable retry to identity-contract failures now repairable."""
    timestamp = _iso(now or datetime.now(UTC))
    repairable_errors = (
        "New-episode identity requires an anchor difference",
    )
    placeholders = ",".join("?" for _ in repairable_errors)
    with connection:
        inserted = connection.execute(
            f"""INSERT OR IGNORE INTO news_ai_impact_failure_recoveries_v1
               (failure_id,recovery_version,annotation_id,llm_model_version,
                prompt_version,authorized_at)
               SELECT f.failure_id,?,f.annotation_id,f.llm_model_version,
                      f.prompt_version,?
               FROM news_impact_failures_v1 f
               WHERE f.prompt_version=? AND f.is_terminal=1
                 AND f.error IN ({placeholders})
                 AND f.attempt_number=(
                   SELECT max(f2.attempt_number)
                   FROM news_impact_failures_v1 f2
                   WHERE f2.annotation_id=f.annotation_id
                     AND f2.llm_model_version=f.llm_model_version
                     AND f2.prompt_version=f.prompt_version)""",
            (recovery_version, timestamp, prompt_version, *repairable_errors),
        ).rowcount
        connection.execute(
            """UPDATE news_ai_jobs_v1 AS j
               SET state='QUEUED',available_at=?,lease_owner=NULL,
                   lease_expires_at=NULL,last_error=NULL,updated_at=?,
                   completed_at=NULL
               WHERE j.task_type='ACTIVE_IMPACT'
                 AND j.prompt_version=? AND j.state='DEAD_LETTER'
                 AND EXISTS (
                   SELECT 1 FROM news_impact_failures_v1 f
                   JOIN news_ai_impact_failure_recoveries_v1 r
                     ON r.failure_id=f.failure_id AND r.recovery_version=?
                   WHERE f.annotation_id=j.annotation_id
                     AND f.prompt_version=j.prompt_version
                     AND j.updated_at < r.authorized_at
                     AND f.attempt_number=(
                       SELECT max(f2.attempt_number)
                       FROM news_impact_failures_v1 f2
                       WHERE f2.annotation_id=f.annotation_id
                         AND f2.llm_model_version=f.llm_model_version
                         AND f2.prompt_version=f.prompt_version))""",
            (timestamp, timestamp, prompt_version, recovery_version),
        )
    return max(0, inserted)


def _contract_backfill_has_current_value(
    connection: sqlite3.Connection,
    row: sqlite3.Row | dict[str, object],
    *,
    activated_at: datetime,
    prompt_version: str,
) -> bool:
    """Keep migration work only while it can affect current decision behavior."""
    from .news_time import assess_news_time, category_time_rule

    prior = connection.execute(
        """SELECT annotation_json FROM news_annotations
           WHERE source=? AND source_item_id=? AND revision_number=?
             AND prompt_version<>?
           ORDER BY parsed_at DESC LIMIT 1""",
        (
            row["source"], row["source_item_id"], row["revision_number"],
            prompt_version,
        ),
    ).fetchone()
    category: str | None = None
    if prior is not None:
        try:
            annotation = json.loads(str(prior[0]))
        except (TypeError, ValueError):
            return False
        if str(annotation.get("xauusd_relevance") or "") == "IRRELEVANT":
            return False
        category = str(annotation.get("primary_category") or "") or None
    forward_epoch = datetime.fromisoformat(str(connection.execute(
        "SELECT value FROM runtime_metadata WHERE key='FORWARD_EPOCH'"
    ).fetchone()[0]))
    actionable_age, _ = category_time_rule(category)
    try:
        return assess_news_time(
            row,
            decision_time=activated_at,
            forward_epoch=forward_epoch,
            max_actionable_age=actionable_age,
            max_discovery_delay=None,
        ).eligible
    except (TypeError, ValueError):
        return False


def _install_annotation_contract_lanes(
    connection: sqlite3.Connection,
    *,
    prompt_version: str,
    now: datetime,
) -> datetime:
    marker = f"annotation-work-lanes:{prompt_version}"
    backfill_state = connection.execute(
        "SELECT activated_at FROM news_annotation_contract_backfill_v1 "
        "WHERE prompt_version=?",
        (prompt_version,),
    ).fetchone()
    lane_state = connection.execute(
        "SELECT * FROM news_annotation_work_lane_migrations_v1 "
        "WHERE prompt_version=?",
        (prompt_version,),
    ).fetchone()
    if lane_state is not None:
        activated_at = datetime.fromisoformat(str(lane_state["activated_at"]))
    elif backfill_state is not None:
        activated_at = datetime.fromisoformat(str(backfill_state[0]))
    else:
        first_job = connection.execute(
            """SELECT min(created_at) FROM news_ai_jobs_v1
               WHERE task_type='ACTIVE_ANNOTATION' AND prompt_version=?""",
            (prompt_version,),
        ).fetchone()[0]
        activated_at = datetime.fromisoformat(str(first_job)) if first_job else now
    timestamp = _iso(now)
    with connection:
        if backfill_state is None:
            connection.execute(
                """INSERT INTO news_annotation_contract_backfill_v1
                   (prompt_version,activated_at,state,updated_at)
                   VALUES (?,?,'ACTIVE',?)""",
                (prompt_version, _iso(activated_at), timestamp),
            )
        if lane_state is None:
            connection.execute(
                """INSERT INTO news_annotation_work_lane_migrations_v1
                   (prompt_version,activated_at,state,updated_at)
                   VALUES (?,?,'ACTIVE',?)""",
                (prompt_version, _iso(activated_at), timestamp),
            )
    lane_state = connection.execute(
        "SELECT * FROM news_annotation_work_lane_migrations_v1 "
        "WHERE prompt_version=?",
        (prompt_version,),
    ).fetchone()
    if str(lane_state["state"]) == "COMPLETE":
        return activated_at
    cursor = None
    if lane_state["cursor_created_at"] is not None:
        cursor = (
            str(lane_state["cursor_created_at"]),
            str(lane_state["cursor_job_id"]),
        )
    cursor_clause = ""
    parameters: list[object] = [prompt_version]
    if cursor is not None:
        cursor_clause = "AND (j.created_at,j.job_id)>(?,?)"
        parameters.extend(cursor)
    rows = connection.execute(
        f"""SELECT j.*,n.source_published_time,n.collector_first_seen_time,
                   n.source AS revision_source
            FROM news_ai_jobs_v1 j
            LEFT JOIN news_revisions n ON n.source=j.source
             AND n.source_item_id=j.source_item_id
             AND n.revision_number=j.revision_number
            WHERE j.task_type='ACTIVE_ANNOTATION' AND j.prompt_version=?
              AND j.lane_classified=0 {cursor_clause}
            ORDER BY j.created_at,j.job_id LIMIT ?""",
        (*parameters, EXISTING_JOB_LANE_PAGE_SIZE),
    ).fetchall()
    with connection:
        for row in rows:
            missing_revision = row["revision_source"] is None
            historical = (
                missing_revision
                or datetime.fromisoformat(str(row["collector_first_seen_time"]))
                   < activated_at
            )
            lane = CONTRACT_BACKFILL_LANE if historical else LIVE_LANE
            priority = "BACKGROUND" if historical else "FAST"
            retired = (
                historical
                and str(row["state"]) in {"QUEUED", "BACKING_OFF"}
                and (
                    missing_revision
                    or not _contract_backfill_has_current_value(
                        connection, row, activated_at=activated_at,
                        prompt_version=prompt_version,
                    )
                )
            )
            connection.execute(
                """UPDATE news_ai_jobs_v1
                   SET work_lane=?,priority=?,lane_classified=1,
                       state=CASE WHEN ? THEN 'DEAD_LETTER' ELSE state END,
                       last_error=CASE WHEN ? THEN
                         'CURRENT_EVIDENCE_NO_LONGER_ELIGIBLE' ELSE last_error END,
                       lease_owner=CASE WHEN ? THEN NULL ELSE lease_owner END,
                       lease_expires_at=CASE WHEN ? THEN NULL ELSE lease_expires_at END,
                       updated_at=CASE WHEN ? THEN ? ELSE updated_at END,
                       completed_at=CASE WHEN ? THEN ? ELSE completed_at END
                   WHERE job_id=? AND lane_classified=0""",
                (
                    lane, priority, retired, retired, retired, retired,
                    retired, timestamp, retired, timestamp, row["job_id"],
                ),
            )
        if rows:
            last = rows[-1]
            connection.execute(
                """UPDATE news_annotation_work_lane_migrations_v1
                   SET cursor_created_at=?,cursor_job_id=?,
                       processed_count=processed_count+?,updated_at=?
                   WHERE prompt_version=?""",
                (
                    last["created_at"], last["job_id"], len(rows), timestamp,
                    prompt_version,
                ),
            )
        if len(rows) < EXISTING_JOB_LANE_PAGE_SIZE:
            connection.execute(
                """UPDATE news_annotation_work_lane_migrations_v1
                   SET state='COMPLETE',updated_at=? WHERE prompt_version=?""",
                (timestamp, prompt_version),
            )
            connection.execute(
                "INSERT OR IGNORE INTO news_ai_scheduler_migrations_v1 VALUES (?,?)",
                (marker, timestamp),
            )
    return activated_at


def _contract_backfill_page(
    connection: sqlite3.Connection,
    *,
    prompt_version: str,
    activated_at: datetime,
    now: datetime,
    pending_annotation_records,
    page_size: int,
) -> list[dict[str, object]]:
    state = connection.execute(
        "SELECT * FROM news_annotation_contract_backfill_v1 WHERE prompt_version=?",
        (prompt_version,),
    ).fetchone()
    if state is None or str(state["state"]) == "COMPLETE":
        return []
    cursor = None
    if state["cursor_first_seen"]:
        cursor = (
            str(state["cursor_first_seen"]), str(state["cursor_source"]),
            str(state["cursor_source_item_id"]), int(state["cursor_revision_number"]),
        )
    if page_size <= 0:
        return []
    scanned = pending_annotation_records(
        connection,
        observed_at=now,
        limit=min(CONTRACT_BACKFILL_PAGE_SIZE, page_size),
        prompt_version=prompt_version,
        received_before=activated_at,
        before_cursor=cursor,
        selection_order="receipt_desc",
    )
    selected = [
        row for row in scanned
        if _contract_backfill_has_current_value(
            connection, row, activated_at=activated_at,
            prompt_version=prompt_version,
        )
    ]
    with connection:
        if scanned:
            last = scanned[-1]
            connection.execute(
                """UPDATE news_annotation_contract_backfill_v1
                   SET cursor_first_seen=?,cursor_source=?,cursor_source_item_id=?,
                       cursor_revision_number=?,updated_at=? WHERE prompt_version=?""",
                (
                    last["collector_first_seen_time"], last["source"],
                    last["source_item_id"], last["revision_number"],
                    _iso(now), prompt_version,
                ),
            )
        if len(scanned) < min(CONTRACT_BACKFILL_PAGE_SIZE, page_size):
            connection.execute(
                """UPDATE news_annotation_contract_backfill_v1
                   SET state='COMPLETE',updated_at=? WHERE prompt_version=?""",
                (_iso(now), prompt_version),
            )
    return selected


def _install_downstream_work_provenance(
    connection: sqlite3.Connection,
    *,
    current_prompt_version: str,
    now: datetime,
) -> dict[str, int | str]:
    """Repair one bounded page without disturbing active leases or evidence."""
    timestamp = _iso(now)
    state = connection.execute(
        """SELECT * FROM news_downstream_provenance_migrations_v1
           WHERE provenance_version=?""",
        (WORK_PROVENANCE_VERSION,),
    ).fetchone()
    if state is None:
        with connection:
            connection.execute(
                """INSERT INTO news_downstream_provenance_migrations_v1
                   (provenance_version,state,updated_at)
                   VALUES (?,'ACTIVE',?)""",
                (WORK_PROVENANCE_VERSION, timestamp),
            )
        state = connection.execute(
            """SELECT * FROM news_downstream_provenance_migrations_v1
               WHERE provenance_version=?""",
            (WORK_PROVENANCE_VERSION,),
        ).fetchone()
    if str(state["state"]) == "COMPLETE":
        return {
            "state": "COMPLETE", "processed": 0, "resolved": 0,
            "unresolved": 0, "leased_skipped": 0,
        }
    annotation_migration = connection.execute(
        """SELECT state FROM news_annotation_work_lane_migrations_v1
           WHERE prompt_version=?""",
        (current_prompt_version,),
    ).fetchone()
    parents_complete = bool(
        annotation_migration and str(annotation_migration["state"]) == "COMPLETE"
    )
    priority_page = False
    rows: list[sqlite3.Row] = []
    if parents_complete:
        rows = connection.execute(
            """SELECT * FROM news_ai_jobs_v1
               WHERE task_type IN ('ACTIVE_IMPACT','TITLE_TRANSLATION')
                 AND state IN ('QUEUED','BACKING_OFF')
                 AND COALESCE(provenance_version,'')<>?
               ORDER BY created_at,job_id LIMIT ?""",
            (WORK_PROVENANCE_VERSION, DOWNSTREAM_PROVENANCE_PAGE_SIZE),
        ).fetchall()
        priority_page = bool(rows)
    cursor_clause = ""
    parameters: list[object] = [WORK_PROVENANCE_VERSION]
    if state["cursor_created_at"] is not None:
        cursor_clause = "AND (created_at,job_id)>(?,?)"
        parameters.extend((state["cursor_created_at"], state["cursor_job_id"]))
    if not priority_page:
        rows = connection.execute(
            f"""SELECT * FROM news_ai_jobs_v1
                WHERE task_type IN ('ACTIVE_IMPACT','TITLE_TRANSLATION')
                  AND COALESCE(provenance_version,'')<>? {cursor_clause}
                ORDER BY created_at,job_id LIMIT ?""",
            (*parameters, DOWNSTREAM_PROVENANCE_PAGE_SIZE),
        ).fetchall()
    resolved = 0
    unresolved = 0
    leased_skipped = 0
    finalized = 0
    with connection:
        for row in rows:
            if str(row["state"]) == "LEASED":
                leased_skipped += 1
                continue
            provenance = resolve_work_provenance(
                connection,
                source=str(row["source"]),
                source_item_id=str(row["source_item_id"]),
                revision_number=int(row["revision_number"]),
                annotation_id=str(row["annotation_id"] or ""),
                current_prompt_version=current_prompt_version,
            )
            if provenance is None:
                if not parents_complete:
                    continue
                connection.execute(
                    """UPDATE news_ai_jobs_v1
                       SET lane_classified=0,provenance_resolved=0,
                           provenance_version=?,
                           provenance_origin_task='UNRESOLVED',
                           provenance_origin_ref=?,updated_at=?
                       WHERE job_id=? AND state<>'LEASED'""",
                    (
                        WORK_PROVENANCE_VERSION,
                        f"unresolved:{row['job_id']}", timestamp, row["job_id"],
                    ),
                )
                unresolved += 1
                finalized += 1
                continue
            priority = (
                "BACKGROUND"
                if provenance.work_lane == CONTRACT_BACKFILL_LANE
                else str(row["priority"])
            )
            connection.execute(
                """UPDATE news_ai_jobs_v1
                   SET work_lane=?,priority=?,lane_classified=1,
                       provenance_resolved=1,provenance_version=?,
                       provenance_origin_task=?,provenance_origin_ref=?,
                       updated_at=?
                   WHERE job_id=? AND state<>'LEASED'""",
                (
                    provenance.work_lane, priority, WORK_PROVENANCE_VERSION,
                    provenance.origin_task, provenance.origin_ref,
                    timestamp, row["job_id"],
                ),
            )
            resolved += 1
            finalized += 1
        if rows:
            if priority_page:
                connection.execute(
                    """UPDATE news_downstream_provenance_migrations_v1
                       SET processed_count=processed_count+?,
                           resolved_count=resolved_count+?,
                           unresolved_count=unresolved_count+?,
                           leased_skipped_count=leased_skipped_count+?,
                           state='ACTIVE',updated_at=?
                       WHERE provenance_version=?""",
                    (
                        finalized, resolved, unresolved, leased_skipped,
                        timestamp, WORK_PROVENANCE_VERSION,
                    ),
                )
            else:
                last = rows[-1]
                connection.execute(
                    """UPDATE news_downstream_provenance_migrations_v1
                       SET cursor_created_at=?,cursor_job_id=?,
                           processed_count=processed_count+?,
                           resolved_count=resolved_count+?,
                           unresolved_count=unresolved_count+?,
                           leased_skipped_count=leased_skipped_count+?,
                           state='ACTIVE',updated_at=?
                       WHERE provenance_version=?""",
                    (
                        last["created_at"], last["job_id"], finalized, resolved,
                        unresolved, leased_skipped, timestamp,
                        WORK_PROVENANCE_VERSION,
                    ),
                )
        if not priority_page and len(rows) < DOWNSTREAM_PROVENANCE_PAGE_SIZE:
            remaining = int(connection.execute(
                """SELECT count(*) FROM news_ai_jobs_v1
                   WHERE task_type IN ('ACTIVE_IMPACT','TITLE_TRANSLATION')
                     AND COALESCE(provenance_version,'')<>?""",
                (WORK_PROVENANCE_VERSION,),
            ).fetchone()[0])
            next_state = "COMPLETE" if remaining == 0 else "WAITING_INPUT"
            connection.execute(
                """UPDATE news_downstream_provenance_migrations_v1
                   SET cursor_created_at=NULL,cursor_job_id=NULL,
                       pass_count=pass_count+1,state=?,updated_at=?
                   WHERE provenance_version=?""",
                (next_state, timestamp, WORK_PROVENANCE_VERSION),
            )
    current_state = str(connection.execute(
        """SELECT state FROM news_downstream_provenance_migrations_v1
           WHERE provenance_version=?""",
        (WORK_PROVENANCE_VERSION,),
    ).fetchone()[0])
    return {
        "state": current_state,
        "processed": finalized,
        "resolved": resolved,
        "unresolved": unresolved,
        "leased_skipped": leased_skipped,
    }


def sync_pending_jobs(
    connection: sqlite3.Connection,
    *,
    now: datetime | None = None,
    limit: int = 2_000,
) -> dict[str, int]:
    """Discover eligible evidence work and enqueue deterministic job identities."""
    from .annotation import (
        ANNOTATION_FAILURE_RECOVERY_VERSION,
        IMPACT_FAILURE_RECOVERY_VERSION,
        IMPACT_PROMPT_VERSION,
        PROMPT_VERSION,
        TITLE_PROMPT_VERSION,
        pending_annotation_records,
        pending_impact_records,
        pending_title_translation_records,
    )
    from .news_semantics import PREVIOUS_NEWS_PROMPT_VERSION
    from .semantic_transition import (
        MODEL_REVIEW_REQUIRED,
        execute_transition_page,
        transition_for,
    )

    transition = transition_for(PREVIOUS_NEWS_PROMPT_VERSION, PROMPT_VERSION)
    from .daily_brief import brief_dates_to_process

    instant = now or datetime.now(UTC)
    authorize_repairable_annotation_failures(
        connection,
        prompt_version=PROMPT_VERSION,
        recovery_version=ANNOTATION_FAILURE_RECOVERY_VERSION,
        now=instant,
    )
    authorize_repairable_impact_failures(
        connection,
        prompt_version=IMPACT_PROMPT_VERSION,
        recovery_version=IMPACT_FAILURE_RECOVERY_VERSION,
        now=instant,
    )
    activated_at = _install_annotation_contract_lanes(
        connection, prompt_version=PROMPT_VERSION, now=instant,
    )
    execute_transition_page(
        connection, transition, activated_at=activated_at, now=instant,
        page_size=CONTRACT_BACKFILL_PAGE_SIZE,
    )
    _install_downstream_work_provenance(
        connection, current_prompt_version=PROMPT_VERSION, now=instant,
    )
    brief_backlog = brief_dates_to_process(connection, now=instant)[1:]
    backfill_capacity = (
        min(CONTRACT_BACKFILL_PAGE_SIZE, max(1, limit // 2))
        if limit > 1 else 0
    )
    live_capacity = max(1, limit - backfill_capacity)
    live_annotations = pending_annotation_records(
        connection,
        observed_at=instant,
        limit=live_capacity,
        prompt_version=PROMPT_VERSION,
        priority_receipt_days=tuple(brief_backlog),
        received_from=activated_at,
    )
    model_review_backfill = transition.kind == MODEL_REVIEW_REQUIRED
    protected_capacity = (
        backfill_capacity if brief_backlog and model_review_backfill else 0
    )
    protected_annotations = pending_annotation_records(
        connection,
        observed_at=instant,
        limit=protected_capacity,
        prompt_version=PROMPT_VERSION,
        priority_receipt_days=tuple(brief_backlog),
        received_before=activated_at,
    ) if protected_capacity else []
    backfill_annotations = (
        _contract_backfill_page(
            connection,
            prompt_version=PROMPT_VERSION,
            activated_at=activated_at,
            now=instant,
            pending_annotation_records=pending_annotation_records,
            page_size=max(0, backfill_capacity - len(protected_annotations)),
        )
        if model_review_backfill else []
    )
    live_by_revision = {
        (str(row["source"]), str(row["source_item_id"]), int(row["revision_number"])): row
        for row in live_annotations
    }
    backfill_by_revision = {
        (str(row["source"]), str(row["source_item_id"]), int(row["revision_number"])): row
        for row in (*protected_annotations, *backfill_annotations)
        if (str(row["source"]), str(row["source_item_id"]), int(row["revision_number"]))
        not in live_by_revision
    }
    for row in live_by_revision.values():
        enqueue_job(
            connection,
            task_type="ACTIVE_ANNOTATION",
            source=str(row["source"]),
            source_item_id=str(row["source_item_id"]),
            revision_number=int(row["revision_number"]),
            prompt_version=PROMPT_VERSION,
            priority="FAST",
            work_lane=LIVE_LANE,
            reopen_completed=True,
            now=instant,
        )
    for row in backfill_by_revision.values():
        enqueue_job(
            connection,
            task_type="ACTIVE_ANNOTATION",
            source=str(row["source"]),
            source_item_id=str(row["source_item_id"]),
            revision_number=int(row["revision_number"]),
            prompt_version=PROMPT_VERSION,
            priority="BACKGROUND",
            work_lane=CONTRACT_BACKFILL_LANE,
            reopen_completed=True,
            now=instant,
        )
    oldest_impact_limit = (limit + 1) // 2
    newest_impact_limit = limit // 2
    oldest_impacts = pending_impact_records(
        connection, observed_at=instant, limit=oldest_impact_limit,
        annotation_prompt_version=PROMPT_VERSION,
        impact_prompt_version=IMPACT_PROMPT_VERSION,
        selection_order="oldest",
    )
    newest_impacts = pending_impact_records(
        connection, observed_at=instant, limit=newest_impact_limit,
        annotation_prompt_version=PROMPT_VERSION,
        impact_prompt_version=IMPACT_PROMPT_VERSION,
        selection_order="newest",
    ) if newest_impact_limit else []
    active_impacts_by_annotation = {
        str(row["annotation_id"]): row
        for row in (*oldest_impacts, *newest_impacts)
    }
    active_impacts = list(active_impacts_by_annotation.values())
    titles = pending_title_translation_records(
        connection, observed_at=instant, limit=limit,
    )
    batches = (
        ("ACTIVE_IMPACT", IMPACT_PROMPT_VERSION, active_impacts),
        ("TITLE_TRANSLATION", TITLE_PROMPT_VERSION, titles),
    )
    discovered: dict[str, int] = {
        "ACTIVE_ANNOTATION": len(live_by_revision) + len(backfill_by_revision),
    }
    for task_type, prompt_version, rows in batches:
        enqueued = 0
        for row in rows:
            provenance = resolve_work_provenance(
                connection,
                source=str(row["source"]),
                source_item_id=str(row["source_item_id"]),
                revision_number=int(row["revision_number"]),
                annotation_id=str(row.get("annotation_id") or ""),
                current_prompt_version=PROMPT_VERSION,
            )
            if provenance is None:
                continue
            enqueue_derived_ai_job(
                connection,
                provenance=provenance,
                task_type=task_type,
                source=str(row["source"]),
                source_item_id=str(row["source_item_id"]),
                revision_number=int(row["revision_number"]),
                annotation_id=str(row.get("annotation_id") or ""),
                prompt_version=prompt_version,
                priority=(
                    _annotation_priority(row)
                    if task_type == "ACTIVE_IMPACT"
                    else "BACKGROUND" if task_type == "TITLE_TRANSLATION"
                    else "NORMAL"
                ),
                now=instant,
            )
            enqueued += 1
        discovered[task_type] = enqueued
    reconcile_completed_jobs(connection, now=instant)
    _reopen_protected_annotation_jobs(
        connection, protected_annotations, prompt_version=PROMPT_VERSION,
        now=instant,
    )
    return discovered


def _reopen_protected_annotation_jobs(
    connection: sqlite3.Connection,
    records: list[dict[str, object]],
    *,
    prompt_version: str,
    now: datetime,
) -> int:
    """Recover only obsolete jobs proven claimable by the protected backlog."""
    timestamp = _iso(now)
    job_ids = {
        _job_id(
            "ACTIVE_ANNOTATION", str(row["source"]),
            str(row["source_item_id"]), int(row["revision_number"]), "",
            prompt_version,
        )
        for row in records
    }
    if not job_ids:
        return 0
    recovered = 0
    with connection:
        for job_id in sorted(job_ids):
            result = connection.execute(
                """UPDATE news_ai_jobs_v1
                   SET state='QUEUED',available_at=?,lease_owner=NULL,
                       lease_expires_at=NULL,last_error=NULL,updated_at=?,
                       completed_at=NULL
                   WHERE job_id=? AND task_type='ACTIVE_ANNOTATION'
                     AND state='DEAD_LETTER'
                     AND last_error='CURRENT_EVIDENCE_NO_LONGER_ELIGIBLE'""",
                (timestamp, timestamp, job_id),
            )
            recovered += result.rowcount
    return recovered


def reconcile_completed_jobs(
    connection: sqlite3.Connection,
    *,
    now: datetime | None = None,
    manage_transaction: bool = True,
) -> int:
    """Close jobs already satisfied or superseded by immutable evidence."""
    from contextlib import nullcontext

    from .annotation import (
        ANNOTATION_BODY_MIN_CHARACTERS,
        INVALID_CHINESE_TITLE,
        PROMPT_VERSION,
    )
    from .news_identity import preferred_cluster_peer_predicate
    from .news_semantics import model_usable_annotation_predicate
    from .news_time import (
        register_news_semantic_eligibility_sql,
        semantic_eligibility_sql_predicate,
    )

    timestamp = _iso(now or datetime.now(UTC))
    register_news_semantic_eligibility_sql(connection)
    forward_epoch = str(connection.execute(
        "SELECT value FROM runtime_metadata WHERE key='FORWARD_EPOCH'"
    ).fetchone()[0])
    transaction = connection if manage_transaction else nullcontext()
    with transaction:
        completed = connection.execute(
            f"""UPDATE news_ai_jobs_v1 AS j
               SET state='COMPLETED',lease_owner=NULL,lease_expires_at=NULL,
                   updated_at=?,completed_at=?
               WHERE state<>'COMPLETED' AND (
                 (task_type='ACTIVE_ANNOTATION' AND EXISTS (
                   SELECT 1 FROM news_annotations a
                    WHERE a.source=j.source AND a.source_item_id=j.source_item_id
                      AND a.revision_number=j.revision_number
                      AND a.prompt_version=j.prompt_version
                      AND {model_usable_annotation_predicate('a')}))
                 OR (task_type='ACTIVE_IMPACT' AND EXISTS (
                   SELECT 1 FROM news_impact_assessments_v1 i
                   WHERE i.annotation_id=j.annotation_id
                     AND i.prompt_version=j.prompt_version))
                 OR (task_type='TITLE_TRANSLATION' AND EXISTS (
                   SELECT 1 FROM news_title_translations t
                   WHERE t.source=j.source AND t.source_item_id=j.source_item_id
                     AND t.revision_number=j.revision_number
                     AND t.prompt_version=j.prompt_version
                     AND trim(t.headline_zh)<>?
                     AND t.headline_zh NOT LIKE ?
                     AND t.headline_zh GLOB '*[一-龥]*')))""",
            (timestamp, timestamp, INVALID_CHINESE_TITLE, "%相关数值%"),
        )
        obsolete = connection.execute(
            f"""UPDATE news_ai_jobs_v1 AS j
               SET state='DEAD_LETTER',lease_owner=NULL,lease_expires_at=NULL,
                   last_error='CURRENT_EVIDENCE_NO_LONGER_ELIGIBLE',
                   updated_at=?,completed_at=?
               WHERE state IN ('QUEUED','BACKING_OFF','COMPLETED','DEAD_LETTER')
                 AND (
                   (j.task_type='ACTIVE_ANNOTATION'
                    AND j.prompt_version<>?)
                   OR
                   EXISTS (
                     SELECT 1 FROM news_revisions newer
                     WHERE newer.source=j.source
                       AND newer.source_item_id=j.source_item_id
                       AND newer.revision_number>j.revision_number)
                   OR (j.task_type='ACTIVE_ANNOTATION' AND EXISTS (
                     SELECT 1 FROM news_revisions current
                     WHERE current.source=j.source
                       AND current.source_item_id=j.source_item_id
                       AND current.revision_number=j.revision_number
                       AND (
                         length(trim(COALESCE(current.body,'')))<
                           {ANNOTATION_BODY_MIN_CHARACTERS}
                         OR NOT ({semantic_eligibility_sql_predicate('current')})
                         OR (j.state='COMPLETED' AND NOT EXISTS (
                           SELECT 1 FROM news_annotations current_annotation
                           WHERE current_annotation.source=j.source
                             AND current_annotation.source_item_id=j.source_item_id
                             AND current_annotation.revision_number=j.revision_number
                             AND current_annotation.prompt_version=j.prompt_version
                             AND {model_usable_annotation_predicate('current_annotation')}
                         ))
                         OR EXISTS (
                         SELECT 1 FROM news_revisions peer
                         WHERE peer.cluster_id=current.cluster_id
                           AND {semantic_eligibility_sql_predicate('peer')}
                           AND NOT EXISTS (
                             SELECT 1 FROM news_revisions peer_newer
                             WHERE peer_newer.source=peer.source
                               AND peer_newer.source_item_id=peer.source_item_id
                               AND peer_newer.revision_number>peer.revision_number)
                           AND {preferred_cluster_peer_predicate('peer', 'current')}
                         )
                       )
                   ))
                 )""",
            (
                timestamp, timestamp, PROMPT_VERSION,
                forward_epoch, forward_epoch,
            ),
        )
    return completed.rowcount + obsolete.rowcount


def pending_record_for_job(
    connection: sqlite3.Connection,
    job: ScheduledJob,
    *,
    now: datetime | None = None,
) -> dict[str, object] | None:
    """Resolve a lease back to the exact current evidence row it represents."""
    from .annotation import (
        IMPACT_PROMPT_VERSION,
        PROMPT_VERSION,
        pending_annotation_records,
        pending_impact_records,
        pending_title_translation_records,
    )
    from .daily_brief import brief_dates_to_process

    instant = now or datetime.now(UTC)
    if job.task_type == "ACTIVE_ANNOTATION":
        protected_days = tuple(brief_dates_to_process(connection, now=instant)[1:])
        rows = pending_annotation_records(
            connection, observed_at=instant, limit=100_000,
            prompt_version=PROMPT_VERSION,
            priority_receipt_days=protected_days,
        )
    elif job.task_type == "ACTIVE_IMPACT":
        rows = pending_impact_records(
            connection, observed_at=instant, limit=100_000,
            annotation_prompt_version=PROMPT_VERSION,
            impact_prompt_version=IMPACT_PROMPT_VERSION,
        )
    else:
        rows = pending_title_translation_records(
            connection, observed_at=instant, limit=100_000,
        )
    for row in rows:
        if (
            str(row["source"]) == job.source
            and str(row["source_item_id"]) == job.source_item_id
            and int(row["revision_number"]) == job.revision_number
            and str(row.get("annotation_id") or "") == job.annotation_id
        ):
            return row
    return None
