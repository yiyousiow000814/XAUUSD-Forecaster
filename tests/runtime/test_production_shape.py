from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from xauusd_forecaster.evidence.ledger import ForwardLedger
from xauusd_forecaster.ai.provider_registry import AI_QUOTA_SURFACES
from xauusd_forecaster.decision.inference import MODEL_IDENTITIES
from xauusd_forecaster.news.scheduler.state import reserve_account_request
from xauusd_forecaster.news.collection.source_registry import NEWS_SOURCE_REGISTRY
from xauusd_forecaster.news.collection.intake import RUNTIME_NEWS_POLL_SOURCES
from xauusd_forecaster.runtime.production_shape import (
    production_contract_snapshot,
    production_shape_violations,
)


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


def _seed_active_generation(
    ledger: ForwardLedger,
    *,
    omitted_identity: str | None = None,
) -> str:
    generation_id = "generation-live"
    ledger.connection.execute(
        "INSERT INTO news_model_generations_v1 VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (generation_id, "SHADOW", NOW.isoformat(), NOW.isoformat(),
         "policy", "features", "eligibility", "events", "market", "official",
         "broad", "weights", 5, "READY"),
    )
    for identity in sorted(MODEL_IDENTITIES):
        version = f"model-{identity.lower()}"
        ledger.connection.execute(
            "INSERT INTO model_updates_v2 VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (version, identity, "SHADOW", NOW.isoformat(), NOW.isoformat(),
             0, 0, 0, 0, 0, 0, f"hash-{identity}", "features", "eligibility",
             "artifact", f"artifact-{identity}", "CHALLENGER"),
        )
        if identity == omitted_identity:
            continue
        table = (
            "news_model_generation_aux_members_v1"
            if identity == "NEWS_ONLY" else "news_model_generation_members_v1"
        )
        ledger.connection.execute(
            f"INSERT INTO {table} VALUES (?,?,?)",
            (generation_id, identity, version),
        )
    ledger.connection.execute(
        "INSERT INTO news_model_generation_activations_v1 VALUES (?,?,?,?,?)",
        ("activation-live", generation_id, None,
         (NOW + timedelta(seconds=1)).isoformat(), "TEST"),
    )
    ledger.connection.commit()
    return generation_id


