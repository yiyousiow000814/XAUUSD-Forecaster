"""Frozen, first-seen-aged news features for Phase 2F V2."""

from __future__ import annotations

import json
import math
from datetime import datetime

from xauusd_forecaster.evidence.schema import ELIGIBILITY_VERSION
from xauusd_forecaster.factors import MACRO_FEATURE_MAP, NEWS_FEATURES
from xauusd_forecaster.evidence.ledger import canonical_hash
from xauusd_forecaster.news.collection.macro_release import macro_release_features_at
from xauusd_forecaster.news.semantics.model_contracts import CORE_MODEL_STORAGE_PERMISSION
from xauusd_forecaster.news.semantics.evidence import BROAD_NEWS_FEATURES, event_evidence_rows
from xauusd_forecaster.news.annotation.impact import impact_time_rule
from xauusd_forecaster.news.semantics.contracts import ACTIONABLE_CATEGORIES


COLLECTION_SOURCES = (
    "bea_economic_releases", "bls_consumer_price_index",
    "bls_employment_situation", "bls_job_openings", "ecb_press_releases",
    "eia_press_releases", "eia_today_in_energy", "federal_reserve_monetary",
    "federal_reserve_press_all", "federal_reserve_speeches_testimony",
    "gdelt_gold_geopolitics", "google_news_bls_official_releases",
    "google_news_fed_rates", "google_news_gold_context",
    "google_news_gold_geopolitics", "google_news_us_employment",
    "google_news_us_inflation", "us_treasury_press_releases",
    "world_gold_council_central_banks",
)

EVIDENCE_GRADE_WEIGHT = {
    "PRIMARY": 1.0,
    "CORROBORATED": 1.0,
    "SINGLE_RELIABLE": 0.35,
    "SINGLE_SOURCE": 0.20,
}


def _finalize_weighted_signals(
    totals: dict[str, float], names: tuple[str, ...], weight_sum: float,
) -> None:
    """Keep sparse evidence trust after averaging directional measurements."""
    if weight_sum <= 0:
        return
    signal_strength = min(1.0, weight_sum)
    for name in names:
        totals[name] = totals[name] / weight_sum * signal_strength


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


def frozen_rule_rows() -> list[tuple[str, str, int, int, str]]:
    return [
        (source, "RESEARCH_CANDIDATE", 1, 200,
         "collection is permission-neutral; generation evaluates source attributes")
        for source in COLLECTION_SOURCES
    ]


def event_raw_weight(row: dict) -> float:
    """Return the event's live-known contribution before generation budgeting."""
    age_minutes = max(0.0, float(row.get("economic_age_minutes") or 0.0))
    _, half_life_minutes = impact_time_rule(str(row.get("impact_class") or "BACKGROUND"))
    freshness = math.exp(-math.log(2.0) * age_minutes / half_life_minutes)
    evidence_weight = EVIDENCE_GRADE_WEIGHT.get(
        str(row.get("evidence_grade") or ""), 0.0
    )
    return (
        evidence_weight * freshness * float(row["confidence"])
        * max(0.05, float(row["novelty"]))
    )


