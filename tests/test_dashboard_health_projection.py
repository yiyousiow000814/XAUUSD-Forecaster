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
    _collector_component,
    _decision_collector_component,
    _materialized_semantic_health,
    _semantic_pipeline_component,
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


def test_semantic_component_separates_freshness_from_readiness() -> None:
    now = datetime(2026, 8, 17, 6, 0, tzinfo=UTC)
    fresh_pending = {
        "observed_at": (now - timedelta(seconds=18)).isoformat(),
        "heartbeat_at": (now - timedelta(seconds=10)).isoformat(),
        "status": "UNHEALTHY",
        "reason_codes_json": json.dumps(["ACTIONABLE_NEWS_SEMANTICS_PENDING"]),
    }

    component = _semantic_pipeline_component(fresh_pending, now=now)

    assert component["status"] == "WARN"
    assert component["age_seconds"] == 18
    assert component["last_error"] == "ACTIONABLE_NEWS_SEMANTICS_PENDING"

    stale_snapshot = {
        **fresh_pending,
        "observed_at": (
            now - timedelta(seconds=SEMANTIC_SNAPSHOT_MAX_STALE_SECONDS + 1)
        ).isoformat(),
        "status": "HEALTHY",
        "reason_codes_json": "[]",
    }
    assert _semantic_pipeline_component(stale_snapshot, now=now)["status"] == "STALE"


def test_semantic_component_preserves_missing_and_reason_states() -> None:
    now = datetime(2026, 8, 17, 6, 0, tzinfo=UTC)
    assert _semantic_pipeline_component(None, now=now) == {
        "last_success": None,
        "age_seconds": None,
        "status": "STALE",
        "last_error": "尚无决策时点的新闻语义健康记录",
        "reason_codes": [],
        "actionable_failure_counts": {},
    }

    base = {
        "observed_at": now.isoformat(),
        "heartbeat_at": now.isoformat(),
        "status": "UNHEALTHY",
        "actionable_failure_counts": {"PROVIDER_HTTP_ERROR": 2},
    }
    for reason_code in (
        "ANNOTATOR_HEARTBEAT_STALE", "NEWS_COLLECTOR_POLL_MISSING",
    ):
        stale_freshness = _semantic_pipeline_component({
            **base, "reason_codes": [reason_code],
        }, now=now)
        assert stale_freshness["status"] == "STALE"
        assert stale_freshness["actionable_failure_counts"] == {
            "PROVIDER_HTTP_ERROR": 2,
        }

    recovering = _semantic_pipeline_component({
        **base,
        "reason_codes": [
            "ACTIONABLE_NEWS_SEMANTICS_PENDING",
            "ACTIONABLE_NEWS_IMPACT_RECOVERING",
        ],
    }, now=now)
    assert recovering["status"] == "WARN"

    terminal = _semantic_pipeline_component({
        "observed_at": base["observed_at"],
        "heartbeat_at": base["heartbeat_at"],
        "status": base["status"],
        "reason_codes_json": json.dumps([
            "ACTIONABLE_NEWS_SEMANTICS_PENDING",
            "ACTIONABLE_NEWS_SEMANTICS_TERMINAL",
        ]),
        "actionable_failure_counts_json": '{"PROVIDER_HTTP_ERROR":1}',
    }, now=now)
    assert terminal["status"] == "ERROR"
    assert terminal["actionable_failure_counts"] == {"PROVIDER_HTTP_ERROR": 1}


@pytest.mark.parametrize(
    "age,state,expected",
    [
        (10, "RUNNING", "OK"),
        (COLLECTOR_HEARTBEAT_EXPECTED_SECONDS, "RUNNING", "OK"),
        (61, "RUNNING", "WARN"),
        (COLLECTOR_HEARTBEAT_FAILURE_SECONDS, "RUNNING", "WARN"),
        (300.1, "RUNNING", "STALE"),
        (10, "ERROR", "STALE"),
    ],
)
def test_collector_component_heartbeat_boundaries(
    age: float, state: str, expected: str,
) -> None:
    now = datetime(2026, 8, 18, 8, 40, tzinfo=UTC)
    component = _collector_component(
        _heartbeat(now, age=age, state=state), latest_poll=None, now=now,
    )
    assert component["status"] == expected