def _append_complete_decision(
    ledger: ForwardLedger,
    *,
    omitted_identity: str | None = None,
    mismatched_identity: str | None = None,
) -> str:
    decision_time = NOW + timedelta(minutes=5)
    ledger.append_snapshot({
        "snapshot_id": "snapshot-live", "decision_time": decision_time,
        "collected_at": decision_time, "data_role": "FORWARD", "source": "TEST",
        "source_event_time": decision_time, "source_received_time": decision_time,
        "bid": 4300.0, "ask": 4300.1, "spread": 0.1, "features": {},
        "feature_version": "features", "u5": 1.0, "u5_status": "READY",
        "data_health": "HEALTHY", "active_signal": False, "reason_codes": [],
    })
    ledger.append_decision({
        "decision_id": "decision-live", "decision_time": decision_time,
        "snapshot_id": "snapshot-live", "data_health": "HEALTHY",
        "reason_codes": [], "predictions": [], "created_at": decision_time,
    })
    if mismatched_identity:
        ledger.connection.execute(
            "INSERT INTO model_updates_v2 VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("old-model-version", mismatched_identity, "SHADOW", NOW.isoformat(),
             NOW.isoformat(), 0, 0, 0, 0, 0, 0, "old-hash", "features",
             "eligibility", "artifact", "old-artifact", "CHALLENGER"),
        )
    for identity in sorted(MODEL_IDENTITIES):
        if identity == omitted_identity:
            continue
        ledger.connection.execute(
            """INSERT INTO predictions_v2 VALUES
               (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            ("decision-live", (
                "old-model-version" if identity == mismatched_identity
                else f"model-{identity.lower()}"
            ), identity,
             decision_time.isoformat(), decision_time.isoformat(), "LIVE_OOS",
             "snapshot-hash", 0.0, 0.0, 0.0, 0.0, None, None,
             "UTC_DAY_BLOCK_OOS_ABS_RESIDUAL_Q95", "calibration", 0, 0, 0,
             None, "UNCALIBRATED", "WAIT", "WAIT", "PROVISIONAL"),
        )
    ledger.connection.commit()
    return "decision-live"


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
    _seed_active_generation(ledger)
    _append_complete_decision(ledger)
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


def test_missing_active_generation_fails_closed(tmp_path) -> None:
    ledger = ForwardLedger(tmp_path / "forward.sqlite3", now=NOW)
    _seed_scheduler_usage(ledger)
    for source in (spec.source for spec in NEWS_SOURCE_REGISTRY):
        ledger.append_source_poll({
            "poll_id": f"{source}-ok", "source": source,
            "fetched_time": NOW, "status": "OK",
        })

    assert _violations(ledger, _status()) == [
        "production has no active model generation",
    ]


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


def test_active_generation_requires_a_subsequent_live_decision(tmp_path) -> None:
    ledger = ForwardLedger(tmp_path / "forward.sqlite3", now=NOW)
    _seed_active_generation(ledger)
    _seed_scheduler_usage(ledger)

    assert _violations(ledger, _status()) == [
        "active generation has no subsequent live decision",
    ]


def test_runtime_observation_may_wait_for_first_generation_decision(tmp_path) -> None:
    ledger = ForwardLedger(tmp_path / "forward.sqlite3", now=NOW)
    _seed_active_generation(ledger)
    _seed_scheduler_usage(ledger)
    status = _status()
    status["production_contract"] = production_contract_snapshot(
        ledger.connection, now=VALIDATION_TIME,
    )
    status["dashboard_sync"] = {"status": "OK", "degraded_resources": []}

    assert production_shape_violations(
        status, allow_pending_generation_decision=True,
    ) == []


@pytest.mark.parametrize(
    "boundary,expected",
    [
        pytest.param(
            "generation-member",
            "active generation is incomplete: FULL", id="generation-member",
        ),
        pytest.param(
            "live-prediction",
            "latest decision is missing models: FULL", id="live-prediction",
        ),
        pytest.param(
            "model-version",
            "latest decision does not use active generation versions: FULL",
            id="model-version",
        ),
    ],
)
def test_complete_model_family_is_required_at_generation_and_prediction_boundaries(
    tmp_path,
    boundary,
    expected,
) -> None:
    ledger = ForwardLedger(tmp_path / f"{boundary}.sqlite3", now=NOW)
    _seed_active_generation(
        ledger,
        omitted_identity="FULL" if boundary == "generation-member" else None,
    )
    _append_complete_decision(
        ledger,
        omitted_identity="FULL" if boundary == "live-prediction" else None,
        mismatched_identity="FULL" if boundary == "model-version" else None,
    )
    _seed_scheduler_usage(ledger)

    assert _violations(ledger, _status()) == [expected]


def test_candidate_preflight_treats_a_partially_appended_decision_as_pending(
    tmp_path,
) -> None:
    ledger = ForwardLedger(tmp_path / "pending-live-decision.sqlite3", now=NOW)
    _seed_active_generation(ledger)
    _append_complete_decision(ledger, omitted_identity="FULL")
    _seed_scheduler_usage(ledger)
    status = _status()
    status["production_contract"] = production_contract_snapshot(
        ledger.connection, now=VALIDATION_TIME,
    )
    status["dashboard_sync"] = {"status": "OK", "degraded_resources": []}

    assert production_shape_violations(status) == [
        "latest decision is missing models: FULL",
    ]
    assert production_shape_violations(
        status, allow_pending_generation_decision=True,
    ) == []
    status["production_contract"]["active_generation"][
        "latest_decision_models"
    ]["MARKET_ONLY"] = "wrong-version"
    assert production_shape_violations(
        status, allow_pending_generation_decision=True,
    ) == [
        "latest decision does not use active generation versions: MARKET_ONLY",
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


def test_broker_confirmed_close_forbids_later_decisions(complete_shape) -> None:
    ledger, status = complete_shape
    status["system"].update({
        "market_session": "CLOSED",
        "market_session_observed_at": NOW.isoformat(),
    })

    assert _violations(ledger, status) == [
        "decision was appended after broker-confirmed market close",
    ]


def test_invalid_market_close_clock_fails_closed(complete_shape) -> None:
    ledger, status = complete_shape
    status["system"].update({
        "market_session": "CLOSED",
        "market_session_observed_at": "not-a-time",
    })

    assert _violations(ledger, status) == [
        "broker market-close observation time is invalid",
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
