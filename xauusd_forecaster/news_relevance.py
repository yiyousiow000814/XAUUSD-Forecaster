"""Cheap, deterministic admission gates for broad Google News lanes.

The raw ledger remains append-only.  These gates decide whether a search result
is useful enough to spend article-fetch and LLM capacity on.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import re


GOOGLE_NEWS_MAX_AGE = timedelta(hours=72)
GOOGLE_NEWS_FUTURE_TOLERANCE = timedelta(minutes=10)
GOOGLE_NEWS_MAX_ITEMS_PER_EVENT_FAMILY = 3

_TERMS = {
    "google_news_gold_context": (
        ("gold", "xau", "bullion", "金价", "黄金"),
        ("fed", "rate", "yield", "dollar", "inflation", "payroll", "job",
         "oil", "war", "conflict", "sanction", "geopolit", "central bank",
         "reserve", "hormuz", "美联储", "利率", "收益率", "美元", "通胀",
         "就业", "油价", "战争", "制裁", "央行", "储备", "霍尔木兹"),
    ),
    "google_news_bls_official_releases": (
        ("employment situation", "nonfarm payroll", "consumer price index",
         "job openings", "labor turnover", "unemployment", "hourly earnings",
         "就业形势", "非农", "消费者价格指数", "职位空缺", "失业率", "时薪"),
    ),
    "google_news_us_employment": (
        ("nonfarm", "payroll", "jobs report", "employment situation",
         "unemployment", "hourly earnings", "jolts", "job openings",
         "非农", "就业报告", "就业形势", "失业率", "时薪", "职位空缺"),
    ),
    "google_news_us_inflation": (
        ("cpi", "consumer price", "inflation", "pce", "producer price",
         "通胀", "消费者价格", "生产者价格"),
        ("u.s.", " us ", "america", "bls", "bea", "federal reserve",
         "美国", "美联储"),
    ),
    "google_news_fed_rates": (
        ("fomc", "federal reserve", "fed ", "interest rate", "rate cut",
         "rate hike", "treasury yield", "美联储", "利率", "降息", "加息",
         "美债收益率"),
    ),
}


def is_google_news_source(source: str) -> bool:
    return source in _TERMS


def google_news_item_is_relevant(
    source: str,
    headline: str,
    published_at: datetime | None,
    observed_at: datetime,
) -> tuple[bool, str]:
    """Return whether a Google search item may enter expensive processing."""
    if source not in _TERMS:
        return True, "NOT_GOOGLE_LANE"
    if published_at is None:
        return False, "MISSING_PUBLISHED_TIME"
    published = published_at.replace(tzinfo=published_at.tzinfo or UTC).astimezone(UTC)
    observed = observed_at.replace(tzinfo=observed_at.tzinfo or UTC).astimezone(UTC)
    if published > observed + GOOGLE_NEWS_FUTURE_TOLERANCE:
        return False, "FUTURE_PUBLISHED_TIME"
    if observed - published > GOOGLE_NEWS_MAX_AGE:
        return False, "SEARCH_RESULT_TOO_OLD"
    text = " " + re.sub(r"\s+", " ", (headline or "").casefold()).strip() + " "
    for required_group in _TERMS[source]:
        if not any(term in text for term in required_group):
            return False, "TITLE_NOT_RELEVANT_TO_LANE"
    return True, "RELEVANT_RECENT_ITEM"


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
    period = "unknown"
    if published_at is not None:
        published = published_at.replace(
            tzinfo=published_at.tzinfo or UTC
        ).astimezone(UTC)
        period = f"{published.year:04d}-{published.month:02d}"
        if published.day <= 10:
            year = published.year if published.month > 1 else published.year - 1
            month = published.month - 1 if published.month > 1 else 12
            prior_period = f"{year:04d}-{month:02d}"
        else:
            prior_period = period
    else:
        prior_period = period

    employment_terms = (
        "nonfarm", "payroll", "jobs report", "employment situation",
        "unemployment", "就业报告", "非农", "就业形势", "失业率",
    )
    if any(term in text for term in employment_terms):
        return f"us-employment-report:{prior_period}"
    if any(term in text for term in ("consumer price", " cpi ", "消费者价格", "通胀报告")):
        return f"us-cpi:{prior_period}"
    if any(term in text for term in (" fomc ", "federal reserve", "美联储")):
        return f"fed:{period}"
    if any(term in text for term in ("central bank", "gold reserve", "gold purchase", "央行", "黄金储备", "购金")):
        return f"central-bank-gold:{period}"

    # Fallback only collapses near-identical titles. Publisher suffixes and
    # punctuation do not create separate admission families.
    tokens = re.findall(r"[a-z0-9\u4e00-\u9fff]+", text)
    stop = {"the", "a", "an", "and", "or", "to", "of", "in", "on", "for"}
    canonical = "-".join(token for token in tokens if token not in stop)[:120]
    return f"{source}:{canonical or 'untitled'}"
