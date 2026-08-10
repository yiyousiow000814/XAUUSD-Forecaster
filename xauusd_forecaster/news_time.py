"""Point-in-time and economic-time rules for action-bearing news."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Mapping

from .market_session import market_open_elapsed


MAX_ACTIONABLE_NEWS_AGE = timedelta(hours=72)
MAX_ACTIONABLE_DISCOVERY_DELAY = timedelta(hours=1)

_CATEGORY_TIME_RULES = {
    "inflation_employment": (timedelta(hours=24), 180.0),
    "rates_fed": (timedelta(hours=72), 360.0),
    "growth_economy": (timedelta(hours=48), 360.0),
    "usd_liquidity": (timedelta(hours=48), 360.0),
    "oil_energy": (timedelta(hours=48), 360.0),
    "war_geopolitics": (timedelta(hours=36), 360.0),
    "central_bank_gold": (timedelta(days=7), 1440.0),
    "risk_sentiment": (timedelta(hours=24), 180.0),
}


def category_time_rule(category: str | None) -> tuple[timedelta, float]:
    """Return the frozen actionable window and freshness half-life."""
    return _CATEGORY_TIME_RULES.get(str(category or ""), (MAX_ACTIONABLE_NEWS_AGE, 360.0))


@dataclass(frozen=True)
class NewsTimeAssessment:
    eligible: bool
    event_time: datetime | None
    age_minutes: float | None
    discovery_delay_seconds: float | None
    reason_code: str


def _value(row: Mapping[str, object], key: str) -> object:
    try:
        return row[key]
    except (KeyError, IndexError):
        return None


def _time(value: object) -> datetime | None:
    if value in (None, ""):
        return None
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def assess_news_time(
    row: Mapping[str, object],
    *,
    decision_time: datetime,
    forward_epoch: datetime,
    max_actionable_age: timedelta = MAX_ACTIONABLE_NEWS_AGE,
    max_discovery_delay: timedelta | None = MAX_ACTIONABLE_DISCOVERY_DELAY,
    allow_pre_forward_publication: bool = False,
    exclude_weekly_closure: bool = False,
) -> NewsTimeAssessment:
    """Keep receipt visibility separate from economic freshness.

    ``collector_first_seen_time`` remains the no-lookahead visibility clock.
    ``source_published_time`` is the economic clock used for freshness.  A
    missing publisher time is deliberately display-only: treating first-seen
    as a substitute would make an archive page look like a new event whenever
    a collector is first installed.
    """
    decision = _time(decision_time)
    epoch = _time(forward_epoch)
    first_seen = _time(_value(row, "collector_first_seen_time"))
    published = _time(_value(row, "source_published_time"))
    if decision is None or epoch is None or first_seen is None:
        raise ValueError("decision_time, forward_epoch, and first_seen require timestamps")
    if first_seen > decision:
        return NewsTimeAssessment(False, published, None, None, "NOT_YET_VISIBLE")
    if published is None:
        return NewsTimeAssessment(False, None, None, None, "PUBLISHED_TIME_MISSING")
    delay = (first_seen - published).total_seconds()
    elapsed = (
        market_open_elapsed(published, decision)
        if exclude_weekly_closure and published <= decision
        else decision - published
    )
    age_minutes = elapsed.total_seconds() / 60.0
    if published < epoch and not allow_pre_forward_publication:
        return NewsTimeAssessment(
            False, published, age_minutes, delay, "PRE_FORWARD_PUBLICATION"
        )
    if published > decision:
        return NewsTimeAssessment(
            False, published, age_minutes, delay, "PUBLISHED_AFTER_DECISION"
        )
    if (
        max_discovery_delay is not None
        and first_seen - published > max_discovery_delay
    ):
        return NewsTimeAssessment(False, published, age_minutes, delay, "LATE_DISCOVERY")
    if elapsed > max_actionable_age:
        return NewsTimeAssessment(False, published, age_minutes, delay, "STALE_EVENT")
    return NewsTimeAssessment(True, published, max(0.0, age_minutes), delay, "CURRENT_EVENT")
