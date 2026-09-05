from datetime import datetime, timedelta, timezone

import pytest

from xauusd_forecaster.market import BrokerMarketSession, MarketObservation
from xauusd_forecaster.market_session import (
    expected_weekly_closure,
    has_fresh_quote,
    horizon_crosses_weekly_closure,
    skipped_grid_reason,
)
from xauusd_forecaster.forward_engine import ForwardEngine
from xauusd_forecaster.forward_ledger import ForwardLedger
from scripts.run_forward_collector import (
    append_current_grid_events,
    append_due_grid_events,
    startup_reconciliation_plan,
)


UTC = timezone.utc


def test_current_generation_makes_startup_reconciliation_background(monkeypatch) -> None:
    monkeypatch.setattr(
        "scripts.run_forward_collector.require_current_contract_generation",
        lambda _connection: "generation-current",
    )
    assert startup_reconciliation_plan(object()) == {
        "synchronous": False,
        "active_generation_id": "generation-current",
    }


def test_missing_generation_keeps_startup_fail_closed(monkeypatch) -> None:
    def missing(_connection):
        raise RuntimeError("missing current generation")
    monkeypatch.setattr(
        "scripts.run_forward_collector.require_current_contract_generation", missing,
    )
    assert startup_reconciliation_plan(object())["synchronous"] is True


def broker_session(
    at: datetime, *, is_open: bool = True, time_till_close: timedelta = timedelta(hours=1)
) -> BrokerMarketSession:
    time_till_open = timedelta(0) if is_open else timedelta(hours=2)
    return BrokerMarketSession(
        observed_at=at,
        server_time=at,
        is_open=is_open,
        time_till_open=time_till_open,
        time_till_close=time_till_close if is_open else timedelta(0),
        next_open_time=at + time_till_open if not is_open else None,
        next_close_time=at + time_till_close if is_open else None,
    )


def test_expected_weekly_closure_window() -> None:
    assert not expected_weekly_closure(datetime(2026, 8, 7, 20, 59, tzinfo=UTC))
    assert expected_weekly_closure(datetime(2026, 8, 7, 21, 0, tzinfo=UTC))
    assert expected_weekly_closure(datetime(2026, 8, 8, 12, 0, tzinfo=UTC))
    assert expected_weekly_closure(datetime(2026, 8, 9, 21, 59, tzinfo=UTC))
    assert not expected_weekly_closure(datetime(2026, 8, 9, 22, 0, tzinfo=UTC))


def test_fresh_received_quote_overrides_weekly_clock() -> None:
    decision = datetime(2026, 8, 7, 20, 0, tzinfo=UTC)
    quote = MarketObservation(
        decision - timedelta(seconds=2), decision - timedelta(seconds=1), 4300, 4300.1
    )
    assert has_fresh_quote([quote], decision)
    assert skipped_grid_reason(
        decision, decision, [quote], broker_session(decision), decision
    ) is None


def test_broker_status_supersedes_the_operational_weekly_clock() -> None:
    last_eligible = datetime(2026, 8, 7, 20, 29, tzinfo=UTC)
    first_blocked = datetime(2026, 8, 7, 20, 30, tzinfo=UTC)
    quote = MarketObservation(
        first_blocked - timedelta(seconds=2),
        first_blocked - timedelta(seconds=1),
        4300,
        4300.1,
    )

    assert not horizon_crosses_weekly_closure(last_eligible)
    assert horizon_crosses_weekly_closure(first_blocked)
    assert skipped_grid_reason(
        first_blocked, first_blocked, [quote],
        broker_session(first_blocked), first_blocked,
    ) is None


def test_weekend_and_retroactive_missing_grids_are_not_reconstructed() -> None:
    saturday = datetime(2026, 8, 8, 12, 0, tzinfo=UTC)
    assert skipped_grid_reason(
        saturday, saturday, [], broker_session(saturday, is_open=False), saturday
    ) == "BROKER_MARKET_CLOSED"

    monday_boundary = datetime(2026, 8, 10, 2, 0, tzinfo=UTC)
    missed = monday_boundary - timedelta(hours=1)
    assert skipped_grid_reason(
        missed, monday_boundary, [], broker_session(monday_boundary), monday_boundary
    ) == (
        "MISSED_GRID_WITHOUT_POINT_IN_TIME_QUOTE"
    )
    assert skipped_grid_reason(
        monday_boundary, monday_boundary, [], broker_session(monday_boundary), monday_boundary
    ) == "CURRENT_GRID_WITHOUT_FRESH_QUOTE"


