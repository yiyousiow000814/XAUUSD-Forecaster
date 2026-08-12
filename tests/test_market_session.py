from datetime import datetime, timedelta, timezone

from xauusd_forecaster.market import BrokerMarketSession, MarketObservation
from xauusd_forecaster.market_session import (
    expected_weekly_closure,
    has_fresh_quote,
    horizon_crosses_weekly_closure,
    skipped_grid_reason,
)
from xauusd_forecaster.forward_engine import ForwardEngine
from xauusd_forecaster.forward_ledger import ForwardLedger
from scripts.run_forward_collector import append_due_grid_events


UTC = timezone.utc


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
    quote = MarketObservation(
        decision - timedelta(seconds=2), decision - timedelta(seconds=1), 4300, 4300.1
    )

    class ClosedProvider:
        name = "test-ctrader"

        def observations(self, _decision_time):
            return [quote]

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
