"""Canonical identities shared by news evidence, stories, and training."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Mapping


SOURCE_ORGANIZATION_ALIASES = {
    "ap_news": "ap",
    "apnews_com": "ap",
    "associated_press": "ap",
    "bea_economic_releases": "bureau_of_economic_analysis",
    "bls_consumer_price_index": "bureau_of_labor_statistics",
    "bls_employment_situation": "bureau_of_labor_statistics",
    "bls_job_openings": "bureau_of_labor_statistics",
    "bloomberg_com": "bloomberg",
    "bullionvault_com": "bullionvault",
    "bitcoinworld": "bitcoin_world",
    "cnbc_com": "cnbc",
    "ecb_press_releases": "european_central_bank",
    "eia_press_releases": "energy_information_administration",
    "eia_today_in_energy": "energy_information_administration",
    "federal_reserve_monetary": "federal_reserve",
    "federal_reserve_press_all": "federal_reserve",
    "federal_reserve_speeches_testimony": "federal_reserve",
    "finance_yahoo_com": "yahoo_finance",
    "ft_com": "financial_times",
    "google_news_bls_official_releases": "bureau_of_labor_statistics",
    "kitco_com": "kitco",
    "kitco_news": "kitco",
    "marketwatch_com": "marketwatch",
    "npr_org": "npr",
    "reuters_com": "reuters",
    "reuters_news": "reuters",
    "theguardian_com": "the_guardian",
    "thomson_reuters": "reuters",
    "us_bls": "bureau_of_labor_statistics",
    "us_bureau_of_labor_statistics": "bureau_of_labor_statistics",
    "us_treasury_press_releases": "us_treasury",
    "world_gold_council_central_banks": "world_gold_council",
    "wsj_com": "wall_street_journal",
}

RESOLVED_IDENTITY_RELATIONS = frozenset({
    "NEW_EPISODE", "SAME_EPISODE", "SAME_EVENT",
})
NEWS_CURRENT_REPRESENTATIVE_CONTRACT_VERSION = "news-current-representative-v1"


def _sql_alias(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value):
        raise ValueError("invalid SQL alias")
    return value


def preferred_cluster_peer_predicate(peer_alias: str, candidate_alias: str) -> str:
    """Return the canonical current-revision ordering shared by news readers.

    A cluster keeps the longest complete body. Equal-length bodies use the
    stable ``(source, source_item_id)`` identity because ``source_item_id`` is
    only unique inside one source. Callers may add their own time/revision
    scope around this predicate, but may not invent another tie-break.
    """
    peer = _sql_alias(peer_alias)
    candidate = _sql_alias(candidate_alias)
    return (
        f"(length(COALESCE({peer}.body,''))>"
        f"length(COALESCE({candidate}.body,'')) OR "
        f"(length(COALESCE({peer}.body,''))="
        f"length(COALESCE({candidate}.body,'')) AND "
        f"({peer}.source<{candidate}.source OR "
        f"({peer}.source={candidate}.source AND "
        f"{peer}.source_item_id<{candidate}.source_item_id))))"
    )


def news_representative_key(row: Mapping[str, object]) -> tuple[int, str, str]:
    """Return the sortable key for the same representative contract in Python."""
    return (
        -len(str(row.get("body") or "")),
        str(row.get("source") or ""),
        str(row.get("source_item_id") or ""),
    )


def canonical_id(value: object) -> str:
    value = re.sub(
        r"[^a-z0-9\u3400-\u9fff]+", "_",
        str(value or "").casefold(),
    ).strip("_")
    return SOURCE_ORGANIZATION_ALIASES.get(value, value)


def canonical_source_organization(value: object) -> str:
    """Collapse an organization label and its domain spelling to one id."""
    value = canonical_id(value)
    if value.startswith("yahoo_finance"):
        return "yahoo_finance"
    return SOURCE_ORGANIZATION_ALIASES.get(value, value)


def resolved_identity_ids(event: dict) -> tuple[str, str] | None:
    """Return the sole model-authoritative episode/event identity pair."""
    relation = str(event.get("resolved_identity_relation") or "").upper()
    if relation not in RESOLVED_IDENTITY_RELATIONS:
        return None
    episode_id = str(
        event.get("resolved_episode_id") or event.get("canonical_episode_id") or ""
    ).strip()
    event_id = str(
        event.get("resolved_event_id") or event.get("canonical_event_id") or ""
    ).strip()
    return (episode_id, event_id) if episode_id and event_id else None


def identity_resolution_status(event: dict) -> str:
    """Classify identity authority without inventing a semantic fallback."""
    relation = str(event.get("resolved_identity_relation") or "").upper()
    if relation == "UNRESOLVED":
        return "UNRESOLVED"
    return "RESOLVED" if resolved_identity_ids(event) is not None else "MISSING"


def _parse_time(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.replace(tzinfo=parsed.tzinfo or UTC).astimezone(UTC)


def _period(event: dict) -> str:
    text = "_".join(str(event.get(name) or "") for name in (
        "episode_key", "material_event_key", "event_time",
        "source_published_time", "collector_first_seen_time",
    )).casefold()
    match = re.search(r"(20\d{2})[_-](0?[1-9]|1[0-2])", text)
    if match:
        return f"{match.group(1)}_{int(match.group(2)):02d}"
    for name in (
        "event_time", "source_published_time", "collector_first_seen_time",
    ):
        parsed = _parse_time(event.get(name))
        if parsed is not None:
            return f"{parsed.year:04d}_{parsed.month:02d}"
    return "current"


def canonical_story_episode(event: dict) -> str | None:
    """Resolve known episode aliases from structured entities, not LLM slugs."""
    episode = canonical_id(event.get("episode_key"))
    object_id = canonical_id(
        event.get("canonical_object_id") or event.get("object")
    )
    text = "_".join(canonical_id(event.get(name)) for name in (
        "episode_key", "material_event_key", "canonical_object_id", "object",
        "canonical_actor_id", "actor", "canonical_headline", "headline",
        "headline_zh", "action",
    ))
    cook_subject = "lisa_cook" in text or (
        "cook" in text and any(token in text for token in (
            "fire", "firing", "remove", "removal", "dismiss", "termination",
            "罢免", "解雇", "撤换",
        ))
    )
    removal_subject = any(token in text for token in (
        "fire", "firing", "remove", "removal", "dismiss", "termination",
        "罢免", "解雇", "撤换",
    ))
    if cook_subject and removal_subject and (
        "lisa_cook" in object_id or "cook" in object_id or "lisa_cook" in text
    ):
        return f"lisa_cook_removal_{_period(event)}"
    if ("lisa_cook" in text or "lisa_d_cook" in text) and any(
        token in text for token in (
            "rate_hike", "interest_rate", "inflation", "monetary_policy",
            "加息", "利率", "通胀", "货币政策",
        )
    ):
        return f"lisa_cook_rate_policy_{_period(event)}"
    if any(token in text for token in (
        "treasury_borrowing_advisory_committee", "tbac_",
    )) and any(token in text for token in (
        "meeting", "minutes", "report", "quarterly_refunding", "auction_sizes",
        "会议", "纪要",
    )):
        return f"tbac_meeting_{_period(event)}"
    return episode or None


def canonical_material_event_anchor(event: dict) -> tuple[str, ...] | None:
    """Return a deterministic occurrence anchor when available.

    Free-form material_event_key remains a fallback.  The structured anchor is
    intentionally conservative: generic rows are merged only when they are an
    initial report or an explicit confirmation.  Known noisy episodes can use
    a stricter domain anchor that is shared by stories and model snapshots.
    """
    episode = canonical_story_episode(event)
    actor = canonical_id(event.get("canonical_actor_id") or event.get("actor"))
    action = canonical_id(event.get("action_family") or event.get("action"))
    object_id = canonical_id(
        event.get("canonical_object_id") or event.get("object")
    )
    location = canonical_id(
        event.get("canonical_location_id") or event.get("location")
    )
    relation = str(event.get("relation_to_prior") or "NONE").upper()

    if episode and episode.startswith("lisa_cook_removal_"):
        # A headline may mention an older court ruling as background.  Classify
        # the development from the structured actor/action, never from headline
        # words alone.
        structured_action = "_".join((action, actor))
        if "court" in actor or any(token in structured_action for token in (
            "court_decision", "court_ruling",
        )):
            development = "court_decision"
        elif any(token in action for token in (
            "legal_filing", "filed_motion", "lawsuit", "petition",
        )):
            development = "legal_filing"
        elif actor not in {
            "donald_trump", "trump", "trump_administration", "white_house",
            "dan_scavino",
        }:
            development = f"response_{actor or 'unknown'}"
        else:
            development = "removal_attempt"
        return ("canonical-development", episode, development)

    if not (actor and action and object_id):
        return None
    if relation not in {"", "NONE", "CONFIRMS"}:
        return None
    occurred = _parse_time(event.get("event_time")) or _parse_time(
        event.get("source_published_time")
    )
    day = occurred.date().isoformat() if occurred is not None else "unknown-day"
    return (
        "structured-occurrence", actor, action, object_id, location, day,
    )