def test_broker_daily_close_and_horizon_boundary_block_new_decisions() -> None:
    decision = datetime(2026, 8, 11, 21, 0, tzinfo=UTC)
    quote = MarketObservation(
        decision - timedelta(seconds=2), decision - timedelta(seconds=1), 4300, 4300.1
    )
    assert skipped_grid_reason(
        decision, decision, [quote], broker_session(decision, is_open=False), decision
    ) == "BROKER_MARKET_CLOSED"
    assert skipped_grid_reason(
        decision, decision, [quote],
        broker_session(decision, time_till_close=timedelta(minutes=29)), decision,
    ) == "FIXED_HORIZON_CROSSES_BROKER_CLOSE"


def test_fresh_but_aging_heartbeat_cannot_cross_absolute_close() -> None:
    decision = datetime(2026, 8, 11, 20, 30, tzinfo=UTC)
    heartbeat_at = decision - timedelta(seconds=9)
    quote = MarketObservation(
        decision - timedelta(seconds=2), decision - timedelta(seconds=1),
        4300, 4300.1,
    )
    session = broker_session(
        heartbeat_at, time_till_close=timedelta(minutes=30, seconds=9)
    )

    assert session.is_fresh(decision + timedelta(seconds=9))
    assert session.observed_at + session.time_till_close == (
        decision + timedelta(minutes=30)
    )
    assert skipped_grid_reason(
        decision, decision, [quote], session, decision + timedelta(seconds=9)
    ) == "FIXED_HORIZON_CROSSES_BROKER_CLOSE"


def test_missing_or_stale_broker_status_fails_closed() -> None:
    decision = datetime(2026, 8, 11, 21, 0, tzinfo=UTC)
    quote = MarketObservation(
        decision - timedelta(seconds=2), decision - timedelta(seconds=1), 4300, 4300.1
    )
    assert skipped_grid_reason(decision, decision, [quote]) == (
        "BROKER_MARKET_STATUS_UNAVAILABLE"
    )
    stale = broker_session(decision - timedelta(minutes=1))
    assert skipped_grid_reason(
        decision, decision, [quote], stale, decision
    ) == "BROKER_MARKET_STATUS_UNAVAILABLE"


def test_collector_does_not_append_prediction_during_broker_close(tmp_path) -> None:
    decision = datetime(2026, 8, 11, 21, 0, tzinfo=UTC)

    class ClosedProvider:
        name = "test-ctrader"

        def observations(self, _decision_time):
            raise AssertionError("closed decision path must not read quote payloads")

        def market_session(self, _observed_at):
            return broker_session(decision, is_open=False)

    ledger = ForwardLedger(tmp_path / "forward.sqlite3", now=decision - timedelta(minutes=5))
    provider = ClosedProvider()
    engine = ForwardEngine(ledger, provider)

    last_decision, appended, skipped = append_due_grid_events(
        ledger, engine, provider, decision - timedelta(minutes=5),
        decision, decision, [],
    )

    assert last_decision == decision
    assert appended == []
    assert skipped == {"BROKER_MARKET_CLOSED": 1}
    assert ledger.connection.execute("SELECT count(*) FROM decision_events").fetchone()[0] == 0


@pytest.mark.parametrize("admission", ["not_due", "before_epoch", "unknown", "stale"])
def test_collector_no_work_admission_precedes_expensive_quote_reads(admission):
    now = datetime(2026, 8, 11, 21, 0, tzinfo=UTC)
    calls = []

    class Provider:
        def observations(self, _):
            raise AssertionError("ineligible clock read quote payload")

        def market_session(self, at):
            calls.append("session")
            if admission == "unknown":
                return None
            return broker_session(at - timedelta(hours=1))

    ledger = type("Ledger", (), {"forward_epoch": now + timedelta(minutes=5)
                                if admission == "before_epoch" else now})()
    last = now if admission == "not_due" else now - timedelta(minutes=5)
    cursor, appended, skipped = append_due_grid_events(
        ledger, object(), Provider(), last, now, now, [],
    )
    assert cursor == now
    assert appended == []
    assert calls == ([] if admission in {"not_due", "before_epoch"} else ["session"])
    assert skipped == ({} if not calls else {"BROKER_MARKET_STATUS_UNAVAILABLE": 1})


