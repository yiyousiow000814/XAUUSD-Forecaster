"""Point-in-time factor aggregation and explicit coverage registry."""

from __future__ import annotations

import math
from datetime import datetime
from typing import TYPE_CHECKING

from .news_semantics import V1_NEWS_PROMPT_VERSIONS
from .news_identity import news_representative_key
from .macro_release import MACRO_RELEASE_FEATURES

if TYPE_CHECKING:
    from xauusd_forecaster.evidence.ledger import ForwardLedger


NEWS_FEATURES = (
    "news_hawkishness",
    "news_inflation_impulse",
    "news_growth_impulse",
    "news_geopolitical_risk",
    "news_usd_impulse",
    "news_novelty",
    "news_confidence",
    "news_event_count",
    "rate_2y_level",
    "rate_2y_change",
    "real_yield_10y_level",
    "real_yield_10y_change",
    "usd_broad_level",
    "usd_broad_change",
    "oil_wti_level",
    "oil_wti_change",
    "fed_assets_level",
    "fed_assets_change",
    "vix_level",
    "vix_change",
    *MACRO_RELEASE_FEATURES,
)

NEWS_PROMPT_VERSIONS = V1_NEWS_PROMPT_VERSIONS
NEWS_MODEL_VERSIONS = (
    "gemini-3.5-flash-lite",
    "gemini-3.1-flash-lite",
)

MACRO_FEATURE_MAP = {
    "DGS2": ("rate_2y_level", "rate_2y_change"),
    "DFII10": ("real_yield_10y_level", "real_yield_10y_change"),
    "DTWEXBGS": ("usd_broad_level", "usd_broad_change"),
    "DCOILWTICO": ("oil_wti_level", "oil_wti_change"),
    "WALCL": ("fed_assets_level", "fed_assets_change"),
    "VIXCLS": ("vix_level", "vix_change"),
}


def aggregate_news_features(
    ledger: ForwardLedger,
    decision_time: datetime,
    *,
    model_versions: tuple[str, ...] = NEWS_MODEL_VERSIONS,
    prompt_versions: tuple[str, ...] = NEWS_PROMPT_VERSIONS,
    half_life_minutes: float = 360.0,
) -> dict[str, float]:
    """Aggregate only annotations that were actually visible at decision time."""
    if half_life_minutes <= 0:
        raise ValueError("news half-life must be positive")
    canonical_news: dict[str, object] = {}
    for row in ledger.visible_news(decision_time):
        cluster = str(row["cluster_id"])
        current = canonical_news.get(cluster)
        if current is None or news_representative_key(row) < news_representative_key(
            current,
        ):
            canonical_news[cluster] = row
    latest_news = {
        (row["source"], row["source_item_id"]): int(row["revision_number"])
        for row in canonical_news.values()
    }
    prompt_priority = {version: index for index, version in enumerate(prompt_versions)}
    model_priority = {version: index for index, version in enumerate(model_versions)}
    selected_by_item = {}
    for row in ledger.visible_annotations(decision_time):
        key = (row["source"], row["source_item_id"])
        if (
            row["llm_model_version"] not in model_priority
            or row["prompt_version"] not in prompt_priority
            or latest_news.get(key) != int(row["revision_number"])
        ):
            continue
        current = selected_by_item.get(key)
        candidate_priority = (
            prompt_priority[row["prompt_version"]],
            model_priority[row["llm_model_version"]],
        )
        current_priority = (
            prompt_priority[current["prompt_version"]],
            model_priority[current["llm_model_version"]],
        ) if current is not None else None
        if current_priority is None or candidate_priority < current_priority:
            selected_by_item[key] = row
    selected = list(selected_by_item.values())
    totals = {name: 0.0 for name in NEWS_FEATURES}
    weight_sum = 0.0
    for row in selected:
        parsed_at = datetime.fromisoformat(row["parsed_at"])
        age_minutes = max(0.0, (decision_time - parsed_at).total_seconds() / 60.0)
        freshness = math.exp(-math.log(2.0) * age_minutes / half_life_minutes)
        confidence = float(row["confidence"])
        novelty = float(row["novelty"])
        weight = freshness * confidence * max(0.05, novelty)
        weight_sum += weight
        totals["news_hawkishness"] += weight * float(row["hawkishness"])
        totals["news_inflation_impulse"] += weight * float(row["inflation_impulse"])
        totals["news_growth_impulse"] += weight * float(row["growth_impulse"])
        totals["news_geopolitical_risk"] += weight * float(row["geopolitical_risk"])
        totals["news_usd_impulse"] += weight * float(row["usd_impulse"])
        totals["news_novelty"] += weight * novelty
        totals["news_confidence"] += weight * confidence
        totals["news_event_count"] += freshness
    if weight_sum:
        for name in (
            "news_hawkishness", "news_inflation_impulse", "news_growth_impulse",
            "news_geopolitical_risk", "news_usd_impulse", "news_novelty",
            "news_confidence",
        ):
            totals[name] /= weight_sum
    macro_rows = ledger.connection.execute(
        """SELECT m.series_id, m.observation_period, m.value
           FROM macro_observations m
           WHERE m.collector_first_seen_time <= ?
             AND m.series_id IN ('DGS2','DFII10','DTWEXBGS','DCOILWTICO','WALCL','VIXCLS')
             AND NOT EXISTS (
               SELECT 1 FROM macro_observations newer
               WHERE newer.source=m.source AND newer.series_id=m.series_id
                 AND newer.observation_period=m.observation_period
                 AND newer.revision_number > m.revision_number
                 AND newer.collector_first_seen_time <= ?)
           ORDER BY m.series_id, m.observation_period""",
        (decision_time.isoformat(), decision_time.isoformat()),
    ).fetchall()
    grouped: dict[str, list[float]] = {}
    for row in macro_rows:
        grouped.setdefault(row["series_id"], []).append(float(row["value"]))
    for series_id, (level_name, change_name) in MACRO_FEATURE_MAP.items():
        values = grouped.get(series_id, [])
        if values:
            totals[level_name] = values[-1]
            totals[change_name] = values[-1] - values[-2] if len(values) > 1 else 0.0
    return totals


