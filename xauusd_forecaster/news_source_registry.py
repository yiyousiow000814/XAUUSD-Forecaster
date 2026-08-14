"""Runtime registry for every source emitted by the news collection pipeline."""

from __future__ import annotations

from dataclasses import dataclass

from .news import (
    BEA_API_SOURCE,
    BLS_SOURCE,
    DIRECT_FULL_TEXT_HTML_SOURCES,
    DIRECT_FULL_TEXT_RSS_SOURCES,
    EIA_API_SOURCE,
    FED_POLL_SOURCE,
    FRED_POLL_SOURCE,
    GDELT_SOURCE,
    GOOGLE_NEWS_LANES,
    RUNTIME_NEWS_POLL_SOURCES,
    WGC_SOURCE,
)


@dataclass(frozen=True)
class NewsSourceHealthSpec:
    source: str
    label: str
    role: str
    stale_minutes: int
    revision_sources: tuple[str, ...]


def _revision_spec(
    source: str,
    label: str,
    role: str,
    stale_minutes: int,
) -> NewsSourceHealthSpec:
    return NewsSourceHealthSpec(
        source, label, role, stale_minutes, (source,),
    )


_DIRECT_LABELS = {
    "eia_today_in_energy": "U.S. EIA Energy",
    "eia_press_releases": "U.S. EIA Press",
    "ecb_press_releases": "European Central Bank",
    "us_treasury_press_releases": "U.S. Treasury",
    "bea_economic_releases": "U.S. BEA",
}
_GOOGLE_LABELS = {
    "google_news_gold_context": "Google News Context",
    "google_news_us_employment": "Google News U.S. Employment",
    "google_news_us_inflation": "Google News U.S. Inflation",
    "google_news_fed_rates": "Google News Fed & Rates",
}


NEWS_SOURCE_REGISTRY = (
    NewsSourceHealthSpec(
        FED_POLL_SOURCE,
        "Federal Reserve",
        "发布源",
        15,
        (
            "federal_reserve_monetary",
            "federal_reserve_press_all",
            "federal_reserve_speeches_testimony",
        ),
    ),
    *(
        _revision_spec(
            source.name,
            _DIRECT_LABELS[source.name],
            "发布源",
            45,
        )
        for source in DIRECT_FULL_TEXT_HTML_SOURCES
    ),
    *(
        _revision_spec(
            source.name,
            _DIRECT_LABELS[source.name],
            "发布源",
            45,
        )
        for source in DIRECT_FULL_TEXT_RSS_SOURCES
    ),
    NewsSourceHealthSpec(
        BLS_SOURCE, "BLS Public Data API", "数值与修订链路", 75, (),
    ),
    NewsSourceHealthSpec(
        FRED_POLL_SOURCE, "Federal Reserve Economic Data", "数值与修订链路", 75, (),
    ),
    NewsSourceHealthSpec(
        EIA_API_SOURCE, "U.S. EIA Open Data API", "数值与修订链路", 75, (),
    ),
    NewsSourceHealthSpec(
        BEA_API_SOURCE, "U.S. BEA Data API", "数值与修订链路", 75, (),
    ),
    _revision_spec(GDELT_SOURCE, "GDELT", "发现源", 75),
    *(
        _revision_spec(
            lane.name,
            _GOOGLE_LABELS[lane.name],
            "发现源",
            45,
        )
        for lane in GOOGLE_NEWS_LANES
    ),
    _revision_spec(WGC_SOURCE, "World Gold Council", "发布源", 420),
)

NEWS_SOURCE_BY_NAME = {spec.source: spec for spec in NEWS_SOURCE_REGISTRY}
if len(NEWS_SOURCE_BY_NAME) != len(NEWS_SOURCE_REGISTRY):
    raise RuntimeError("news source registry contains duplicate source names")
if set(NEWS_SOURCE_BY_NAME) != set(RUNTIME_NEWS_POLL_SOURCES):
    missing = sorted(RUNTIME_NEWS_POLL_SOURCES - set(NEWS_SOURCE_BY_NAME))
    obsolete = sorted(set(NEWS_SOURCE_BY_NAME) - RUNTIME_NEWS_POLL_SOURCES)
    raise RuntimeError(
        f"news source health registry drift: missing={missing}, obsolete={obsolete}"
    )
