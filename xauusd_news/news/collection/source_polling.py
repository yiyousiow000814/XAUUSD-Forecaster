"""Durable source-poll cadence and bounded failure recovery."""

from __future__ import annotations

import re
import sqlite3
import urllib.error
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from math import ceil


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
MAX_PROVIDER_RETRY_AFTER_SECONDS = 24 * 60 * 60
RECOVERY_HISTORY_LIMIT = max(
    len(TRANSIENT_RETRY_DELAYS), len(RATE_LIMIT_RETRY_DELAYS),
)


@dataclass(frozen=True)
class PollFailureClassification:
    """Safe structured transport facts for one append-only poll receipt."""

    error_type: str
    provider_http_status: int | None
    retry_after_seconds: int | None

    def poll_fields(self, message: str) -> dict[str, object]:
        return {
            "error_type": self.error_type,
            "error": message[:500],
            "provider_http_status": self.provider_http_status,
            "retry_after_seconds": self.retry_after_seconds,
        }


def _instant(value: object) -> datetime | None:
    if value in (None, ""):
        return None
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _failure_class(
    error_type: object, provider_http_status: object, status: object,
) -> str:
    kind = str(error_type or "")
    if kind == "RateLimited" or provider_http_status == 429:
        return "RATE_LIMITED"
    if kind == "AuthConfigurationError":
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
    cursor = connection.execute(
        """SELECT fetched_time,status,error_type,error,provider_http_status,
                  retry_after_seconds
           FROM source_polls WHERE source=?
           ORDER BY fetched_time DESC,poll_id DESC LIMIT ?""",
        (source, RECOVERY_HISTORY_LIMIT),
    )
    rows = cursor.fetchmany(RECOVERY_HISTORY_LIMIT)
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
        latest["error_type"], latest["provider_http_status"], latest["status"],
    )
    if recovery_mode == "OPERATOR_ACTION_REQUIRED":
        delay = AUTH_RETRY_DELAY
    elif recovery_mode == "RATE_LIMITED":
        retry_after_seconds = latest["retry_after_seconds"]
        delay = (
            timedelta(seconds=max(
                1,
                min(MAX_PROVIDER_RETRY_AFTER_SECONDS, int(retry_after_seconds)),
            ))
            if retry_after_seconds is not None
            else RATE_LIMIT_RETRY_DELAYS[
                min(failure_count - 1, len(RATE_LIMIT_RETRY_DELAYS) - 1)
            ]
        )
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
        "provider_http_status": latest["provider_http_status"],
        "retry_after_seconds": latest["retry_after_seconds"],
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
        """SELECT fetched_time FROM source_polls
           WHERE source=? AND status='OK'
           ORDER BY fetched_time DESC,poll_id DESC LIMIT 1""",
        (source,),
    ).fetchone()
    last_success = _instant(row["fetched_time"] if row else None)
    if last_success is not None and observed_at - last_success < success_interval:
        return {
            "source": source,
            "status": "SKIPPED_INTERVAL",
            "next_poll_after": (last_success + success_interval).isoformat(),
        }
    return None


def _retry_after_seconds(
    error: Exception, *, observed_at: datetime,
) -> int | None:
    if not isinstance(error, urllib.error.HTTPError):
        return None
    value = error.headers.get("Retry-After") if error.headers else None
    if not value:
        return None
    normalized = str(value).strip()
    if re.fullmatch(r"\d+", normalized):
        return max(1, min(MAX_PROVIDER_RETRY_AFTER_SECONDS, int(normalized)))
    try:
        retry_at = parsedate_to_datetime(normalized)
        if retry_at.tzinfo is None:
            retry_at = retry_at.replace(tzinfo=UTC)
        seconds = ceil((retry_at - observed_at).total_seconds())
        return max(1, min(MAX_PROVIDER_RETRY_AFTER_SECONDS, seconds))
    except (TypeError, ValueError, OverflowError):
        return None


def classified_poll_error(
    error: Exception,
    *,
    credentialed: bool,
    observed_at: datetime,
) -> PollFailureClassification:
    """Classify transport facts from an explicit source authentication contract."""
    code = getattr(error, "code", None)
    if code == 429:
        error_type = "RateLimited"
    elif credentialed and code in {401, 403}:
        error_type = "AuthConfigurationError"
    elif code in {401, 403}:
        error_type = "RemoteAccessRejected"
    else:
        error_type = type(error).__name__
    return PollFailureClassification(
        error_type=error_type,
        provider_http_status=code if isinstance(code, int) else None,
        retry_after_seconds=_retry_after_seconds(error, observed_at=observed_at),
    )
