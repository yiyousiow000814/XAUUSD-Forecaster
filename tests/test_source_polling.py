import sqlite3
import urllib.error
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime

import pytest

from xauusd_forecaster.forward_ledger import ForwardLedger
from xauusd_forecaster.news import (
    BEA_API_SOURCE,
    EIA_API_SOURCE,
    FRED_POLL_SOURCE,
    GDELT_SOURCE,
)
from xauusd_forecaster.source_polling import (
    MAX_PROVIDER_RETRY_AFTER_SECONDS,
    classified_poll_error,
    source_poll_gate,
    source_poll_recovery_state,
)


NOW = datetime(2026, 8, 18, 2, 35, tzinfo=UTC)
BOUNDED_COLLECTOR_SOURCES = (
    FRED_POLL_SOURCE,
    EIA_API_SOURCE,
    BEA_API_SOURCE,
    GDELT_SOURCE,
)


def _poll(
    ledger: ForwardLedger,
    source: str,
    at: datetime,
    status: str,
    *,
    error_type: str | None = None,
    error: str | None = None,
    provider_http_status: int | None = None,
    retry_after_seconds: int | None = None,
) -> None:
    ledger.append_source_poll({
        "poll_id": f"{source}-{at.isoformat()}-{status}",
        "source": source,
        "fetched_time": at,
        "status": status,
        "error_type": error_type,
        "error": error,
        "provider_http_status": provider_http_status,
        "retry_after_seconds": retry_after_seconds,
    })


@pytest.mark.parametrize("source", BOUNDED_COLLECTOR_SOURCES)
def test_transient_failure_uses_retry_cadence_not_normal_success_interval(
    tmp_path, source,
) -> None:
    ledger = ForwardLedger(tmp_path / "forward.sqlite3", now=NOW)
    _poll(ledger, source, NOW, "ERROR", error_type="TimeoutError", error="timeout")

    waiting = source_poll_gate(
        ledger.connection, source, observed_at=NOW + timedelta(minutes=4),
        success_interval=timedelta(hours=1),
    )
    due = source_poll_gate(
        ledger.connection, source, observed_at=NOW + timedelta(minutes=5),
        success_interval=timedelta(hours=1),
    )

    assert waiting == {
        "source": source,
        "status": "SKIPPED_RETRY_BACKOFF",
        "recovery_mode": "AUTO_RECOVERING",
        "failure_count": 1,
        "next_retry_at": (NOW + timedelta(minutes=5)).isoformat(),
    }
    assert due is None


@pytest.mark.parametrize("source", BOUNDED_COLLECTOR_SOURCES)
def test_successful_retry_returns_source_to_normal_cadence(tmp_path, source) -> None:
    ledger = ForwardLedger(tmp_path / "forward.sqlite3", now=NOW)
    _poll(ledger, source, NOW, "ERROR", error_type="TimeoutError", error="timeout")
    recovered_at = NOW + timedelta(minutes=5)
    _poll(ledger, source, recovered_at, "OK")

    gate = source_poll_gate(
        ledger.connection, source, observed_at=recovered_at + timedelta(minutes=1),
        success_interval=timedelta(hours=1),
    )

    assert gate["status"] == "SKIPPED_INTERVAL"
    assert gate["next_poll_after"] == (recovered_at + timedelta(hours=1)).isoformat()
    assert ledger.latest_successful_source_poll_time(source) == recovered_at


def test_repeated_transient_failures_back_off_five_fifteen_then_thirty_minutes(
    tmp_path,
) -> None:
    ledger = ForwardLedger(tmp_path / "forward.sqlite3", now=NOW)
    _poll(ledger, EIA_API_SOURCE, NOW, "ERROR", error_type="TimeoutError")
    _poll(
        ledger, EIA_API_SOURCE, NOW + timedelta(minutes=5), "ERROR",
        error_type="ConnectionError",
    )
    third = NOW + timedelta(minutes=20)
    _poll(ledger, EIA_API_SOURCE, third, "ERROR", error_type="HTTPError")

    waiting = source_poll_gate(
        ledger.connection, EIA_API_SOURCE,
        observed_at=third + timedelta(minutes=29),
        success_interval=timedelta(hours=1),
    )
    due = source_poll_gate(
        ledger.connection, EIA_API_SOURCE,
        observed_at=third + timedelta(minutes=30),
        success_interval=timedelta(hours=1),
    )

    assert waiting["failure_count"] == 3
    assert waiting["next_retry_at"] == (third + timedelta(minutes=30)).isoformat()
    assert due is None


def test_partial_bundle_retries_without_claiming_a_complete_success(tmp_path) -> None:
    ledger = ForwardLedger(tmp_path / "forward.sqlite3", now=NOW)
    _poll(
        ledger, FRED_POLL_SOURCE, NOW, "PARTIAL",
        error_type="SeriesErrors", error="one series failed",
    )

    gate = source_poll_gate(
        ledger.connection, FRED_POLL_SOURCE,
        observed_at=NOW + timedelta(minutes=1),
        success_interval=timedelta(hours=1),
    )

    assert gate["recovery_mode"] == "PARTIAL_RECOVERY"
    assert gate["next_retry_at"] == (NOW + timedelta(minutes=5)).isoformat()
    assert ledger.latest_successful_source_poll_time(FRED_POLL_SOURCE) is None


