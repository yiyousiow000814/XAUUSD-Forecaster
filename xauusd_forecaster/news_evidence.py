"""Deterministic event-level evidence policy for broad XAUUSD news."""

from __future__ import annotations

import hashlib
import json
import re
import urllib.parse
from collections import defaultdict
from datetime import datetime

from .forward_ledger import canonical_hash
from .news_time import assess_news_time


EVIDENCE_POLICY_VERSION = "news-event-evidence-v2-economic-time"
CORE_OFFICIAL_SOURCES = frozenset({
    "federal_reserve_monetary",
    "federal_reserve_press_all",
    "federal_reserve_speeches_testimony",
    "bea_economic_releases",
    "us_treasury_press_releases",
})
BROAD_PRIMARY_SOURCES = CORE_OFFICIAL_SOURCES | frozenset({
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


def _event_key(row: dict, topics: tuple[str, ...]) -> str:
    day = str(row.get("source_published_time") or row["collector_first_seen_time"])[:10]
    entities = sorted({
        re.sub(r"\s+", " ", str(value).casefold()).strip()
        for value in json.loads(row.get("entities_json") or "[]") if str(value).strip()
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


def event_evidence_rows_from_connection(connection, decision_time: datetime) -> list[dict]:
    """Return one point-in-time canonical row per event cluster."""
    cutoff = decision_time.isoformat()
    raw_rows = connection.execute(
        """SELECT n.*,a.annotation_id,a.event_type,a.entities_json,a.hawkishness,
                  a.inflation_impulse,a.growth_impulse,a.geopolitical_risk,
                  a.usd_impulse,a.novelty,a.confidence,a.annotation_json,a.parsed_at
           FROM news_revisions n JOIN news_annotations a
             ON a.source=n.source AND a.source_item_id=n.source_item_id
            AND a.revision_number=n.revision_number AND a.raw_content_hash=n.content_hash
           WHERE n.collector_first_seen_time<=? AND a.parsed_at<=?
             AND length(trim(coalesce(n.body,'')))>=240
             AND a.llm_model_version IN ('gemini-3.5-flash-lite','gemini-3.1-flash-lite')
             AND a.prompt_version IN ('news-json-v10-controlled-category-zh',
                                      'news-json-v9-local-display-recovery',
                                      'news-json-v8-strict-zh-source-number-lexemes')
             AND NOT EXISTS (
               SELECT 1 FROM news_revisions newer
               WHERE newer.source=n.source AND newer.source_item_id=n.source_item_id
                 AND newer.revision_number>n.revision_number
                 AND newer.collector_first_seen_time<=?)
           ORDER BY a.parsed_at DESC,a.annotation_id DESC""",
        (cutoff, cutoff, cutoff),
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
        row["time_assessment"] = assess_news_time(
            row, decision_time=decision_time, forward_epoch=forward_epoch
        )
        row["publisher_domain"] = _domain(row.get("link"))
        row["reliable_domain"] = _reliable_domain(row["publisher_domain"])
        row["topics"] = _topics(row)
        row["event_cluster_id"] = _event_key(row, row["topics"])
        grouped[row["event_cluster_id"]].append(row)

    events = []
    for event_id, members in grouped.items():
        timely = [row for row in members if row["time_assessment"].eligible]
        evidence_members = timely or members
        primary = [
            row for row in evidence_members if row["source"] in BROAD_PRIMARY_SOURCES
        ]
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
        canonical = max(
            candidates,
            key=lambda row: (float(row["confidence"]), len(str(row["body"])), row["source_item_id"]),
        )
        topics = tuple(sorted({topic for row in members for topic in row["topics"]}))
        annotation = json.loads(canonical.get("annotation_json") or "{}")
        controlled_category = str(annotation.get("primary_category") or "")
        relevant = controlled_category in {
            "rates_fed", "inflation_employment", "growth_economy", "usd_liquidity",
            "oil_energy", "war_geopolitics", "central_bank_gold", "risk_sentiment",
        }
        eligible = (
            bool(timely)
            and grade in {"PRIMARY", "CORROBORATED"}
            and bool(ACTION_TOPICS & set(topics))
            and relevant
        )
        source_names = sorted({row["source"] for row in members})
        publisher_domains = sorted({
            row["publisher_domain"] for row in members if row["publisher_domain"]
        })
        independent_publishers = len(reliable_domains) if not primary else len({
            row["source"] for row in primary
        })
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
            "policy_version": EVIDENCE_POLICY_VERSION,
            "topics": topics,
            "evidence_grade": grade,
            "broad_model_eligible": eligible,
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
            "confidence": float(canonical["confidence"]),
            "reason_codes": reasons,
            "source_hash": canonical_hash(sorted(
                (row["content_hash"], row["annotation_id"]) for row in members
            )),
        })
    return sorted(events, key=lambda row: (row["collector_first_seen_time"], row["event_cluster_id"]))


def event_evidence_rows(ledger, decision_time: datetime) -> list[dict]:
    return event_evidence_rows_from_connection(ledger.connection, decision_time)