def test_collector_component_starting_grace_and_missing_heartbeat() -> None:
    now = datetime(2026, 8, 18, 8, 40, tzinfo=UTC)
    starting = _collector_component(
        _heartbeat(now, age=300, state="STARTING"), latest_poll=None, now=now,
    )
    expired = _collector_component(
        _heartbeat(now, age=300.1, state="STARTING"), latest_poll=None, now=now,
    )
    missing = _collector_component(
        {"state": "RUNNING"}, latest_poll=None, now=now,
    )
    unavailable = _collector_component(
        _heartbeat(now, age=10, state="ERROR"), latest_poll=None, now=now,
    )

    assert (starting["status"], starting["last_error"]) == (
        "WARN", "采集器启动中",
    )
    assert (expired["status"], expired["last_error"]) == (
        "STALE", "采集器启动心跳已过期",
    )
    assert (missing["status"], missing["last_error"]) == ("STALE", None)
    assert unavailable["last_error"] == "采集器运行心跳不可用"


def test_collector_component_fresh_heartbeat_is_independent_of_old_poll() -> None:
    now = datetime(2026, 8, 18, 8, 40, tzinfo=UTC)
    old_poll = (now - timedelta(hours=1)).isoformat()

    component = _collector_component(
        _heartbeat(now), latest_poll=old_poll, now=now,
    )

    assert component["status"] == "OK"
    assert component["source_poll_age_seconds"] == 3600


def test_decision_collector_stays_live_when_decision_output_stalls() -> None:
    now = datetime(2026, 8, 18, 8, 40, tzinfo=UTC)
    old_decision = (now - timedelta(minutes=20)).isoformat()

    component = _decision_collector_component(
        _heartbeat(now),
        latest_decision=old_decision,
        decision_observation_start=now.isoformat(),
        broker_session=_open_session(now),
        quote_current=True,
        now=now,
    )

    assert component["status"] == "OK"
    assert component["last_success"] == now.isoformat()
    assert component["latest_decision"] == old_decision
    assert component["decision_age_seconds"] == 1200
    assert component["decision_output_status"] == "STALLED"
    assert component["decision_output_reason"] == "DECISION_OUTPUT_CADENCE_EXCEEDED"
    assert component["decision_output_message"] == "决策输出已超过正常 5 分钟节奏"
    assert "latest_source_poll" not in component
    assert "source_poll_age_seconds" not in component


@pytest.mark.parametrize(
    "broker_session,quote_current,expected",
    [
        ({"is_open": True, "next_close_time": "2026-08-18T09:40:00+00:00"}, False, "NO_RECENT_DECISION"),
        ({"is_open": True, "next_close_time": "2026-08-18T09:10:00+00:00"}, True, "EXPECTED_PAUSE"),
        (None, True, "NO_RECENT_DECISION"),
    ],
)
def test_decision_output_stall_requires_current_quote_and_full_horizon(
    broker_session: dict | None, quote_current: bool, expected: str,
) -> None:
    now = datetime(2026, 8, 18, 8, 40, tzinfo=UTC)
    component = _decision_collector_component(
        _heartbeat(now),
        latest_decision=(now - timedelta(minutes=20)).isoformat(),
        decision_observation_start=now.isoformat(),
        broker_session=broker_session,
        quote_current=quote_current,
        now=now,
    )
    assert component["status"] == "OK"
    assert component["decision_output_status"] == expected


@pytest.mark.parametrize("age_seconds,expected", [(420, "CURRENT"), (421, "STALLED")])
def test_decision_output_stall_honors_exact_boundary(
    age_seconds: float, expected: str,
) -> None:
    now = datetime(2026, 8, 18, 8, 40, tzinfo=UTC)
    component = _decision_collector_component(
        _heartbeat(now),
        latest_decision=(now - timedelta(seconds=age_seconds)).isoformat(),
        decision_observation_start=now.isoformat(),
        broker_session=_open_session(now),
        quote_current=True,
        now=now,
    )
    assert component["decision_output_status"] == expected


@pytest.mark.parametrize(
    "observation_age_seconds,expected",
    [(420, "NO_RECENT_DECISION"), (421, "STALLED")],
)
def test_decision_output_without_prior_row_uses_observation_start(
    observation_age_seconds: float, expected: str,
) -> None:
    now = datetime(2026, 8, 18, 8, 40, tzinfo=UTC)
    component = _decision_collector_component(
        _heartbeat(now),
        latest_decision=None,
        decision_observation_start=(
            now - timedelta(seconds=observation_age_seconds)
        ).isoformat(),
        broker_session=_open_session(now),
        quote_current=True,
        now=now,
    )
    assert component["latest_decision"] is None
    assert component["decision_age_seconds"] is None
    assert component["decision_output_age_seconds"] == observation_age_seconds
    assert component["decision_output_status"] == expected


