from __future__ import annotations

import concurrent.futures
import hashlib
import gzip
import importlib.util
import json
import sqlite3
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from xauusd_forecaster.dashboard.status_cache import (
    StatusSnapshotCache,
    StatusSnapshotUnavailable,
)
from xauusd_forecaster.news.annotation.product import (
    ANNOTATION_FAILURE_RECOVERY_VERSION,
    INVALID_CHINESE_TITLE,
    PROMPT_VERSION,
)
from xauusd_forecaster.ai.provider_registry import AI_QUOTA_SURFACES
from xauusd_forecaster.evidence.ledger import ForwardLedger
from xauusd_forecaster.dashboard.read_models import (
    DashboardReadModelOwner,
    DashboardReadModelUnavailable,
    READ_MODEL_CONTRACTS,
    read_dashboard_read_model,
)
from xauusd_forecaster.dashboard.summaries import (
    DASHBOARD_COUNT_TABLES,
    dashboard_distinct_article_count,
    dashboard_news_source_summary,
    dashboard_table_counts,
    install_dashboard_summary_schema,
)
from xauusd_forecaster.ai.quota import GeminiQuotaLedger
from xauusd_forecaster.news.scheduler.state import (
    authorize_repairable_annotation_failures,
    configured_api_credentials,
    reserve_account_request,
)
from xauusd_forecaster.news.collection.source_registry import NEWS_SOURCE_REGISTRY
from tests.dashboard_news_fixtures import (
    _append_basic_annotation,
    _basic_annotation_payload,
)


UTC = timezone.utc


def _dashboard_module():
    path = Path(__file__).resolve().parents[1] / "scripts" / "run_dashboard_api.py"
    spec = importlib.util.spec_from_file_location("run_dashboard_api_test", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_market_session(
    root: Path, *, observed_at: datetime, is_open: bool,
    next_close_time: datetime | None = None,
    opened_at: datetime | None = None,
    first_quote_after_open_at: datetime | None = None,
) -> None:
    quotes = root / "quotes"
    quotes.mkdir(exist_ok=True)
    (quotes / "market-session.json").write_text(json.dumps({
        "schema": "xauusd.forward.market-session.v1",
        "symbol": "XAUUSD",
        "observed_at": observed_at.isoformat(),
        "is_open": is_open,
        "next_open_time": (
            (observed_at + timedelta(hours=1)).isoformat()
            if not is_open else None
        ),
        "next_close_time": (
            (next_close_time or observed_at + timedelta(hours=23)).isoformat()
            if is_open else None
        ),
        "opened_at": opened_at.isoformat() if opened_at else None,
        "first_quote_after_open_at": (
            first_quote_after_open_at.isoformat()
            if first_quote_after_open_at else None
        ),
    }), encoding="utf-8")


def _write_quote(root: Path, *, received_at: datetime) -> None:
    quotes = root / "quotes"
    quotes.mkdir(exist_ok=True)
    (quotes / "xauusd-quotes-test.jsonl").write_text(json.dumps({
        "schema": "xauusd.forward.quote.v1",
        "symbol": "XAUUSD",
        "event_time": received_at.isoformat(),
        "received_time": received_at.isoformat(),
        "bid": 2400.0,
        "ask": 2400.2,
    }) + "\n", encoding="utf-8")


def _write_collector_heartbeat(
    root: Path, *, last_success: datetime, state: str = "RUNNING",
) -> None:
    (root / "collector-status.json").write_text(json.dumps({
        "service": "collector",
        "state": state,
        "last_success": last_success.isoformat(),
        "last_error": None,
        "work_items": 0,
    }), encoding="utf-8")


def _write_annotator_heartbeat(
    root: Path, *, last_success: datetime, state: str = "RUNNING",
) -> None:
    (root / "news-annotator-status.json").write_text(json.dumps({
        "service": "annotator",
        "state": state,
        "last_success": last_success.isoformat(),
        "last_error": None,
        "work_items": 0,
    }), encoding="utf-8")


def _append_decision_at(
    database: Path, created_at: datetime, *, identifier: str = "",
) -> None:
    snapshot_id = f"snapshot{identifier}"
    decision_id = f"decision{identifier}"
    connection = sqlite3.connect(database)
    try:
        connection.execute(
            "INSERT INTO market_snapshots VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                snapshot_id, created_at.isoformat(), created_at.isoformat(),
                "FORWARD", "fixture", created_at.isoformat(),
                created_at.isoformat(), 2400.0, 2400.2, 0.2, "{}", "fixture",
                None, "WARMUP", "OK", 0, "[]", "snapshot-hash",
            ),
        )
        connection.execute(
            "INSERT INTO decision_events VALUES (?,?,?,?,?,?,?,?)",
            (
                decision_id, created_at.isoformat(), snapshot_id,
                created_at.isoformat(), "visible-news-hash", "OK", "WAIT", "[]",
            ),
        )
        connection.commit()
    finally:
        connection.close()


def _append_semantic_snapshot(
    database: Path, *, observed_at: datetime, reason_code: str,
) -> None:
    connection = sqlite3.connect(database)
    try:
        connection.execute(
            "INSERT INTO news_semantic_health_snapshots_v1 VALUES (?,?,?,?,?,?,?,?,?)",
            (
                "decision", observed_at.isoformat(), observed_at.isoformat(),
                "UNHEALTHY", json.dumps([reason_code]), observed_at.isoformat(),
                1, observed_at.isoformat(), "semantic-hash",
            ),
        )
        connection.commit()
    finally:
        connection.close()


