"""Point-in-time and economic-time rules for action-bearing news."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Mapping


MAX_ACTIONABLE_NEWS_AGE = timedelta(hours=72)


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
    age_minutes = (decision - published).total_seconds() / 60.0
    if published < epoch:
        return NewsTimeAssessment(
            False, published, age_minutes, delay, "PRE_FORWARD_PUBLICATION"
        )
    if published > decision:
        return NewsTimeAssessment(
            False, published, age_minutes, delay, "PUBLISHED_AFTER_DECISION"
        )
    if first_seen - published > MAX_ACTIONABLE_NEWS_AGE:
        return NewsTimeAssessment(False, published, age_minutes, delay, "LATE_DISCOVERY")
    if decision - published > MAX_ACTIONABLE_NEWS_AGE:
        return NewsTimeAssessment(False, published, age_minutes, delay, "STALE_EVENT")
    return NewsTimeAssessment(True, published, max(0.0, age_minutes), delay, "CURRENT_EVENT")