def test_decision_output_market_closed_and_expected_pause() -> None:
    now = datetime(2026, 8, 18, 8, 40, tzinfo=UTC)
    common = {
        "heartbeat": _heartbeat(now),
        "latest_decision": (now - timedelta(minutes=20)).isoformat(),
        "decision_observation_start": now.isoformat(),
        "quote_current": True,
        "now": now,
    }
    closed = _decision_collector_component(
        **common, broker_session={"is_open": False, "next_close_time": None},
    )
    pause = _decision_collector_component(
        **common, broker_session=_open_session(now, closes_in=DECISION_HORIZON),
    )

    assert closed["decision_output_status"] == "MARKET_CLOSED"
    assert pause["decision_output_status"] == "EXPECTED_PAUSE"
    assert pause["decision_output_reason"] == "FIXED_HORIZON_CROSSES_BROKER_CLOSE"
    assert pause["decision_output_message"] == "等待下一个完整 30 分钟决策窗口"


def test_decision_output_reopen_waits_through_first_eligible_grid() -> None:
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

    waiting = _decision_collector_component(
        _heartbeat(stall_after),
        latest_decision=(opened_at - timedelta(minutes=20)).isoformat(),
        decision_observation_start=opened_at.isoformat(),
        broker_session=session,
        quote_current=True,
        now=stall_after,
    )
    stalled = _decision_collector_component(
        _heartbeat(stall_after + timedelta(seconds=1)),
        latest_decision=(opened_at - timedelta(minutes=20)).isoformat(),
        decision_observation_start=opened_at.isoformat(),
        broker_session=session,
        quote_current=True,
        now=stall_after + timedelta(seconds=1),
    )

    assert waiting["decision_output_eligible_grid"] == eligible_grid.isoformat()
    assert waiting["decision_output_stall_after"] == stall_after.isoformat()
    assert waiting["decision_output_status"] == "NO_RECENT_DECISION"
    assert stalled["decision_output_status"] == "STALLED"


@pytest.mark.parametrize("state,age", [("RUNNING", 301), ("STOPPED", 1), ("ERROR", 1)])
def test_decision_collector_preserves_invalid_heartbeat_failure(
    state: str, age: float,
) -> None:
    now = datetime(2026, 8, 18, 8, 40, tzinfo=UTC)
    component = _decision_collector_component(
        _heartbeat(now, age=age, state=state),
        latest_decision=now.isoformat(),
        decision_observation_start=now.isoformat(),
        broker_session=_open_session(now),
        quote_current=True,
        now=now,
    )
    assert component["status"] == "STALE"


def test_materialized_semantic_health_decodes_current_row() -> None:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.execute(
        """CREATE TABLE news_semantic_health_snapshots_v1 (
               source_decision_id TEXT PRIMARY KEY,
               observed_at TEXT,
               status TEXT,
               reason_codes_json TEXT,
               heartbeat_at TEXT,
               unresolved_items INTEGER,
               oldest_unresolved_at TEXT,
               snapshot_hash TEXT
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

    result = _materialized_semantic_health(connection, "decision-1")

    assert result == {
        "observed_at": "2026-08-18T08:40:00+00:00",
        "status": "UNHEALTHY",
        "heartbeat_at": "2026-08-18T08:39:50+00:00",
        "unresolved_items": 1,
        "oldest_unresolved_at": "2026-08-18T08:30:00+00:00",
        "snapshot_hash": "snapshot-hash",
        "reason_codes": ["ACTIONABLE_NEWS_SEMANTICS_PENDING"],
        "actionable_failure_counts": {},
    }
    connection.close()


def test_materialized_semantic_health_without_decision_id_returns_none() -> None:
    connection = sqlite3.connect(":memory:")
    assert _materialized_semantic_health(connection, None) is None
    connection.close()


def test_projection_threshold_contract_values() -> None:
    assert SEMANTIC_SNAPSHOT_MAX_STALE_SECONDS == 420.0
    assert COLLECTOR_HEARTBEAT_EXPECTED_SECONDS == 60.0
    assert COLLECTOR_HEARTBEAT_FAILURE_SECONDS == 300.0
    assert DECISION_OUTPUT_CADENCE_SECONDS == 300.0
    assert DECISION_OUTPUT_STALLED_SECONDS == 420.0
    assert DECISION_OUTPUT_GRACE_SECONDS == 120.0
    assert DECISION_HORIZON == timedelta(minutes=30)
