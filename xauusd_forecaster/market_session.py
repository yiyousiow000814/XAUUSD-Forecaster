"""Expected XAUUSD weekly closure and point-in-time quote helpers."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from .market import MarketObservation


UTC = timezone.utc
NEW_YORK = ZoneInfo("America/New_York")


def expected_weekly_closure(at: datetime) -> bool:
    """Return whether *at* is inside the normal weekend closure window.

    The window is deliberately used only as an operational classification.
    A genuinely received fresh quote always takes precedence over this clock.
    """

    value = at.astimezone(NEW_YORK)
    weekday = value.weekday()
    return (
        (weekday == 4 and value.hour >= 17)
        or weekday == 5
        or (weekday == 6 and value.hour < 18)
    )


def horizon_crosses_weekly_closure(
    decision_time: datetime,
    horizon: timedelta = timedelta(minutes=30),
) -> bool:
    """Return whether a new fixed-horizon observation would cross Friday close."""

    if horizon <= timedelta(0):
        raise ValueError("horizon must be positive")
    if expected_weekly_closure(decision_time):
        return True
    return expected_weekly_closure(decision_time + horizon)


def has_fresh_quote(
    observations: list[MarketObservation],
    decision_time: datetime,
    max_age: timedelta = timedelta(seconds=20),
) -> bool:
    """Return whether a causally visible executable quote is fresh enough."""

    visible = [
        row
        for row in observations
        if row.event_time <= decision_time and row.received_time <= decision_time
    ]
    if not visible:
        return False
    return timedelta(0) <= decision_time - visible[-1].received_time <= max_age


def skipped_grid_reason(
    decision_time: datetime,
    current_boundary: datetime,
    observations: list[MarketObservation],
) -> str | None:
    """Classify grids that must not be reconstructed as live predictions."""

    if (
        not expected_weekly_closure(decision_time)
        and horizon_crosses_weekly_closure(decision_time)
    ):
        return "FIXED_HORIZON_CROSSES_WEEKLY_CLOSE"
    if has_fresh_quote(observations, decision_time):
        return None
    if expected_weekly_closure(decision_time):
        return "EXPECTED_WEEKLY_MARKET_CLOSURE"
    if decision_time < current_boundary - timedelta(minutes=10):
        return "MISSED_GRID_WITHOUT_POINT_IN_TIME_QUOTE"
    return None
