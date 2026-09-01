from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from xauusd_forecaster.dashboard.health_projection import (
    COLLECTOR_HEARTBEAT_EXPECTED_SECONDS,
    COLLECTOR_HEARTBEAT_FAILURE_SECONDS,
    DECISION_HORIZON,
    DECISION_OUTPUT_CADENCE_SECONDS,
    DECISION_OUTPUT_GRACE_SECONDS,
    DECISION_OUTPUT_STALLED_SECONDS,
    SEMANTIC_SNAPSHOT_MAX_STALE_SECONDS,
    collector_component,
    decision_collector_component,
    materialized_semantic_health,
    semantic_pipeline_component,
)


UTC = timezone.utc


def _heartbeat(now: datetime, *, age: float = 0, state: str = "RUNNING") -> dict:
    return {
        "service": "collector",
        "state": state,
        "last_success": (now - timedelta(seconds=age)).isoformat(),
        "last_error": None,
    }


def _open_session(now: datetime, *, closes_in: timedelta = timedelta(hours=1)):
    return {
        "is_open": True,
        "next_close_time": (now + closes_in).isoformat(),
    }


def test_semantic_projection_separates_freshness_and_readiness() -> None:
    now = datetime(2026, 8, 17, 6, 0, tzinfo=UTC)
    base = {
        "observed_at": (now - timedelta(seconds=18)).isoformat(),
        "heartbeat_at": (now - timedelta(seconds=10)).isoformat(),
        "status": "UNHEALTHY",
    }

    pending = semantic_pipeline_component({
        **base,
        "reason_codes_json": json.dumps(["ACTIONABLE_NEWS_SEMANTICS_PENDING"]),
    }, now=now)
    assert (pending["status"], pending["age_seconds"]) == ("WARN", 18)

    for reason in ("ANNOTATOR_HEARTBEAT_STALE", "NEWS_COLLECTOR_POLL_MISSING"):
        assert semantic_pipeline_component({
            **base, "reason_codes": [reason],
        }, now=now)["status"] == "STALE"

    terminal = semantic_pipeline_component({
        **base,
        "reason_codes_json": json.dumps([
            "ACTIONABLE_NEWS_SEMANTICS_PENDING",
            "ACTIONABLE_NEWS_SEMANTICS_TERMINAL",
        ]),
    }, now=now)
    assert terminal["status"] == "ERROR"

    stale = semantic_pipeline_component({
        **base,
        "observed_at": (
            now - timedelta(seconds=SEMANTIC_SNAPSHOT_MAX_STALE_SECONDS + 1)
        ).isoformat(),
        "status": "HEALTHY",
        "reason_codes_json": "[]",
    }, now=now)
    assert stale["status"] == "STALE"


@pytest.mark.parametrize(
    "age,state,expected",
    [
        (10, "RUNNING", "OK"),
        (COLLECTOR_HEARTBEAT_EXPECTED_SECONDS, "RUNNING", "OK"),
        (61, "RUNNING", "WARN"),
        (COLLECTOR_HEARTBEAT_FAILURE_SECONDS, "RUNNING", "WARN"),
        (300.1, "RUNNING", "STALE"),
        (10, "STARTING", "WARN"),
        (300.1, "STARTING", "STALE"),
        (10, "ERROR", "STALE"),
    ],
)
def test_collector_projection_uses_bounded_heartbeat_grace(
    age: float, state: str, expected: str,
) -> None:
    now = datetime(2026, 8, 18, 8, 40, tzinfo=UTC)

    component = collector_component(
        _heartbeat(now, age=age, state=state),
        latest_poll=(now - timedelta(hours=1)).isoformat(),
        now=now,
    )

    assert component["status"] == expected
    assert component["source_poll_age_seconds"] == 3600


@pytest.mark.parametrize(
    "decision_age,session,quote_current,expected",
    [
        (420, _open_session(datetime(2026, 8, 18, 8, 40, tzinfo=UTC)), True, "CURRENT"),
        (421, _open_session(datetime(2026, 8, 18, 8, 40, tzinfo=UTC)), True, "STALLED"),
        (1200, _open_session(datetime(2026, 8, 18, 8, 40, tzinfo=UTC)), False, "NO_RECENT_DECISION"),
        (1200, {"is_open": False, "next_close_time": None}, True, "MARKET_CLOSED"),
        (1200, _open_session(datetime(2026, 8, 18, 8, 40, tzinfo=UTC), closes_in=DECISION_HORIZON), True, "EXPECTED_PAUSE"),
        (1200, None, True, "NO_RECENT_DECISION"),
    ],
)
def test_decision_projection_classifies_cadence_and_market_state(
    decision_age: float,
    session: dict | None,
    quote_current: bool,
    expected: str,
) -> None:
    now = datetime(2026, 8, 18, 8, 40, tzinfo=UTC)

    component = decision_collector_component(
        _heartbeat(now),
        latest_decision=(now - timedelta(seconds=decision_age)).isoformat(),
        decision_observation_start=now.isoformat(),
        broker_session=session,
        quote_current=quote_current,
        now=now,
    )

    assert component["status"] == "OK"
    assert component["decision_output_status"] == expected


