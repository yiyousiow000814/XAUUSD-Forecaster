"""Frozen economic-time news features for already-trained V3 artifacts.

The active feature contract may evolve, but deployed Shadow artifacts must keep
receiving the exact point-in-time feature surface they were trained against.
"""

from __future__ import annotations

import json
import math
from datetime import datetime

from .factors import MACRO_FEATURE_MAP, NEWS_FEATURES
from .forward_ledger import canonical_hash
from .news_evidence import (
    BROAD_NEWS_FEATURES,
    LEGACY_V3_EVIDENCE_POLICY_VERSION,
    event_evidence_rows_from_connection,
)
from .news_time import assess_news_time


LEGACY_V3_NEWS_FEATURE_VERSION = "eligible-news-event-evidence-v4-economic-time"
LEGACY_V3_ELIGIBILITY_VERSION = "news-source-eligibility-v3-economic-time"
LEGACY_V3_BROAD_ELIGIBILITY_VERSION = (
    f"{LEGACY_V3_ELIGIBILITY_VERSION}+{LEGACY_V3_EVIDENCE_POLICY_VERSION}"
)
LEGACY_V3_SOURCE_RULES = {
    "federal_reserve_monetary": ("MODEL_ELIGIBLE", True, 200),
    "federal_reserve_press_all": ("MODEL_ELIGIBLE", True, 200),
    "federal_reserve_speeches_testimony": ("MODEL_ELIGIBLE", True, 200),
    "bea_economic_releases": ("MODEL_ELIGIBLE", True, 200),
    "us_treasury_press_releases": ("MODEL_ELIGIBLE", True, 200),
}
ACTIONABLE_CATEGORIES = frozenset({
    "rates_fed", "inflation_employment", "growth_economy", "usd_liquidity",
    "oil_energy", "war_geopolitics", "central_bank_gold", "risk_sentiment",
})


def _visibility_event_ref(event: dict | None, news, annotation) -> dict:
    news_row = dict(news)
    annotation_row = dict(annotation)
    annotation_payload = json.loads(annotation_row.get("annotation_json") or "{}")
    if event is not None:
        return {
            "event_key": event["event_key"],
            "source_hash": event["source_hash"],
            "canonical_headline": event["canonical_headline"],
            "canonical_source": event["canonical_source"],
            "source_published_time": event.get("source_published_time"),
            "collector_first_seen_time": event["collector_first_seen_time"],
            "topics": list(event.get("topics") or []),
            "evidence_grade": event.get("evidence_grade") or "FROZEN_MODEL_INPUT",
        }
    return {
        "event_key": str(news_row["cluster_id"]),
        "source_hash": canonical_hash((news_row["content_hash"], annotation_row["annotation_id"])),
        "canonical_headline": str(
            annotation_payload.get("headline_zh") or news_row["headline"]
        ),
        "canonical_source": str(news_row["source"]),
        "source_published_time": news_row.get("source_published_time"),
        "collector_first_seen_time": news_row["collector_first_seen_time"],
        "topics": [str(annotation_payload.get("primary_category") or "official")],
        "evidence_grade": "PRIMARY",
    }


