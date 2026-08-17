"""Persistent operational scheduling for versioned news AI work.

The scheduler tables are deliberately mutable operational state.  Model inputs,
annotations, assessments, failures, and predictions remain append-only evidence.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo


PACIFIC = ZoneInfo("America/Los_Angeles")
ROUTINE_POOL = "ROUTINE"
PREEMPTIBLE_POOL = "PREEMPTIBLE"
URGENT_PRIORITIES = frozenset({"IMMEDIATE", "FAST"})
PRIORITIES = ("IMMEDIATE", "FAST", "NORMAL", "BACKGROUND")
PRIORITY_HEAD_START = timedelta(minutes=1)
TASKS = (
    "ACTIVE_ANNOTATION",
    "ACTIVE_IMPACT",
    "TITLE_TRANSLATION",
)
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
    vectors_committed_at TEXT
);

CREATE INDEX IF NOT EXISTS news_ai_account_request_usage_window_v1
ON news_ai_account_request_usage_v1(account_id,reserved_at,model_family);

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
    api_key: str
    credential_id: str


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
    state: str
    available_at: datetime
    lease_owner: str | None
    lease_expires_at: datetime | None
    attempt_count: int


def install_scheduler_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(SCHEDULER_SCHEMA)
    installed_at = datetime.now(UTC)
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
    connection.commit()


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
        *((account_id,) if not share_across_accounts else ()),
        *families,
    )
    row = connection.execute(
        f"""SELECT COALESCE(sum(request_count),0) AS requests,
                   COALESCE(sum(input_token_count),0) AS tokens
            FROM news_ai_account_request_usage_v1
            WHERE reserved_at>? {account_clause}
              AND model_family IN ({placeholders})""",
        parameters,
    ).fetchone()
    return int(row["requests"]), int(row["tokens"])


def _iso(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("scheduler timestamps must be timezone-aware")
    return value.astimezone(UTC).isoformat(timespec="microseconds")


def _credential_id(api_key: str) -> str:
    return hashlib.sha256(api_key.encode("utf-8")).hexdigest()[:12]


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
    ``pool``, and ``api_keys``.  Legacy key variables remain routine-only and
    preserve the previous one-key/one-account behavior.
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

    credentials: list[ApiCredential] = []
    account_pools: dict[str, str] = {}
    key_accounts: dict[str, str] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("each Gemini account must be an object")
        account_id = str(entry.get("account_id") or "").strip()
        pool = str(entry.get("pool") or "").strip().upper()
        api_keys = entry.get("api_keys")
        if not account_id:
            raise ValueError("Gemini account_id is required")
        if pool not in {ROUTINE_POOL, PREEMPTIBLE_POOL}:
            raise ValueError("Gemini account pool is not controlled")
        if not isinstance(api_keys, list) or not api_keys:
            raise ValueError("Gemini account api_keys must be a non-empty list")
        prior_pool = account_pools.setdefault(account_id, pool)
        if prior_pool != pool:
            raise ValueError("one Gemini account cannot belong to two pools")
        for raw_key in api_keys:
            api_key = str(raw_key or "").strip()
            if not api_key:
                raise ValueError("Gemini account contains an empty API key")
            prior_account = key_accounts.setdefault(api_key, account_id)
            if prior_account != account_id:
                raise ValueError("one Gemini API key cannot belong to two accounts")
            credential = ApiCredential(
                account_id=account_id,
                pool=pool,
                api_key=api_key,
                credential_id=_credential_id(api_key),
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


def enqueue_job(
    connection: sqlite3.Connection,
    *,
    task_type: str,
    source: str,
    source_item_id: str,
    revision_number: int,
    prompt_version: str,
    priority: str,
    annotation_id: str = "",
    reopen_completed: bool = False,
    now: datetime | None = None,
) -> str:
    if task_type not in TASKS:
        raise ValueError("scheduler task type is not controlled")
    if priority not in PRIORITIES:
        raise ValueError("scheduler priority is not controlled")
    created = now or datetime.now(UTC)
    job_id = _job_id(
        task_type, source, source_item_id, revision_number,
        annotation_id, prompt_version,
    )
    timestamp = _iso(created)
    with connection:
        connection.execute(
            """INSERT OR IGNORE INTO news_ai_jobs_v1 VALUES
            (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                job_id, task_type, source, source_item_id, revision_number,
                annotation_id, prompt_version, priority, "QUEUED", timestamp,
                None, None, 0, None, timestamp, timestamp, None,
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
                str(status.get("error") or status.get("reason") or "")[:500] or None,
                _iso(attempted_at),
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
        state=str(row["state"]),
        available_at=datetime.fromisoformat(str(row["available_at"])),
        lease_owner=str(row["lease_owner"]) if row["lease_owner"] else None,
        lease_expires_at=(
            datetime.fromisoformat(str(row["lease_expires_at"]))
            if row["lease_expires_at"] else None
        ),
        attempt_count=int(row["attempt_count"]),
    )