@pytest.mark.parametrize("observation_age,expected", [(420, "NO_RECENT_DECISION"), (421, "STALLED")])
def test_decision_projection_uses_observation_start_without_a_prior_row(
    observation_age: float, expected: str,
) -> None:
    now = datetime(2026, 8, 18, 8, 40, tzinfo=UTC)
    component = decision_collector_component(
        _heartbeat(now),
        latest_decision=None,
        decision_observation_start=(now - timedelta(seconds=observation_age)).isoformat(),
        broker_session=_open_session(now),
        quote_current=True,
        now=now,
    )

    assert component["decision_age_seconds"] is None
    assert component["decision_output_age_seconds"] == observation_age
    assert component["decision_output_status"] == expected


def test_decision_projection_waits_through_first_post_reopen_grid() -> None:
    opened_at = datetime(2026, 8, 18, 8, 40, tzinfo=UTC)
    first_quote = opened_at + timedelta(minutes=1, seconds=1)
    eligible_grid = datetime(2026, 8, 18, 8, 45, tzinfo=UTC)
    stall_after = eligible_grid + timedelta(seconds=DECISION_OUTPUT_GRACE_SECONDS)
    session = {
        "is_open": True,
        "opened_at": opened_at.isoformat(),
        "first_quote_after_open_at": first_quote.isoformat(),
        "next_close_time": (opened_at + timedelta(hours=1)).isoformat(),
    }

    waiting = decision_collector_component(
        _heartbeat(stall_after),
        latest_decision=(opened_at - timedelta(minutes=20)).isoformat(),
        decision_observation_start=opened_at.isoformat(),
        broker_session=session,
        quote_current=True,
        now=stall_after,
    )
    stalled = decision_collector_component(
        _heartbeat(stall_after + timedelta(seconds=1)),
        latest_decision=(opened_at - timedelta(minutes=20)).isoformat(),
        decision_observation_start=opened_at.isoformat(),
        broker_session=session,
        quote_current=True,
        now=stall_after + timedelta(seconds=1),
    )

    assert waiting["decision_output_eligible_grid"] == eligible_grid.isoformat()
    assert waiting["decision_output_status"] == "NO_RECENT_DECISION"
    assert stalled["decision_output_status"] == "STALLED"


def test_materialized_semantic_health_decodes_current_row() -> None:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.execute(
        """CREATE TABLE news_semantic_health_snapshots_v1 (
               source_decision_id TEXT PRIMARY KEY,
               observed_at TEXT, status TEXT, reason_codes_json TEXT,
               heartbeat_at TEXT, unresolved_items INTEGER,
               oldest_unresolved_at TEXT, snapshot_hash TEXT
           )"""
    )
    connection.execute(
        "INSERT INTO news_semantic_health_snapshots_v1 VALUES (?,?,?,?,?,?,?,?)",
        (
            "decision-1", "2026-08-18T08:40:00+00:00", "UNHEALTHY",
            '["ACTIONABLE_NEWS_SEMANTICS_PENDING"]',
            "2026-08-18T08:39:50+00:00", 1,
            "2026-08-18T08:30:00+00:00", "snapshot-hash",
        ),
    )

    result = materialized_semantic_health(connection, "decision-1")
    connection.close()

    assert result["reason_codes"] == ["ACTIONABLE_NEWS_SEMANTICS_PENDING"]
    assert result["actionable_failure_counts"] == {}
    assert result["snapshot_hash"] == "snapshot-hash"


def test_projection_threshold_contract_values() -> None:
    assert SEMANTIC_SNAPSHOT_MAX_STALE_SECONDS == 420.0
    assert COLLECTOR_HEARTBEAT_EXPECTED_SECONDS == 60.0
    assert COLLECTOR_HEARTBEAT_FAILURE_SECONDS == 300.0
    assert DECISION_OUTPUT_CADENCE_SECONDS == 300.0
    assert DECISION_OUTPUT_STALLED_SECONDS == 420.0
    assert DECISION_OUTPUT_GRACE_SECONDS == 120.0
    assert DECISION_HORIZON == timedelta(minutes=30)
