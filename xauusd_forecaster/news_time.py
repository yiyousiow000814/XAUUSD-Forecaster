"""Point-in-time and economic-time rules for action-bearing news."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Mapping

if TYPE_CHECKING:
    from sqlite3 import Connection

MAX_ACTIONABLE_NEWS_AGE = timedelta(hours=72)
MAX_ACTIONABLE_DISCOVERY_DELAY = timedelta(hours=1)
MAX_PUBLICATION_CLOCK_SKEW = timedelta(minutes=10)

SOURCE_REPORTED_TIME = "SOURCE_REPORTED"
MIXED_PRECISE_OR_BATCH_PROXY_TIME = "MIXED_PRECISE_OR_BATCH_PROXY"
_COARSE_PUBLICATION_TIME_SOURCES = frozenset({"gdelt_gold_geopolitics"})
_SEMANTIC_ELIGIBILITY_SQL_FUNCTION = "news_semantic_is_eligible"
NEWS_SEMANTIC_ELIGIBILITY_CONTRACT_VERSION = "news-semantic-eligibility-v2"

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


@dataclass(frozen=True)
class NewsSemanticEligibility:
    """Separate semantic permission from economic timing evidence."""

    eligible: bool
    reason_code: str
    timing_reason_codes: tuple[str, ...]
    publication_time_reliability: str


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
    if exclude_weekly_closure and published <= decision:
        from .market_session import market_open_elapsed
        elapsed = market_open_elapsed(published, decision)
    else:
        elapsed = decision - published
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


def assess_news_semantic_eligibility(
    row: Mapping[str, object], *, forward_epoch: datetime,
) -> NewsSemanticEligibility:
    """Decide whether received evidence may be understood by the semantic model.

    Discovery delay and publication age remain auditable timing evidence. They
    do not decide whether readable current evidence is worth classifying;
    impact and trading contracts own economic freshness after classification.
    """
    first_seen = _time(_value(row, "collector_first_seen_time"))
    if first_seen is None:
        raise ValueError("collector_first_seen_time requires a timestamp")
    epoch = _time(forward_epoch)
    if epoch is None:
        raise ValueError("forward_epoch requires a timestamp")
    source = str(_value(row, "source") or "")
    published = _time(_value(row, "source_published_time"))
    reliability = (
        MIXED_PRECISE_OR_BATCH_PROXY_TIME
        if source in _COARSE_PUBLICATION_TIME_SOURCES
        else SOURCE_REPORTED_TIME
    )
    if first_seen < epoch:
        return NewsSemanticEligibility(
            False, "PRE_FORWARD_RECEIPT", (), reliability,
        )
    if published is None:
        return NewsSemanticEligibility(
            False, "PUBLISHED_TIME_MISSING", (), reliability,
        )
    if published < epoch:
        return NewsSemanticEligibility(
            False, "PRE_FORWARD_PUBLICATION", (), reliability,
        )

    timing_reasons: list[str] = []
    if published > first_seen:
        timing_reasons.append("PUBLISHED_AFTER_RECEIPT")
        if published - first_seen > MAX_PUBLICATION_CLOCK_SKEW:
            return NewsSemanticEligibility(
                False,
                "PUBLISHED_AFTER_DECISION",
                tuple(timing_reasons),
                reliability,
            )
    else:
        discovery_delay = first_seen - published
        if discovery_delay > MAX_ACTIONABLE_DISCOVERY_DELAY:
            timing_reasons.append("LATE_DISCOVERY")
        if discovery_delay > MAX_ACTIONABLE_NEWS_AGE:
            timing_reasons.append("STALE_EVENT")

    return NewsSemanticEligibility(
        True,
        "SEMANTIC_ELIGIBLE",
        tuple(timing_reasons) or ("CURRENT_EVENT",),
        reliability,
    )


def register_news_semantic_eligibility_sql(connection: Connection) -> None:
    """Expose the centralized semantic decision to canonical-owner queries."""

    def is_eligible(
        source: object,
        source_published_time: object,
        collector_first_seen_time: object,
        forward_epoch: object,
    ) -> int:
        try:
            assessment = assess_news_semantic_eligibility(
                {
                    "source": source,
                    "source_published_time": source_published_time,
                    "collector_first_seen_time": collector_first_seen_time,
                },
                forward_epoch=_time(forward_epoch),
            )
        except (TypeError, ValueError):
            return 0
        return int(assessment.eligible)

    connection.create_function(
        _SEMANTIC_ELIGIBILITY_SQL_FUNCTION,
        4,
        is_eligible,
        deterministic=True,
    )


def semantic_eligibility_sql_predicate(alias: str) -> str:
    """Limit canonical ownership to rows allowed by the semantic contract.

    The existing representative ordering then chooses at most one owner from
    current eligible peers; invalid timing evidence cannot shadow an eligible
    member merely because its body is longer.
    """
    if not alias.isidentifier():
        raise ValueError("invalid SQL alias")
    return (
        f"{_SEMANTIC_ELIGIBILITY_SQL_FUNCTION}("
        f"{alias}.source,{alias}.source_published_time,"
        f"{alias}.collector_first_seen_time,?)=1"
    )
