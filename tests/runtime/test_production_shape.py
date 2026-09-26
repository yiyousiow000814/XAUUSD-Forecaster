from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from xauusd_forecaster.evidence.ledger import ForwardLedger
from xauusd_forecaster.ai.provider_registry import AI_QUOTA_SURFACES
from xauusd_forecaster.news.scheduler.state import reserve_account_request
from xauusd_forecaster.news.collection.source_registry import NEWS_SOURCE_REGISTRY
from xauusd_forecaster.news.collection.intake import RUNTIME_NEWS_POLL_SOURCES
from xauusd_forecaster.runtime.production_shape import production_contract_snapshot
from xauusd_forecaster.runtime.production_shape import production_shape_violations


NOW = datetime(2026, 8, 13, 3, 0, tzinfo=UTC)
VALIDATION_TIME = NOW + timedelta(minutes=10)


def _status() -> dict:
    return {
        "system": {
            "market_session": "OPEN",
            "market_session_observed_at": NOW.isoformat(),
        },
        "gemini_quota": {
            "accounting_source": "SCHEDULER_DB", "total_sent": 1,
        },
        "gemini_31_quota": {
            "accounting_source": "SCHEDULER_DB", "total_sent": 0,
        },
        "gemma_quota": {
            "accounting_source": "SCHEDULER_DB", "total_sent": 2,
        },
        "gemini_embedding_quota": {
            "accounting_source": "SCHEDULER_DB", "total_sent": 0,
        },
        "news_source_health": [{
            "source": source, "health": "HEALTHY", "latest_status": "OK",
            "recovery_mode": None, "next_retry_time": None,
        } for source in (spec.source for spec in NEWS_SOURCE_REGISTRY)],
    }






def _seed_scheduler_usage(ledger: ForwardLedger) -> None:
    reserve_account_request(
        ledger.connection, account_id="account",
        model_family="gemini-3.5-flash-lite", daily_limit=500,
        requests_per_minute=12, now=NOW,
    )
    for family in ("gemma-impact", "gemma-title"):
        reserve_account_request(
            ledger.connection, account_id="account", model_family=family,
            daily_limit=15_000, requests_per_minute=12, now=NOW,
        )


@pytest.fixture
def complete_shape(tmp_path) -> tuple[ForwardLedger, dict]:
    ledger = ForwardLedger(tmp_path / "forward.sqlite3", now=NOW)
    _seed_scheduler_usage(ledger)
    for source in (spec.source for spec in NEWS_SOURCE_REGISTRY):
        ledger.append_source_poll({
            "poll_id": f"{source}-ok", "source": source,
            "fetched_time": NOW, "status": "OK",
        })
    return ledger, _status()


def _violations(
    ledger: ForwardLedger,
    status: dict,
    *,
    sync_status: dict | None = None,
) -> list[str]:
    status["production_contract"] = production_contract_snapshot(
        ledger.connection, now=VALIDATION_TIME,
    )
    status["dashboard_sync"] = (
        {"status": "OK", "degraded_resources": []}
        if sync_status is None else sync_status
    )
    return production_shape_violations(status)


def test_production_shape_accepts_complete_live_shape(complete_shape) -> None:
    ledger, status = complete_shape
    assert _violations(ledger, status) == []


def test_news_health_registry_is_the_runtime_collector_family() -> None:
    assert {spec.source for spec in NEWS_SOURCE_REGISTRY} == set(
        RUNTIME_NEWS_POLL_SOURCES
    )




def test_missing_scheduler_quota_ledger_fails_closed(complete_shape) -> None:
    ledger, status = complete_shape
    contract = production_contract_snapshot(
        ledger.connection, now=VALIDATION_TIME,
    )
    contract["scheduler_usage_available"] = False
    contract["scheduler_usage"] = {}
    status["production_contract"] = contract
    status["dashboard_sync"] = {"status": "OK", "degraded_resources": []}

    violations = production_shape_violations(status)

    assert violations[0] == "scheduler quota ledger is unavailable"
    assert {
        violation.rsplit(": ", 1)[-1]
        for violation in violations[1:]
    } == {surface.payload_key for surface in AI_QUOTA_SURFACES}