def aggregate_news_features_v2(ledger, decision_time: datetime) -> dict:
    """Aggregate one unified event snapshot into Core and Broad features."""
    all_evidence_events = event_evidence_rows(ledger, decision_time)
    core_events = [row for row in all_evidence_events if row["core_model_eligible"]]
    broad_events = [row for row in all_evidence_events if row["broad_model_eligible"]]
    totals = {name: 0.0 for name in NEWS_FEATURES}
    weight_sum = 0.0
    event_types = set()
    evidence = []
    for row in core_events:
        age_minutes = float(row["economic_age_minutes"])
        _, half_life_minutes = impact_time_rule(str(row.get("impact_class") or "BACKGROUND"))
        freshness = math.exp(-math.log(2.0) * age_minutes / half_life_minutes)
        confidence = float(row["confidence"])
        novelty = float(row["novelty"])
        weight = event_raw_weight(row)
        weight_sum += weight
        totals["news_hawkishness"] += weight * float(row["hawkishness"])
        totals["news_inflation_impulse"] += weight * float(row["inflation_impulse"])
        totals["news_growth_impulse"] += weight * float(row["growth_impulse"])
        totals["news_geopolitical_risk"] += weight * float(row["geopolitical_risk"])
        totals["news_usd_impulse"] += weight * float(row["usd_impulse"])
        totals["news_novelty"] += weight * novelty
        totals["news_confidence"] += weight * confidence
        totals["news_event_count"] += freshness
        event_types.add(str(row.get("event_type") or row.get("record_kind") or ""))
        evidence.append((
            row["event_id"], row["event_version_id"], row["source_hash"],
            row["event_occurred_at"], age_minutes,
        ))
    _finalize_weighted_signals(
        totals,
        (
            "news_hawkishness", "news_inflation_impulse", "news_growth_impulse",
            "news_geopolitical_risk", "news_usd_impulse", "news_novelty",
            "news_confidence",
        ),
        weight_sum,
    )

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

    release_features, release_packets = macro_release_features_at(ledger, decision_time)
    totals.update(release_features)
    evidence.extend(
        (packet["series_id"], packet["content_hash"], packet["relation_to_prior"])
        for packet in release_packets
    )

    broad_totals = {name: 0.0 for name in BROAD_NEWS_FEATURES}
    broad_weight_sum = 0.0
    broad_evidence = []
    for row in broad_events:
        age_minutes = float(row["economic_age_minutes"])
        _, half_life_minutes = impact_time_rule(
            str(row.get("impact_class") or "BACKGROUND")
        )
        evidence_weight = EVIDENCE_GRADE_WEIGHT[row["evidence_grade"]]
        freshness = (
            evidence_weight
            * math.exp(-math.log(2.0) * age_minutes / half_life_minutes)
        )
        weight = event_raw_weight(row)
        broad_weight_sum += weight
        broad_totals["broad_news_hawkishness"] += weight * row["hawkishness"]
        broad_totals["broad_news_inflation_impulse"] += weight * row["inflation_impulse"]
        broad_totals["broad_news_growth_impulse"] += weight * row["growth_impulse"]
        broad_totals["broad_news_geopolitical_risk"] += weight * row["geopolitical_risk"]
        broad_totals["broad_news_usd_impulse"] += weight * row["usd_impulse"]
        broad_totals["broad_news_novelty"] += weight * row["novelty"]
        broad_totals["broad_news_confidence"] += weight * row["confidence"]
        broad_totals["broad_news_event_count"] += freshness
        if row["evidence_grade"] == "PRIMARY":
            broad_totals["broad_primary_event_count"] += freshness
        elif row["evidence_grade"] == "CORROBORATED":
            broad_totals["broad_corroborated_event_count"] += freshness
        else:
            broad_totals["broad_single_source_event_count"] += freshness
        broad_totals["broad_first_party_source_count"] += (
            freshness * float(row.get("first_party_source") or 0.0)
        )
        broad_totals["broad_independent_source_count"] += (
            freshness * float(row.get("independent_publishers") or 0.0)
        )
        broad_totals["broad_source_reliability"] += (
            freshness * float(row.get("source_reliability") or 0.0)
        )
        broad_totals["broad_syndicated_duplicate_count"] += (
            freshness * float(row.get("syndicated_duplicate_count") or 0.0)
        )
        for topic in row["topics"]:
            name = f"broad_topic_{topic}"
            if name in broad_totals:
                broad_totals[name] += freshness
        broad_evidence.append((
            row["event_id"], row["event_version_id"], row["source_hash"], age_minutes,
        ))
    _finalize_weighted_signals(
        broad_totals,
        (
            "broad_news_hawkishness", "broad_news_inflation_impulse",
            "broad_news_growth_impulse", "broad_news_geopolitical_risk",
            "broad_news_usd_impulse", "broad_news_novelty",
            "broad_news_confidence",
        ),
        broad_weight_sum,
    )
    totals.update(broad_totals)
    core_visible_events = [_visibility_event_ref(row, row, row) for row in core_events]
    broad_visible_events = [
        _visibility_event_ref(row, row, row)
        for row in broad_events
    ]
    event_snapshots = []
    for permission, rows in (
        (CORE_MODEL_STORAGE_PERMISSION, core_events), ("BROAD_MODEL", broad_events),
    ):
        for row in rows:
            event_snapshots.append({
                "event_id": row["event_id"],
                "event_version_id": row["event_version_id"],
                "policy_version": row["policy_version"],
                "event_occurred_at": row["event_occurred_at"],
                "event_clock_source": row["event_clock_source"],
                "event_time_precision": row["event_time_precision"],
                "canonical_source": row["canonical_source"],
                "source_budget_id": row["source_budget_id"],
                "canonical_source_item_id": row["canonical_source_item_id"],
                "source_hash": row["source_hash"],
                "evidence_grade": row["evidence_grade"],
                "model_permission": permission,
                "model_permissions": (
                    [CORE_MODEL_STORAGE_PERMISSION, "BROAD_MODEL"]
                    if row["core_model_eligible"] else ["BROAD_MODEL"]
                ),
                "reason_codes": list(row["reason_codes"]),
                "raw_weight": event_raw_weight(row),
                "age_minutes": float(row["economic_age_minutes"]),
            })
    return {
        "features": totals,
        "eligibility_version": ELIGIBILITY_VERSION,
        "model_visible_items": len(core_events),
        "news_exposed": int(bool(core_events)),
        "distinct_news_clusters": len(core_events),
        "distinct_event_types": len(event_types),
        "broad_model_visible_items": len(broad_events),
        "broad_news_exposed": int(bool(broad_events)),
        "distinct_broad_clusters": len(broad_events),
        "broad_source_evidence_hash": canonical_hash(broad_evidence),
        "source_evidence_hash": canonical_hash((evidence, broad_evidence)),
        # These references are written to a separate append-only visibility ledger.
        # They are intentionally excluded from the aggregate feature snapshot hash.
        "core_visible_events": core_visible_events,
        "broad_visible_events": broad_visible_events,
        "event_snapshots": event_snapshots,
    }