def aggregate_legacy_news_features_v3(ledger, decision_time: datetime) -> dict:
    """Reproduce the committed V3 eligibility and economic-time contract."""
    selected = []
    for annotation in ledger.visible_annotations(decision_time):
        news = ledger.connection.execute(
            """SELECT * FROM news_revisions
            WHERE source=? AND source_item_id=? AND revision_number=?""",
            (annotation["source"], annotation["source_item_id"], annotation["revision_number"]),
        ).fetchone()
        if news is None:
            continue
        tier, requires_body, minimum = LEGACY_V3_SOURCE_RULES.get(
            str(news["source"]), ("COLLECT_ONLY", True, 200)
        )
        body = str(news["body"] or "")
        if tier != "MODEL_ELIGIBLE" or (requires_body and len(body) < minimum):
            continue
        timing = assess_news_time(
            news, decision_time=decision_time, forward_epoch=ledger.forward_epoch
        )
        annotation_payload = json.loads(annotation["annotation_json"] or "{}")
        if (
            not timing.eligible
            or annotation_payload.get("primary_category") not in ACTIONABLE_CATEGORIES
        ):
            continue
        selected.append((news, annotation, timing))

    canonical = {}
    for news, annotation, timing in selected:
        cluster = str(news["cluster_id"])
        candidate = (len(str(news["body"] or "")), str(news["source_item_id"]))
        if cluster not in canonical or candidate > canonical[cluster][0]:
            canonical[cluster] = (candidate, news, annotation, timing)

    totals = {name: 0.0 for name in NEWS_FEATURES}
    weight_sum = 0.0
    event_types = set()
    evidence = []
    for _, news, row, timing in canonical.values():
        first_seen = datetime.fromisoformat(news["collector_first_seen_time"])
        parsed_at = datetime.fromisoformat(row["parsed_at"])
        age_minutes = float(timing.age_minutes or 0.0)
        freshness = math.exp(-math.log(2.0) * age_minutes / 360.0)
        confidence = float(row["confidence"])
        novelty = float(row["novelty"])
        weight = freshness * confidence * max(0.05, novelty)
        weight_sum += weight
        for name, column in (
            ("news_hawkishness", "hawkishness"),
            ("news_inflation_impulse", "inflation_impulse"),
            ("news_growth_impulse", "growth_impulse"),
            ("news_geopolitical_risk", "geopolitical_risk"),
            ("news_usd_impulse", "usd_impulse"),
        ):
            totals[name] += weight * float(row[column])
        totals["news_novelty"] += weight * novelty
        totals["news_confidence"] += weight * confidence
        totals["news_event_count"] += freshness
        event_types.add(str(row["event_type"]))
        evidence.append((
            news["content_hash"], row["annotation_id"],
            timing.event_time.isoformat() if timing.event_time else None,
            age_minutes, (parsed_at - first_seen).total_seconds(),
        ))
    if weight_sum:
        for name in (
            "news_hawkishness", "news_inflation_impulse", "news_growth_impulse",
            "news_geopolitical_risk", "news_usd_impulse", "news_novelty",
            "news_confidence",
        ):
            totals[name] /= weight_sum

    macro_rows = ledger.connection.execute(
        """SELECT m.series_id,m.observation_period,m.value,m.content_hash
        FROM macro_observations m WHERE m.collector_first_seen_time<=?
          AND NOT EXISTS (
            SELECT 1 FROM macro_observations newer
            WHERE newer.source=m.source AND newer.series_id=m.series_id
              AND newer.observation_period=m.observation_period
              AND newer.revision_number>m.revision_number
              AND newer.collector_first_seen_time<=?)
        ORDER BY m.series_id,m.observation_period""",
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

    all_events = event_evidence_rows_from_connection(
        ledger.connection, decision_time, legacy_v3=True,
    )
    broad_events = [row for row in all_events if row["broad_model_eligible"]]
    broad_totals = {name: 0.0 for name in BROAD_NEWS_FEATURES}
    broad_weight_sum = 0.0
    broad_evidence = []
    for row in broad_events:
        age_minutes = float(row["economic_age_minutes"])
        freshness = math.exp(-math.log(2.0) * age_minutes / 360.0)
        weight = freshness * row["confidence"] * max(0.05, row["novelty"])
        broad_weight_sum += weight
        for name, column in (
            ("broad_news_hawkishness", "hawkishness"),
            ("broad_news_inflation_impulse", "inflation_impulse"),
            ("broad_news_growth_impulse", "growth_impulse"),
            ("broad_news_geopolitical_risk", "geopolitical_risk"),
            ("broad_news_usd_impulse", "usd_impulse"),
        ):
            broad_totals[name] += weight * row[column]
        broad_totals["broad_news_novelty"] += weight * row["novelty"]
        broad_totals["broad_news_confidence"] += weight * row["confidence"]
        broad_totals["broad_news_event_count"] += freshness
        count_name = (
            "broad_primary_event_count" if row["evidence_grade"] == "PRIMARY"
            else "broad_corroborated_event_count"
        )
        broad_totals[count_name] += freshness
        for topic in row["topics"]:
            name = f"broad_topic_{topic}"
            if name in broad_totals:
                broad_totals[name] += freshness
        broad_evidence.append((row["event_cluster_id"], row["source_hash"], age_minutes))
    if broad_weight_sum:
        for name in (
            "broad_news_hawkishness", "broad_news_inflation_impulse",
            "broad_news_growth_impulse", "broad_news_geopolitical_risk",
            "broad_news_usd_impulse", "broad_news_novelty", "broad_news_confidence",
        ):
            broad_totals[name] /= broad_weight_sum
    totals.update(broad_totals)

    event_by_item = {
        (row["canonical_source"], row["canonical_source_item_id"]): row
        for row in all_events
    }
    official_visible_events = []
    for _, news, row, _ in canonical.values():
        event = event_by_item.get((news["source"], news["source_item_id"]))
        official_visible_events.append(_visibility_event_ref(event, news, row))
    broad_visible_events = [
        _visibility_event_ref(row, row, row)
        for row in broad_events
    ]
    return {
        "features": totals,
        "eligibility_version": LEGACY_V3_ELIGIBILITY_VERSION,
        "evidence_policy_version": LEGACY_V3_EVIDENCE_POLICY_VERSION,
        "model_visible_items": len(canonical),
        "news_exposed": int(bool(canonical)),
        "distinct_news_clusters": len(canonical),
        "distinct_event_types": len(event_types),
        "source_evidence_hash": canonical_hash((evidence, broad_evidence)),
        "official_visible_events": official_visible_events,
        "broad_visible_events": broad_visible_events,
    }