def test_authentication_failure_requires_operator_action_without_rapid_retry(
    tmp_path,
) -> None:
    ledger = ForwardLedger(tmp_path / "forward.sqlite3", now=NOW)
    _poll(
        ledger, EIA_API_SOURCE, NOW, "ERROR",
        error_type="AuthConfigurationError", error="HTTP Error 403: Forbidden",
    )

    gate = source_poll_gate(
        ledger.connection, EIA_API_SOURCE,
        observed_at=NOW + timedelta(hours=1),
        success_interval=timedelta(hours=1),
    )

    assert gate["recovery_mode"] == "OPERATOR_ACTION_REQUIRED"
    assert gate["next_retry_at"] == (NOW + timedelta(hours=6)).isoformat()


def test_recovery_does_not_infer_authentication_from_error_copy(tmp_path) -> None:
    ledger = ForwardLedger(tmp_path / "forward.sqlite3", now=NOW)
    _poll(
        ledger, GDELT_SOURCE, NOW, "ERROR",
        error_type="HTTPError", error="HTTP Error 403: Forbidden",
    )

    state = source_poll_recovery_state(
        ledger.connection, GDELT_SOURCE, observed_at=NOW,
    )

    assert state["recovery_mode"] == "AUTO_RECOVERING"
    assert state["next_retry_at"] == NOW + timedelta(minutes=5)


def _http_error(code: int, retry_after: str | None = None) -> urllib.error.HTTPError:
    headers = {"Retry-After": retry_after} if retry_after is not None else {}
    return urllib.error.HTTPError(
        "https://provider.invalid", code, "provider failure", headers, None,
    )


@pytest.mark.parametrize(
    ("credentialed", "expected_type"),
    ((True, "AuthConfigurationError"), (False, "RemoteAccessRejected")),
)
def test_403_classification_uses_explicit_transport_auth_contract(
    credentialed, expected_type,
) -> None:
    failure = classified_poll_error(
        _http_error(403), credentialed=credentialed, observed_at=NOW,
    )

    assert failure.error_type == expected_type
    assert failure.provider_http_status == 403
    assert failure.retry_after_seconds is None


@pytest.mark.parametrize(
    ("retry_after", "expected_seconds"),
    (
        ("300", 300),
        ("7200", 7200),
        (str(MAX_PROVIDER_RETRY_AFTER_SECONDS * 2),
         MAX_PROVIDER_RETRY_AFTER_SECONDS),
    ),
)
def test_rate_limit_retry_after_seconds_are_honored_and_bounded(
    tmp_path, retry_after, expected_seconds,
) -> None:
    ledger = ForwardLedger(tmp_path / "forward.sqlite3", now=NOW)
    failure = classified_poll_error(
        _http_error(429, retry_after), credentialed=False, observed_at=NOW,
    )
    _poll(
        ledger, GDELT_SOURCE, NOW, "ERROR",
        error_type=failure.error_type,
        provider_http_status=failure.provider_http_status,
        retry_after_seconds=failure.retry_after_seconds,
    )

    waiting = source_poll_gate(
        ledger.connection, GDELT_SOURCE,
        observed_at=NOW + timedelta(seconds=expected_seconds - 1),
        success_interval=timedelta(hours=1),
    )
    due = source_poll_gate(
        ledger.connection, GDELT_SOURCE,
        observed_at=NOW + timedelta(seconds=expected_seconds),
        success_interval=timedelta(hours=1),
    )

    assert waiting["status"] == "SKIPPED_RETRY_BACKOFF"
    assert waiting["next_retry_at"] == (
        NOW + timedelta(seconds=expected_seconds)
    ).isoformat()
    assert due is None


def test_http_date_retry_after_is_converted_at_poll_time() -> None:
    retry_at = NOW + timedelta(minutes=17)
    failure = classified_poll_error(
        _http_error(429, format_datetime(retry_at, usegmt=True)),
        credentialed=True,
        observed_at=NOW,
    )

    assert failure.retry_after_seconds == 17 * 60


def test_malformed_retry_after_uses_rate_limit_fallback(tmp_path) -> None:
    ledger = ForwardLedger(tmp_path / "forward.sqlite3", now=NOW)
    failure = classified_poll_error(
        _http_error(429, "not-a-date"), credentialed=False, observed_at=NOW,
    )
    _poll(
        ledger, GDELT_SOURCE, NOW, "ERROR",
        error_type=failure.error_type,
        provider_http_status=failure.provider_http_status,
        retry_after_seconds=failure.retry_after_seconds,
    )

    state = source_poll_recovery_state(
        ledger.connection, GDELT_SOURCE, observed_at=NOW,
    )

    assert failure.retry_after_seconds is None
    assert state["next_retry_at"] == NOW + timedelta(minutes=60)


