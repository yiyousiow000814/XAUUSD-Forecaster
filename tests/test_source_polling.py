from datetime import UTC, datetime, timedelta

import pytest

from xauusd_forecaster.forward_ledger import ForwardLedger
from xauusd_forecaster.news import (
    BEA_API_SOURCE,
    EIA_API_SOURCE,
    FRED_POLL_SOURCE,
    GDELT_SOURCE,
)
from xauusd_forecaster.source_polling import source_poll_gate


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
) -> None:
    ledger.append_source_poll({
        "poll_id": f"{source}-{at.isoformat()}-{status}",
        "source": source,
        "fetched_time": at,
        "status": status,
        "error_type": error_type,
        "error": error,
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
    assert ledger.latest_source_poll_time(source) == recovered_at


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
    assert ledger.latest_source_poll_time(FRED_POLL_SOURCE) is None


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