def test_missing_source_health_member_fails_closed(complete_shape) -> None:
    ledger, status = complete_shape
    missing = NEWS_SOURCE_REGISTRY[-1].source
    status["news_source_health"] = [
        row for row in status["news_source_health"]
        if row["source"] != missing
    ]

    assert _violations(ledger, status) == [
        f"source health family mismatch: missing=['{missing}'], unexpected=[]",
    ]










@pytest.mark.parametrize(
    "payload_key",
    [surface.payload_key for surface in AI_QUOTA_SURFACES],
)
def test_every_ai_quota_surface_must_match_scheduler_accounting(
    complete_shape,
    payload_key,
) -> None:
    ledger, status = complete_shape
    status[payload_key]["total_sent"] += 1

    violations = _violations(ledger, status)

    assert len(violations) == 1
    assert violations[0].startswith(
        f"{payload_key} does not match scheduler usage:"
    )


def test_scheduler_snapshot_counts_only_currently_configured_accounts(
    complete_shape,
) -> None:
    ledger, _ = complete_shape
    reserve_account_request(
        ledger.connection, account_id="retired-account",
        model_family="gemini-3.5-flash-lite", daily_limit=500,
        requests_per_minute=12, now=NOW,
    )

    current = production_contract_snapshot(
        ledger.connection, now=VALIDATION_TIME,
        account_ids=frozenset({"account"}),
    )
    all_accounts = production_contract_snapshot(
        ledger.connection, now=VALIDATION_TIME,
    )

    assert current["scheduler_usage"]["gemini_quota"] == 1
    assert all_accounts["scheduler_usage"]["gemini_quota"] == 2


@pytest.mark.parametrize("source", [spec.source for spec in NEWS_SOURCE_REGISTRY])
def test_every_successful_news_source_clears_old_degraded_recovery_state(
    complete_shape,
    source,
) -> None:
    ledger, status = complete_shape
    row = next(
        item for item in status["news_source_health"]
        if item["source"] == source
    )
    row.update({
        "health": "DEGRADED", "latest_status": "RATE_LIMITED",
        "recovery_mode": "FALLBACK_ACTIVE", "next_retry_time": NOW.isoformat(),
    })

    assert _violations(ledger, status) == [
        f"successful source poll is still reported as degraded: {source}",
    ]






@pytest.mark.parametrize(
    "resource,error_code",
    [
        ("market_history", "PAYLOAD_LIMIT_EXCEEDED"),
        ("learning", "PAYLOAD_CONTRACT_REJECTED"),
        ("news", "PAYLOAD_LIMIT_EXCEEDED"),
    ],
)
def test_every_sync_resource_rejects_structured_payload_limit_failure(
    complete_shape,
    resource,
    error_code,
) -> None:
    ledger, status = complete_shape
    sync_status = {"status": "DEGRADED", "degraded_resources": [{
        "resource": resource,
        "error_code": error_code,
        "error": "human-readable text is not part of the contract",
    }]}

    assert _violations(ledger, status, sync_status=sync_status) == [
        f"{resource} sync still exceeds the remote payload limit",
    ]


@pytest.mark.parametrize(
    "sync_status,expected",
    [
        pytest.param(
            {}, "dashboard synchronizer status is unavailable",
            id="missing-status",
        ),
        pytest.param(
            {"status": "ERROR", "last_error_code": "PAYLOAD_LIMIT_EXCEEDED"},
            "dashboard heartbeat exceeds the remote payload limit",
            id="heartbeat-payload-limit",
        ),
    ],
)
def test_dashboard_sync_contract_fails_closed(
    complete_shape,
    sync_status,
    expected,
) -> None:
    ledger, status = complete_shape

    assert _violations(ledger, status, sync_status=sync_status) == [expected]


def test_validator_does_not_reopen_database_after_snapshot(complete_shape) -> None:
    ledger, status = complete_shape
    status["production_contract"] = production_contract_snapshot(
        ledger.connection, now=VALIDATION_TIME,
    )
    status["dashboard_sync"] = {"status": "OK", "degraded_resources": []}
    ledger.close()

    assert production_shape_violations(status) == []