def test_retry_after_survives_database_restart(tmp_path) -> None:
    database = tmp_path / "forward.sqlite3"
    ledger = ForwardLedger(database, now=NOW)
    _poll(
        ledger, EIA_API_SOURCE, NOW, "ERROR",
        error_type="RateLimited", provider_http_status=429,
        retry_after_seconds=300,
    )
    expected = source_poll_recovery_state(
        ledger.connection, EIA_API_SOURCE, observed_at=NOW,
    )["next_retry_at"]
    ledger.close()

    reopened = ForwardLedger(database, now=NOW)
    restored = source_poll_recovery_state(
        reopened.connection, EIA_API_SOURCE, observed_at=NOW,
    )

    assert restored["next_retry_at"] == expected == NOW + timedelta(minutes=5)
    assert restored["retry_after_seconds"] == 300


@pytest.mark.parametrize(
    ("persisted_seconds", "bounded_seconds"),
    (
        (0, 1),
        (MAX_PROVIDER_RETRY_AFTER_SECONDS + 1, MAX_PROVIDER_RETRY_AFTER_SECONDS),
    ),
)
def test_persisted_retry_after_is_defensively_bounded(
    tmp_path, persisted_seconds, bounded_seconds,
) -> None:
    ledger = ForwardLedger(tmp_path / "forward.sqlite3", now=NOW)
    _poll(
        ledger, EIA_API_SOURCE, NOW, "ERROR",
        error_type="RateLimited", provider_http_status=429,
        retry_after_seconds=persisted_seconds,
    )

    state = source_poll_recovery_state(
        ledger.connection, EIA_API_SOURCE, observed_at=NOW,
    )

    assert state["next_retry_at"] == NOW + timedelta(seconds=bounded_seconds)


def test_recovery_lookup_is_bounded_after_large_append_only_history(tmp_path) -> None:
    ledger = ForwardLedger(tmp_path / "forward.sqlite3", now=NOW)
    historical = []
    for offset in range(2_000):
        at = NOW - timedelta(days=2, seconds=offset)
        historical.append((
            f"history-{offset}", EIA_API_SOURCE, at.isoformat(), "OK",
            None, None, None, None, None,
        ))
    ledger.connection.executemany(
        """INSERT INTO source_polls
           (poll_id,source,fetched_time,status,payload_hash,error_type,error,
            provider_http_status,retry_after_seconds)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        historical,
    )
    for offset in range(3):
        _poll(
            ledger, EIA_API_SOURCE, NOW + timedelta(minutes=offset), "ERROR",
            error_type="TimeoutError",
        )
    statements = []
    ledger.connection.set_trace_callback(statements.append)

    state = source_poll_recovery_state(
        ledger.connection, EIA_API_SOURCE,
        observed_at=NOW + timedelta(minutes=2),
    )
    ledger.connection.set_trace_callback(None)
    plan = ledger.connection.execute(
        """EXPLAIN QUERY PLAN SELECT fetched_time,status,error_type,error,
                  provider_http_status,retry_after_seconds
           FROM source_polls WHERE source=?
           ORDER BY fetched_time DESC,poll_id DESC LIMIT 4""",
        (EIA_API_SOURCE,),
    ).fetchall()

    assert state["failure_count"] == 3
    assert any("LIMIT 4" in statement for statement in statements)
    assert any("idx_source_polls_source_time_id" in str(row["detail"]) for row in plan)


def test_latest_successful_poll_is_distinct_from_latest_attempt(tmp_path) -> None:
    ledger = ForwardLedger(tmp_path / "forward.sqlite3", now=NOW)
    completed_at = NOW - timedelta(minutes=10)
    _poll(ledger, EIA_API_SOURCE, completed_at, "OK")
    _poll(
        ledger, EIA_API_SOURCE, NOW, "ERROR", error_type="TimeoutError",
    )

    assert ledger.latest_successful_source_poll_time(
        EIA_API_SOURCE
    ) == completed_at


def test_existing_source_poll_ledger_adds_safe_transport_columns(tmp_path) -> None:
    database = tmp_path / "forward.sqlite3"
    connection = sqlite3.connect(database)
    connection.execute(
        """CREATE TABLE source_polls (
             poll_id TEXT PRIMARY KEY,source TEXT NOT NULL,fetched_time TEXT NOT NULL,
             status TEXT NOT NULL,payload_hash TEXT,error_type TEXT,error TEXT)"""
    )
    connection.commit()
    connection.close()

    ledger = ForwardLedger(database, now=NOW)
    columns = {
        row["name"] for row in ledger.connection.execute(
            "PRAGMA table_info(source_polls)"
        ).fetchall()
    }
    _poll(
        ledger, EIA_API_SOURCE, NOW, "ERROR", error_type="RateLimited",
        provider_http_status=429, retry_after_seconds=300,
    )

    assert {"provider_http_status", "retry_after_seconds"}.issubset(columns)