def test_collector_arithmetically_settles_a_multi_year_unobservable_gap(
    monkeypatch,
) -> None:
    boundary = datetime(2026, 8, 14, 10, 20, tzinfo=UTC)
    start = boundary - timedelta(days=365 * 5)
    detailed_checks: list[datetime] = []

    class Provider:
        def observations(self, _boundary):
            return []

        def market_session(self, collected_at):
            return broker_session(collected_at, time_till_close=timedelta(hours=2))

    def recording_skip_reason(*args, **kwargs):
        detailed_checks.append(args[0])
        return skipped_grid_reason(*args, **kwargs)

    monkeypatch.setattr(
        "scripts.run_forward_collector.skipped_grid_reason", recording_skip_reason,
    )
    ledger = type("Ledger", (), {"forward_epoch": start})()
    engine = type(
        "Engine",
        (),
        {"append_clock_event": lambda *_args: (_ for _ in ()).throw(AssertionError())},
    )()

    last_decision, appended, skipped = append_due_grid_events(
        ledger, engine, Provider(), start, boundary, boundary, [],
    )

    assert last_decision == boundary
    assert appended == []
    assert skipped == {
        "MISSED_GRID_WITHOUT_POINT_IN_TIME_QUOTE": 525_597,
        "CURRENT_GRID_WITHOUT_FRESH_QUOTE": 3,
    }
    assert len(detailed_checks) == 13
    assert detailed_checks[0] == boundary - timedelta(minutes=60)
    assert detailed_checks[-1] == boundary


def test_collector_keeps_quote_backed_work_at_the_observation_window_edge() -> None:
    boundary = datetime(2026, 8, 14, 10, 20, tzinfo=UTC)
    decision = boundary - timedelta(minutes=60)
    start = boundary - timedelta(days=365)
    quote = MarketObservation(
        decision - timedelta(seconds=2), decision - timedelta(seconds=1), 4300, 4300.1,
    )
    appended_times: list[datetime] = []

    class Provider:
        def observations(self, _boundary):
            return [quote]

        def market_session(self, collected_at):
            return broker_session(collected_at, time_till_close=timedelta(hours=2))

    class Engine:
        def append_clock_event(self, decision_time, _collected_at, _news):
            appended_times.append(decision_time)
            return "snapshot", "decision"

    ledger = type("Ledger", (), {"forward_epoch": start})()
    last_decision, appended, skipped = append_due_grid_events(
        ledger, Engine(), Provider(), start, boundary, boundary, [],
    )

    assert last_decision == boundary
    assert appended_times == [decision]
    assert appended == [(decision, "snapshot", "decision")]
    assert skipped == {
        "MISSED_GRID_WITHOUT_POINT_IN_TIME_QUOTE": 105_116,
        "CURRENT_GRID_WITHOUT_FRESH_QUOTE": 3,
    }


def test_collector_aggregates_a_multi_year_closed_market_gap() -> None:
    boundary = datetime(2026, 8, 14, 10, 20, tzinfo=UTC)
    start = boundary - timedelta(days=365 * 2)

    class Provider:
        def observations(self, _boundary):
            return []

        def market_session(self, collected_at):
            return broker_session(collected_at, is_open=False)

    ledger = type("Ledger", (), {"forward_epoch": start})()
    last_decision, appended, skipped = append_due_grid_events(
        ledger, object(), Provider(), start, boundary, boundary, [],
    )

    assert last_decision == boundary
    assert appended == []
    assert skipped == {"BROKER_MARKET_CLOSED": 210_240}


def test_collector_takes_decision_time_after_blocking_maintenance() -> None:
    fresh = datetime(2026, 8, 14, 10, 20, 10, tzinfo=UTC)
    prior = fresh.replace(second=0) - timedelta(minutes=5)
    observed: dict[str, datetime] = {}

    class RecordingProvider:
        def observations(self, boundary):
            observed["boundary"] = boundary
            return []

        def market_session(self, collected_at):
            observed["collected_at"] = collected_at
            return broker_session(collected_at)

    collected_at, last_decision, appended, skipped = append_current_grid_events(
        type("Ledger", (), {"forward_epoch": prior})(),
        object(),
        RecordingProvider(),
        prior,
        [],
        clock=lambda: fresh,
    )

    assert collected_at == fresh
    assert observed == {
        "boundary": datetime(2026, 8, 14, 10, 20, tzinfo=UTC),
        "collected_at": fresh,
    }
    assert last_decision == fresh.replace(second=0)
    assert appended == []
    assert skipped == {"CURRENT_GRID_WITHOUT_FRESH_QUOTE": 1}
