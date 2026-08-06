"""Frozen compatibility features for already-trained news V2 artifacts.

This module preserves the exact pre-economic-time eligibility contract for
future Shadow evaluation.  It never trains a new model and never rewrites an
old prediction.  New models continue to use :mod:`news_features_v2`.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections import defaultdict
from datetime import datetime, timedelta

from .factors import MACRO_FEATURE_MAP, NEWS_FEATURES
from .forward_ledger import canonical_hash
from .news_evidence import (
    ACTION_TOPICS,
    BROAD_NEWS_FEATURES,
    BROAD_PRIMARY_SOURCES,
    _TITLE_STOPWORDS,
    _domain,
    _reliable_domain,
    _topics,
)


LEGACY_NEWS_FEATURE_VERSION = "eligible-news-event-evidence-v3"
LEGACY_ELIGIBILITY_VERSION = "news-source-eligibility-v2-event-evidence"
LEGACY_EVIDENCE_POLICY_VERSION = "news-event-evidence-v1"
LEGACY_BROAD_ELIGIBILITY_VERSION = (
    f"{LEGACY_ELIGIBILITY_VERSION}+{LEGACY_EVIDENCE_POLICY_VERSION}"
)
MAX_NEWS_AGE = timedelta(hours=72)

_CORE_MODEL_SOURCES = frozenset({
    "federal_reserve_monetary",
    "federal_reserve_press_all",
    "federal_reserve_speeches_testimony",
    "bea_economic_releases",
    "us_treasury_press_releases",
})
_LEGACY_PROMPTS = (
    "news-json-v10-controlled-category-zh",
    "news-json-v9-local-display-recovery",
    "news-json-v8-strict-zh-source-number-lexemes",
)


def _legacy_event_key(row: dict, topics: tuple[str, ...]) -> str:
    day = str(row["collector_first_seen_time"])[:10]
    entities = sorted({
        re.sub(r"\s+", " ", str(value).casefold()).strip()
        for value in json.loads(row.get("entities_json") or "[]")
        if str(value).strip()
    })
    if len(entities) >= 2:
        identity = (day, topics[0], tuple(entities))
    else:
        tokens = sorted({
            token for token in re.findall(r"[a-z0-9]+", str(row["headline"]).casefold())
            if token not in _TITLE_STOPWORDS
        })[:12]
        identity = (day, topics[0], tuple(entities), tuple(tokens))
    return hashlib.sha256(repr(identity).encode("utf-8")).hexdigest()


def _legacy_event_evidence_rows(connection, decision_time: datetime) -> list[dict]:
    cutoff = decision_time.isoformat()
    placeholders = ",".join("?" for _ in _LEGACY_PROMPTS)
    rows = connection.execute(
        f"""SELECT n.*,a.annotation_id,a.event_type,a.entities_json,a.hawkishness,
                   a.inflation_impulse,a.growth_impulse,a.geopolitical_risk,
                   a.usd_impulse,a.novelty,a.confidence,a.annotation_json,a.parsed_at
            FROM news_revisions n JOIN news_annotations a
              ON a.source=n.source AND a.source_item_id=n.source_item_id
             AND a.revision_number=n.revision_number AND a.raw_content_hash=n.content_hash
            WHERE n.collector_first_seen_time<=? AND a.parsed_at<=?
              AND length(trim(coalesce(n.body,'')))>=240
              AND a.llm_model_version IN ('gemini-3.5-flash-lite','gemini-3.1-flash-lite')
              AND a.prompt_version IN ({placeholders})
              AND NOT EXISTS (
                SELECT 1 FROM news_revisions newer
                WHERE newer.source=n.source AND newer.source_item_id=n.source_item_id
                  AND newer.revision_number>n.revision_number
                  AND newer.collector_first_seen_time<=?)
            ORDER BY a.parsed_at DESC,a.annotation_id DESC""",
        (cutoff, cutoff, *_LEGACY_PROMPTS, cutoff),
    ).fetchall()
    latest: dict[tuple[str, str, int], dict] = {}
    for raw in rows:
        row = dict(raw)
        latest.setdefault((row["source"], row["source_item_id"], row["revision_number"]), row)

    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in latest.values():
        row["publisher_domain"] = _domain(row.get("link"))
        row["reliable_domain"] = _reliable_domain(row["publisher_domain"])
        row["topics"] = _topics(row)
        row["event_cluster_id"] = _legacy_event_key(row, row["topics"])
        grouped[row["event_cluster_id"]].append(row)

    events = []
    for event_id, members in grouped.items():
        primary = [row for row in members if row["source"] in BROAD_PRIMARY_SOURCES]
        reliable_domains = {row["reliable_domain"] for row in members if row["reliable_domain"]}
        grade = (
            "PRIMARY" if primary else
            "CORROBORATED" if len(reliable_domains) >= 2 else
            "SINGLE_RELIABLE" if reliable_domains else
            "DISCOVERY_ONLY"
        )
        candidates = primary or [
            row for row in members if row["reliable_domain"] in reliable_domains
        ] or members
        canonical = max(
            candidates,
            key=lambda row: (float(row["confidence"]), len(str(row["body"])), row["source_item_id"]),
        )
        topics = tuple(sorted({topic for row in members for topic in row["topics"]}))
        eligible = grade in {"PRIMARY", "CORROBORATED"} and bool(ACTION_TOPICS & set(topics))
        events.append({
            "event_cluster_id": event_id,
            "topics": topics,
            "evidence_grade": grade,
            "broad_model_eligible": eligible,
            "collector_first_seen_time": min(row["collector_first_seen_time"] for row in members),
            "hawkishness": float(canonical["hawkishness"]),
            "inflation_impulse": float(canonical["inflation_impulse"]),
            "growth_impulse": float(canonical["growth_impulse"]),
            "geopolitical_risk": float(canonical["geopolitical_risk"]),
            "usd_impulse": float(canonical["usd_impulse"]),
            "novelty": float(canonical["novelty"]),
            "confidence": float(canonical["confidence"]),
            "source_hash": canonical_hash(sorted(
                (row["content_hash"], row["annotation_id"]) for row in members
            )),
        })
    return sorted(events, key=lambda row: (row["collector_first_seen_time"], row["event_cluster_id"]))


def aggregate_legacy_news_features_v2(ledger, decision_time: datetime) -> dict:
    """Reproduce the frozen V3/V1 feature contract for legacy OOS only."""
    selected = []
    for annotation in ledger.visible_annotations(decision_time):
        news = ledger.connection.execute(
            """SELECT * FROM news_revisions
            WHERE source=? AND source_item_id=? AND revision_number=?""",
            (annotation["source"], annotation["source_item_id"], annotation["revision_number"]),
        ).fetchone()
        if news is None or str(news["source"]) not in _CORE_MODEL_SOURCES:
            continue
        if len(str(news["body"] or "")) < 200:
            continue
        first_seen = datetime.fromisoformat(news["collector_first_seen_time"])
        if decision_time - first_seen > MAX_NEWS_AGE:
            continue
        selected.append((news, annotation))

    canonical = {}
    for news, annotation in selected:
        cluster = str(news["cluster_id"])
        candidate = (len(str(news["body"] or "")), str(news["source_item_id"]))
        if cluster not in canonical or candidate > canonical[cluster][0]:
            canonical[cluster] = (candidate, news, annotation)

    totals = {name: 0.0 for name in NEWS_FEATURES}
    weight_sum = 0.0
    event_types = set()
    evidence = []
    for _, news, row in canonical.values():
        first_seen = datetime.fromisoformat(news["collector_first_seen_time"])
        parsed_at = datetime.fromisoformat(row["parsed_at"])
        age_minutes = max(0.0, (decision_time - first_seen).total_seconds() / 60.0)
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
            news["content_hash"], row["annotation_id"], age_minutes,
            (parsed_at - first_seen).total_seconds(),
        ))
    if weight_sum:
        for name in (
            "news_hawkishness", "news_inflation_impulse", "news_growth_impulse",
            "news_geopolitical_risk", "news_usd_impulse", "news_novelty", "news_confidence",
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

    broad_totals = {name: 0.0 for name in BROAD_NEWS_FEATURES}
    broad_events = [
        row for row in _legacy_event_evidence_rows(ledger.connection, decision_time)
        if row["broad_model_eligible"]
        and decision_time - datetime.fromisoformat(row["collector_first_seen_time"]) <= MAX_NEWS_AGE
    ]
    broad_weight_sum = 0.0
    broad_evidence = []
    for row in broad_events:
        first_seen = datetime.fromisoformat(row["collector_first_seen_time"])
        age_minutes = max(0.0, (decision_time - first_seen).total_seconds() / 60.0)
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
    return {
        "features": totals,
        "eligibility_version": LEGACY_ELIGIBILITY_VERSION,
        "model_visible_items": len(canonical),
        "news_exposed": int(bool(canonical)),
        "distinct_news_clusters": len(canonical),
        "distinct_event_types": len(event_types),
        "source_evidence_hash": canonical_hash((evidence, broad_evidence)),
    }
