"""Deterministic event-level evidence policy for broad XAUUSD news."""

from __future__ import annotations

import hashlib
import json
import re
import urllib.parse
from collections import defaultdict
from datetime import datetime

from .forward_ledger import canonical_hash
from .news_impact import (
    IMPACT_MODEL,
    IMPACT_PROMPT_VERSION,
    impact_is_actionable,
    impact_time_rule,
)
from .news_contracts import CURRENT_NEWS_CONTRACT, UNIFIED_EVENT_CLOCK_V4
from .news_time import NewsTimeAssessment, assess_news_time, category_time_rule


EVIDENCE_POLICY_VERSION = CURRENT_NEWS_CONTRACT.policy_version
LEGACY_V4_EVIDENCE_POLICY_VERSION = UNIFIED_EVENT_CLOCK_V4.policy_version
LEGACY_V3_EVIDENCE_POLICY_VERSION = "news-event-evidence-v2-economic-time"
CURRENT_EVENT_PROMPT_VERSION = "news-json-v14-material-event-evidence"
ACTIONABLE_RECORD_KINDS = frozenset({
    "FACT_EVENT", "OFFICIAL_CLAIM", "MARKET_REACTION",
})
ACTIONABLE_EVIDENCE_ROLES = frozenset({
    "CORE_CLAIM", "EVIDENCE_DOCUMENT", "MARKET_REACTION",
})
MIN_ACTIONABLE_MATERIALITY = 0.50
CORE_OFFICIAL_SOURCES = frozenset({
    "federal_reserve_monetary",
    "federal_reserve_press_all",
    "federal_reserve_speeches_testimony",
    "bea_economic_releases",
    "us_treasury_press_releases",
    "bls_employment_situation",
    "bls_consumer_price_index",
    "bls_job_openings",
    "google_news_bls_official_releases",
})
BROAD_PRIMARY_SOURCES = CORE_OFFICIAL_SOURCES | frozenset({
    "eia_press_releases",
    "eia_today_in_energy",
    "ecb_press_releases",
    "world_gold_council_central_banks",
})
LEGACY_V3_CORE_OFFICIAL_SOURCES = frozenset({
    "federal_reserve_monetary",
    "federal_reserve_press_all",
    "federal_reserve_speeches_testimony",
    "bea_economic_releases",
    "us_treasury_press_releases",
})
LEGACY_V3_BROAD_PRIMARY_SOURCES = LEGACY_V3_CORE_OFFICIAL_SOURCES | frozenset({
    "eia_press_releases",
    "eia_today_in_energy",
    "ecb_press_releases",
    "world_gold_council_central_banks",
})
RELIABLE_PUBLISHER_DOMAINS = frozenset({
    "aljazeera.com", "apnews.com", "bbc.com", "bloomberg.com", "cnbc.com",
    "dw.com", "finance.yahoo.com", "france24.com", "ft.com",
    "marketwatch.com", "nikkei.com", "npr.org", "reuters.com",
    "thebanker.com", "theguardian.com", "wsj.com", "kitco.com",
    "bullionvault.com",
})
ACTION_TOPICS = frozenset({
    "rates_fed", "inflation", "employment", "growth_economy",
    "usd_liquidity", "oil_energy", "war_geopolitics",
    "central_bank_gold", "risk_sentiment",
})
TOPIC_FEATURES = tuple(f"broad_topic_{topic}" for topic in sorted(ACTION_TOPICS))
BROAD_NEWS_FEATURES = (
    "broad_news_hawkishness",
    "broad_news_inflation_impulse",
    "broad_news_growth_impulse",
    "broad_news_geopolitical_risk",
    "broad_news_usd_impulse",
    "broad_news_novelty",
    "broad_news_confidence",
    "broad_news_event_count",
    "broad_primary_event_count",
    "broad_corroborated_event_count",
    *TOPIC_FEATURES,
)

