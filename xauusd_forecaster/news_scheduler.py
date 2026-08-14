"""Persistent operational scheduling for versioned news AI work.

The scheduler tables are deliberately mutable operational state.  Model inputs,
annotations, assessments, failures, and predictions remain append-only evidence.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
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


def _iso(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("scheduler timestamps must be timezone-aware")
    return value.astimezone(UTC).isoformat(timespec="microseconds")


def _credential_id(api_key: str) -> str:
    return hashlib.sha256(api_key.encode("utf-8")).hexdigest()[:12]


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
    raw = raw_accounts if raw_accounts is not None else os.environ.get(
        "GEMINI_API_ACCOUNTS", ""
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
            keys.extend(os.environ.get("GEMINI_API_KEYS", "").split(";"))
            keys.append(os.environ.get("GEMINI_API_KEY", ""))
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
    now: datetime | None = None,
    lease_seconds: int = 180,
) -> ScheduledJob | None:
    if pool not in {ROUTINE_POOL, PREEMPTIBLE_POOL}:
        raise ValueError("scheduler pool is not controlled")
    if not worker_id.strip():
        raise ValueError("scheduler worker_id is required")
    instant = now or datetime.now(UTC)
    timestamp = _iso(instant)
    aged_before = _iso(instant - PRIORITY_HEAD_START)
    lease_expires = _iso(instant + timedelta(seconds=max(30, lease_seconds)))
    priority_filter = (
        "AND priority IN ('IMMEDIATE','FAST')"
        if pool == PREEMPTIBLE_POOL else ""
    )
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
                  AND task_type IN (
                    'ACTIVE_ANNOTATION','ACTIVE_IMPACT','TITLE_TRANSLATION')
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
            (timestamp, aged_before, aged_before),
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
    recent_minutes = (
        minute_bucket(instant - timedelta(minutes=1)), minute_bucket(instant),
    )
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
        account_clause = "" if policy.share_minute_across_accounts else "AND account_id=?"
        parameters: tuple[object, ...] = (
            *recent_minutes,
            *((account_id,) if not policy.share_minute_across_accounts else ()),
            *families,
        )
        recent = connection.execute(
            f"""SELECT COALESCE(sum(request_count),0) AS requests,
                       COALESCE(sum(input_token_count),0) AS tokens
                FROM news_ai_account_minute_usage_v1
                WHERE minute_bucket IN (?,?) {account_clause}
                  AND model_family IN ({placeholders})""",
            parameters,
        ).fetchone()
        ratios = (
            max(0.0, (daily_limit - int(daily["requests"])) / max(1, daily_limit)),
            max(0.0, (policy.requests_per_minute - int(recent["requests"]))
                / max(1, policy.requests_per_minute)),
            max(0.0, (policy.input_tokens_per_minute - int(recent["tokens"]))
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


def reserve_account_request(
    connection: sqlite3.Connection,
    *,
    account_id: str,
    model_family: str,
    daily_limit: int,
    requests_per_minute: int,
    input_tokens: int = 0,
    input_tokens_per_minute: int | None = None,
    shared_model_families: tuple[str, ...] | None = None,
    share_minute_across_accounts: bool = False,
    reserve_total: int = 0,
    urgent: bool = False,
    now: datetime | None = None,
) -> bool:
    """Atomically count one attempted request against an independent account."""
    instant = now or datetime.now(UTC)
    day = quota_day(instant)
    minute = minute_bucket(instant)
    recent_minutes = (
        minute_bucket(instant - timedelta(minutes=1)), minute,
    )
    timestamp = _iso(instant)
    estimated_tokens = max(0, int(input_tokens))
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
        account_clause = "" if share_minute_across_accounts else "AND account_id=?"
        recent_parameters: tuple[object, ...] = (
            *recent_minutes,
            *((account_id,) if not share_minute_across_accounts else ()),
            *families,
        )
        recent = connection.execute(
            f"""SELECT COALESCE(sum(request_count),0) AS request_count,
                       COALESCE(sum(input_token_count),0) AS input_token_count
                FROM news_ai_account_minute_usage_v1
                WHERE minute_bucket IN (?,?) {account_clause}
                  AND model_family IN ({placeholders})""",
            recent_parameters,
        ).fetchone()
        minute_count = int(recent["request_count"])
        minute_tokens = int(recent["input_token_count"])
        token_exhausted = (
            input_tokens_per_minute is not None
            and minute_tokens + estimated_tokens > input_tokens_per_minute
        )
        if (
            daily_count >= usable_daily_limit
            or minute_count >= requests_per_minute
            or token_exhausted
        ):
            connection.rollback()
            return False
        connection.execute(
            """INSERT INTO news_ai_account_daily_usage_v1 VALUES (?,?,?,?,?)
               ON CONFLICT(quota_day,account_id,model_family) DO UPDATE SET
                 request_count=request_count+1,updated_at=excluded.updated_at""",
            (day, account_id, model_family, 1, timestamp),
        )
        connection.execute(
            """INSERT INTO news_ai_account_minute_usage_v1
               (minute_bucket,account_id,model_family,request_count,
                input_token_count,updated_at) VALUES (?,?,?,?,?,?)
               ON CONFLICT(minute_bucket,account_id,model_family) DO UPDATE SET
                 request_count=request_count+1,
                 input_token_count=input_token_count+excluded.input_token_count,
                 updated_at=excluded.updated_at""",
            (minute, account_id, model_family, 1, estimated_tokens, timestamp),
        )
        connection.commit()
        return True
    except Exception:
        connection.rollback()
        raise


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


def sync_pending_jobs(
    connection: sqlite3.Connection,
    *,
    now: datetime | None = None,
    limit: int = 2_000,
) -> dict[str, int]:
    """Discover eligible evidence work and enqueue deterministic job identities."""
    from .annotation import (
        IMPACT_PROMPT_VERSION,
        PROMPT_VERSION,
        TITLE_PROMPT_VERSION,
        pending_annotation_records,
        pending_impact_records,
        pending_title_translation_records,
    )

    instant = now or datetime.now(UTC)
    active_annotations = pending_annotation_records(
        connection, observed_at=instant, limit=limit, prompt_version=PROMPT_VERSION,
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
                now=instant,
            )
    reconcile_completed_jobs(connection, now=instant)
    return discovered


def reconcile_completed_jobs(
    connection: sqlite3.Connection,
    *,
    now: datetime | None = None,
) -> int:
    """Close jobs already satisfied or superseded by immutable evidence."""
    from .annotation import INVALID_CHINESE_TITLE

    timestamp = _iso(now or datetime.now(UTC))
    with connection:
        completed = connection.execute(
            """UPDATE news_ai_jobs_v1 AS j
               SET state='COMPLETED',lease_owner=NULL,lease_expires_at=NULL,
                   updated_at=?,completed_at=?
               WHERE state<>'COMPLETED' AND (
                 (task_type='ACTIVE_ANNOTATION' AND EXISTS (
                   SELECT 1 FROM news_annotations a
                   WHERE a.source=j.source AND a.source_item_id=j.source_item_id
                     AND a.revision_number=j.revision_number
                     AND a.prompt_version=j.prompt_version))
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

    instant = now or datetime.now(UTC)
    if job.task_type == "ACTIVE_ANNOTATION":
        rows = pending_annotation_records(
            connection, observed_at=instant, limit=100_000,
            prompt_version=PROMPT_VERSION,
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