def _append_news_input_coverage(database: Path, observed_at: datetime) -> None:
    connection = sqlite3.connect(database)
    try:
        connection.execute(
            """INSERT INTO news_input_coverage_snapshots_v1 VALUES
               (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                "decision", observed_at.isoformat(), observed_at.isoformat(),
                "DEGRADED", 25, 30, 0, 2, 2, 0,
                json.dumps([
                    "ACTIONABLE_NEWS_IMPACT_PENDING",
                    "ACTIONABLE_NEWS_IMPACT_RECOVERING",
                ]),
                json.dumps([
                    "ACTIONABLE_NEWS_IMPACT_PENDING",
                    "ACTIONABLE_NEWS_IMPACT_RECOVERING",
                ]),
                json.dumps({
                    "observable_source_count": 12,
                    "unavailable_source_count": 0,
                }),
                "visible-news-hash", "coverage-hash",
            ),
        )
        connection.commit()
    finally:
        connection.close()


def _component_alert_scopes(payload: dict) -> set[str]:
    return {
        str(alert["scope"])
        for alert in payload["operational_health"]["alerts"]
        if alert["code"] == "OPS_COMPONENT_UNHEALTHY"
    }


def test_dashboard_api_exposes_health_projection_owner() -> None:
    import xauusd_forecaster.dashboard.health_projection as health_projection

    module = _dashboard_module()
    functions = (
        "_semantic_pipeline_component",
        "_materialized_semantic_health",
        "_collector_component",
        "_decision_collector_component",
    )
    constants = (
        "SEMANTIC_SNAPSHOT_MAX_STALE_SECONDS",
        "COLLECTOR_HEARTBEAT_EXPECTED_SECONDS",
        "COLLECTOR_HEARTBEAT_FAILURE_SECONDS",
        "DECISION_OUTPUT_CADENCE_SECONDS",
        "DECISION_OUTPUT_STALLED_SECONDS",
        "DECISION_OUTPUT_GRACE_SECONDS",
        "DECISION_HORIZON",
    )
    for name in functions:
        assert getattr(module, name) is getattr(health_projection, name)
    for name in constants:
        assert getattr(module, name) == getattr(health_projection, name)


def test_dashboard_api_exposes_news_resource_owner() -> None:
    from xauusd_forecaster.dashboard import news_resources

    module = _dashboard_module()

    assert module._news_archive_page is news_resources._news_archive_page
    assert module._news_evidence_page is news_resources._news_evidence_page
    assert module._news_metrics is news_resources._news_metrics
    assert module._NEWS_EVIDENCE_CACHE is news_resources._NEWS_EVIDENCE_CACHE


def test_dashboard_api_exposes_market_resource_owner() -> None:
    from xauusd_forecaster.dashboard import market_resources

    module = _dashboard_module()

    assert module._market_history_page is market_resources._market_history_page
    assert module._recent_market_chart is market_resources._recent_market_chart
    assert module._QUOTE_CANDLE_CACHE is market_resources._QUOTE_CANDLE_CACHE


def test_dashboard_api_exposes_status_resource_owner() -> None:
    from xauusd_forecaster.dashboard import status_resources

    module = _dashboard_module()

    assert module._dashboard_payload is status_resources._dashboard_payload
    assert module._optional_resource_payload is status_resources._optional_resource_payload
    assert module._LEARNING_CACHE is status_resources._LEARNING_CACHE


def test_dashboard_api_uses_operator_bridge_owner() -> None:
    from xauusd_forecaster.dashboard import operator_bridge

    module = _dashboard_module()

    assert module.apply_retry_overrides is operator_bridge.apply_retry_overrides
    assert module.retry_jobs_response is operator_bridge.retry_jobs_response


def test_starting_news_collector_alert_remains_nonblocking_warning() -> None:
    module = _dashboard_module()
    now = datetime(2026, 8, 18, 8, 40, tzinfo=UTC)
    starting = module._collector_component(
        {
            "service": "collector",
            "state": "STARTING",
            "last_success": (now - timedelta(seconds=10)).isoformat(),
            "last_error": None,
        },
        latest_poll=(now - timedelta(minutes=8)).isoformat(),
        now=now,
    )

    operational = module.extend_with_component_alerts(
        {"alerts": []},
        components={"news_collector": starting},
        news_sources=[],
        runtime_update_failure=None,
    )
    alert = operational["alerts"][0]
    assert operational["status"] == "WARNING"
    assert alert["severity"] == "WARNING"
    assert alert["blocking"] is False








def test_dashboard_reports_broker_close_and_reopen_time(tmp_path) -> None:
    now = datetime(2026, 8, 18, 21, 15, tzinfo=UTC)
    database = tmp_path / "forward-evidence.sqlite3"
    ForwardLedger(database, now=now).close()
    _write_quote(tmp_path, received_at=now - timedelta(minutes=58))
    _append_decision_at(database, now - timedelta(minutes=90))
    _append_semantic_snapshot(
        database,
        observed_at=now - timedelta(minutes=90),
        reason_code="ACTIONABLE_NEWS_SEMANTICS_PENDING",
    )
    reopens_at = now + timedelta(hours=1)
    _write_market_session(tmp_path, observed_at=now, is_open=False)

    payload = _dashboard_module()._dashboard_payload(database, clock=lambda: now)

    assert payload["system"]["market_session"] == "CLOSED"
    assert payload["system"]["market_reopens_at"] == reopens_at.isoformat()
    expected_silence = {
        "quote_bridge", "decision_collector", "outcome_settler",
        "news_semantic_pipeline",
    }
    for component in expected_silence:
        assert payload["system"]["components"][component]["status"] == "MARKET_CLOSED"
        assert payload["system"]["components"][component]["last_error"] is None
    assert _component_alert_scopes(payload).isdisjoint(expected_silence)


def test_dashboard_exposes_frozen_news_coverage_separately_from_current_health(
    tmp_path,
    monkeypatch,
) -> None:
    now = datetime(2026, 8, 18, 12, 0, tzinfo=UTC)
    database = tmp_path / "forward-evidence.sqlite3"
    ForwardLedger(database, now=now).close()
    _append_decision_at(database, now)
    _append_semantic_snapshot(
        database, observed_at=now,
        reason_code="ACTIONABLE_NEWS_IMPACT_TERMINAL",
    )
    _append_news_input_coverage(database, now)
    module = _dashboard_module()
    from xauusd_forecaster.dashboard import status_resources

    monkeypatch.setattr(status_resources, "news_semantic_pipeline_health", lambda *_args, **_kwargs: {
        "observed_at": now.isoformat(),
        "status": "HEALTHY",
        "reason_codes": (),
        "heartbeat_at": now.isoformat(),
        "actionable_failure_counts": {},
    })

    payload = module._dashboard_payload(database, clock=lambda: now)

    assert payload["news_input_coverage"]["state"] == "DEGRADED"
    assert payload["news_input_coverage"]["usable_broad_event_count"] == 30
    assert payload["news_input_coverage"]["recovering_count"] == 2
    assert payload["system"]["components"]["news_semantic_pipeline"][
        "status"
    ] == "OK"


def test_dashboard_samples_mutable_semantic_heartbeat_at_snapshot_boundary(
    tmp_path,
    monkeypatch,
) -> None:
    query_started = datetime(2026, 8, 18, 12, 0, tzinfo=UTC)
    database = tmp_path / "forward-evidence.sqlite3"
    ForwardLedger(database, now=query_started).close()
    _write_annotator_heartbeat(tmp_path, last_success=query_started)
    module = _dashboard_module()
    from xauusd_forecaster.dashboard import status_resources

    original_evidence_reader = status_resources.event_evidence_rows_from_connection

    def evidence_reader(connection, decision_time):
        _write_annotator_heartbeat(
            tmp_path, last_success=query_started + timedelta(minutes=2),
        )
        return original_evidence_reader(connection, decision_time)

    monkeypatch.setattr(
        status_resources, "event_evidence_rows_from_connection", evidence_reader,
    )

    payload = module._dashboard_payload(
        database, clock=lambda: query_started,
    )

    semantic = payload["system"]["components"]["news_semantic_pipeline"]
    assert "ANNOTATOR_HEARTBEAT_STALE" not in semantic["reason_codes"]
    assert semantic["last_success"] == query_started.isoformat()


def test_dashboard_refreshes_clock_before_reading_live_broker_heartbeat(
    tmp_path,
) -> None:
    query_started = datetime(2026, 8, 18, 21, 14, 45, tzinfo=UTC)
    runtime_observed = query_started + timedelta(seconds=14)
    database = tmp_path / "forward-evidence.sqlite3"
    ForwardLedger(database, now=query_started).close()
    _write_market_session(tmp_path, observed_at=runtime_observed, is_open=False)
    clock_values = iter((query_started, runtime_observed))

    payload = _dashboard_module()._dashboard_payload(
        database, clock=lambda: next(clock_values),
    )

    assert payload["generated_at"] == runtime_observed.isoformat()
    assert payload["system"]["market_session"] == "CLOSED"
    assert payload["system"]["market_session_observed_at"] == runtime_observed.isoformat()


def test_dashboard_reopens_sqlite_for_decision_cadence_at_final_boundary(
    tmp_path,
) -> None:
    query_started = datetime(2026, 8, 18, 11, 40, tzinfo=UTC)
    runtime_observed = query_started + timedelta(minutes=20)
    database = tmp_path / "forward-evidence.sqlite3"
    ForwardLedger(database, now=query_started).close()
    _write_market_session(tmp_path, observed_at=runtime_observed, is_open=True)
    _write_quote(tmp_path, received_at=runtime_observed)
    _write_collector_heartbeat(tmp_path, last_success=runtime_observed)
    clock_calls = 0

    def advancing_clock() -> datetime:
        nonlocal clock_calls
        clock_calls += 1
        if clock_calls == 2:
            _append_decision_at(database, runtime_observed)
            return runtime_observed
        return query_started

    payload = _dashboard_module()._dashboard_payload(
        database, clock=advancing_clock,
    )

    decision = payload["system"]["components"]["decision_collector"]
    assert decision["status"] == "OK"
    assert decision["latest_decision"] == runtime_observed.isoformat()
    assert decision["decision_output_status"] == "CURRENT"
    assert not any(
        alert["code"] == "OPS_DECISION_OUTPUT_STALLED"
        for alert in payload["operational_health"]["alerts"]
    )


def test_open_market_stale_quote_and_decision_remain_unhealthy(tmp_path) -> None:
    now = datetime(2026, 8, 18, 12, 0, tzinfo=UTC)
    database = tmp_path / "forward-evidence.sqlite3"
    ForwardLedger(database, now=now).close()
    _write_market_session(tmp_path, observed_at=now, is_open=True)
    _write_quote(tmp_path, received_at=now - timedelta(minutes=58))
    _append_decision_at(database, now - timedelta(minutes=90))

    payload = _dashboard_module()._dashboard_payload(database, clock=lambda: now)

    assert payload["system"]["market_session"] == "DATA_UNAVAILABLE"
    assert payload["system"]["components"]["quote_bridge"]["status"] == "STALE"
    assert payload["system"]["components"]["decision_collector"]["status"] == "STALE"
    assert {"quote_bridge", "decision_collector"}.issubset(
        _component_alert_scopes(payload)
    )


def test_healthy_collector_old_decision_reports_output_stall_not_collector_stale(
    tmp_path,
) -> None:
    now = datetime(2026, 8, 18, 12, 0, tzinfo=UTC)
    database = tmp_path / "forward-evidence.sqlite3"
    ForwardLedger(database, now=now).close()
    _write_market_session(tmp_path, observed_at=now, is_open=True)
    _write_quote(tmp_path, received_at=now)
    _write_collector_heartbeat(tmp_path, last_success=now)
    _append_decision_at(database, now - timedelta(minutes=20))

    payload = _dashboard_module()._dashboard_payload(
        database, clock=lambda: now,
    )

    decision = payload["system"]["components"]["decision_collector"]
    assert payload["system"]["online"] is True
    assert payload["system"]["market_session"] == "OPEN"
    assert decision["status"] == "OK"
    assert decision["decision_output_status"] == "STALLED"
    assert "decision_collector" not in _component_alert_scopes(payload)
    stalled = next(
        alert for alert in payload["operational_health"]["alerts"]
        if alert["code"] == "OPS_DECISION_OUTPUT_STALLED"
    )
    assert stalled["scope"] == "decision_output"
    assert stalled["blocking"] is True
    assert stalled["evidence"]["age_seconds"] == 1200


def test_broker_reopen_waits_for_first_quote_eligible_grid_before_stall(
    tmp_path,
) -> None:
    reopened_at = datetime(2026, 8, 18, 10, 0, tzinfo=UTC)
    first_quote = reopened_at + timedelta(seconds=1)
    immediate = reopened_at + timedelta(seconds=10)
    eligible_grid = reopened_at + timedelta(minutes=5)
    stall_after = eligible_grid + timedelta(seconds=120)
    database = tmp_path / "forward-evidence.sqlite3"
    ForwardLedger(database, now=reopened_at - timedelta(hours=1)).close()
    _append_decision_at(database, reopened_at - timedelta(minutes=20))

    def payload_at(now: datetime) -> dict:
        _write_market_session(
            tmp_path,
            observed_at=now,
            is_open=True,
            next_close_time=now + timedelta(hours=1),
            opened_at=reopened_at,
            first_quote_after_open_at=first_quote,
        )
        _write_quote(tmp_path, received_at=now)
        _write_collector_heartbeat(tmp_path, last_success=now)
        return _dashboard_module()._dashboard_payload(
            database, clock=lambda: now,
        )

    waiting = payload_at(immediate)
    waiting_decision = waiting["system"]["components"]["decision_collector"]
    assert waiting_decision["decision_output_status"] == "NO_RECENT_DECISION"
    assert waiting_decision["decision_output_eligible_grid"] == (
        eligible_grid.isoformat()
    )
    assert waiting_decision["decision_output_stall_after"] == stall_after.isoformat()
    assert not any(
        alert["code"] == "OPS_DECISION_OUTPUT_STALLED"
        for alert in waiting["operational_health"]["alerts"]
    )

    grace_boundary = payload_at(stall_after)
    assert grace_boundary["system"]["components"]["decision_collector"][
        "decision_output_status"
    ] == "NO_RECENT_DECISION"

    overdue = payload_at(stall_after + timedelta(seconds=1))
    overdue_decision = overdue["system"]["components"]["decision_collector"]
    assert overdue_decision["decision_output_status"] == "STALLED"
    assert any(
        alert["code"] == "OPS_DECISION_OUTPUT_STALLED"
        for alert in overdue["operational_health"]["alerts"]
    )


def test_no_first_decision_stalls_after_forward_epoch_grace(tmp_path) -> None:
    now = datetime(2026, 8, 18, 12, 0, tzinfo=UTC)
    epoch = now - timedelta(seconds=421)
    database = tmp_path / "forward-evidence.sqlite3"
    ForwardLedger(database, now=epoch).close()
    _write_market_session(tmp_path, observed_at=now, is_open=True)
    _write_quote(tmp_path, received_at=now)
    _write_collector_heartbeat(tmp_path, last_success=now)

    payload = _dashboard_module()._dashboard_payload(
        database, clock=lambda: now,
    )

    decision = payload["system"]["components"]["decision_collector"]
    assert decision["status"] == "OK"
    assert decision["latest_decision"] is None
    assert decision["decision_age_seconds"] is None
    assert decision["decision_observation_started_at"] == epoch.isoformat()
    assert decision["decision_output_age_seconds"] == 421
    assert decision["decision_output_status"] == "STALLED"
    assert any(
        alert["code"] == "OPS_DECISION_OUTPUT_STALLED"
        for alert in payload["operational_health"]["alerts"]
    )


@pytest.mark.parametrize("state,heartbeat_age,expected_status", [
    ("RUNNING", 301, "STALE"),
    ("STOPPED", 1, "STALE"),
])
def test_collector_fault_suppresses_duplicate_decision_output_incident(
    tmp_path, state: str, heartbeat_age: float, expected_status: str,
) -> None:
    now = datetime(2026, 8, 18, 12, 0, tzinfo=UTC)
    database = tmp_path / "forward-evidence.sqlite3"
    ForwardLedger(database, now=now - timedelta(hours=1)).close()
    _write_market_session(tmp_path, observed_at=now, is_open=True)
    _write_quote(tmp_path, received_at=now)
    _write_collector_heartbeat(
        tmp_path,
        last_success=now - timedelta(seconds=heartbeat_age),
        state=state,
    )
    _append_decision_at(database, now - timedelta(minutes=20))

    payload = _dashboard_module()._dashboard_payload(
        database, clock=lambda: now,
    )

    decision = payload["system"]["components"]["decision_collector"]
    assert decision["status"] == expected_status
    assert decision["decision_output_status"] == "STALLED"
    assert "decision_collector" in _component_alert_scopes(payload)
    assert not any(
        alert["code"] == "OPS_DECISION_OUTPUT_STALLED"
        for alert in payload["operational_health"]["alerts"]
    )


def test_healthy_collector_recent_decision_remains_current(tmp_path) -> None:
    now = datetime(2026, 8, 18, 12, 0, tzinfo=UTC)
    database = tmp_path / "forward-evidence.sqlite3"
    ForwardLedger(database, now=now).close()
    _write_market_session(tmp_path, observed_at=now, is_open=True)
    _write_quote(tmp_path, received_at=now)
    _write_collector_heartbeat(tmp_path, last_success=now)
    _append_decision_at(database, now - timedelta(minutes=5))

    payload = _dashboard_module()._dashboard_payload(
        database, clock=lambda: now,
    )

    decision = payload["system"]["components"]["decision_collector"]
    assert payload["system"]["online"] is True
    assert decision["status"] == "OK"
    assert decision["decision_output_status"] == "CURRENT"
    assert "decision_collector" not in _component_alert_scopes(payload)
    assert not any(
        alert["code"] == "OPS_DECISION_OUTPUT_STALLED"
        for alert in payload["operational_health"]["alerts"]
    )


def test_online_still_fails_closed_without_broker_session(tmp_path) -> None:
    now = datetime(2026, 8, 18, 12, 0, tzinfo=UTC)
    database = tmp_path / "forward-evidence.sqlite3"
    ForwardLedger(database, now=now).close()
    _write_quote(tmp_path, received_at=now)
    _write_collector_heartbeat(tmp_path, last_success=now)
    _append_decision_at(database, now)

    payload = _dashboard_module()._dashboard_payload(
        database, clock=lambda: now,
    )

    assert payload["system"]["online"] is False
    assert payload["system"]["market_session"] == "DATA_UNAVAILABLE"


def test_starting_collector_does_not_claim_system_online(tmp_path) -> None:
    now = datetime(2026, 8, 18, 12, 0, tzinfo=UTC)
    database = tmp_path / "forward-evidence.sqlite3"
    ForwardLedger(database, now=now).close()
    _write_market_session(tmp_path, observed_at=now, is_open=True)
    _write_quote(tmp_path, received_at=now)
    _write_collector_heartbeat(
        tmp_path, last_success=now, state="STARTING",
    )
    _append_decision_at(database, now)

    payload = _dashboard_module()._dashboard_payload(
        database, clock=lambda: now,
    )

    decision = payload["system"]["components"]["decision_collector"]
    assert payload["system"]["online"] is False
    assert decision["status"] == "WARN"
    alert = next(
        item for item in payload["operational_health"]["alerts"]
        if item["scope"] == "decision_collector"
    )
    assert alert["severity"] == "WARNING"
    assert alert["blocking"] is False


def test_pre_close_horizon_is_expected_pause_not_incident(tmp_path) -> None:
    now = datetime(2026, 8, 18, 20, 35, tzinfo=UTC)
    database = tmp_path / "forward-evidence.sqlite3"
    ForwardLedger(database, now=now).close()
    _write_market_session(
        tmp_path,
        observed_at=now,
        is_open=True,
        next_close_time=now + timedelta(minutes=25),
    )
    _write_quote(tmp_path, received_at=now)
    _write_collector_heartbeat(tmp_path, last_success=now)
    _append_decision_at(database, now - timedelta(minutes=10))

    payload = _dashboard_module()._dashboard_payload(
        database, clock=lambda: now,
    )

    decision = payload["system"]["components"]["decision_collector"]
    assert payload["system"]["online"] is True
    assert payload["system"]["market_session"] == "OPEN"
    assert decision["status"] == "OK"
    assert decision["decision_output_status"] == "EXPECTED_PAUSE"
    assert decision["decision_output_reason"] == (
        "FIXED_HORIZON_CROSSES_BROKER_CLOSE"
    )
    assert decision["decision_output_message"] == (
        "等待下一个完整 30 分钟决策窗口"
    )
    assert "decision_collector" not in _component_alert_scopes(payload)


def test_weekend_fallback_suspends_only_expected_silence(tmp_path) -> None:
    saturday = datetime(2026, 8, 15, 12, 0, tzinfo=UTC)
    database = tmp_path / "forward-evidence.sqlite3"
    ForwardLedger(database, now=saturday).close()

    payload = _dashboard_module()._dashboard_payload(
        database, clock=lambda: saturday,
    )

    assert payload["system"]["market_session"] == "WEEKLY_CLOSED"
    expected_silence = {
        "quote_bridge", "decision_collector", "outcome_settler",
        "news_semantic_pipeline",
    }
    assert _component_alert_scopes(payload).isdisjoint(expected_silence)
    for component in expected_silence:
        assert payload["system"]["components"][component]["status"] == "MARKET_CLOSED"


def test_stale_weekday_broker_heartbeat_does_not_infer_closure(tmp_path) -> None:
    now = datetime(2026, 8, 18, 12, 0, tzinfo=UTC)
    database = tmp_path / "forward-evidence.sqlite3"
    ForwardLedger(database, now=now).close()
    _write_market_session(
        tmp_path, observed_at=now - timedelta(seconds=21), is_open=False,
    )

    payload = _dashboard_module()._dashboard_payload(database, clock=lambda: now)

    assert payload["system"]["market_session"] == "DATA_UNAVAILABLE"
    assert payload["system"]["market_session_observed_at"] is None
    assert payload["system"]["components"]["quote_bridge"]["status"] == "STALE"
    assert "quote_bridge" in _component_alert_scopes(payload)


def test_closed_market_keeps_current_annotation_failure_visible(tmp_path) -> None:
    now = datetime(2026, 8, 18, 21, 15, tzinfo=UTC)
    database = tmp_path / "forward-evidence.sqlite3"
    ForwardLedger(database, now=now).close()
    _write_market_session(tmp_path, observed_at=now, is_open=False)
    (tmp_path / "news-annotator-status.json").write_text(json.dumps({
        "service": "annotator",
        "state": "RUNNING",
        "last_success": (now - timedelta(minutes=20)).isoformat(),
        "last_error": "MODEL_OUTPUT_CONTRACT_FAILED",
    }), encoding="utf-8")

    payload = _dashboard_module()._dashboard_payload(database, clock=lambda: now)

    assert payload["system"]["components"]["news_semantic_pipeline"]["status"] == "MARKET_CLOSED"
    assert payload["system"]["components"]["gemini_annotator"]["status"] == "STALE"
    assert "gemini_annotator" in _component_alert_scopes(payload)


def test_broker_reopen_immediately_restores_freshness_enforcement(tmp_path) -> None:
    closed_at = datetime(2026, 8, 18, 21, 15, tzinfo=UTC)
    reopened_at = closed_at + timedelta(hours=1)
    database = tmp_path / "forward-evidence.sqlite3"
    ForwardLedger(database, now=closed_at).close()
    _write_market_session(tmp_path, observed_at=closed_at, is_open=False)
    module = _dashboard_module()

    closed = module._dashboard_payload(database, clock=lambda: closed_at)
    _write_market_session(tmp_path, observed_at=reopened_at, is_open=True)
    reopened = module._dashboard_payload(database, clock=lambda: reopened_at)

    assert closed["system"]["components"]["quote_bridge"]["status"] == "MARKET_CLOSED"
    assert reopened["system"]["market_session"] == "DATA_UNAVAILABLE"
    assert reopened["system"]["components"]["quote_bridge"]["status"] == "STALE"
    assert reopened["system"]["components"]["decision_collector"]["status"] == "STALE"
    assert {"quote_bridge", "decision_collector"}.issubset(
        _component_alert_scopes(reopened)
    )

    _write_quote(tmp_path, received_at=reopened_at)
    _write_collector_heartbeat(tmp_path, last_success=reopened_at)
    _append_decision_at(database, reopened_at)
    live = module._dashboard_payload(database, clock=lambda: reopened_at)
    assert live["system"]["market_session"] == "OPEN"
    assert live["system"]["components"]["quote_bridge"]["status"] == "OK"
    assert live["system"]["components"]["decision_collector"]["status"] == "OK"


def test_outcome_settler_health_uses_successful_loop_heartbeat_not_output_age(
    tmp_path,
) -> None:
    now = datetime(2026, 8, 19, 10, 0, tzinfo=UTC)
    database = tmp_path / "forward-evidence.sqlite3"
    ForwardLedger(database, now=now).close()
    (tmp_path / "collector-status.json").write_text(json.dumps({
        "service": "collector",
        "state": "RUNNING",
        "last_success": now.isoformat(),
        "last_error": None,
        "work_items": 0,
    }), encoding="utf-8")

    payload = _dashboard_module()._dashboard_payload(database, clock=lambda: now)

    outcome = payload["system"]["components"]["outcome_settler"]
    assert outcome["status"] == "OK"
    assert outcome["last_success"] == now.isoformat()
    assert not any(
        alert["code"] == "OPS_COMPONENT_UNHEALTHY"
        and alert["scope"] == "outcome_settler"
        for alert in payload["operational_health"]["alerts"]
    )


def test_dashboard_exposes_only_runtime_update_failures(tmp_path) -> None:
    now = datetime.now(UTC).replace(microsecond=0)
    database = tmp_path / "forward-evidence.sqlite3"
    ForwardLedger(database, now=now).close()
    state_path = tmp_path / "runtime-update-state.json"
    state_path.write_text(json.dumps({
        "update_status": "ACTIVE", "user_visible_failure": False,
        "failure_message": None,
    }), encoding="utf-8")

    healthy = _dashboard_module()._dashboard_payload(database)
    assert healthy["system"]["runtime_update_failure"] is None

    state_path.write_text(json.dumps({
        "update_status": "ROLLED_BACK", "user_visible_failure": True,
        "failure_message": "新版运行验证失败，已自动恢复上一版。",
        "failed_at": now.isoformat(),
    }), encoding="utf-8")
    failed = _dashboard_module()._dashboard_payload(database)
    assert failed["system"]["runtime_update_failure"] == {
        "status": "ROLLED_BACK",
        "failed_at": now.isoformat(),
    }
    assert any(
        alert["code"] == "OPS_RUNTIME_UPDATE_FAILED"
        for alert in failed["operational_health"]["alerts"]
    )


















def test_dashboard_annotation_counts_match_current_worker_policy(tmp_path) -> None:
    now = datetime(2026, 8, 8, 1, 0, tzinfo=UTC)
    database = tmp_path / "forward.sqlite3"
    ledger = ForwardLedger(database, now=now)
    for item_id in ("completed", "pending"):
        body = (f"Official Treasury release {item_id}. " * 30).strip()
        digest = hashlib.sha256(body.encode()).hexdigest()
        ledger.append_news_revision(
            {
                "source": "us_treasury_press_releases",
                "source_item_id": item_id,
                "source_published_time": now,
                "collector_first_seen_time": now,
                "fetched_time": now,
                "headline": f"Treasury publishes {item_id} economic release",
                "body": body,
                "content_hash": digest,
                "cluster_id": item_id,
            }
        )
        if item_id == "completed":
            _append_basic_annotation(
                ledger,
                source="us_treasury_press_releases",
                item_id=item_id,
                digest=digest,
                parsed_at=now + timedelta(seconds=1),
                prompt_version=PROMPT_VERSION,
            )
    from xauusd_forecaster.news.scheduler.state import sync_pending_jobs
    sync_pending_jobs(ledger.connection, now=now + timedelta(seconds=2))
    ledger.connection.close()

    payload = _dashboard_module()._dashboard_payload(database)

    assert payload["annotation_queue"]["ready"] == 1
    assert payload["annotation_queue"]["semantic_pending"] == 1
    assert payload["annotation_queue"]["queued"] == 0
    assert payload["annotation_queue"]["contract_backfill_queued"] == 1
    assert payload["annotation_queue"]["unclassified_annotation_jobs"] == 0
    active_identities = {
        row["model_identity"]
        for row in payload["learning_curves"]["models"]
        if row["active_rank"] is not None
    }
    assert payload["counts"]["live_oos_model_groups"] == len(active_identities)
    transition = payload["learning_curves"]["news_contract_transition"]
    assert payload["news_evidence_summary"]["current_contract_exposed_rows"] == transition["current_contract_exposed_rows"]
    assert payload["news_evidence_summary"]["current_contract_distinct_events"] == transition["current_contract_distinct_events"]
    metrics = payload["news_metrics"]
    assert metrics["schema_version"] == "news-metrics-v1"
    assert metrics["articles"]["stored_revisions"] == payload["counts"]["news_revisions"]
    assert metrics["articles"]["semantic_reviews_complete"] == payload["counts"]["parsed_news_items"]
    assert metrics["training"]["current_contract_rows"] == transition["current_contract_exposed_rows"]
    assert metrics["training"]["distinct_events"] == transition["current_contract_distinct_events"]


def test_dashboard_quota_uses_scheduler_ledger(tmp_path, monkeypatch) -> None:
    import xauusd_forecaster.news.scheduler.state as news_scheduler

    configured = {"GEMINI_API_KEYS": "key-a;key-b", "GEMINI_API_KEY": ""}
    monkeypatch.setattr(
        news_scheduler, "_runtime_environment_value",
        lambda name: configured.get(name, ""),
    )
    now = datetime.now(UTC)
    database = tmp_path / "forward.sqlite3"
    ledger = ForwardLedger(database, now=now)
    monkeypatch.setenv("GEMINI_API_KEYS", "key-a;key-b")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    credentials = configured_api_credentials()
    for credential in credentials:
        assert reserve_account_request(
            ledger.connection, account_id=credential.account_id,
            model_family="gemini-3.5-flash-lite", daily_limit=500,
            requests_per_minute=12, now=now,
        )
    assert reserve_account_request(
        ledger.connection, account_id=credentials[0].account_id,
        model_family="gemma-impact", daily_limit=15_000,
        requests_per_minute=12, now=now,
    )
    assert reserve_account_request(
        ledger.connection, account_id=credentials[0].account_id,
        model_family="gemma-title", daily_limit=15_000,
        requests_per_minute=12, now=now,
    )
    ledger.close()

    payload = _dashboard_module()._dashboard_payload(database)

    assert payload["gemini_quota"]["accounting_source"] == "SCHEDULER_DB"
    assert payload["gemini_quota"]["total_sent"] == 2
    assert [row["sent"] for row in payload["gemini_quota"]["keys"]] == [1, 1]
    assert payload["gemma_quota"]["total_sent"] == 2
    assert [row["sent"] for row in payload["gemma_quota"]["keys"]] == [2, 0]
    scheduler_usage = payload["production_contract"]["scheduler_usage"]
    for surface in AI_QUOTA_SURFACES:
        assert scheduler_usage[surface.payload_key] == payload[
            surface.payload_key
        ]["total_sent"]


def test_dashboard_quota_keeps_pre_scheduler_file_compatibility(
    tmp_path, monkeypatch,
) -> None:
    import xauusd_forecaster.news.scheduler.state as news_scheduler

    configured = {"GEMINI_API_KEYS": "legacy-key", "GEMINI_API_KEY": ""}
    monkeypatch.setattr(
        news_scheduler, "_runtime_environment_value",
        lambda name: configured.get(name, ""),
    )
    now = datetime.now(UTC)
    database = tmp_path / "forward.sqlite3"
    ledger = ForwardLedger(database, now=now)
    ledger.connection.execute("DROP TABLE news_ai_account_daily_usage_v1")
    ledger.connection.commit()
    ledger.close()
    monkeypatch.setenv("GEMINI_API_KEYS", "legacy-key")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    quota_day = GeminiQuotaLedger.quota_day(now)
    quota_paths = {
        "gemini_quota": tmp_path / "gemini-quota.json",
        "gemini_31_quota": tmp_path / "gemini-3.1-flash-lite-quota.json",
        "gemma_quota": tmp_path / "gemma-quota.json",
        "gemini_embedding_quota": tmp_path / "gemini-embedding-2-quota.json",
    }
    expected_counts = {
        "gemini_quota": 7,
        "gemini_31_quota": 8,
        "gemma_quota": 9,
        "gemini_embedding_quota": 10,
    }
    for payload_key, path in quota_paths.items():
        path.write_text(json.dumps({
            "quota_day": quota_day,
            "counts": {"94eeb7bbe979": expected_counts[payload_key]},
        }), encoding="utf-8")
    original_bytes = {path: path.read_bytes() for path in quota_paths.values()}

    payload = _dashboard_module()._dashboard_payload(database, clock=lambda: now)

    for payload_key, path in quota_paths.items():
        assert payload[payload_key]["total_sent"] == expected_counts[payload_key]
        assert payload[payload_key]["keys"][0]["sent"] == expected_counts[payload_key]
        assert "accounting_source" not in payload[payload_key]
        assert path.read_bytes() == original_bytes[path]


def test_dashboard_api_exposes_status_cache_owner_types() -> None:
    module = _dashboard_module()
    assert module.StatusSnapshotCache is StatusSnapshotCache
    assert module.StatusSnapshotUnavailable is StatusSnapshotUnavailable


def test_critical_status_route_uses_the_independent_bounded_builder(
    monkeypatch, tmp_path,
) -> None:
    module = _dashboard_module()
    module.Handler.database = tmp_path / "unused.sqlite3"
    module.Handler.status_cache = module.StatusSnapshotCache()
    module.Handler.critical_status_cache = module.StatusSnapshotCache()
    calls = []

    def builder(_database, *, include_optional=True):
        calls.append(include_optional)
        if include_optional:
            raise AssertionError("critical status must not build optional history")
        return {
            "generated_at": "2026-08-19T00:00:00+00:00",
            "system": {"online": True},
            "future_accumulated_records": [{"body": "x" * 20_000}],
        }

    monkeypatch.setattr(module, "_dashboard_payload", builder)
    server = module.ThreadingHTTPServer(("127.0.0.1", 0), module.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{server.server_port}/api/critical-status",
            timeout=2,
        ) as response:
            payload = json.loads(response.read())
        assert calls == [False]
        assert payload["system"] == {"online": True}
        assert "future_accumulated_records" not in payload
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@pytest.mark.parametrize("database", [
    Path(".local/preflight/forward.sqlite3"),
    Path(".local/forward/forward.sqlite3"),
])
def test_status_alias_is_always_the_bounded_first_paint_contract(
    monkeypatch, tmp_path, database,
) -> None:
    module = _dashboard_module()
    module.Handler.database = tmp_path / database
    module.Handler.status_cache = module.StatusSnapshotCache()
    module.Handler.critical_status_cache = module.StatusSnapshotCache()
    calls = []

    def builder(_database, *, include_optional=True):
        calls.append(include_optional)
        return {
            "generated_at": "2026-08-20T00:00:00+00:00",
            "system": {"online": True},
        }

    monkeypatch.setattr(module, "_dashboard_payload", builder)
    server = module.ThreadingHTTPServer(("127.0.0.1", 0), module.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{server.server_port}/api/status", timeout=2,
        ) as response:
            assert response.status == 200
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert calls == [False]


def test_health_endpoint_tracks_critical_readiness_not_optional_status(
    monkeypatch, tmp_path,
) -> None:
    module = _dashboard_module()
    module.Handler.database = tmp_path / "unused.sqlite3"
    module.Handler.status_cache = module.StatusSnapshotCache()
    module.Handler.critical_status_cache = module.StatusSnapshotCache()
    critical_fails = [True]

    def builder(_database, *, include_optional=True, **_kwargs):
        if include_optional:
            raise RuntimeError("optional status failed")
        if critical_fails[0]:
            raise RuntimeError("critical status failed")
        return {"generated_at": "2026-08-19T00:00:00+00:00"}

    monkeypatch.setattr(
        module, "_dashboard_payload", builder,
    )
    server = module.ThreadingHTTPServer(("127.0.0.1", 0), module.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        try:
            urllib.request.urlopen(
                f"http://127.0.0.1:{server.server_port}/api/health", timeout=2
            )
        except urllib.error.HTTPError as error:
            assert error.code == 503
            failed = json.loads(error.read())
            assert failed["status"] == "UNAVAILABLE"
            assert failed["readiness_scope"] == "PROCESS_AND_CRITICAL_STATUS"
        else:
            raise AssertionError("health must fail when critical status cannot build")

        critical_fails[0] = False
        with urllib.request.urlopen(
            f"http://127.0.0.1:{server.server_port}/api/health", timeout=2
        ) as response:
            assert response.status == 200
            healthy = json.loads(response.read())
            assert healthy["status"] == "OK"
            assert healthy["optional_resources"] == "SEPARATE_DEGRADATION"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@pytest.mark.parametrize("failed_resource", tuple(READ_MODEL_CONTRACTS))
def test_optional_api_producers_fail_independently(
    monkeypatch, tmp_path, failed_resource,
) -> None:
    module = _dashboard_module()
    database = tmp_path / "forward.sqlite3"
    ForwardLedger(database).close()
    module.Handler.database = database

    def resource(_database, name):
        if name == failed_resource:
            raise RuntimeError(f"{name} source failed")
        return {"generated_at": "2026-08-19T00:00:00+00:00", "resource": name}

    owner = DashboardReadModelOwner(
        database, {name: lambda database, name=name: resource(database, name)
                   for name in READ_MODEL_CONTRACTS},
    )
    expected_refresh = {resource: 1 for resource in READ_MODEL_CONTRACTS}
    expected_refresh[failed_resource] = -1
    assert owner.refresh_once() == expected_refresh
    monkeypatch.setattr(
        module, "_optional_resource_payload",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("GET must not invoke an optional producer")
        ),
    )
    server = module.ThreadingHTTPServer(("127.0.0.1", 0), module.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        for path, expected in (
            ("/api/audit", "audit"), ("/api/learning", "learning"),
            ("/api/market-chart", "market_chart"),
        ):
            if expected == failed_resource:
                with pytest.raises(urllib.error.HTTPError) as failed:
                    urllib.request.urlopen(
                        f"http://127.0.0.1:{server.server_port}{path}", timeout=2,
                    )
                assert failed.value.code == 503
            else:
                with urllib.request.urlopen(
                    f"http://127.0.0.1:{server.server_port}{path}", timeout=2,
                ) as response:
                    assert json.loads(response.read())["resource"] == expected
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_durable_optional_read_models_are_atomic_bounded_and_incremental(
    monkeypatch, tmp_path,
) -> None:
    import xauusd_forecaster.dashboard.read_models as read_models

    database = tmp_path / "forward.sqlite3"
    ForwardLedger(database).close()
    calls = {resource: 0 for resource in READ_MODEL_CONTRACTS}

    def builder(resource):
        def build(_database):
            calls[resource] += 1
            return {
                "generated_at": "2026-08-21T10:20:00+00:00",
                "resource": resource,
                "generation": calls[resource],
            }
        return build

    owner = DashboardReadModelOwner(
        database, {resource: builder(resource) for resource in READ_MODEL_CONTRACTS},
    )
    assert owner.refresh_once() == {
        "audit": 1, "learning": 1, "market_chart": 1,
    }
    assert owner.refresh_once() == {
        "audit": 0, "learning": 0, "market_chart": 0,
    }
    assert calls == {"audit": 1, "learning": 1, "market_chart": 1}

    connection = sqlite3.connect(database)
    with connection:
        connection.execute(
            """UPDATE dashboard_optional_read_model_state_v1
                  SET source_revision=source_revision+1 WHERE resource='audit'"""
        )
    connection.close()
    assert owner.refresh_once() == {
        "audit": 1, "learning": 0, "market_chart": 0,
    }
    assert calls == {"audit": 2, "learning": 1, "market_chart": 1}

    prior, _ = read_dashboard_read_model(database, "audit")
    monkeypatch.setattr(
        read_models, "_payload_bytes",
        lambda _payload: (_ for _ in ()).throw(RuntimeError("crash before commit")),
    )
    connection = sqlite3.connect(database)
    with connection:
        connection.execute(
            """UPDATE dashboard_optional_read_model_state_v1
                  SET source_revision=source_revision+1 WHERE resource='audit'"""
        )
    connection.close()
    with pytest.raises(RuntimeError, match="crash before commit"):
        owner.refresh_resource("audit")
    current, _ = read_dashboard_read_model(database, "audit")
    assert current == prior


def test_optional_read_model_validation_and_concurrent_reads(tmp_path) -> None:
    database = tmp_path / "forward.sqlite3"
    ForwardLedger(database).close()
    owner = DashboardReadModelOwner(
        database,
        {
            resource: lambda _database, resource=resource: {
                "generated_at": "2026-08-21T10:20:00+00:00",
                "resource": resource,
            }
            for resource in READ_MODEL_CONTRACTS
        },
    )
    owner.refresh_once()
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(
            lambda _index: read_dashboard_read_model(database, "learning")[0],
            range(24),
        ))
    assert len(set(results)) == 1
    _, stale = read_dashboard_read_model(
        database, "learning",
        now=datetime(2026, 8, 22, 10, 20, tzinfo=UTC),
    )
    assert stale["state"] == "STALE"

    connection = sqlite3.connect(database)
    with connection:
        connection.execute(
            """UPDATE dashboard_optional_read_models_v1
                  SET payload_hash='broken' WHERE resource='learning'"""
        )
    with pytest.raises(DashboardReadModelUnavailable, match="corrupt"):
        read_dashboard_read_model(database, "learning")
    with connection:
        connection.execute(
            """UPDATE dashboard_optional_read_models_v1
                  SET contract_version='old-contract' WHERE resource='market_chart'"""
        )
    connection.close()
    with pytest.raises(DashboardReadModelUnavailable, match="contract mismatch"):
        read_dashboard_read_model(database, "market_chart")


def test_retry_operator_bridge_lists_and_atomically_applies_idempotent_override(
    monkeypatch, tmp_path,
) -> None:
    from xauusd_forecaster.news.scheduler.state import (
        ROUTINE_POOL, backoff_job, claim_job, enqueue_job,
    )

    module = _dashboard_module()
    bridge_token = "test-operator-bridge-token-" + "x" * 32
    monkeypatch.setenv("DASHBOARD_OPERATOR_BRIDGE_TOKEN", bridge_token)
    now = datetime.now(UTC).replace(microsecond=0)
    database = tmp_path / "forward.sqlite3"
    ForwardLedger(database, now=now).close()
    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    try:
        job_id = enqueue_job(
            connection, task_type="ACTIVE_IMPACT", source="source",
            source_item_id="operator-job", revision_number=1,
            annotation_id="annotation", prompt_version="prompt",
            priority="NORMAL", now=now,
        )
        claimed = claim_job(
            connection, worker_id="worker", pool=ROUTINE_POOL, now=now,
        )
        assert claimed and claimed.job_id == job_id
        backoff_job(
            connection, job_id, "worker", available_at=now + timedelta(hours=4),
            error="ConnectionResetError",
        )
        observed = connection.execute(
            "SELECT state,available_at,attempt_count FROM news_ai_jobs_v1 WHERE job_id=?",
            (job_id,),
        ).fetchone()
    finally:
        connection.close()

    module.Handler.database = database
    module.Handler.status_cache = module.StatusSnapshotCache()
    server = module.ThreadingHTTPServer(("127.0.0.1", 0), module.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{server.server_port}"
        read_request = urllib.request.Request(
            f"{base}/api/retry-jobs",
            headers={"X-Aurum-Operator-Bridge-Token": bridge_token},
        )
        with urllib.request.urlopen(read_request, timeout=2) as response:
            item = json.loads(response.read())["items"][0]
        assert item["job_id"] == job_id
        assert item["last_error"] == "ConnectionResetError"
        payload = {
            "operator_id": "cloudflare-access:owner",
            "items": [{
                "request_id": "request-idempotent", "job_id": job_id,
                "mode": "IMMEDIATE", "reason": "repair deployed",
                "expected_state": observed["state"],
                "expected_available_at": observed["available_at"],
            }],
        }
        request = lambda: urllib.request.Request(
            f"{base}/api/retry-overrides", method="POST",
            headers={
                "Content-Type": "application/json",
                "X-Aurum-Operator-Bridge-Token": bridge_token,
            },
            data=json.dumps(payload).encode(),
        )
        with urllib.request.urlopen(request(), timeout=2) as response:
            first = json.loads(response.read())["results"][0]
        with urllib.request.urlopen(request(), timeout=2) as response:
            duplicate = json.loads(response.read())["results"][0]
        assert first["status"] == duplicate["status"] == "APPLIED"

        connection = sqlite3.connect(database)
        connection.row_factory = sqlite3.Row
        try:
            current = connection.execute(
                "SELECT state,attempt_count,last_error FROM news_ai_jobs_v1 WHERE job_id=?",
                (job_id,),
            ).fetchone()
            audits = connection.execute(
                "SELECT count(*) FROM news_ai_retry_schedule_overrides_v1 WHERE job_id=?",
                (job_id,),
            ).fetchone()[0]
        finally:
            connection.close()
        assert tuple(current) == ("BACKING_OFF", observed["attempt_count"], "ConnectionResetError")
        assert audits == 1
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=2)


def test_retry_operator_bridge_requires_both_loopback_and_dedicated_credential(
    monkeypatch, tmp_path,
) -> None:
    module = _dashboard_module()
    bridge_token = "test-operator-bridge-token-" + "y" * 32
    monkeypatch.setenv("DASHBOARD_OPERATOR_BRIDGE_TOKEN", bridge_token)
    database = tmp_path / "forward.sqlite3"
    ForwardLedger(database, now=datetime.now(UTC)).close()
    module.Handler.database = database
    server = module.ThreadingHTTPServer(("127.0.0.1", 0), module.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"

    def status(request: urllib.request.Request) -> int:
        for attempt in range(3):
            try:
                urllib.request.urlopen(request, timeout=2)
            except urllib.error.HTTPError as error:
                return error.code
            except (ConnectionAbortedError, ConnectionResetError):
                if attempt == 2:
                    raise
                continue
            break
        raise AssertionError("bridge request should have failed")

    try:
        assert status(urllib.request.Request(f"{base}/api/retry-jobs")) == 401
        assert status(urllib.request.Request(
            f"{base}/api/retry-jobs",
            headers={"X-Aurum-Operator-Bridge-Token": "wrong" * 10},
        )) == 401
        payload = b'{"items":[]}'
        assert status(urllib.request.Request(
            f"{base}/api/retry-overrides", method="POST", data=payload,
            headers={
                "Content-Type": "application/json",
                "X-Aurum-Operator-Bridge-Token": bridge_token,
                "Origin": "https://attacker.example",
            },
        )) == 403
        assert status(urllib.request.Request(
            f"{base}/api/retry-overrides", method="POST", data=payload,
            headers={"X-Aurum-Operator-Bridge-Token": bridge_token},
        )) == 415
        assert status(urllib.request.Request(
            f"{base}/api/retry-overrides", method="POST", data=b"{" + b"x" * 100_001,
            headers={
                "Content-Type": "application/json",
                "X-Aurum-Operator-Bridge-Token": bridge_token,
            },
        )) == 400

        direct = type("DirectRequest", (), {
            "client_address": ("192.0.2.8", 1234),
            "headers": {"X-Aurum-Operator-Bridge-Token": bridge_token},
        })()
        assert module.Handler._operator_bridge_auth_error(direct)[0] == 403
        connection = sqlite3.connect(database)
        try:
            assert connection.execute(
                "SELECT count(*) FROM news_ai_retry_schedule_overrides_v1"
            ).fetchone()[0] == 0
        finally:
            connection.close()
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=2)


def test_dashboard_status_does_not_scan_live_database_integrity(
    monkeypatch, tmp_path,
) -> None:
    module = _dashboard_module()
    now = datetime.now(UTC).replace(microsecond=0)
    database = tmp_path / "forward.sqlite3"
    ForwardLedger(database, now=now).close()
    real_connect = module.sqlite3.connect
    statements: list[str] = []

    def tracked_connect(target, *args, **kwargs):
        connection = real_connect(target, *args, **kwargs)
        if str(database) in str(target):
            connection.set_trace_callback(statements.append)
        return connection

    monkeypatch.setattr(module.sqlite3, "connect", tracked_connect)
    module._dashboard_payload(database)

    assert not any("integrity_check" in statement.lower() for statement in statements)


def test_critical_summary_reads_stay_fixed_as_append_only_history_grows(
    tmp_path,
) -> None:
    module = _dashboard_module()
    database = tmp_path / "forward.sqlite3"
    ledger = ForwardLedger(database, now=datetime(2026, 8, 19, tzinfo=UTC))
    rows = [(
        "fixture", f"item-{index}", 1, None,
        f"2026-08-19T10:{index % 60:02d}:00+00:00",
        f"2026-08-19T10:{index % 60:02d}:00+00:00",
        f"2026-08-19T10:{index % 60:02d}:00+00:00",
        f"headline {index}", "body" * 80, f"https://example.test/{index}",
        f"{index:064x}", f"cluster-{index}", None,
    ) for index in range(2_500)]
    with ledger.connection:
        ledger.connection.executemany(
            "INSERT INTO news_revisions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", rows,
        )
    statements: list[str] = []
    ledger.connection.set_trace_callback(statements.append)

    counts = dashboard_table_counts(ledger.connection)
    articles = dashboard_distinct_article_count(ledger.connection)
    source = dashboard_news_source_summary(ledger.connection, ("fixture",))

    assert counts["news_revisions"] == len(rows)
    assert articles == len(rows)
    assert source == {
        "item_count": len(rows),
        "revision_count": len(rows),
        "full_text_count": 0,
        "latest_item_time": "2026-08-19T10:59:00+00:00",
    }
    reads = [
        " ".join(statement.lower().split()) for statement in statements
        if statement.lstrip().upper().startswith("SELECT")
    ]
    assert len(reads) == 3
    assert "from dashboard_table_counts_v1" in reads[0]
    assert "from dashboard_news_article_summary_v1" in reads[1]
    assert "from dashboard_news_source_summary_v1" in reads[2]
    assert all("count(" not in statement for statement in reads)
    statements.clear()
    install_dashboard_summary_schema(ledger.connection)
    assert not any(
        "count(" in statement.lower() or "sum(" in statement.lower()
        for statement in statements
    )
    ledger.close()


def test_critical_builder_uses_bounded_u5_window_and_materialized_counts(
    monkeypatch, tmp_path,
) -> None:
    module = _dashboard_module()
    now = datetime(2026, 8, 19, 10, 0, tzinfo=UTC)
    database = tmp_path / "forward.sqlite3"
    ledger = ForwardLedger(database, now=now)
    historical_jobs = [(
        f"historical-{index}", "ACTIVE_ANNOTATION", "fixture",
        f"item-{index}", 1, "", "historical-prompt", "NORMAL", "COMPLETED",
        now.isoformat(), None, None, 1, None, now.isoformat(), now.isoformat(),
        now.isoformat(),
    ) for index in range(4_000)]
    with ledger.connection:
        ledger.connection.executemany(
            """INSERT INTO news_ai_jobs_v1
               (job_id,task_type,source,source_item_id,revision_number,annotation_id,
                prompt_version,priority,state,available_at,lease_owner,
                lease_expires_at,attempt_count,last_error,created_at,updated_at,
                completed_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            historical_jobs,
        )
    ledger.close()
    real_connect = module.sqlite3.connect
    statements: list[str] = []

    def tracked_connect(target, *args, **kwargs):
        connection = real_connect(target, *args, **kwargs)
        if str(database) in str(target):
            connection.set_trace_callback(statements.append)
        return connection

    monkeypatch.setattr(module.sqlite3, "connect", tracked_connect)
    payload = module._dashboard_payload(database, clock=lambda: now, include_optional=False)

    u5_query = next(
        statement for statement in statements
        if "select u5 from market_snapshots" in statement.lower()
    )
    assert f"LIMIT {module.U5_CONTEXT_SAMPLE_LIMIT}" in u5_query
    assert payload["u5_context"]["sample_limit"] == module.U5_CONTEXT_SAMPLE_LIMIT
    assert payload["u5_context"]["scope"] == "RECENT_READY_WINDOW"
    assert any(
        "from dashboard_table_counts_v1" in statement.lower()
        for statement in statements
    )
    assert any(
        "from dashboard_news_source_summary_v1" in statement.lower()
        for statement in statements
    )
    unbounded_counts = [
        " ".join(statement.lower().split()) for table in DASHBOARD_COUNT_TABLES
        for statement in statements
        if " ".join(statement.lower().split()) == f"select count(*) from {table}"
    ]
    assert not unbounded_counts, unbounded_counts
    normalized = [" ".join(statement.lower().split()) for statement in statements]
    assert not any(
        statement.startswith("select model_identity, model_version, created_at,")
        and "from model_updates order by" in statement
        for statement in normalized
    )
    assert not any(
        "from news_revisions where source in" in statement
        and ("count(" in statement or "max(" in statement)
        for statement in normalized
    )
    assert "select max(created_at) from decision_events" not in normalized
    job_reads = [
        statement for statement in normalized
        if "from news_ai_jobs_v1" in statement
    ]
    assert job_reads
    assert all(
        " state in (" in statement
        or " state='" in statement
        or " j.state in (" in statement
        or " j.state='" in statement
        for statement in job_reads
    ), job_reads


def test_critical_status_owns_bounded_recent_decisions_and_live_oos_count(
    monkeypatch, tmp_path,
) -> None:
    module = _dashboard_module()
    now = datetime(2026, 8, 23, 2, 0, tzinfo=UTC)
    database = tmp_path / "forward.sqlite3"
    ledger = ForwardLedger(database, now=now)
    ledger.close()
    for index in range(20):
        _append_decision_at(
            database, now - timedelta(minutes=5 * index), identifier=f"-{index}",
        )

    real_connect = module.sqlite3.connect
    statements: list[str] = []

    def tracked_connect(target, *args, **kwargs):
        connection = real_connect(target, *args, **kwargs)
        if str(database) in str(target):
            connection.set_trace_callback(statements.append)
        return connection

    monkeypatch.setattr(module.sqlite3, "connect", tracked_connect)
    payload = module._dashboard_payload(
        database, clock=lambda: now, include_optional=False,
    )
    critical = module.critical_status_payload(payload)

    assert len(critical["recent_decisions"]) == 18
    assert [row["decision_id"] for row in critical["recent_decisions"][:2]] == [
        "decision-0", "decision-1",
    ]
    assert all("features" not in row for row in critical["recent_decisions"])
    assert all("predictions" not in row for row in critical["recent_decisions"])
    assert critical["counts"]["live_oos_model_groups"] == 0
    normalized = [" ".join(statement.lower().split()) for statement in statements]
    decision_reads = [
        statement for statement in statements
        if "from decision_events d join market_snapshots"
        in " ".join(statement.lower().split())
    ]
    bounded_decision_reads = [
        statement for statement in decision_reads if "limit 18" in statement.lower()
    ]
    assert bounded_decision_reads
    assert not any("features_json" in statement for statement in bounded_decision_reads)
    assert not any("row_number() over" in statement for statement in normalized)
    with real_connect(database) as connection:
        plan = [
            str(row[3]) for row in connection.execute(
                "EXPLAIN QUERY PLAN " + bounded_decision_reads[0]
            )
        ]
    assert any("source_decision_id=?" in step for step in plan)
    assert not any("prediction_v2_time" in step for step in plan)


def test_critical_status_returns_every_available_decision_below_window(tmp_path) -> None:
    module = _dashboard_module()
    now = datetime(2026, 8, 23, 2, 0, tzinfo=UTC)
    database = tmp_path / "forward.sqlite3"
    ledger = ForwardLedger(database, now=now)
    ledger.close()
    for index in range(2):
        _append_decision_at(
            database, now - timedelta(minutes=5 * index), identifier=f"-{index}",
        )

    payload = module._dashboard_payload(
        database, clock=lambda: now, include_optional=False,
    )
    critical = module.critical_status_payload(payload)

    assert [row["decision_id"] for row in critical["recent_decisions"]] == [
        "decision-0", "decision-1",
    ]
    assert critical["mirror_window"] == {
        "bounded": True,
        "critical_only": True,
        "audit_embedded": False,
        "growing_collections_embedded": False,
    }




def test_forward_ledger_adds_dashboard_news_lookup_indexes(tmp_path) -> None:
    now = datetime.now(UTC).replace(microsecond=0)
    ledger = ForwardLedger(tmp_path / "forward.sqlite3", now=now)
    try:
        annotation_indexes = {
            row[1] for row in ledger.connection.execute(
                "PRAGMA index_list(news_annotations)"
            )
        }
        revision_indexes = {
            row[1] for row in ledger.connection.execute(
                "PRAGMA index_list(news_revisions)"
            )
        }
    finally:
        ledger.close()

    assert "news_annotations_revision_contract" in annotation_indexes
    assert "news_revisions_cluster_latest" in revision_indexes


def test_dashboard_prefers_valid_title_over_later_placeholder(tmp_path) -> None:
    now = datetime.now(UTC).replace(microsecond=0)
    database = tmp_path / "forward.sqlite3"
    ledger = ForwardLedger(database, now=now)
    body = "full evidence body " * 30
    digest = hashlib.sha256(body.encode()).hexdigest()
    ledger.append_news_revision(
        {
            "source": "bea_economic_releases", "source_item_id": "release",
            "collector_first_seen_time": now, "fetched_time": now,
            "headline": "Personal Income and Outlays, June 2026", "body": body,
            "content_hash": digest, "cluster_id": "release",
        }
    )
    common = {
        "source": "bea_economic_releases", "source_item_id": "release",
        "revision_number": 1, "raw_content_hash": digest,
        "llm_model_version": "gemini-3.5-flash-lite",
        "parse_started_at": now,
    }
    ledger.append_title_translation(
        {
            **common, "translation_id": "valid", "headline_zh": "2026年6月个人收入与支出",
            "prompt_version": "headline-zh-v1", "parsed_at": now,
        }
    )
    ledger.append_title_translation(
        {
            **common, "translation_id": "placeholder",
            "headline_zh": INVALID_CHINESE_TITLE,
            "prompt_version": "headline-zh-placeholder-test",
            "parsed_at": now + timedelta(seconds=1),
        }
    )
    _append_basic_annotation(
        ledger,
        source="bea_economic_releases",
        item_id="release",
        digest=digest,
        parsed_at=now + timedelta(seconds=2),
    )
    ledger.connection.close()

    sync_success = now.isoformat()
    (tmp_path / "dashboard-sync-status.json").write_text(
        json.dumps({"last_success": sync_success, "last_error": None}),
        encoding="utf-8",
    )

    payload = _dashboard_module()._dashboard_payload(database)
    assert payload["recent_news"][0]["headline"] == "2026年6月个人收入与支出"
    assert {row["source"] for row in payload["news_source_health"]} == {
        spec.source for spec in NEWS_SOURCE_REGISTRY
    }
    assert all(
        row["source"] not in {
            "non_fed_full_text",
            "bls_employment_situation",
            "bls_consumer_price_index",
            "bls_job_openings",
            "google_news_bls_official_releases",
        }
        for row in payload["news_source_health"]
    )
    synchronizer = payload["system"]["components"]["sites_synchronizer"]
    assert synchronizer["last_success"] == sync_success
    assert synchronizer["status"] == "OK"


def test_public_source_403_does_not_claim_credentials_are_broken(tmp_path) -> None:
    now = datetime.now(UTC).replace(microsecond=0)
    ledger = ForwardLedger(tmp_path / "forward.sqlite3", now=now)
    ledger.append_source_poll({
        "poll_id": "eia-error", "source": "eia_press_releases",
        "fetched_time": now, "status": "ERROR",
        "error_type": "RemoteAccessRejected",
        "error": "HTTP Error 403: Forbidden",
        "provider_http_status": 403,
    })

    rows = _dashboard_module()._news_source_health(ledger.connection, now)
    direct = next(row for row in rows if row["source"] == "eia_press_releases")

    assert direct["health"] == "ERROR"
    assert direct["semantic_status"] == "SOURCE_ERROR"
    assert direct["recovery_mode"] == "AUTO_RECOVERING"
    assert direct["next_retry_time"] == (now + timedelta(minutes=5)).isoformat()
    assert "凭据" not in str(direct["semantic_message"])


def test_fresh_source_success_with_transient_failure_is_auto_recovering(
    tmp_path,
) -> None:
    now = datetime.now(UTC).replace(microsecond=0)
    ledger = ForwardLedger(tmp_path / "forward.sqlite3", now=now)
    ledger.append_source_poll({
        "poll_id": "eia-ok", "source": "eia_open_data_api",
        "fetched_time": now - timedelta(minutes=10), "status": "OK",
    })
    ledger.append_source_poll({
        "poll_id": "eia-timeout", "source": "eia_open_data_api",
        "fetched_time": now, "status": "ERROR", "error_type": "TimeoutError",
        "error": "The read operation timed out",
    })

    rows = _dashboard_module()._news_source_health(ledger.connection, now)
    eia = next(row for row in rows if row["source"] == "eia_open_data_api")

    assert eia["health"] == "DEGRADED"
    assert eia["semantic_status"] == "AUTO_RECOVERING"
    assert eia["recovery_mode"] == "AUTO_RECOVERING"
    assert eia["age_seconds"] == 600
    assert eia["next_retry_time"] == (now + timedelta(minutes=5)).isoformat()


def test_transient_source_failure_escalates_when_last_success_is_stale(
    tmp_path,
) -> None:
    now = datetime.now(UTC).replace(microsecond=0)
    ledger = ForwardLedger(tmp_path / "forward.sqlite3", now=now)
    ledger.append_source_poll({
        "poll_id": "eia-old-ok", "source": "eia_open_data_api",
        "fetched_time": now - timedelta(hours=2), "status": "OK",
    })
    ledger.append_source_poll({
        "poll_id": "eia-timeout", "source": "eia_open_data_api",
        "fetched_time": now, "status": "ERROR", "error_type": "TimeoutError",
        "error": "The read operation timed out",
    })

    rows = _dashboard_module()._news_source_health(ledger.connection, now)
    eia = next(row for row in rows if row["source"] == "eia_open_data_api")

    assert eia["health"] == "STALE"
    assert eia["semantic_status"] == "SOURCE_ERROR"


def test_newer_partial_is_the_freshness_reference_but_keeps_recovery_active(
    tmp_path,
) -> None:
    now = datetime(2026, 8, 18, 4, 30, tzinfo=UTC)
    ok_at = datetime(2026, 8, 18, 3, 0, tzinfo=UTC)
    partial_at = datetime(2026, 8, 18, 4, 20, tzinfo=UTC)
    ledger = ForwardLedger(tmp_path / "forward.sqlite3", now=now)
    ledger.append_source_poll({
        "poll_id": "eia-old-ok", "source": "eia_open_data_api",
        "fetched_time": ok_at, "status": "OK",
    })
    ledger.append_source_poll({
        "poll_id": "eia-new-partial", "source": "eia_open_data_api",
        "fetched_time": partial_at, "status": "PARTIAL",
        "error_type": "SeriesErrors", "error": "one sibling failed",
    })

    rows = _dashboard_module()._news_source_health(ledger.connection, now)
    eia = next(row for row in rows if row["source"] == "eia_open_data_api")

    assert datetime.fromisoformat(eia["last_success"]) == ok_at
    assert datetime.fromisoformat(eia["freshness_reference_time"]) == partial_at
    assert eia["freshness_reference_status"] == "PARTIAL"
    assert eia["age_seconds"] == 600
    assert eia["health"] == "DEGRADED"
    assert eia["recovery_mode"] == "PARTIAL_RECOVERY"
    assert eia["next_retry_time"] == (
        partial_at + timedelta(minutes=5)
    ).isoformat()


def test_newer_complete_success_wins_over_older_partial(tmp_path) -> None:
    now = datetime(2026, 8, 18, 4, 30, tzinfo=UTC)
    partial_at = datetime(2026, 8, 18, 3, 0, tzinfo=UTC)
    ok_at = datetime(2026, 8, 18, 4, 20, tzinfo=UTC)
    ledger = ForwardLedger(tmp_path / "forward.sqlite3", now=now)
    ledger.append_source_poll({
        "poll_id": "eia-old-partial", "source": "eia_open_data_api",
        "fetched_time": partial_at, "status": "PARTIAL",
        "error_type": "SeriesErrors", "error": "historical partial",
    })
    ledger.append_source_poll({
        "poll_id": "eia-new-ok", "source": "eia_open_data_api",
        "fetched_time": ok_at, "status": "OK",
    })

    rows = _dashboard_module()._news_source_health(ledger.connection, now)
    eia = next(row for row in rows if row["source"] == "eia_open_data_api")

    assert datetime.fromisoformat(eia["last_success"]) == ok_at
    assert datetime.fromisoformat(eia["freshness_reference_time"]) == ok_at
    assert eia["freshness_reference_status"] == "OK"
    assert eia["age_seconds"] == 600
    assert eia["health"] == "HEALTHY"
    assert eia["recovery_mode"] is None


@pytest.mark.parametrize(
    ("age", "expected_health"),
    ((timedelta(minutes=2), "DEGRADED"), (timedelta(hours=2), "STALE")),
)
def test_partial_without_historical_ok_has_factual_freshness(
    tmp_path, age, expected_health,
) -> None:
    now = datetime.now(UTC).replace(microsecond=0)
    partial_at = now - age
    ledger = ForwardLedger(tmp_path / "forward.sqlite3", now=now)
    ledger.append_source_poll({
        "poll_id": "eia-partial", "source": "eia_open_data_api",
        "fetched_time": partial_at, "status": "PARTIAL",
        "error_type": "SeriesErrors", "error": "one sibling failed",
    })

    rows = _dashboard_module()._news_source_health(ledger.connection, now)
    eia = next(row for row in rows if row["source"] == "eia_open_data_api")

    assert eia["last_success"] is None
    assert datetime.fromisoformat(eia["freshness_reference_time"]) == partial_at
    assert eia["freshness_reference_status"] == "PARTIAL"
    assert eia["health"] == expected_health
    assert eia["recovery_mode"] == "PARTIAL_RECOVERY"


def test_transient_failure_after_fresh_partial_remains_auto_recovering(
    tmp_path,
) -> None:
    now = datetime(2026, 8, 18, 4, 30, tzinfo=UTC)
    partial_at = now - timedelta(minutes=2)
    ledger = ForwardLedger(tmp_path / "forward.sqlite3", now=now)
    ledger.append_source_poll({
        "poll_id": "eia-partial", "source": "eia_open_data_api",
        "fetched_time": partial_at, "status": "PARTIAL",
        "error_type": "SeriesErrors", "error": "one sibling failed",
    })
    ledger.append_source_poll({
        "poll_id": "eia-timeout", "source": "eia_open_data_api",
        "fetched_time": now, "status": "ERROR",
        "error_type": "TimeoutError", "error": "transport timed out",
    })

    rows = _dashboard_module()._news_source_health(ledger.connection, now)
    eia = next(row for row in rows if row["source"] == "eia_open_data_api")

    assert eia["last_success"] is None
    assert datetime.fromisoformat(eia["freshness_reference_time"]) == partial_at
    assert eia["freshness_reference_status"] == "PARTIAL"
    assert eia["age_seconds"] == 120
    assert eia["health"] == "DEGRADED"
    assert eia["semantic_status"] == "AUTO_RECOVERING"
    assert eia["recovery_mode"] == "AUTO_RECOVERING"


@pytest.mark.parametrize("source", [spec.source for spec in NEWS_SOURCE_REGISTRY])
def test_every_monitored_source_clears_active_failure_state_after_success(
    tmp_path,
    source,
) -> None:
    now = datetime.now(UTC).replace(microsecond=0)
    ledger = ForwardLedger(tmp_path / "forward.sqlite3", now=now)
    ledger.append_source_poll({
        "poll_id": f"{source}-old-error", "source": source,
        "fetched_time": now - timedelta(minutes=5), "status": "ERROR",
        "error_type": "HTTPError", "error": "HTTP Error 429: historical",
    })
    ledger.append_source_poll({
        "poll_id": f"{source}-recovered", "source": source,
        "fetched_time": now, "status": "OK",
    })

    rows = _dashboard_module()._news_source_health(ledger.connection, now)
    recovered = next(row for row in rows if row["source"] == source)

    assert recovered["latest_status"] == "OK"
    assert recovered["health"] not in {"ERROR", "DEGRADED", "FALLBACK_ACTIVE"}
    assert recovered["recovery_mode"] is None
    assert recovered["next_retry_time"] is None
    assert "429" in recovered["last_error"]


def test_polled_release_source_without_items_is_not_reported_healthy(tmp_path) -> None:
    now = datetime.now(UTC).replace(microsecond=0)
    ledger = ForwardLedger(tmp_path / "forward.sqlite3", now=now)
    ledger.append_source_poll({
        "poll_id": "bea-empty-ok", "source": "bea_economic_releases",
        "fetched_time": now, "status": "OK",
    })

    rows = _dashboard_module()._news_source_health(ledger.connection, now)
    bea = next(row for row in rows if row["source"] == "bea_economic_releases")
    assert bea["health"] == "WARMING_UP"
    assert bea["semantic_status"] == "NO_RELEASE_CAPTURED"
    assert bea["item_count"] == 0


def test_dashboard_orders_news_by_publisher_time_not_discovery_time(tmp_path) -> None:
    now = datetime(2026, 8, 6, 6, 0, tzinfo=UTC)
    database = tmp_path / "forward.sqlite3"
    ledger = ForwardLedger(database, now=now)

    for item_id, published_at, first_seen in (
        ("visible-first", now - timedelta(minutes=5), now - timedelta(minutes=20)),
        ("arrived-first", now - timedelta(days=2), now - timedelta(minutes=1)),
    ):
        body = f"full evidence for {item_id} " * 30
        digest = hashlib.sha256(body.encode()).hexdigest()
        ledger.append_news_revision(
            {
                "source": "bea_economic_releases",
                "source_item_id": item_id,
                "source_published_time": published_at,
                "collector_first_seen_time": first_seen,
                "fetched_time": first_seen,
                "headline": item_id,
                "body": body,
                "content_hash": digest,
                "cluster_id": item_id,
            }
        )
        _append_basic_annotation(
            ledger,
            source="bea_economic_releases",
            item_id=item_id,
            digest=digest,
            parsed_at=first_seen + timedelta(seconds=1),
        )
    ledger.connection.close()

    payload = _dashboard_module()._dashboard_payload(database)

    assert [row["source_item_id"] for row in payload["recent_news"][:2]] == [
        "visible-first",
        "arrived-first",
    ]




def test_news_projection_source_freezes_until_exact_snapshot_is_activated(tmp_path) -> None:
    from xauusd_forecaster.dashboard import news_resources as module
    module._NEWS_PROJECTION_CACHE.clear()
    now = datetime.now(UTC).replace(microsecond=0)
    ledger = ForwardLedger(tmp_path / "forward.sqlite3", now=now)

    def append(item_id: str) -> None:
        body = f"complete projection evidence for {item_id} " * 30
        ledger.append_news_revision({
            "source": "bea_economic_releases", "source_item_id": item_id,
            "source_published_time": now, "collector_first_seen_time": now,
            "fetched_time": now, "headline": item_id, "body": body,
            "content_hash": hashlib.sha256(body.encode()).hexdigest(),
            "cluster_id": item_id,
        })

    append("first")
    frozen = module._news_projection_source(ledger.connection, None)
    append("second")
    retry = module._news_projection_source(ledger.connection, None)

    assert retry is frozen
    assert frozen.manifest["expected_index_count"] == 1
    assert sum(map(len, frozen.detail_batches)) == 1
    assert sum(map(len, frozen.index_batches)) == 1

    replacement = module._news_projection_source(
        ledger.connection, frozen.manifest["snapshot_id"],
    )
    assert replacement.manifest["snapshot_id"] != frozen.manifest["snapshot_id"]
    assert replacement.manifest["expected_index_count"] == 2
    assert replacement.manifest["expected_detail_count"] == 2
    assert replacement.manifest["expected_receipt_digest"] != frozen.manifest[
        "expected_receipt_digest"
    ]


def test_news_projection_scans_candidate_universe_once_across_detail_pages(
    monkeypatch,
) -> None:
    from xauusd_forecaster.dashboard import news_resources as module
    frozen_context = ("epoch", set())
    candidate_keys = [
        ("example", f"item-{index}", 1, f"2026-08-24T00:{index:03d}:00+00:00")
        for index in range(1_001)
    ]
    context_calls = 0
    candidate_calls = 0
    detail_page_sizes: list[int] = []

    def context(_connection, _now):
        nonlocal context_calls
        context_calls += 1
        return frozen_context

    def candidates(_connection, *, cutoff, after, limit):
        nonlocal candidate_calls
        candidate_calls += 1
        assert after is None
        assert limit == module.NEWS_PROJECTION_MAX_ITEMS + 1
        return candidate_keys

    def rows(_connection, _now, *, after=None, limit, candidate_keys=None):
        assert after is None
        assert candidate_keys is not None
        assert limit == len(candidate_keys)
        detail_page_sizes.append(limit)
        return [{
            "source": source, "source_item_id": item_id,
            "revision_number": revision, "cluster_id": item_id,
            "collector_first_seen_time": updated,
        } for source, item_id, revision, updated in candidate_keys]

    def serialize(rows, _now, epoch, claimable):
        assert epoch == frozen_context[0]
        assert claimable is frozen_context[1]
        return rows

    monkeypatch.setattr(module, "_news_archive_context", context)
    monkeypatch.setattr(module, "_news_mirror_candidate_keys", candidates)
    monkeypatch.setattr(module, "_news_reader_rows", rows)
    monkeypatch.setattr(module, "_serialize_news_rows", serialize)

    generation = module._build_news_projection_source(object())

    assert context_calls == 1
    assert candidate_calls == 1
    assert detail_page_sizes == [1_000, 1]
    assert generation.manifest["expected_index_count"] == 1_001


def test_news_projection_request_starts_one_background_build(monkeypatch, tmp_path) -> None:
    from xauusd_forecaster.dashboard import news_resources as module
    module._NEWS_PROJECTION_CACHE.clear()
    generation = __import__(
        "xauusd_forecaster.news_projection", fromlist=["build_news_projection_generation"],
    ).build_news_projection_generation(
        [], [], window_start="2026-06-25T00:00:00+00:00",
        watermark="2026-08-24T00:00:00+00:00",
    )
    pending_threads = []

    class DeferredThread:
        def __init__(self, *, target, args, name, daemon):
            assert name == "news-projection-source"
            assert daemon is True
            self.target = target
            self.args = args
            pending_threads.append(self)

        def start(self):
            return None

    monkeypatch.setattr(module.threading, "Thread", DeferredThread)
    monkeypatch.setattr(
        module, "_build_news_projection_source_from_database",
        lambda _database: generation,
    )

    with pytest.raises(module.NewsProjectionSourcePending):
        module._news_projection_source_for_request(tmp_path / "db.sqlite3", None)
    with pytest.raises(module.NewsProjectionSourcePending):
        module._news_projection_source_for_request(tmp_path / "db.sqlite3", None)
    assert len(pending_threads) == 1

    pending_threads[0].target(*pending_threads[0].args)

    assert module._news_projection_source_for_request(
        tmp_path / "db.sqlite3", None,
    ) is generation

    module._NEWS_PROJECTION_CACHE["built_at"] = (
        time.monotonic() - module.NEWS_PROJECTION_SOURCE_REFRESH_SECONDS - 1
    )
    assert module._news_projection_source_for_request(
        tmp_path / "db.sqlite3", generation.manifest["snapshot_id"],
    ) is generation
    assert len(pending_threads) == 2
    assert module._news_projection_source_for_request(
        tmp_path / "db.sqlite3", generation.manifest["snapshot_id"],
    ) is generation
    assert len(pending_threads) == 2
    module._NEWS_PROJECTION_CACHE.clear()


def test_news_projection_manifest_is_authorized_and_nonblocking(
    monkeypatch, tmp_path,
) -> None:
    module = _dashboard_module()
    from xauusd_forecaster.dashboard import news_resources as owner
    owner._NEWS_PROJECTION_CACHE.clear()
    module.Handler.database = tmp_path / "forward.sqlite3"
    token = "operator-bridge-" + "x" * 32
    monkeypatch.setenv("DASHBOARD_OPERATOR_BRIDGE_TOKEN", token)
    generation = __import__(
        "xauusd_forecaster.news_projection", fromlist=["build_news_projection_generation"],
    ).build_news_projection_generation(
        [], [], window_start="2026-06-25T00:00:00+00:00",
        watermark="2026-08-24T00:00:00+00:00",
    )
    started = threading.Event()
    release = threading.Event()

    def build(_database):
        started.set()
        assert release.wait(timeout=2)
        return generation

    monkeypatch.setattr(owner, "_build_news_projection_source_from_database", build)
    server = module.ThreadingHTTPServer(("127.0.0.1", 0), module.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_port}/api/news-archive?mode=manifest"
    try:
        with pytest.raises(urllib.error.HTTPError) as unauthorized:
            urllib.request.urlopen(url, timeout=2)
        assert unauthorized.value.code == 401

        request = urllib.request.Request(
            url, headers={"X-Aurum-Operator-Bridge-Token": token},
        )
        with pytest.raises(urllib.error.HTTPError) as warming:
            urllib.request.urlopen(request, timeout=2)
        assert warming.value.code == 503
        assert warming.value.headers["Retry-After"] == "30"
        payload = json.loads(warming.value.read())
        assert payload == {
            "error": "news projection source is building",
            "error_code": "NEWS_PROJECTION_SOURCE_BUILDING",
            "projection_state": "REPLAYING",
        }
        assert started.wait(timeout=1)
        release.set()
        for _ in range(100):
            if owner._NEWS_PROJECTION_CACHE.get("building") is False:
                break
            time.sleep(0.01)

        with urllib.request.urlopen(request, timeout=2) as response:
            ready = json.loads(response.read())
        assert response.status == 200
        assert ready["manifest"] == generation.manifest
    finally:
        release.set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_news_projection_source_rejects_non_batch_offsets(tmp_path) -> None:
    from xauusd_forecaster.dashboard import news_resources as module
    module._NEWS_PROJECTION_CACHE.clear()
    generation = __import__(
        "xauusd_forecaster.news_projection", fromlist=["build_news_projection_generation"],
    ).build_news_projection_generation(
        [{
            "source": "example", "source_item_id": str(index), "revision_number": 1,
            "category": "其他", "cluster_id": str(index),
            "collector_first_seen_time": "2026-08-24T00:00:00+00:00",
        } for index in range(10)], [],
        window_start="2026-06-25T00:00:00+00:00",
        watermark="2026-08-24T00:00:00+00:00",
    )

    first = module._news_projection_batch(generation, "detail", 0)
    assert len(first["items"]) == 8
    with pytest.raises(ValueError, match="frozen batch boundary"):
        module._news_projection_batch(generation, "detail", 1)


def test_news_projection_accepts_a_large_realistic_article_within_worker_bound() -> None:
    projection = __import__(
        "xauusd_forecaster.news_projection", fromlist=["build_news_projection_generation"],
    )
    generation = projection.build_news_projection_generation(
        [{
            "source": "example", "source_item_id": "large", "revision_number": 1,
            "cluster_id": "large", "body": "x" * 175_000,
            "collector_first_seen_time": "2026-08-24T00:00:00+00:00",
        }], [], window_start="2026-06-25T00:00:00+00:00",
        watermark="2026-08-24T00:00:00+00:00",
    )

    encoded = projection.compact_json(list(generation.detail_batches[0])).encode()
    assert len(encoded) <= projection.NEWS_DETAIL_BATCH_LIMIT_BYTES
    assert projection.NEWS_DETAIL_BATCH_LIMIT_BYTES == 400_000


def test_news_archive_discovers_a_bounded_changed_key_page(tmp_path) -> None:
    module = _dashboard_module()
    now = datetime.now(UTC).replace(microsecond=0)
    ledger = ForwardLedger(tmp_path / "forward.sqlite3", now=now)
    body = "complete bounded mirror evidence " * 20
    digest = hashlib.sha256(body.encode()).hexdigest()
    ledger.connection.executemany(
        """INSERT INTO news_revisions VALUES
           (?,?,1,NULL,?,?,?,?,?,NULL,?,?,NULL)""",
        [
            (
                "bea_economic_releases", f"item-{index:03d}",
                now.isoformat(), now.isoformat(), now.isoformat(),
                f"headline {index}", body, digest, f"cluster-{index}",
            )
            for index in range(250)
        ],
    )
    ledger.connection.commit()
    cursor = json.dumps([
        now.isoformat(), "bea_economic_releases", "item-099", 1,
    ])

    keys = module._news_mirror_candidate_keys(
        ledger.connection,
        cutoff=(now - timedelta(days=60)).isoformat(),
        after=cursor,
        limit=20,
    )

    assert keys == [
        (
            "bea_economic_releases", f"item-{index:03d}", 1,
            now.isoformat(),
        )
        for index in range(100, 120)
    ]
    ledger.close()


















def test_dashboard_distinguishes_unavailable_content_from_pending(tmp_path) -> None:
    now = datetime(2026, 8, 7, 9, 0, tzinfo=UTC)
    database = tmp_path / "forward.sqlite3"
    ledger = ForwardLedger(database, now=now)
    digest = hashlib.sha256(b"headline-only").hexdigest()
    ledger.append_news_revision(
        {
            "source": "google_news_gold_context",
            "source_item_id": "blocked",
            "collector_first_seen_time": now,
            "fetched_time": now,
            "headline": "Publisher blocks automated article access",
            "body": "headline-only",
            "link": "https://publisher.example/blocked",
            "content_hash": digest,
            "cluster_id": "blocked",
        }
    )
    ledger.append_content_failure(
        {
            "failure_id": "blocked-403",
            "source": "google_news_gold_context",
            "source_item_id": "blocked",
            "revision_number": 1,
            "raw_content_hash": digest,
            "attempt_number": 1,
            "error_type": "HTTPError",
            "error_signature": hashlib.sha256(b"403").hexdigest(),
            "error": "HTTP Error 403: Forbidden",
            "failed_at": now,
            "is_terminal": True,
        }
    )
    ledger.connection.close()

    payload = _dashboard_module()._dashboard_payload(database)
    # A failed-body discovery remains in the immutable audit ledger, but the
    # reader surface must not present it as usable news.
    assert payload["recent_news"] == []
    assert payload["annotation_queue"]["waiting_content"] == 0
    assert payload["annotation_queue"]["unavailable_content"] == 1


def test_dashboard_shows_readable_unparsed_news_without_model_visibility(tmp_path) -> None:
    now = datetime(2026, 8, 8, 1, 0, tzinfo=UTC)
    database = tmp_path / "forward.sqlite3"
    ledger = ForwardLedger(database, now=now)
    body = "readable point in time evidence body " * 20
    ledger.append_news_revision(
        {
            "source": "us_treasury_press_releases",
            "source_item_id": "queued-readable",
            "source_published_time": now,
            "collector_first_seen_time": now,
            "fetched_time": now,
            "headline": "Readable official release awaiting annotation",
            "body": body,
            "content_hash": hashlib.sha256(body.encode()).hexdigest(),
            "cluster_id": "queued-readable",
        }
    )
    ledger.connection.close()

    payload = _dashboard_module()._dashboard_payload(database)

    assert len(payload["recent_news"]) == 1
    assert payload["recent_news"][0]["model_visibility"] == "NOT_YET_PARSED"
    assert payload["recent_news"][0]["annotation_status"] == "QUEUED"
    assert payload["counts"]["readable_news_items"] == 1
    assert payload["counts"]["parsed_news_items"] == 0
    assert payload["counts"]["model_candidate_news_items"] == 0


def test_dashboard_keeps_readable_late_news_in_semantic_queue(tmp_path) -> None:
    now = datetime(2026, 8, 8, 1, 0, tzinfo=UTC)
    database = tmp_path / "forward.sqlite3"
    ledger = ForwardLedger(database, now=now - timedelta(hours=3))
    body = "readable late official evidence body " * 20
    ledger.append_news_revision(
        {
            "source": "us_treasury_press_releases",
            "source_item_id": "late-readable",
            "source_published_time": now - timedelta(hours=2),
            "collector_first_seen_time": now,
            "fetched_time": now,
            "headline": "Readable official release discovered too late",
            "body": body,
            "content_hash": hashlib.sha256(body.encode()).hexdigest(),
            "cluster_id": "late-readable",
        }
    )
    from xauusd_forecaster.news.scheduler.state import sync_pending_jobs
    sync_pending_jobs(ledger.connection, now=now)
    ledger.connection.close()

    payload = _dashboard_module()._dashboard_payload(database)

    assert len(payload["recent_news"]) == 1
    row = payload["recent_news"][0]
    assert row["annotation_status"] == "QUEUED"
    assert row["impact_status"] == "PENDING_ANNOTATION"
    assert row["model_visibility"] == "NOT_YET_PARSED"
    assert "annotation_reason_code" not in row
    assert payload["annotation_queue"]["queued"] == 1


def test_dashboard_keeps_small_positive_skew_in_semantic_queue(tmp_path) -> None:
    received = datetime(2026, 8, 19, 15, 51, 15, 685775, tzinfo=UTC)
    database = tmp_path / "forward.sqlite3"
    ledger = ForwardLedger(database, now=received - timedelta(days=1))
    body = "production-shaped Federal Reserve evidence body " * 20
    ledger.append_news_revision({
        "source": "google_news_fed_rates", "source_item_id": "skew-2.3s",
        "source_published_time": received + timedelta(seconds=2.314225),
        "collector_first_seen_time": received, "fetched_time": received,
        "headline": "Federal Reserve report", "body": body,
        "content_hash": hashlib.sha256(body.encode()).hexdigest(),
        "cluster_id": "skew-2.3s",
    })
    ledger.connection.close()

    row = _dashboard_module()._dashboard_payload(database)["recent_news"][0]

    assert row["annotation_status"] == "QUEUED"
    assert row["impact_status"] == "PENDING_ANNOTATION"
    assert row["model_visibility"] == "NOT_YET_PARSED"
    assert "annotation_reason_code" not in row


def test_dashboard_explains_active_and_expired_on_receipt_impacts(tmp_path) -> None:
    now = datetime.now(UTC)
    database = tmp_path / "forward.sqlite3"
    ledger = ForwardLedger(database, now=now - timedelta(hours=4))
    for item_id, published_at, impact_class in (
        ("active", now - timedelta(hours=2), "SAME_DAY"),
        ("expired-on-receipt", now - timedelta(hours=3), "IMMEDIATE"),
    ):
        first_seen = now - timedelta(minutes=30)
        body = f"official impact evidence {item_id} " * 30
        digest = hashlib.sha256(body.encode()).hexdigest()
        ledger.append_news_revision({
            "source": "us_treasury_press_releases",
            "source_item_id": item_id,
            "source_published_time": published_at,
            "collector_first_seen_time": first_seen,
            "fetched_time": first_seen,
            "headline": item_id,
            "body": body,
            "content_hash": digest,
            "cluster_id": item_id,
        })
        parsed_at = first_seen + timedelta(seconds=1)
        _append_basic_annotation(
            ledger, source="us_treasury_press_releases", item_id=item_id,
            digest=digest, parsed_at=parsed_at,
        )
        ledger.append_news_impact_assessment({
            "assessment_id": f"impact-{item_id}",
            "source": "us_treasury_press_releases",
            "source_item_id": item_id,
            "revision_number": 1,
            "raw_content_hash": digest,
            "annotation_id": f"annotation-us_treasury_press_releases-{item_id}",
            "llm_model_version": "gemma-4-31b-it",
            "prompt_version": "news-impact-v3-independent-semantic-review",
            "parse_started_at": parsed_at,
            "assessed_at": parsed_at + timedelta(seconds=1),
            "impact_class": impact_class,
            "event_state": "ACTIVE",
            "update_type": "NEW_EVENT",
            "confidence": 0.9,
            "reason_zh": "测试有效期判断。",
        })
    ledger.connection.close()

    payload = _dashboard_module()._dashboard_payload(database)
    rows = {row["source_item_id"]: row for row in payload["recent_news"]}

    assert rows["active"]["impact_status"] == "ACTIVE"
    assert rows["active"]["model_visibility"] == "MODEL_VISIBLE"
    assert rows["expired-on-receipt"]["impact_status"] == "EXPIRED_ON_RECEIPT"
    assert rows["expired-on-receipt"]["model_visibility"] == "IMPACT_EXPIRED"


def test_dashboard_uses_same_explicit_event_clock_as_model(tmp_path) -> None:
    now = datetime.now(UTC)
    database = tmp_path / "forward.sqlite3"
    ledger = ForwardLedger(database, now=now - timedelta(days=2))
    published = now - timedelta(hours=2)
    event_time = now - timedelta(hours=13)
    first_seen = now - timedelta(hours=1)
    body = "official event happened before the publication timestamp " * 20
    digest = hashlib.sha256(body.encode()).hexdigest()
    ledger.append_news_revision({
        "source": "us_treasury_press_releases", "source_item_id": "old-event",
        "source_published_time": published, "collector_first_seen_time": first_seen,
        "fetched_time": first_seen, "headline": "Delayed official event report",
        "body": body, "content_hash": digest, "cluster_id": "old-event",
    })
    parsed_at = first_seen + timedelta(seconds=1)
    _append_basic_annotation(
        ledger, source="us_treasury_press_releases", item_id="old-event",
        digest=digest, parsed_at=parsed_at, event_time=event_time,
    )
    ledger.append_news_impact_assessment({
        "assessment_id": "old-event-impact", "source": "us_treasury_press_releases",
        "source_item_id": "old-event", "revision_number": 1,
        "raw_content_hash": digest,
        "annotation_id": "annotation-us_treasury_press_releases-old-event",
        "llm_model_version": "gemma-4-31b-it",
        "prompt_version": "news-impact-v3-independent-semantic-review",
        "parse_started_at": parsed_at, "assessed_at": parsed_at + timedelta(seconds=1),
        "impact_class": "SAME_DAY", "event_state": "ACTIVE",
        "update_type": "NEW_EVENT", "confidence": 0.9,
        "reason_zh": "事件发生时间早于文章发布时间。",
    })
    ledger.connection.close()

    row = _dashboard_module()._dashboard_payload(database)["recent_news"][0]

    assert row["impact_status"] == "EXPIRED_ON_RECEIPT"
    assert datetime.fromisoformat(row["impact_expires_at"]) == event_time + timedelta(hours=12)




def test_dashboard_reports_gdelt_fallback_and_retry_time(tmp_path) -> None:
    now = datetime(2026, 8, 6, 4, 0, tzinfo=UTC)
    ledger = ForwardLedger(tmp_path / "forward.sqlite3", now=now)
    ledger.append_source_poll({
        "poll_id": "gdelt-429", "source": "gdelt_gold_geopolitics",
        "fetched_time": now - timedelta(minutes=30), "status": "ERROR",
        "error_type": "RateLimited", "error": "HTTP Error 429: Too Many Requests",
        "provider_http_status": 429,
    })
    ledger.append_source_poll({
        "poll_id": "google-ok", "source": "google_news_gold_context",
        "fetched_time": now - timedelta(minutes=5), "status": "OK",
    })
    body = "complete Google News fallback evidence " * 12
    ledger.append_news_revision({
        "source": "google_news_gold_context", "source_item_id": "fallback-item",
        "source_published_time": now - timedelta(minutes=10),
        "collector_first_seen_time": now - timedelta(minutes=5),
        "fetched_time": now - timedelta(minutes=5),
        "headline": "Gold rises as Treasury yields fall",
        "body": body, "link": "https://example.test/fallback",
        "content_hash": hashlib.sha256(body.encode()).hexdigest(),
        "cluster_id": "fallback-item",
    })
    connection = ledger.connection
    rows = _dashboard_module()._news_source_health(connection, now)
    gdelt = next(row for row in rows if row["source"] == "gdelt_gold_geopolitics")
    assert gdelt["health"] == "FALLBACK_ACTIVE"
    assert gdelt["latest_status"] == "RATE_LIMITED"
    assert gdelt["fallback_label"] == "Google News Context"
    assert gdelt["fallback_health"] == "HEALTHY"
    assert gdelt["recovery_mode"] == "RATE_LIMITED"
    assert gdelt["next_retry_time"] == (now + timedelta(minutes=30)).isoformat()


def test_dashboard_does_not_activate_fallback_from_stale_historical_evidence(
    tmp_path,
) -> None:
    now = datetime(2026, 8, 11, 10, 0, tzinfo=UTC)
    ledger = ForwardLedger(tmp_path / "forward.sqlite3", now=now - timedelta(days=10))
    ledger.append_source_poll({
        "poll_id": "gdelt-429", "source": "gdelt_gold_geopolitics",
        "fetched_time": now - timedelta(minutes=5), "status": "ERROR",
        "error_type": "RateLimited", "error": "HTTP Error 429: Too Many Requests",
        "provider_http_status": 429,
    })
    ledger.append_source_poll({
        "poll_id": "google-empty-now", "source": "google_news_gold_context",
        "fetched_time": now - timedelta(minutes=5), "status": "OK",
    })
    body = "historical Google News evidence " * 20
    ledger.append_news_revision({
        "source": "google_news_gold_context", "source_item_id": "old-item",
        "source_published_time": now - timedelta(days=10),
        "collector_first_seen_time": now - timedelta(days=10),
        "fetched_time": now - timedelta(days=10),
        "headline": "Old gold report", "body": body,
        "link": "https://example.test/old",
        "content_hash": hashlib.sha256(body.encode()).hexdigest(),
        "cluster_id": "old-item",
    })

    rows = _dashboard_module()._news_source_health(ledger.connection, now)
    google = next(row for row in rows if row["source"] == "google_news_gold_context")
    gdelt = next(row for row in rows if row["source"] == "gdelt_gold_geopolitics")

    assert google["health"] == "HEALTHY"
    assert google["semantic_status"] == "NO_RECENT_EVIDENCE"
    assert google["recent_evidence"] is False
    assert gdelt["health"] == "ERROR"
    assert gdelt["fallback_health"] == "NO_RECENT_EVIDENCE"


def test_dashboard_clears_historical_gdelt_429_after_successful_gkg_poll(
    tmp_path,
) -> None:
    now = datetime(2026, 8, 13, 3, 0, tzinfo=UTC)
    ledger = ForwardLedger(tmp_path / "forward.sqlite3", now=now)
    ledger.append_source_poll({
        "poll_id": "gdelt-old-doc-429", "source": "gdelt_gold_geopolitics",
        "fetched_time": now - timedelta(hours=2), "status": "ERROR",
        "error_type": "HTTPError", "error": "HTTP Error 429: Too Many Requests",
    })
    ledger.append_source_poll({
        "poll_id": "gdelt-gkg-ok", "source": "gdelt_gold_geopolitics",
        "fetched_time": now - timedelta(minutes=5), "status": "OK",
    })
    body = "current GDELT GKG evidence " * 20
    ledger.append_news_revision({
        "source": "gdelt_gold_geopolitics", "source_item_id": "gkg-item",
        "source_published_time": now - timedelta(minutes=10),
        "collector_first_seen_time": now - timedelta(minutes=5),
        "fetched_time": now - timedelta(minutes=5),
        "headline": "Current GKG report", "body": body,
        "link": "https://example.test/gkg",
        "content_hash": hashlib.sha256(body.encode()).hexdigest(),
        "cluster_id": "gkg-item",
    })

    rows = _dashboard_module()._news_source_health(ledger.connection, now)
    gdelt = next(row for row in rows if row["source"] == "gdelt_gold_geopolitics")

    assert gdelt["health"] == "HEALTHY"
    assert gdelt["latest_status"] == "OK"
    assert gdelt["recovery_mode"] is None
    assert gdelt["fallback_label"] is None
    assert gdelt["next_retry_time"] is None
    assert "429" in gdelt["last_error"]












def test_news_reader_materializations_exclude_semantically_irrelevant_articles(
    tmp_path,
) -> None:
    module = _dashboard_module()
    now = datetime.now(UTC).replace(microsecond=0)
    database = tmp_path / "forward.sqlite3"
    ledger = ForwardLedger(database, now=now)
    for item_id, relevance in (
        ("relevant-market-report", "MACRO_DRIVER"),
        ("irrelevant-entertainment-report", "IRRELEVANT"),
        ("pending-semantic-review", None),
    ):
        body = f"complete evidence for {item_id} " * 30
        digest = hashlib.sha256(body.encode()).hexdigest()
        ledger.append_news_revision({
            "source": "gdelt_gold_geopolitics",
            "source_item_id": item_id,
            "source_published_time": now,
            "collector_first_seen_time": now,
            "fetched_time": now,
            "headline": item_id,
            "body": body,
            "content_hash": digest,
            "cluster_id": item_id,
        })
        if relevance is not None:
            _append_basic_annotation(
                ledger,
                source="gdelt_gold_geopolitics",
                item_id=item_id,
                digest=digest,
                parsed_at=now + timedelta(seconds=1),
                xauusd_relevance=relevance,
            )
    archive = module._news_archive_page(ledger.connection, None, 20)
    ledger.close()
    dashboard = module._dashboard_payload(database)

    expected = {"relevant-market-report", "pending-semantic-review"}
    assert {row["source_item_id"] for row in archive["items"]} == expected
    assert archive["withdrawals"] == [{
        "source": "gdelt_gold_geopolitics",
        "source_item_id": "irrelevant-entertainment-report",
        "revision_number": 1,
    }]
    assert {row["source_item_id"] for row in dashboard["recent_news"]} == expected
def test_news_archive_explains_terminal_model_contract_failure(tmp_path) -> None:
    module = _dashboard_module()
    now = datetime.now(UTC).replace(microsecond=0)
    ledger = ForwardLedger(tmp_path / "forward.sqlite3", now=now)
    body = "Complete source body with one exact evidence sentence. " * 20
    digest = hashlib.sha256(body.encode()).hexdigest()
    ledger.append_news_revision({
        "source": "google_news_fed_rates", "source_item_id": "contract-failure",
        "source_published_time": now, "collector_first_seen_time": now,
        "fetched_time": now, "headline": "Fed policy report", "body": body,
        "content_hash": digest, "cluster_id": "contract-failure",
    })
    cause = "annotation supporting evidence is absent from source"
    ledger.append_llm_failure({
        "failure_id": "terminal-contract-failure", "task_type": "ANNOTATION",
        "source": "google_news_fed_rates", "source_item_id": "contract-failure",
        "revision_number": 1, "raw_content_hash": digest,
        "llm_model_version": "gemini-3.5-flash-lite",
        "prompt_version": PROMPT_VERSION, "attempt_number": 2,
        "error_type": "ValueError",
        "error_signature": hashlib.sha256(cause.encode()).hexdigest(),
        "error": cause, "failed_at": now, "is_terminal": True,
        "failure_evidence": {
            "failure_code": "MODEL_OUTPUT_CONTRACT_FAILED",
            "failure_stage": "SEMANTIC_CONTRACT", "response_hash": "a" * 64,
            "selected_output": {"supporting_evidence": ["bounded excerpt"]},
            "cause_type": "ValueError", "cause": cause,
        },
    })
    ledger.append_news_revision({
        "source": "google_news_fed_rates", "source_item_id": "provider-backoff",
        "source_published_time": now - timedelta(minutes=1),
        "collector_first_seen_time": now, "fetched_time": now,
        "headline": "Fed service retry", "body": body,
        "content_hash": digest, "cluster_id": "provider-backoff",
    })
    provider_cause = "HTTP Error 429: quota temporarily unavailable"
    ledger.append_llm_failure({
        "failure_id": "provider-backoff", "task_type": "ANNOTATION",
        "source": "google_news_fed_rates", "source_item_id": "provider-backoff",
        "revision_number": 1, "raw_content_hash": digest,
        "llm_model_version": "gemini-3.5-flash-lite",
        "prompt_version": PROMPT_VERSION, "attempt_number": 1,
        "error_type": "HTTPError",
        "error_signature": hashlib.sha256(provider_cause.encode()).hexdigest(),
        "error": provider_cause, "failed_at": now,
        "next_retry_at": now + timedelta(minutes=10), "is_terminal": False,
        "failure_evidence": {
            "failure_code": "PROVIDER_HTTP_ERROR",
            "failure_stage": "PROVIDER_REQUEST", "response_hash": "b" * 64,
            "selected_output": {}, "cause_type": "HTTPError",
            "cause": provider_cause,
        },
    })

    archive = module._news_archive_page(ledger.connection, None, 20)
    ledger.close()
    dashboard = module._dashboard_payload(tmp_path / "forward.sqlite3")
    archive_by_id = {row["source_item_id"]: row for row in archive["items"]}
    dashboard_by_id = {
        row["source_item_id"]: row for row in dashboard["recent_news"]
    }

    terminal = archive_by_id["contract-failure"]
    assert terminal["annotation_status"] == "DEAD_LETTER"
    assert terminal["annotation_reason_code"] == (
        "MODEL_OUTPUT_CONTRACT_FAILED"
    )
    assert terminal["annotation_reason"] == (
        "Gemini 返回的证据片段无法在来源正文中逐字找到。"
    )
    assert archive_by_id["provider-backoff"]["annotation_status"] == "BACKING_OFF"
    assert archive_by_id["provider-backoff"]["annotation_reason_code"] == (
        "PROVIDER_HTTP_ERROR"
    )
    assert archive_by_id["provider-backoff"]["annotation_reason"] == (
        "Gemini 服务返回 HTTP 错误。"
    )
    assert dashboard_by_id["contract-failure"]["annotation_reason_code"] == (
        "MODEL_OUTPUT_CONTRACT_FAILED"
    )
    assert dashboard_by_id["contract-failure"]["annotation_reason"] == (
        "Gemini 返回的证据片段无法在来源正文中逐字找到。"
    )
    assert dashboard_by_id["provider-backoff"]["annotation_reason_code"] == (
        "PROVIDER_HTTP_ERROR"
    )
