"""Objective intake checks for external news discovery lanes.

Headline meaning is intentionally not decided here.  Search results are written
by humans and routinely contain casing mistakes, metaphors and translations.
Fresh candidates with usable publication metadata proceed to full-text fetch and
AI semantic annotation; only objective timing failures are rejected here.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import re


GOOGLE_NEWS_MAX_AGE = timedelta(hours=72)
GOOGLE_NEWS_FUTURE_TOLERANCE = timedelta(minutes=10)
GOOGLE_NEWS_MAX_ITEMS_PER_EVENT_FAMILY = 3

_HIGH_QUALITY_PUBLISHERS = (
    "reuters", "bloomberg", "associated press", " ap news", "cnbc",
    "financial times", "wall street journal", "bureau of labor statistics",
    "federal reserve", "u.s. department of labor", "world gold council",
)

_GOOGLE_NEWS_SOURCES = frozenset({
    "google_news_gold_context",
    "google_news_bls_official_releases",
    "google_news_us_employment",
    "google_news_us_inflation",
    "google_news_fed_rates",
})
_AI_DISCOVERY_SOURCES = _GOOGLE_NEWS_SOURCES | {"gdelt_gold_geopolitics"}


def is_google_news_source(source: str) -> bool:
    return source in _GOOGLE_NEWS_SOURCES


def google_news_item_is_relevant(
    source: str,
    headline: str,
    published_at: datetime | None,
    observed_at: datetime,
) -> tuple[bool, str]:
    """Admit fresh discovery candidates for full-text AI semantic review.

    ``headline`` remains in the signature because callers operate on complete
    candidate records.  It is deliberately not interpreted here.
    """
    del headline
    if source not in _AI_DISCOVERY_SOURCES:
        return True, "NOT_GOOGLE_LANE"
    if published_at is None:
        return False, "MISSING_PUBLISHED_TIME"
    published = published_at.replace(tzinfo=published_at.tzinfo or UTC).astimezone(UTC)
    observed = observed_at.replace(tzinfo=observed_at.tzinfo or UTC).astimezone(UTC)
    if published > observed + GOOGLE_NEWS_FUTURE_TOLERANCE:
        return False, "FUTURE_PUBLISHED_TIME"
    if observed - published > GOOGLE_NEWS_MAX_AGE:
        return False, "SEARCH_RESULT_TOO_OLD"
    return True, "AI_SEMANTIC_REVIEW_REQUIRED"


def google_news_quality_rank(headline: str) -> int:
    """Prefer official and established publishers within the same fresh feed."""
    text = " " + re.sub(r"\s+", " ", (headline or "").casefold()).strip() + " "
    publisher = text.rsplit(" - ", 1)[-1]
    return 0 if any(name in publisher for name in _HIGH_QUALITY_PUBLISHERS) else 1


def google_news_candidate_family(
    source: str,
    headline: str,
    published_at: datetime | None,
) -> str:
    """Return a conservative family key used only to limit duplicate intake.

    This is not an event judgment and never enters model features.  It prevents
    ten publisher rewrites of one release from consuming all ten intake slots;
    up to three independent articles remain available for corroboration.
    """
    text = " " + re.sub(r"\s+", " ", (headline or "").casefold()).strip() + " "
    publication_day = "unknown"
    if published_at is not None:
        published = published_at.replace(
            tzinfo=published_at.tzinfo or UTC
        ).astimezone(UTC)
        publication_day = published.date().isoformat()

    # Collapse only near-identical titles from the same publication day.
    # Event meaning belongs to the AI contract; this mechanical intake key
    # must not guess that two differently worded reports are the same event.
    tokens = re.findall(r"[a-z0-9\u4e00-\u9fff]+", text)
    stop = {"the", "a", "an", "and", "or", "to", "of", "in", "on", "for"}
    canonical = "-".join(token for token in tokens if token not in stop)[:120]
    return f"{source}:{publication_day}:{canonical or 'untitled'}"