_TOPIC_TERMS = (
    ("central_bank_gold", ("central bank gold", "gold reserve", "gold purchase", "gold buying")),
    ("war_geopolitics", ("war", "conflict", "sanction", "military", "geopolit", "iran", "russia", "ukraine", "hormuz", "terror")),
    ("oil_energy", ("oil", "crude", "petroleum", "opec", "energy", "gasoline", "supply disruption", "inventory")),
    ("employment", ("payroll", "employment", "unemployment", "job openings", "wage", "earnings")),
    ("inflation", ("inflation", "consumer price", "cpi", "pce", "price index")),
    ("rates_fed", ("fomc", "interest rate", "monetary policy", "rate decision", "yield", "hawkish", "dovish")),
    ("usd_liquidity", ("dollar", "foreign exchange", "currency", "liquidity", "balance sheet", "treasury market")),
    ("growth_economy", ("gdp", "growth", "recession", "personal income", "trade data", "economic outlook")),
    ("risk_sentiment", ("risk sentiment", "market stress", "financial stability", "equity selloff", "safe haven")),
)
_TITLE_STOPWORDS = frozenset({
    "the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "with",
    "as", "at", "by", "from", "after", "before", "says", "said", "update",
    "news", "report", "gold", "xauusd", "price", "market",
})


def _domain(link: str | None) -> str:
    host = (urllib.parse.urlparse(link or "").hostname or "").casefold()
    return host.removeprefix("www.")


def _reliable_domain(host: str) -> str | None:
    return next((
        domain for domain in RELIABLE_PUBLISHER_DOMAINS
        if host == domain or host.endswith(f".{domain}")
    ), None)


def _topics(row: dict) -> tuple[str, ...]:
    annotation = json.loads(row.get("annotation_json") or "{}")
    text = " ".join((
        str(row.get("headline") or ""), str(row.get("event_type") or ""),
        str(annotation.get("summary_zh") or ""),
    )).casefold().replace("_", " ")
    found = [topic for topic, terms in _TOPIC_TERMS if any(term in text for term in terms)]
    category_topics = {
        "rates_fed": ("rates_fed",),
        "inflation_employment": ("inflation", "employment"),
        "growth_economy": ("growth_economy",),
        "usd_liquidity": ("usd_liquidity",),
        "oil_energy": ("oil_energy",),
        "war_geopolitics": ("war_geopolitics",),
        "central_bank_gold": ("central_bank_gold",),
        "risk_sentiment": ("risk_sentiment",),
    }
    if not found:
        for category in (
            annotation.get("primary_category"),
            *(annotation.get("secondary_categories") or []),
        ):
            found.extend(category_topics.get(str(category), ()))
    if not found:
        if abs(float(row.get("geopolitical_risk") or 0.0)) >= 0.2:
            found.append("war_geopolitics")
        elif abs(float(row.get("inflation_impulse") or 0.0)) >= 0.2:
            found.append("inflation")
        elif abs(float(row.get("hawkishness") or 0.0)) >= 0.2:
            found.append("rates_fed")
        elif abs(float(row.get("growth_impulse") or 0.0)) >= 0.2:
            found.append("growth_economy")
        elif abs(float(row.get("usd_impulse") or 0.0)) >= 0.2:
            found.append("usd_liquidity")
    return tuple(dict.fromkeys(found or ["other"]))


def _event_key(
    row: dict, topics: tuple[str, ...], *, use_material_event_key: bool = True,
) -> str:
    annotation = json.loads(row.get("annotation_json") or "{}")
    material_event_key = str(annotation.get("material_event_key") or "").strip().casefold()
    if use_material_event_key and material_event_key:
        identity = ("material-event", material_event_key)
        return hashlib.sha256(repr(identity).encode("utf-8")).hexdigest()
    actor = str(annotation.get("canonical_actor_id") or annotation.get("actor") or "").strip().casefold()
    action = str(annotation.get("action_family") or annotation.get("action") or "").strip().casefold()
    object_id = str(annotation.get("canonical_object_id") or annotation.get("object") or "").strip().casefold()
    location = str(annotation.get("canonical_location_id") or annotation.get("location") or "").strip().casefold()
    if actor and action and object_id:
        identity = ("semantic-event", topics[0], actor, action, object_id, location)
        return hashlib.sha256(repr(identity).encode("utf-8")).hexdigest()
    entities = sorted({
        re.sub(r"\s+", " ", str(value).casefold()).strip()
        for value in json.loads(row.get("entities_json") or "[]") if str(value).strip()
    })
    if len(entities) >= 2:
        identity = ("entities", topics[0], tuple(entities))
    else:
        tokens = sorted({
            token for token in re.findall(r"[a-z0-9]+", str(row["headline"]).casefold())
            if token not in _TITLE_STOPWORDS
        })[:12]
        identity = ("headline", topics[0], tuple(entities), tuple(tokens))
    return hashlib.sha256(repr(identity).encode("utf-8")).hexdigest()