FACTOR_COVERAGE_NEWS_SOURCES = frozenset({
    "gdelt_gold_geopolitics",
    "google_news_gold_geopolitics",
    "google_news_gold_context",
    "us_treasury_press_releases",
    "world_gold_council_central_banks",
})
FACTOR_COVERAGE_MACRO_SERIES = frozenset({
    "DGS2", "DFII10", "DTWEXBGS", "DCOILWTICO", "WALCL", "VIXCLS",
})


def factor_coverage(
    latest_macro: dict[str, dict[str, object]] | None = None,
    news_sources: set[str] | None = None,
    monitored_news_sources: set[str] | None = None,
) -> list[dict[str, object]]:
    """Expose missing domains instead of silently pretending they are covered."""
    latest_macro = latest_macro or {}
    news_sources = news_sources or set()
    monitored_news_sources = monitored_news_sources or set()

    wgc_source = "world_gold_council_central_banks"
    wgc_has_release = wgc_source in news_sources
    wgc_is_monitored = wgc_source in monitored_news_sources

    def macro(domain: str, series_id: str, source: str, cadence: str) -> dict[str, object]:
        observation = latest_macro.get(series_id)
        return {
            "domain": domain,
            "status": "COLLECTING" if observation else "WARMING_UP",
            "source": source,
            "action_bearing": False,
            "cadence": cadence,
            "value": observation.get("value") if observation else None,
            "observed_at": observation.get("observation_period") if observation else None,
            "unit": observation.get("unit") if observation else None,
        }

    return [
        {"domain": "黄金自身", "status": "LIVE", "source": "cTrader XAUUSD Bid/Ask", "action_bearing": True, "cadence": "Tick/5分钟"},
        macro("利率", "DGS2", "FRED DGS2 · 2Y Treasury", "日度"),
        macro("实际收益率", "DFII10", "FRED DFII10 · 10Y TIPS", "日度"),
        macro("美元", "DTWEXBGS", "Fed/FRED Broad Dollar + Gemini", "日度/事件"),
        {"domain": "通胀", "status": "COLLECTING", "source": "BLS CPI/Core CPI + Gemini", "action_bearing": False, "cadence": "月度/事件"},
        {"domain": "就业", "status": "COLLECTING", "source": "BLS Payroll/AHE/Unemployment/JOLTS", "action_bearing": False, "cadence": "月度/事件"},
        macro("油价", "DCOILWTICO", "EIA/FRED WTI spot", "日度"),
        {"domain": "战争/地缘", "status": "COLLECTING" if {"gdelt_gold_geopolitics", "google_news_gold_geopolitics", "google_news_gold_context", "us_treasury_press_releases"} & news_sources else "WARMING_UP", "source": "GDELT + Google News publisher resolution + U.S. Treasury + Gemini", "action_bearing": False, "cadence": "20分钟/事件"},
        {"domain": "央行购金", "status": "COLLECTING" if wgc_has_release or wgc_is_monitored else "WARMING_UP", "status_reason": "已收到 World Gold Council 正式央行购金资料" if wgc_has_release else "监测正常，暂无新的正式月度资料" if wgc_is_monitored else "监测尚未启动", "source": "World Gold Council central-bank monitor", "action_bearing": False, "cadence": "6小时/月度"},
        macro("流动性", "WALCL", "Fed/FRED total assets", "周度"),
        macro("风险偏好", "VIXCLS", "CBOE/FRED VIX", "日度"),
    ]
