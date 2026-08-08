from datetime import datetime, timedelta, timezone

from xauusd_forecaster.market import MarketObservation
from xauusd_forecaster.market_session import (
    expected_weekly_closure,
    has_fresh_quote,
    horizon_crosses_weekly_closure,
    skipped_grid_reason,
)


UTC = timezone.utc


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
    assert skipped_grid_reason(decision, decision, [quote]) is None


def test_fixed_horizon_must_finish_before_weekly_close() -> None:
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
    assert skipped_grid_reason(first_blocked, first_blocked, [quote]) == (
        "FIXED_HORIZON_CROSSES_WEEKLY_CLOSE"
    )


def test_weekend_and_retroactive_missing_grids_are_not_reconstructed() -> None:
    saturday = datetime(2026, 8, 8, 12, 0, tzinfo=UTC)
    assert skipped_grid_reason(saturday, saturday, []) == "EXPECTED_WEEKLY_MARKET_CLOSURE"

    monday_boundary = datetime(2026, 8, 10, 2, 0, tzinfo=UTC)
    missed = monday_boundary - timedelta(hours=1)
    assert skipped_grid_reason(missed, monday_boundary, []) == (
        "MISSED_GRID_WITHOUT_POINT_IN_TIME_QUOTE"
    )
    assert skipped_grid_reason(monday_boundary, monday_boundary, []) is None
