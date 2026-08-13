"""Runtime registry for news-source collection health surfaces."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class NewsSourceHealthSpec:
    source: str
    label: str
    role: str
    stale_minutes: int
    revision_sources: tuple[str, ...]


NEWS_SOURCE_REGISTRY = (
    NewsSourceHealthSpec(
        "federal_reserve_full_text", "Federal Reserve", "发布源", 15,
        ("federal_reserve_monetary", "federal_reserve_press_all",
         "federal_reserve_speeches_testimony"),
    ),
    NewsSourceHealthSpec(
        "us_treasury_press_releases", "U.S. Treasury", "发布源", 45,
        ("us_treasury_press_releases",),
    ),
    NewsSourceHealthSpec(
        "bea_economic_releases", "U.S. BEA", "发布源", 45,
        ("bea_economic_releases",),
    ),
    NewsSourceHealthSpec(
        "bls_public_api", "BLS Public Data API", "数值与修订链路", 75, (),
    ),
    NewsSourceHealthSpec(
        "ecb_press_releases", "European Central Bank", "发布源", 45,
        ("ecb_press_releases",),
    ),
    NewsSourceHealthSpec(
        "eia_press_releases", "U.S. EIA Press", "发布源", 45,
        ("eia_press_releases",),
    ),
    NewsSourceHealthSpec(
        "eia_today_in_energy", "U.S. EIA Energy", "发布源", 45,
        ("eia_today_in_energy",),
    ),
    NewsSourceHealthSpec(
        "gdelt_gold_geopolitics", "GDELT", "发现源", 75,
        ("gdelt_gold_geopolitics",),
    ),
    NewsSourceHealthSpec(
        "google_news_gold_context", "Google News Context", "发现源", 45,
        ("google_news_gold_context",),
    ),
    NewsSourceHealthSpec(
        "google_news_us_employment", "Google News U.S. Employment", "发现源", 45,
        ("google_news_us_employment",),
    ),
    NewsSourceHealthSpec(
        "google_news_us_inflation", "Google News U.S. Inflation", "发现源", 45,
        ("google_news_us_inflation",),
    ),
    NewsSourceHealthSpec(
        "google_news_fed_rates", "Google News Fed & Rates", "发现源", 45,
        ("google_news_fed_rates",),
    ),
    NewsSourceHealthSpec(
        "world_gold_council_central_banks", "World Gold Council", "发布源", 420,
        ("world_gold_council_central_banks",),
    ),
)

NEWS_SOURCE_BY_NAME = {spec.source: spec for spec in NEWS_SOURCE_REGISTRY}
if len(NEWS_SOURCE_BY_NAME) != len(NEWS_SOURCE_REGISTRY):
    raise RuntimeError("news source registry contains duplicate source names")
