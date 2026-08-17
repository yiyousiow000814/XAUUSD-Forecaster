"""Durable source-poll cadence and bounded failure recovery."""

from __future__ import annotations

import re
import sqlite3
from datetime import UTC, datetime, timedelta


TRANSIENT_RETRY_DELAYS = (
    timedelta(minutes=5),
    timedelta(minutes=15),
    timedelta(minutes=30),
)
RATE_LIMIT_RETRY_DELAYS = (
    timedelta(minutes=60),
    timedelta(minutes=120),
    timedelta(minutes=240),
    timedelta(minutes=360),
)
AUTH_RETRY_DELAY = timedelta(hours=6)


def _instant(value: object) -> datetime | None:
    if value in (None, ""):
        return None
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _failure_class(error_type: object, error: object, status: object) -> str:
    kind = str(error_type or "")
    detail = str(error or "")
    if kind == "RateLimited" or re.search(r"\b(?:HTTP Error )?429\b", detail):
        return "RATE_LIMITED"
    if kind == "AuthConfigurationError" or re.search(
        r"\b(?:HTTP Error )?(?:401|403)\b", detail,
    ):
        return "OPERATOR_ACTION_REQUIRED"
    if str(status) == "PARTIAL":
        return "PARTIAL_RECOVERY"
    return "AUTO_RECOVERING"


def source_poll_recovery_state(
    connection: sqlite3.Connection,
    source: str,
    *,
    observed_at: datetime,
) -> dict[str, object] | None:
    """Derive the current bounded retry from append-only poll evidence."""
    rows = connection.execute(
        """SELECT fetched_time,status,error_type,error FROM source_polls
           WHERE source=? ORDER BY fetched_time DESC,poll_id DESC""",
        (source,),
    ).fetchall()
    if not rows or str(rows[0]["status"]) == "OK":
        return None
    failure_rows = []
    for row in rows:
        if str(row["status"]) == "OK":
            break
        failure_rows.append(row)
    latest = failure_rows[0]
    failure_count = len(failure_rows)
    recovery_mode = _failure_class(
        latest["error_type"], latest["error"], latest["status"],
    )
    if recovery_mode == "OPERATOR_ACTION_REQUIRED":
        delay = AUTH_RETRY_DELAY
    elif recovery_mode == "RATE_LIMITED":
        delay = RATE_LIMIT_RETRY_DELAYS[
            min(failure_count - 1, len(RATE_LIMIT_RETRY_DELAYS) - 1)
        ]
    else:
        delay = TRANSIENT_RETRY_DELAYS[
            min(failure_count - 1, len(TRANSIENT_RETRY_DELAYS) - 1)
        ]
    last_attempt_at = _instant(latest["fetched_time"])
    if last_attempt_at is None:
        return None
    next_retry_at = last_attempt_at + delay
    return {
        "recovery_mode": recovery_mode,
        "failure_count": failure_count,
        "last_attempt_at": last_attempt_at,
        "next_retry_at": next_retry_at,
        "retry_due": observed_at >= next_retry_at,
        "latest_status": str(latest["status"]),
        "last_error_type": latest["error_type"],
        "last_error": latest["error"],
    }


def source_poll_gate(
    connection: sqlite3.Connection,
    source: str,
    *,
    observed_at: datetime,
    success_interval: timedelta,
) -> dict[str, object] | None:
    """Return a skip status or allow one normal/recovery collection attempt."""
    recovery = source_poll_recovery_state(
        connection, source, observed_at=observed_at,
    )
    if recovery is not None:
        if recovery["retry_due"]:
            return None
        return {
            "source": source,
            "status": "SKIPPED_RETRY_BACKOFF",
            "recovery_mode": recovery["recovery_mode"],
            "failure_count": recovery["failure_count"],
            "next_retry_at": recovery["next_retry_at"].isoformat(),
        }
    row = connection.execute(
        """SELECT max(fetched_time) AS fetched FROM source_polls
           WHERE source=? AND status='OK'""",
        (source,),
    ).fetchone()
    last_success = _instant(row["fetched"] if row else None)
    if last_success is not None and observed_at - last_success < success_interval:
        return {
            "source": source,
            "status": "SKIPPED_INTERVAL",
            "next_poll_after": (last_success + success_interval).isoformat(),
        }
    return None


def classified_poll_error(error: Exception) -> tuple[str, str]:
    """Classify provider failures without including request URLs or secrets."""
    code = getattr(error, "code", None)
    if code == 429:
        error_type = "RateLimited"
    elif code in {401, 403}:
        error_type = "AuthConfigurationError"
    else:
        error_type = type(error).__name__
    return error_type, str(error)