def _parsed_timestamp(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text or not re.search(r"(?:T|\s)\d{1,2}:\d{2}", text):
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed


def resolve_event_clock(
    row: dict, *, primary_source: bool,
) -> tuple[datetime | None, str, str]:
    """Return a live-known event clock with explicit provenance.

    A precise body clock is preferred.  For an official primary publisher the
    publication itself is an admissible event only when the source supplies a
    precise timestamp.  Media publication time never substitutes for an
    unknown real-world event clock.
    """
    annotation = json.loads(row.get("annotation_json") or "{}")
    explicit = _parsed_timestamp(annotation.get("event_time") or row.get("event_time"))
    if explicit is not None:
        return explicit, "EXPLICIT_BODY_TIME", "TIMESTAMP"
    official_release = _parsed_timestamp(row.get("source_published_time"))
    if primary_source and official_release is not None:
        return official_release, "OFFICIAL_RELEASE_TIME", "TIMESTAMP"
    return None, "UNKNOWN", "UNKNOWN"


def event_evidence_rows_from_connection(
    connection, decision_time: datetime, *, legacy_v3: bool = False,
    legacy_v4: bool = False,
) -> list[dict]:
    """Return one point-in-time canonical row per event cluster."""
    if legacy_v3 and legacy_v4:
        raise ValueError("only one legacy news contract can be selected")
    # Ledger timestamps are canonicalized with microseconds.  Keep the same
    # representation so an annotation parsed exactly at decision time remains
    # visible under SQLite's lexicographic timestamp comparison.
    cutoff = decision_time.isoformat(timespec="microseconds")
    raw_rows = connection.execute(
        """SELECT n.*,a.annotation_id,a.event_type,a.entities_json,a.hawkishness,
                  a.inflation_impulse,a.growth_impulse,a.geopolitical_risk,
                  a.usd_impulse,a.novelty,a.confidence,a.annotation_json,a.parsed_at,
                  a.prompt_version,a.llm_model_version,
                  i.assessment_id AS impact_assessment_id,
                  i.assessed_at AS impact_assessed_at,
                  i.impact_class,i.event_state AS impact_event_state,
                  i.update_type AS impact_update_type,
                  i.confidence AS impact_confidence,
                  i.reason_zh AS impact_reason_zh
           FROM news_revisions n JOIN news_annotations a
             ON a.source=n.source AND a.source_item_id=n.source_item_id
            AND a.revision_number=n.revision_number AND a.raw_content_hash=n.content_hash
           LEFT JOIN news_impact_assessments_v1 i
             ON i.annotation_id=a.annotation_id
            AND i.llm_model_version=? AND i.prompt_version=?
            AND i.assessed_at<=?
           WHERE n.collector_first_seen_time<=? AND a.parsed_at<=?
             AND length(trim(coalesce(n.body,'')))>=240
             AND a.llm_model_version IN ('gemini-3.5-flash-lite','gemini-3.1-flash-lite')
             AND a.prompt_version IN ('news-json-v14-material-event-evidence',
                                      'news-json-v13-event-claims',
                                      'news-json-v12-gemini-story-identity',
                                      'news-json-v11-gemini-story-subjects',
                                      'news-json-v10-controlled-category-zh',
                                      'news-json-v9-local-display-recovery')
             AND NOT EXISTS (
               SELECT 1 FROM news_revisions newer
               WHERE newer.source=n.source AND newer.source_item_id=n.source_item_id
                 AND newer.revision_number>n.revision_number
                 AND newer.collector_first_seen_time<=?)
           ORDER BY a.parsed_at DESC,a.annotation_id DESC""",
        (IMPACT_MODEL, IMPACT_PROMPT_VERSION, cutoff, cutoff, cutoff, cutoff),
    ).fetchall()
    latest: dict[tuple[str, str, int], dict] = {}
    for raw in raw_rows:
        row = dict(raw)
        latest.setdefault(
            (row["source"], row["source_item_id"], row["revision_number"]), row
        )

    epoch_row = connection.execute(
        "SELECT value FROM runtime_metadata WHERE key='FORWARD_EPOCH'"
    ).fetchone()
    if epoch_row is None:
        raise ValueError("FORWARD_EPOCH is missing")
    forward_epoch = datetime.fromisoformat(str(epoch_row["value"]))

    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in latest.values():
        annotation = json.loads(row.get("annotation_json") or "{}")
        if legacy_v3:
            row["time_assessment"] = assess_news_time(
                row, decision_time=decision_time, forward_epoch=forward_epoch,
            )
        elif legacy_v4:
            max_age, _ = category_time_rule(annotation.get("primary_category"))
            row["time_assessment"] = assess_news_time(
                row, decision_time=decision_time, forward_epoch=forward_epoch,
                max_actionable_age=max_age,
            )
        else:
            impact = (
                {
                    "impact_class": row.get("impact_class"),
                    "event_state": row.get("impact_event_state"),
                    "update_type": row.get("impact_update_type"),
                }
                if row.get("impact_assessment_id") else None
            )
            max_age, _ = impact_time_rule(
                str(row.get("impact_class") or "BACKGROUND")
            )
            timing = assess_news_time(
                row, decision_time=decision_time, forward_epoch=forward_epoch,
                max_actionable_age=max_age,
                max_discovery_delay=None,
                allow_pre_forward_publication=True,
            )
            if not impact_is_actionable(impact):
                if impact is None:
                    reason = "IMPACT_NOT_ASSESSED"
                elif impact.get("update_type") == "DUPLICATE_REPORT":
                    reason = "IMPACT_DUPLICATE_REPORT"
                elif impact.get("update_type") == "HISTORICAL_CONTEXT":
                    reason = "IMPACT_HISTORICAL_CONTEXT"
                elif impact.get("update_type") == "COMMENTARY":
                    reason = "IMPACT_COMMENTARY"
                else:
                    reason = "IMPACT_BACKGROUND"
                timing = NewsTimeAssessment(
                    False, timing.event_time, timing.age_minutes,
                    timing.discovery_delay_seconds, reason,
                )
            row["time_assessment"] = timing
        row["publisher_domain"] = _domain(row.get("link"))
        if (
            not legacy_v3
            and
            row["source"] == "google_news_bls_official_releases"
            and row["publisher_domain"] != "bls.gov"
            and not row["publisher_domain"].endswith(".bls.gov")
        ):
            continue
        row["reliable_domain"] = _reliable_domain(row["publisher_domain"])
        row["topics"] = _topics(row)
        row["event_cluster_id"] = _event_key(
            row, row["topics"], use_material_event_key=not legacy_v3,
        )
        grouped[row["event_cluster_id"]].append(row)

    events = []
    for event_id, members in grouped.items():
        timely = [row for row in members if row["time_assessment"].eligible]
        evidence_members = timely or members
        primary_sources = (
            LEGACY_V3_BROAD_PRIMARY_SOURCES if legacy_v3 else BROAD_PRIMARY_SOURCES
        )
        primary = [row for row in evidence_members if row["source"] in primary_sources]
        reliable_domains = {
            row["reliable_domain"] for row in evidence_members if row["reliable_domain"]
        }
        if primary:
            grade = "PRIMARY"
        elif len(reliable_domains) >= 2:
            grade = "CORROBORATED"
        elif reliable_domains:
            grade = "SINGLE_RELIABLE"
        else:
            grade = "DISCOVERY_ONLY"
        candidates = primary or [
            row for row in evidence_members if row["reliable_domain"] in reliable_domains
        ] or evidence_members
        if legacy_v4:
            canonical = max(
                candidates,
                key=lambda row: (
                    float(row["confidence"]), len(str(row["body"])),
                    row["source_item_id"],
                ),
            )
        else:
            canonical = max(
                candidates,
                key=lambda row: (
                    int(row.get("impact_update_type") == "MATERIAL_UPDATE"),
                    str(row["collector_first_seen_time"]),
                    float(row["confidence"]), len(str(row["body"])),
                    row["source_item_id"],
                ),
            )
        topics = tuple(sorted({topic for row in members for topic in row["topics"]}))
        annotation = json.loads(canonical.get("annotation_json") or "{}")
        controlled_category = str(annotation.get("primary_category") or "")
        record_kind = str(annotation.get("record_kind") or "")
        evidence_role = str(annotation.get("evidence_role") or "")
        materiality = float(annotation.get("materiality") or 0.0)
        current_semantic_schema = canonical.get("prompt_version") == CURRENT_EVENT_PROMPT_VERSION
        event_clock, event_clock_source, event_time_precision = resolve_event_clock(
            canonical, primary_source=canonical["source"] in primary_sources,
        )
        event_clock_valid = legacy_v3 or bool(
            event_clock is not None
            and event_clock <= decision_time
            and (not legacy_v4 or event_clock >= forward_epoch)
        )
        semantic_eligible = legacy_v3 or (
            current_semantic_schema
            and record_kind in ACTIONABLE_RECORD_KINDS
            and evidence_role in ACTIONABLE_EVIDENCE_ROLES
            and materiality >= MIN_ACTIONABLE_MATERIALITY
        )
        relevant = controlled_category in {
            "rates_fed", "inflation_employment", "growth_economy", "usd_liquidity",
            "oil_energy", "war_geopolitics", "central_bank_gold", "risk_sentiment",
        }
        eligible = (
            bool(timely)
            and grade in {"PRIMARY", "CORROBORATED"}
            and bool(ACTION_TOPICS & set(topics))
            and relevant
            and semantic_eligible
            and event_clock_valid
        )
        official_eligible = eligible and canonical["source"] in CORE_OFFICIAL_SOURCES
        source_names = sorted({row["source"] for row in members})
        publisher_domains = sorted({
            row["publisher_domain"] for row in members if row["publisher_domain"]
        })
        if legacy_v3:
            independent_publishers = (
                len(reliable_domains) if not primary
                else len({row["source"] for row in primary})
            )
        else:
            primary_organizations = {
                str(json.loads(row.get("annotation_json") or "{}").get(
                    "source_organization_id"
                ) or row["source"]).strip().casefold()
                for row in primary
            }
            independent_publishers = (
                len(reliable_domains) if not primary else len(primary_organizations)
            )
        reasons = [f"EVIDENCE_{grade}"]
        if not timely:
            reasons.extend(sorted({
                row["time_assessment"].reason_code for row in members
            }))
        if not relevant:
            reasons.append("CATEGORY_NOT_ACTIONABLE")
        if not (ACTION_TOPICS & set(topics)):
            reasons.append("NO_ACTION_TOPIC")
        elif timely and grade not in {"PRIMARY", "CORROBORATED"}:
            reasons.append("NEEDS_CONFIRMATION")
        if not legacy_v3:
            if not current_semantic_schema:
                reasons.append("LEGACY_ANNOTATION_SCHEMA")
            if record_kind not in ACTIONABLE_RECORD_KINDS:
                reasons.append("RECORD_KIND_NOT_ACTIONABLE")
            if evidence_role not in ACTIONABLE_EVIDENCE_ROLES:
                reasons.append("EVIDENCE_ROLE_NOT_ACTIONABLE")
            if materiality < MIN_ACTIONABLE_MATERIALITY:
                reasons.append("LOW_MATERIALITY")
            if not event_clock_valid:
                reasons.append("EVENT_TIME_INVALID")
        entities = sorted({
            str(value).strip()
            for row in members
            for value in json.loads(row.get("entities_json") or "[]")
            if str(value).strip()
        })
        canonical_headline = str(
            annotation.get("headline_zh") or canonical["headline"]
        )
        events.append({
            "event_cluster_id": event_id,
            "event_key": event_id,
            "event_id": event_id,
            "event_version_id": canonical_hash((
                event_id, canonical["content_hash"], canonical["annotation_id"],
                *(() if legacy_v4 else (canonical.get("impact_assessment_id"),)),
                (
                    LEGACY_V3_EVIDENCE_POLICY_VERSION if legacy_v3
                    else LEGACY_V4_EVIDENCE_POLICY_VERSION if legacy_v4
                    else EVIDENCE_POLICY_VERSION
                ),
            )),
            "policy_version": (
                LEGACY_V3_EVIDENCE_POLICY_VERSION if legacy_v3
                else LEGACY_V4_EVIDENCE_POLICY_VERSION if legacy_v4
                else EVIDENCE_POLICY_VERSION
            ),
            "prompt_version": canonical.get("prompt_version"),
            "topics": topics,
            "evidence_grade": grade,
            "broad_model_eligible": eligible,
            "official_model_eligible": official_eligible,
            "independent_publishers": independent_publishers,
            "member_count": len(members),
            "source_names": source_names,
            "publisher_domains": publisher_domains,
            "canonical_source": canonical["source"],
            "canonical_source_item_id": canonical["source_item_id"],
            "publisher_domain": canonical["publisher_domain"],
            "headline": canonical_headline,
            "canonical_headline": canonical_headline,
            "event_type": canonical.get("event_type"),
            "entities": entities,
            "primary_category": annotation.get("primary_category"),
            "record_kind": annotation.get("record_kind"),
            "actor": annotation.get("actor"),
            "action": annotation.get("action"),
            "object": annotation.get("object"),
            "location": annotation.get("location"),
            "event_time": annotation.get("event_time"),
            "event_occurred_at": event_clock.isoformat() if event_clock else None,
            "event_clock_source": event_clock_source,
            "event_time_precision": event_time_precision,
            "claim_status": annotation.get("claim_status"),
            "materiality": annotation.get("materiality"),
            "canonical_actor_id": annotation.get("canonical_actor_id"),
            "action_family": annotation.get("action_family"),
            "canonical_object_id": annotation.get("canonical_object_id"),
            "canonical_location_id": annotation.get("canonical_location_id"),
            "episode_key": annotation.get("episode_key"),
            "primary_story_title_zh": annotation.get("primary_story_title_zh"),
            "secondary_contexts_zh": annotation.get("secondary_contexts_zh") or [],
            "relation_to_prior": annotation.get("relation_to_prior"),
            "document_kind": annotation.get("document_kind"),
            "material_event_key": annotation.get("material_event_key"),
            "source_organization_id": annotation.get("source_organization_id"),
            "evidence_role": annotation.get("evidence_role"),
            "impact_assessment_id": canonical.get("impact_assessment_id"),
            "impact_assessed_at": canonical.get("impact_assessed_at"),
            "impact_class": canonical.get("impact_class"),
            "impact_event_state": canonical.get("impact_event_state"),
            "impact_update_type": canonical.get("impact_update_type"),
            "impact_reason_zh": canonical.get("impact_reason_zh"),
            "model_permission": "BROAD_MODEL" if eligible else "DISPLAY_ONLY",
            "source_published_time": (
                canonical["time_assessment"].event_time.isoformat()
                if canonical["time_assessment"].event_time else None
            ),
            "economic_age_minutes": canonical["time_assessment"].age_minutes,
            "freshness_status": canonical["time_assessment"].reason_code,
            "collector_first_seen_time": min(row["collector_first_seen_time"] for row in members),
            "parsed_at": canonical["parsed_at"],
            "hawkishness": float(canonical["hawkishness"]),
            "inflation_impulse": float(canonical["inflation_impulse"]),
            "growth_impulse": float(canonical["growth_impulse"]),
            "geopolitical_risk": float(canonical["geopolitical_risk"]),
            "usd_impulse": float(canonical["usd_impulse"]),
            "novelty": float(canonical["novelty"]),
            "confidence": (
                min(
                    float(canonical["confidence"]),
                    float(canonical.get("impact_confidence") or 0.0),
                )
                if not legacy_v3 and not legacy_v4
                else float(canonical["confidence"])
            ),
            "reason_codes": reasons,
            "source_hash": canonical_hash(sorted(
                (row["content_hash"], row["annotation_id"]) for row in members
            )),
        })
    return sorted(events, key=lambda row: (row["collector_first_seen_time"], row["event_cluster_id"]))


def event_evidence_rows(ledger, decision_time: datetime) -> list[dict]:
    return event_evidence_rows_from_connection(ledger.connection, decision_time)


def legacy_v4_event_evidence_rows(ledger, decision_time: datetime) -> list[dict]:
    """Reproduce the frozen unified-event-clock contract for its active generation."""
    return event_evidence_rows_from_connection(
        ledger.connection, decision_time, legacy_v4=True,
    )