def claim_job(
    connection: sqlite3.Connection,
    *,
    worker_id: str,
    pool: str,
    task_types: tuple[str, ...] | None = None,
    excluded_task_types: frozenset[str] = frozenset(),
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
        "AND priority IN ('IMMEDIATE','FAST')"
        if pool == PREEMPTIBLE_POOL else ""
    )
    task_placeholders = ",".join("?" for _ in claimable_tasks)
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
            f"""SELECT * FROM news_ai_jobs_v1
                WHERE state IN ('QUEUED','BACKING_OFF')
                  AND task_type IN ({task_placeholders})
                  AND available_at<=? {priority_filter}
                ORDER BY
                  CASE WHEN created_at<=? THEN 0 ELSE 1 END,
                  CASE WHEN created_at<=? THEN created_at ELSE NULL END,
                  CASE priority WHEN 'IMMEDIATE' THEN 0 WHEN 'FAST' THEN 1
                                WHEN 'NORMAL' THEN 2 ELSE 3 END,
                  CASE task_type WHEN 'ACTIVE_IMPACT' THEN 0
                                 WHEN 'ACTIVE_ANNOTATION' THEN 1 ELSE 2 END,
                  created_at,job_id
                LIMIT 1""",
            (*claimable_tasks, timestamp, aged_before, aged_before),
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
        keys.append({
            "slot": slot,
            "fingerprint": fingerprint,
            "sent": sent,
            "remaining": max(0, daily_limit - sent),
            "status": "AVAILABLE" if sent < daily_limit else "DAILY_LIMIT",
        })
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
    attempted_requests = int(request_count)
    if attempted_requests < 1:
        raise ValueError("request_count must be positive")
    families = tuple(dict.fromkeys(shared_model_families or (model_family,)))
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
        minute_count, minute_tokens = rolling_account_usage(
            connection,
            account_id=account_id,
            model_families=families,
            now=instant,
            share_across_accounts=share_minute_across_accounts,
        )
        token_exhausted = (
            input_tokens_per_minute is not None
            and minute_tokens + estimated_tokens > input_tokens_per_minute
        )
        if (
            daily_count + attempted_requests > usable_daily_limit
            or minute_count + attempted_requests > requests_per_minute
            or token_exhausted
        ):
            connection.rollback()
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
                input_token_count,reserved_at)
               VALUES (?,?,?,?,?,?)""",
            (
                usage_id or str(uuid.uuid4()), account_id, model_family,
                attempted_requests,
                estimated_tokens, timestamp,
            ),
        )
        connection.commit()
        return True
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
    now: datetime | None = None,
) -> None:
    if outcome not in {
        "PROVIDER_SUCCEEDED", "PROVIDER_THROTTLED", "PROVIDER_FAILED",
    }:
        raise ValueError("provider request outcome is not controlled")
    timestamp = _iso(now or datetime.now(UTC))
    with connection:
        updated = connection.execute(
            """UPDATE news_ai_account_request_usage_v1
               SET provider_outcome=?,provider_http_status=?,
                   provider_completed_at=?
               WHERE usage_id=? AND attempted_at IS NOT NULL
                 AND provider_outcome IS NULL""",
            (outcome, provider_http_status, timestamp, usage_id),
        )
        if updated.rowcount != 1:
            raise ValueError("model request outcome is not claimable")


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
        GROUP BY state"""
    ).fetchall()
    counts = {str(row["state"]): int(row["total"]) for row in rows}
    result = {state.lower(): counts.get(state, 0) for state in (
        "QUEUED", "LEASED", "BACKING_OFF", "COMPLETED", "DEAD_LETTER",
    )}
    obsolete = int(connection.execute(
        """SELECT count(*) FROM news_ai_jobs_v1
        WHERE state='DEAD_LETTER'
          AND last_error='CURRENT_EVIDENCE_NO_LONGER_ELIGIBLE'"""
    ).fetchone()[0])
    result["obsolete"] = obsolete
    result["dead_letter"] = max(0, result["dead_letter"] - obsolete)
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
               JOIN news_llm_failure_evidence_v1 e
                 ON e.failure_id=f.failure_id
               WHERE f.task_type='ANNOTATION' AND f.prompt_version=?
                 AND f.is_terminal=1
                 AND (
                   e.failure_stage IN (
                     'DISPLAY_REPAIR','EVIDENCE_ANCHOR_REPAIR')
                   OR (e.failure_stage='SEMANTIC_CONTRACT'
                     AND e.cause='annotation supporting evidence is absent from source'))
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
    brief_backlog = brief_dates_to_process(connection, now=instant)[1:]
    protected_limit = (limit + 1) // 2 if brief_backlog else 0
    protected_annotations = pending_annotation_records(
        connection, observed_at=instant, limit=max(1, protected_limit),
        prompt_version=PROMPT_VERSION,
        priority_receipt_days=tuple(brief_backlog),
    ) if protected_limit else []
    general_annotations = pending_annotation_records(
        connection, observed_at=instant, limit=limit, prompt_version=PROMPT_VERSION,
    )
    active_annotations_by_revision = {
        (str(row["source"]), str(row["source_item_id"]), int(row["revision_number"])): row
        for row in (*protected_annotations, *general_annotations)
    }
    active_annotations = list(active_annotations_by_revision.values())[:limit]
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
        ("ACTIVE_ANNOTATION", PROMPT_VERSION, active_annotations),
        ("ACTIVE_IMPACT", IMPACT_PROMPT_VERSION, active_impacts),
        ("TITLE_TRANSLATION", TITLE_PROMPT_VERSION, titles),
    )
    discovered: dict[str, int] = {}
    for task_type, prompt_version, rows in batches:
        discovered[task_type] = len(rows)
        for row in rows:
            enqueue_job(
                connection,
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
                reopen_completed=task_type == "ACTIVE_ANNOTATION",
                now=instant,
            )
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
) -> int:
    """Close jobs already satisfied or superseded by immutable evidence."""
    from .annotation import INVALID_CHINESE_TITLE
    from .news_semantics import model_usable_annotation_predicate

    timestamp = _iso(now or datetime.now(UTC))
    with connection:
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
            """UPDATE news_ai_jobs_v1 AS j
               SET state='DEAD_LETTER',lease_owner=NULL,lease_expires_at=NULL,
                   last_error='CURRENT_EVIDENCE_NO_LONGER_ELIGIBLE',
                   updated_at=?,completed_at=?
               WHERE state IN ('QUEUED','BACKING_OFF')
                 AND EXISTS (
                   SELECT 1 FROM news_revisions newer
                   WHERE newer.source=j.source
                     AND newer.source_item_id=j.source_item_id
                     AND newer.revision_number>j.revision_number)""",
            (timestamp, timestamp),
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
