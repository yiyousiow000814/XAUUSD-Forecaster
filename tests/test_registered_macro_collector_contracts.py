"""Family contracts shared by credentialed macro-data collectors."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from xauusd_forecaster.forward_ledger import ForwardLedger
from xauusd_forecaster.news import (
    collect_bea_macro,
    collect_eia_macro,
    collect_fred_macro,
)


UTC = timezone.utc


def _registered_collector_cases():
    return (
        pytest.param(
            "FRED_API_KEY",
            "a" * 32,
            collect_fred_macro,
            lambda url: json.dumps({
                "observations": [
                    {"date": "2026-08-04", "value": "11.0"},
                    {"date": "2026-08-03", "value": "10.0"},
                ]
            }).encode(),
            id="fred",
        ),
        pytest.param(
            "EIA_API_KEY",
            "b" * 40,
            collect_eia_macro,
            lambda url: json.dumps({"response": {"data": [
                {"period": "2026-08-04", "value": "65.25"},
                {"period": "2026-08-03", "value": "64.75"},
            ]}}).encode(),
            id="eia",
        ),
        pytest.param(
            "BEA_API_KEY",
            "00000000-0000-0000-0000-000000000000",
            collect_bea_macro,
            lambda url: json.dumps({"BEAAPI": {"Results": {"Data": (
                [
                    {"LineNumber": "1", "TimePeriod": "2026Q1", "DataValue": "1.0"},
                    {"LineNumber": "1", "TimePeriod": "2026Q2", "DataValue": "2.1"},
                ] if "TableName=T10101" in url else [
                    {"LineNumber": "1", "TimePeriod": "2026Q1", "DataValue": "131.1"},
                    {"LineNumber": "1", "TimePeriod": "2026Q2", "DataValue": "132.2"},
                    {"LineNumber": "2", "TimePeriod": "2026Q1", "DataValue": "129.1"},
                    {"LineNumber": "2", "TimePeriod": "2026Q2", "DataValue": "130.2"},
                ]
            )}}}).encode(),
            id="bea",
        ),
    )


@pytest.mark.parametrize(
    "environment_name,api_key,collector,payload_factory",
    _registered_collector_cases(),
)
def test_registered_macro_collectors_never_persist_or_report_credentials(
    tmp_path,
    monkeypatch,
    environment_name,
    api_key,
    collector,
    payload_factory,
) -> None:
    """Credentials may be sent upstream but never enter evidence or status."""
    monkeypatch.setenv(environment_name, api_key)
    fetched = datetime(2026, 8, 5, 10, 7, tzinfo=UTC)
    ledger = ForwardLedger(tmp_path / "forward.sqlite3", now=fetched)

    def fetcher(url: str) -> bytes:
        assert api_key in url
        return payload_factory(url)

    result = collector(ledger, fetched, fetcher)
    persisted = "\n".join(ledger.connection.iterdump())
    rendered_status = json.dumps(result, sort_keys=True)

    assert result["status"] == "OK"
    assert result["registered"] is True
    assert api_key not in persisted
    assert api_key not in rendered_status


@pytest.mark.parametrize(
    "environment_name,api_key,collector,payload_factory",
    _registered_collector_cases(),
)
def test_registered_macro_collector_errors_redact_credentials_as_one_family(
    tmp_path,
    monkeypatch,
    environment_name,
    api_key,
    collector,
    payload_factory,
) -> None:
    """Every credentialed collector shares the same failure-secrecy contract."""
    del payload_factory
    monkeypatch.setenv(environment_name, api_key)
    fetched = datetime(2026, 8, 5, 10, 7, tzinfo=UTC)
    ledger = ForwardLedger(tmp_path / "forward.sqlite3", now=fetched)

    def fail_with_url(url: str) -> bytes:
        raise ValueError(f"request failed: {url}")

    result = collector(ledger, fetched, fail_with_url)
    observable = "\n".join(ledger.connection.iterdump()) + json.dumps(result)

    assert result["status"] == "ERROR"
    assert api_key not in observable
    assert "[REDACTED]" in observable


@pytest.mark.parametrize(
    "environment_name,api_key,collector,payload_factory",
    _registered_collector_cases(),
)
def test_registered_macro_collectors_retry_failures_before_normal_cadence(
    tmp_path,
    monkeypatch,
    environment_name,
    api_key,
    collector,
    payload_factory,
) -> None:
    """A failed poll must reach a valid result through bounded recovery."""
    monkeypatch.setenv(environment_name, api_key)
    failed_at = datetime(2026, 8, 5, 10, 7, tzinfo=UTC)
    ledger = ForwardLedger(tmp_path / "forward.sqlite3", now=failed_at)

    def timeout(_url: str) -> bytes:
        raise TimeoutError("provider timed out")

    failed = collector(ledger, failed_at, timeout)
    waiting = collector(
        ledger, failed_at + timedelta(minutes=4),
        lambda _url: pytest.fail("backoff must not call the provider"),
    )
    recovered = collector(
        ledger, failed_at + timedelta(minutes=5), payload_factory,
    )

    assert failed["status"] == "ERROR"
    assert waiting["status"] == "SKIPPED_RETRY_BACKOFF"
    assert waiting["next_retry_at"] == (
        failed_at + timedelta(minutes=5)
    ).isoformat()
    assert recovered["status"] == "OK"
    assert ledger.latest_source_poll_time(
        recovered["source"]
    ) == failed_at + timedelta(minutes=5)
