"""Frozen, first-seen-aged news features for Phase 2F V2."""

from __future__ import annotations

import math
from datetime import datetime

from .evidence_v2 import ELIGIBILITY_VERSION
from .factors import MACRO_FEATURE_MAP, NEWS_FEATURES
from .forward_ledger import canonical_hash


SOURCE_RULES = {
    "federal_reserve_monetary": ("MODEL_ELIGIBLE", True, 200, "official monetary policy publisher"),
    "federal_reserve_press_all": ("MODEL_ELIGIBLE", True, 200, "official central-bank publisher"),
    "federal_reserve_speeches_testimony": ("MODEL_ELIGIBLE", True, 200, "official central-bank publisher"),
    "bea_economic_releases": ("MODEL_ELIGIBLE", True, 200, "official economic-statistics publisher"),
    "us_treasury_press_releases": ("MODEL_ELIGIBLE", True, 200, "official fiscal publisher"),
    "eia_press_releases": ("RESEARCH_CANDIDATE", True, 200, "official energy publisher pending feature audit"),
    "eia_today_in_energy": ("RESEARCH_CANDIDATE", True, 200, "official energy publisher pending feature audit"),
    "ecb_press_releases": ("RESEARCH_CANDIDATE", True, 200, "official publisher pending XAUUSD relevance audit"),
    "world_gold_council_central_banks": ("RESEARCH_CANDIDATE", True, 200, "publisher body retained; source qualification pending"),
    "gdelt_gold_geopolitics": ("DISPLAY_ONLY", True, 200, "aggregator metadata is not action-bearing"),
    "google_news_gold_context": ("DISPLAY_ONLY", True, 200, "aggregated discovery is display-only"),
    "google_news_gold_geopolitics": ("COLLECT_ONLY", True, 200, "headline-only aggregation"),
}


def frozen_rule_rows() -> list[tuple[str, str, int, int, str]]:
    return [
        (source, tier, int(requires_body), minimum, rationale)
        for source, (tier, requires_body, minimum, rationale) in sorted(SOURCE_RULES.items())
    ]


def aggregate_news_features_v2(ledger, decision_time: datetime) -> dict:
    """Aggregate only frozen MODEL_ELIGIBLE publisher bodies visible then."""
    selected = []
    for annotation in ledger.visible_annotations(decision_time):
        news = ledger.connection.execute(
            """SELECT * FROM news_revisions
            WHERE source=? AND source_item_id=? AND revision_number=?""",
            (annotation["source"], annotation["source_item_id"], annotation["revision_number"]),
        ).fetchone()
        if news is None:
            continue
        tier, requires_body, minimum, _ = SOURCE_RULES.get(
            str(news["source"]), ("COLLECT_ONLY", True, 200, "unlisted source")
        )
        body = str(news["body"] or "")
        if tier != "MODEL_ELIGIBLE" or (requires_body and len(body) < minimum):
            continue
        selected.append((news, annotation))

    # One canonical publisher item per duplicate cluster.
    canonical = {}
    for news, annotation in selected:
        cluster = str(news["cluster_id"])
        current = canonical.get(cluster)
        candidate = (len(str(news["body"] or "")), str(news["source_item_id"]))
        if current is None or candidate > current[0]:
            canonical[cluster] = (candidate, news, annotation)

    totals = {name: 0.0 for name in NEWS_FEATURES}
    weight_sum = 0.0
    event_types = set()
    evidence = []
    for _, news, row in canonical.values():
        first_seen = datetime.fromisoformat(news["collector_first_seen_time"])
        parsed_at = datetime.fromisoformat(row["parsed_at"])
        age_minutes = max(0.0, (decision_time - first_seen).total_seconds() / 60.0)
        processing_delay = (parsed_at - first_seen).total_seconds()
        freshness = math.exp(-math.log(2.0) * age_minutes / 360.0)
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
        event_types.add(str(row["event_type"]))
        evidence.append((news["content_hash"], row["annotation_id"], age_minutes, processing_delay))
    if weight_sum:
        for name in (
            "news_hawkishness", "news_inflation_impulse", "news_growth_impulse",
            "news_geopolitical_risk", "news_usd_impulse", "news_novelty",
            "news_confidence",
        ):
            totals[name] /= weight_sum

    macro_rows = ledger.connection.execute(
        """SELECT m.series_id, m.observation_period, m.value, m.content_hash
        FROM macro_observations m
        WHERE m.collector_first_seen_time <= ?
          AND NOT EXISTS (
            SELECT 1 FROM macro_observations newer
            WHERE newer.source=m.source AND newer.series_id=m.series_id
              AND newer.observation_period=m.observation_period
              AND newer.revision_number>m.revision_number
              AND newer.collector_first_seen_time <= ?)
        ORDER BY m.series_id, m.observation_period""",
        (decision_time.isoformat(), decision_time.isoformat()),
    ).fetchall()
    grouped = {}
    for row in macro_rows:
        grouped.setdefault(row["series_id"], []).append(row)
    for series_id, (level_name, change_name) in MACRO_FEATURE_MAP.items():
        values = grouped.get(series_id, [])
        if values:
            totals[level_name] = float(values[-1]["value"])
            totals[change_name] = (
                float(values[-1]["value"]) - float(values[-2]["value"])
                if len(values) > 1 else 0.0
            )
            evidence.append((series_id, values[-1]["content_hash"]))
    return {
        "features": totals,
        "eligibility_version": ELIGIBILITY_VERSION,
        "model_visible_items": len(canonical),
        "news_exposed": int(bool(canonical)),
        "distinct_news_clusters": len(canonical),
        "distinct_event_types": len(event_types),
        "source_evidence_hash": canonical_hash(evidence),
    }
