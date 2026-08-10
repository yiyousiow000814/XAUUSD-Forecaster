"""Display-only temporal event graph built from explicit event claims."""

from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from datetime import UTC, datetime

from .news_identity import (
    canonical_material_event_anchor,
    canonical_source_organization,
    canonical_story_episode,
)


STORYLINE_POLICY_VERSION = "temporal-event-graph-v8-canonical-occurrence-chains"
CURRENT_EVENT_PROMPT_VERSION = "news-json-v14-material-event-evidence"
LEGACY_POLICY_STATUS = "temporal-event-graph-v2:EXPERIMENTAL_MEMBERSHIP_INVALID"
MODEL_PERMISSION = "DISPLAY_ONLY"

CORE_KINDS = {"FACT_EVENT", "OFFICIAL_CLAIM", "RESPONSE"}
ATTACHMENT_KINDS = {"MARKET_REACTION", "COMMENTARY_FORECAST", "BACKGROUND"}
RELATIONS = {
    "CONFIRMS", "CONTRADICTS", "RESPONDS_TO", "ESCALATES",
    "DEESCALATES", "SUPERSEDES", "MARKET_REACTS_TO", "FOLLOWED_BY",
}

THEMES = {
    "rates_fed": "利率与央行政策",
    "inflation_employment": "通胀与就业",
    "growth_economy": "增长与经济",
    "usd_liquidity": "美元与流动性",
    "oil_energy": "油价与能源",
    "war_geopolitics": "战争与地缘",
    "central_bank_gold": "央行购金",
    "risk_sentiment": "黄金与风险偏好",
    "regulation_other": "监管与其他",
}

ENTITY_ALIASES = {
    "rbi": "reserve_bank_of_india",
    "reserve bank of india": "reserve_bank_of_india",
    "印度储备银行": "reserve_bank_of_india",
    "bank of korea": "bank_of_korea",
    "korean central bank": "bank_of_korea",
    "韩国央行": "bank_of_korea",
    "韩国银行": "bank_of_korea",
    "federal reserve": "federal_reserve",
    "fed": "federal_reserve",
    "美联储": "federal_reserve",
    "fomc": "fomc",
    "iran": "iran",
    "伊朗": "iran",
    "strait of hormuz": "strait_of_hormuz",
    "hormuz": "strait_of_hormuz",
    "霍尔木兹海峡": "strait_of_hormuz",
}

ROLE_LABELS = {
    "OFFICIAL_PRIMARY": "官方一手",
    "SINGLE_RELIABLE": "单一可靠来源",
    "INDEPENDENT_CONFIRMATION": "独立交叉确认",
    "PHYSICAL_IMPACT": "实体/现场影响",
    "MARKET_REACTION": "市场反应确认",
}

COVERAGE_TEMPLATES = {
    "MONETARY_POLICY_V1": (
        "OFFICIAL_PRIMARY", "INDEPENDENT_CONFIRMATION", "MARKET_REACTION",
    ),
    "GEOPOLITICAL_SHIPPING_V1": (
        "OFFICIAL_PRIMARY", "INDEPENDENT_CONFIRMATION",
        "PHYSICAL_IMPACT", "MARKET_REACTION",
    ),
    "CENTRAL_BANK_GOLD_V1": (
        "OFFICIAL_PRIMARY", "INDEPENDENT_CONFIRMATION", "MARKET_REACTION",
    ),
    "MATERIAL_EVENT_V1": (
        "INDEPENDENT_CONFIRMATION", "MARKET_REACTION",
    ),
}

OFFICIAL_ORGANIZATIONS = {
    "federal_reserve_monetary": "federal_reserve",
    "federal_reserve_press_all": "federal_reserve",
    "federal_reserve_speeches_testimony": "federal_reserve",
    "ecb_press_releases": "european_central_bank",
    "eia_press_releases": "us_energy_information_administration",
    "eia_today_in_energy": "us_energy_information_administration",
    "bea_economic_releases": "us_bureau_of_economic_analysis",
    "us_treasury_press_releases": "us_treasury",
    "world_gold_council_central_banks": "world_gold_council",
}


def _normal(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "").casefold()).strip()


def _canonical_id(value: object) -> str:
    text = _normal(value)
    if text in ENTITY_ALIASES:
        return ENTITY_ALIASES[text]
    return re.sub(r"[^a-z0-9\u3400-\u9fff]+", "_", text).strip("_")


def _parse_time(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    candidates = (text, text.replace("Z", "+00:00"))
    for candidate in candidates:
        try:
            parsed = datetime.fromisoformat(candidate)
            return parsed.replace(tzinfo=parsed.tzinfo or UTC).astimezone(UTC)
        except ValueError:
            pass
    for pattern in ("%d %B %Y", "%d.%m.%Y - %H:%M", "%d.%m.%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(text, pattern).replace(tzinfo=UTC)
        except ValueError:
            pass
    return None


def _event_datetime(event: dict) -> datetime:
    raw_event_time = str(event.get("event_time") or "").strip()
    parsed_event = _parse_time(raw_event_time)
    parsed_published = _parse_time(event.get("source_published_time"))
    visibility_ceiling = _parse_time(event.get("parsed_at")) or _parse_time(
        event.get("collector_first_seen_time")
    )
    # A date-only LLM extraction has day precision, not midnight precision.
    # Within that day, the source timestamp is the more honest ordering key.
    has_clock = bool(re.search(r"(?:T|\s)\d{1,2}:\d{2}", raw_event_time))
    if (
        parsed_event is not None and has_clock
        and (visibility_ceiling is None or parsed_event <= visibility_ceiling)
    ):
        return parsed_event
    if parsed_published is not None:
        return parsed_published
    if parsed_event is not None:
        return parsed_event
    parsed_received = _parse_time(event.get("collector_first_seen_time"))
    if parsed_received is not None:
        return parsed_received
    return datetime.min.replace(tzinfo=UTC)


def _event_time(event: dict) -> str:
    return _event_datetime(event).isoformat()


def _is_archival(event: dict) -> bool:
    return str(event.get("freshness_status") or "") == "PRE_FORWARD_PUBLICATION"


def _document_kind(event: dict) -> str:
    declared = str(event.get("document_kind") or "").upper()
    if declared:
        return declared
    record_kind = _record_kind(event)
    if record_kind == "MARKET_REACTION":
        return "MARKET_REPORT"
    if record_kind == "COMMENTARY_FORECAST":
        return "ANALYSIS"
    if record_kind == "BACKGROUND":
        return "BACKGROUND"
    headline = _normal(event.get("canonical_headline"))
    source_names = set(event.get("source_names") or ())
    if any("minutes" in headline or token in headline for token in ("会议纪要", "纪要")):
        return "MEETING_MINUTES"
    if any(token in headline for token in ("question and answer", "questions and answers", "问答", "记者会")):
        return "PRESS_CONFERENCE"
    if source_names & set(OFFICIAL_ORGANIZATIONS):
        return "OFFICIAL_DOCUMENT"
    if any(token in headline for token in (
        "gold", "silver", "黄金", "金价", "白银", "wall street", "shares", "stocks",
        "股指", "股市", "上涨", "下跌", "market reaction",
    )):
        return "MARKET_REPORT"
    return "NEWS_REPORT"


def _source_organizations(event: dict) -> set[str]:
    official_organizations = {
        OFFICIAL_ORGANIZATIONS[source]
        for source in event.get("source_names") or ()
        if source in OFFICIAL_ORGANIZATIONS
    }
    if official_organizations:
        return official_organizations
    declared = {
        canonical_source_organization(value)
        for value in event.get("source_organizations") or ()
        if canonical_source_organization(value)
    }
    single_declared = canonical_source_organization(
        event.get("source_organization_id")
    )
    if single_declared:
        declared.add(single_declared)
    if declared:
        return declared
    return {
        canonical_source_organization(domain.removeprefix("www."))
        for domain in event.get("publisher_domains") or ()
        if canonical_source_organization(domain.removeprefix("www."))
    }


def _episode_identity(event: dict) -> str | None:
    """Accept only one explicit, component-backed episode identity."""
    episode = _canonical_id(event.get("episode_key"))
    actor = _canonical_id(event.get("canonical_actor_id") or event.get("actor"))
    action = _canonical_id(event.get("action_family") or event.get("action"))
    object_id = _canonical_id(event.get("canonical_object_id") or event.get("object"))
    location = _canonical_id(event.get("canonical_location_id") or event.get("location"))
    if not episode or not actor or not action or not (object_id or location):
        return None
    canonical_episode = canonical_story_episode(event)
    if canonical_episode != episode:
        return canonical_episode
    # One economic release is one episode even when publishers and separate
    # Gemini calls use different names such as jobs_report, NFP or payrolls.
    # The normalization is deliberately anchored to a US labour authority or
    # an explicitly US employment object, so Canadian jobs and NFIB surveys do
    # not get folded into the BLS Employment Situation release.
    structured = "_".join((episode, actor, object_id))
    us_labor_actor = actor in {
        "bls", "bureau_of_labor_statistics", "labor_department",
        "us_bureau_of_labor_statistics", "us_labor_department",
    }
    us_employment_object = (
        any(token in structured for token in (
            "us_employment", "us_jobs", "us_nonfarm", "us_nfp",
            "united_states_employment", "united_states_jobs",
        ))
        and any(token in structured for token in (
            "employment", "jobs", "nonfarm", "payroll", "nfp",
        ))
    )
    if action == "economic_release" and (us_labor_actor or us_employment_object):
        month_aliases = {
            "january": "01", "february": "02", "march": "03", "april": "04",
            "may": "05", "june": "06", "july": "07", "august": "08",
            "september": "09", "october": "10", "november": "11", "december": "12",
        }
        def report_period(value: str) -> tuple[str, str] | None:
            # Prefer the economic period stated by the object.  A July jobs
            # report is normally published in August, so the episode/release
            # timestamp is not a safe proxy for the reporting month.
            for month_name, month_number in month_aliases.items():
                named = re.search(
                    rf"{month_name}[_-]?(20\d{{2}})|(20\d{{2}})[_-]?{month_name}",
                    value,
                )
                if named:
                    year = named.group(1) or named.group(2)
                    return year, month_number
            numeric = re.search(r"(20\d{2})[_-](0[1-9]|1[0-2])", value)
            return numeric.groups() if numeric else None

        # Object is the report's subject and therefore has priority. Episode
        # is only a fallback because some annotators name it by release month.
        object_period = report_period(object_id)
        episode_period = report_period(episode)
        period = object_period or episode_period
        # BLS publishes the monthly Employment Situation near the beginning of
        # the following month.  Some annotation calls named the episode by its
        # release month (for example 2026_08) while leaving the object generic.
        # Only apply this correction to an official US labour actor, an early-
        # month publication, and an episode period equal to that publication
        # month. Explicit report periods in the object always win above.
        published = _parse_time(event.get("source_published_time"))
        if (
            object_period is None
            and episode_period is not None
            and us_labor_actor
            and published is not None
            and published.day <= 10
            and episode_period == (f"{published.year:04d}", f"{published.month:02d}")
        ):
            previous_year = published.year if published.month > 1 else published.year - 1
            previous_month = published.month - 1 if published.month > 1 else 12
            period = (f"{previous_year:04d}", f"{previous_month:02d}")
        if period:
            return f"us_employment_report_{period[0]}_{period[1]}"
    # Gemini may spell the same named episode differently across independent
    # calls. Normalize known canonical anchors only when the structured object
    # or location supports the match; a headline/key mention alone is not
    # enough (for example, a sanctions case mentioning Hormuz stays separate).
    hormuz_anchor = "strait_of_hormuz" in {location, object_id} or any(
        token in object_id for token in ("strait_of_hormuz_", "_strait_of_hormuz")
    )
    if hormuz_anchor and action in {
        "official_statement", "threat", "negotiation", "agreement",
        "reopening", "closure", "military_action", "shipping_disruption",
        "other_fact",
    }:
        match = re.search(r"(20\d{2})[_-](0[1-9]|1[0-2])", episode)
        if not match:
            match = re.search(r"(20\d{2})-(0[1-9]|1[0-2])", _event_time(event))
        period = "_".join(match.groups()) if match else "current"
        return f"strait_of_hormuz_{period}"
    # Generic themes are not event episodes.
    if episode in {
        "gold", "gold_price", "黄金", "黄金价格", "国际金价", "federal_reserve",
        "美联储", "us_monetary_policy", "美国货币政策", "middle_east", "中东地缘政治",
    }:
        return None
    return episode


def _record_kind(event: dict) -> str:
    declared = str(event.get("record_kind") or "").upper()
    headline = str(event.get("canonical_headline") or "").strip()
    if declared in CORE_KINDS and headline.endswith(("?", "？")):
        return "COMMENTARY_FORECAST"
    actor = _canonical_id(event.get("canonical_actor_id") or event.get("actor"))
    action = _canonical_id(event.get("action_family") or event.get("action"))
    object_id = _canonical_id(event.get("canonical_object_id") or event.get("object"))
    # A price move is a market reaction even when the LLM called it a fact.
    # This guard changes only story membership; the immutable annotation stays
    # visible and auditable in the ledger.
    market_actor = actor in {"gold", "spot_gold", "international_gold_market", "gold_market"}
    price_object = object_id == "gold" or any(
        token in object_id for token in ("gold_price", "market_price", "price_", "_price")
    )
    if declared == "FACT_EVENT" and (market_actor or price_object):
        return "MARKET_REACTION"
    # Gemini occasionally describes a market-response article as a fact about
    # the underlying release. Keep it visible, but outside the core event
    # timeline. Both an instrument and a movement verb are required.
    normalized_headline = _normal(headline)
    market_instrument = any(token in normalized_headline for token in (
        "gold", "bullion", "silver", "dollar", "yield", "treasury", "stock", "shares",
        "futures", "oil", "黄金", "金价", "美元", "收益率", "美债", "股市",
        "股指", "期货", "油价", "原油", "白银",
    ))
    market_move = any(token in normalized_headline for token in (
        "rise", "rises", "rose", "fall", "falls", "fell", "drop", "drops",
        "gain", "gains", "climb", "climbs", "steady", "surge", "slip",
        "上涨", "下跌", "走高", "走低", "攀升", "回落", "持稳", "大涨", "大跌",
    ))
    if declared == "FACT_EVENT" and market_instrument and market_move:
        return "MARKET_REACTION"
    market_expectation = any(token in normalized_headline for token in (
        "market bets", "markets bet", "traders bet", "rate-cut bets",
        "rate hike bets", "市场押注", "交易员押注", "降息预期", "加息预期",
    ))
    monetary_channel = any(token in normalized_headline for token in (
        "fed", "rate", "yield", "dollar", "美联储", "利率", "收益率", "美元",
    ))
    if declared == "FACT_EVENT" and market_expectation and monetary_channel:
        return "MARKET_REACTION"
    # Pre-release/watch pieces and generic market narratives are context, not
    # the material event itself. They remain auditable but cannot start or
    # update an event story.
    market_waiting = any(token in normalized_headline for token in (
        "await", "awaits", "watch", "watches", "in focus", "ahead of",
        "备受关注", "等待", "关注焦点", "公布前",
    ))
    if declared == "FACT_EVENT" and market_waiting:
        return "BACKGROUND"
    return declared


def _is_core(event: dict) -> bool:
    return (
        event.get("prompt_version") == CURRENT_EVENT_PROMPT_VERSION
        and event.get("evidence_grade") in {"PRIMARY", "CORROBORATED", "SINGLE_RELIABLE"}
        and _record_kind(event) in CORE_KINDS
        and float(event.get("materiality") or 0.0) >= 0.50
        and _document_kind(event) not in {"MARKET_REPORT", "ANALYSIS", "BACKGROUND"}
        and _episode_identity(event) is not None
    )


def _relation(event: dict, *, first: bool) -> str:
    if first:
        return "STARTS"
    relation = str(event.get("relation_to_prior") or "FOLLOWED_BY").upper()
    return relation if relation in RELATIONS else "FOLLOWED_BY"


def _event_identity(event: dict) -> str:
    """Identify the fact being reported, independently from its article/cluster."""
    episode = _episode_identity(event) or ""
    action_family = _canonical_id(event.get("action_family") or event.get("action"))
    # The initial Employment Situation release is a single material event.
    # Publisher-specific material_event_key values must not split it into many
    # nodes. Explicit revisions remain distinct because they supersede it.
    if episode.startswith("us_employment_report_") and action_family == "economic_release":
        relation = str(event.get("relation_to_prior") or "").upper()
        parts = (episode, "revision", _event_time(event)) if relation == "SUPERSEDES" else (episode, "initial_release")
        return hashlib.sha256("|".join(parts).encode()).hexdigest()[:20]
    anchor = canonical_material_event_anchor({
        **event,
        "episode_key": episode,
    })
    if anchor is not None and anchor[0] == "canonical-development":
        return hashlib.sha256("|".join(anchor).encode()).hexdigest()[:20]
    declared = _canonical_id(event.get("material_event_key"))
    if declared:
        return hashlib.sha256(declared.encode()).hexdigest()[:20]
    if anchor is not None:
        return hashlib.sha256("|".join(anchor).encode()).hexdigest()[:20]
    if action_family == "policy_decision":
        parts = (episode, action_family)
        return hashlib.sha256("|".join(parts).encode()).hexdigest()[:20]
    relation = str(event.get("relation_to_prior") or "").upper()
    fact_parts = (
        episode,
        _canonical_id(event.get("canonical_actor_id") or event.get("actor")),
        action_family,
        _canonical_id(event.get("canonical_object_id") or event.get("object")),
        _canonical_id(event.get("canonical_location_id") or event.get("location")),
        _canonical_id(event.get("action")),
    )
    parts = fact_parts if relation in {"", "NONE", "CONFIRMS"} else fact_parts + (_event_time(event),)
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:20]


def _merge_event_evidence(rows: list[dict]) -> list[dict]:
    """Collapse multiple articles about one fact into one immutable event node."""
    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        grouped[_event_identity(row)].append(row)
    grade_rank = {"DISCOVERY_ONLY": 0, "SINGLE_RELIABLE": 1, "CORROBORATED": 2, "PRIMARY": 3}
    merged = []
    for event_id, members in grouped.items():
        members = sorted(
            members,
            key=lambda row: (_event_time(row), row["collector_first_seen_time"], row["event_cluster_id"]),
        )
        representative = max(
            members,
            key=lambda row: (grade_rank.get(row.get("evidence_grade"), -1), float(row.get("materiality") or 0.0)),
        ).copy()
        domains = sorted({domain for row in members for domain in row.get("publisher_domains") or () if domain})
        sources = sorted({source for row in members for source in row.get("source_names") or () if source})
        representative.update({
            "event_node_id": event_id,
            "evidence_document_count": sum(int(row.get("member_count") or 1) for row in members),
            "evidence_cluster_count": len(members),
            "publisher_domains": tuple(domains),
            "source_names": tuple(sources),
            "source_organizations": tuple(sorted({
                organization
                for row in members for organization in _source_organizations(row)
            })),
            "independent_publishers": len({
                organization
                for row in members for organization in _source_organizations(row)
            }),
            "collector_first_seen_time": min(row["collector_first_seen_time"] for row in members),
            "last_evidence_seen_time": max(row["collector_first_seen_time"] for row in members),
            "evidence_grade": max(
                (row.get("evidence_grade") or "DISCOVERY_ONLY" for row in members),
                key=lambda grade: grade_rank.get(grade, -1),
            ),
            "evidence_documents": tuple({
                (
                    _document_kind(row),
                    organization,
                    str(row.get("source_published_time") or ""),
                )
                for row in members
                for organization in (_source_organizations(row) or {"unknown"})
            }),
        })
        merged.append(representative)
    return sorted(
        merged,
        key=lambda row: (_event_datetime(row), row["collector_first_seen_time"], row["event_node_id"]),
    )


def _core_roles(members: list[dict], attached: list[dict]) -> set[str]:
    roles: set[str] = set()
    official = any(row.get("evidence_grade") == "PRIMARY" for row in members)
    reliable_organizations = {
        organization
        for row in members
        if row.get("evidence_grade") in {"PRIMARY", "CORROBORATED", "SINGLE_RELIABLE"}
        for organization in (
            row.get("source_organizations") or _source_organizations(row)
        )
        if organization
    }
    if official:
        roles.add("OFFICIAL_PRIMARY")
    elif len(reliable_organizations) == 1:
        roles.add("SINGLE_RELIABLE")
    # One publisher never becomes independent confirmation. Official + one
    # independent reliable publisher, or two reliable publishers, is required.
    if len(reliable_organizations) >= 2:
        roles.add("INDEPENDENT_CONFIRMATION")
    if any(_record_kind(row) == "MARKET_REACTION" for row in attached):
        roles.add("MARKET_REACTION")
    if any(
        _canonical_id(row.get("action_family"))
        in {"shipping_disruption", "military_action", "energy_supply_change"}
        for row in members
    ):
        roles.add("PHYSICAL_IMPACT")
    return roles


def _state(core: list[dict], roles: set[str]) -> str:
    latest = core[-1]
    relation = _relation(latest, first=len(core) == 1)
    if relation == "CONTRADICTS":
        return "CONTRADICTED"
    if relation == "ESCALATES":
        return "ESCALATING"
    if relation == "DEESCALATES":
        return "DEESCALATING"
    if "INDEPENDENT_CONFIRMATION" in roles:
        return "CORROBORATED"
    # A publisher saying that something is confirmed is still only a report.
    # The UI may say official confirmation only when the evidence ledger has an
    # actual first-party source; claim_status alone cannot upgrade provenance.
    if "OFFICIAL_PRIMARY" in roles:
        return "OFFICIALLY_CONFIRMED"
    return "REPORTED" if len(core) > 1 else "EMERGING"


def _timeline_row(event: dict, *, first: bool) -> dict:
    return {
        "event_key": event.get("event_node_id") or event["event_cluster_id"],
        "first_seen": event["collector_first_seen_time"],
        "event_time": _event_time(event),
        "source_published_time": event.get("source_published_time"),
        "collector_first_seen_time": event["collector_first_seen_time"],
        "headline": event["canonical_headline"],
        "actor": event.get("actor") or "",
        "action": event.get("action") or "",
        "object": event.get("object") or "",
        "location": event.get("location") or "",
        "claim_status": event.get("claim_status") or "",
        "materiality": float(event.get("materiality") or 0.0),
        "evidence_grade": event["evidence_grade"],
        "independent_publishers": event["independent_publishers"],
        "evidence_documents": int(event.get("evidence_document_count") or event.get("member_count") or 1),
        "independent_organizations": int(event.get("independent_publishers") or 0),
        "document_kinds": sorted({document[0] for document in event.get("evidence_documents") or ()}),
        "archival": _is_archival(event),
        "relation": _relation(event, first=first),
    }


def _is_active_story(core: list[dict], attached: list[dict]) -> bool:
    # Confirmations and reactions strengthen one fact; they do not create a
    # temporal chain. A story requires two distinct real-world developments.
    return len(core) >= 2


def _coverage_template(core: list[dict]) -> tuple[str, tuple[str, ...]]:
    families = {_canonical_id(row.get("action_family")) for row in core}
    episode = _episode_identity(core[0]) or ""
    if "policy_decision" in families:
        key = "MONETARY_POLICY_V1"
    elif "strait_of_hormuz" in episode or families & {
        "shipping_disruption", "military_action", "threat", "negotiation",
    }:
        key = "GEOPOLITICAL_SHIPPING_V1"
    elif "gold_purchase" in families:
        key = "CENTRAL_BANK_GOLD_V1"
    else:
        key = "MATERIAL_EVENT_V1"
    return key, COVERAGE_TEMPLATES[key]


def _is_market_narrative(core: list[dict]) -> bool:
    return not any(_document_kind(row) not in {"MARKET_REPORT", "ANALYSIS"} for row in core)


def _story_row(episode: str, core: list[dict], attached: list[dict]) -> dict:
    roles = _core_roles(core, attached)
    template_key, required_roles = _coverage_template(core)
    by_kind = {
        kind: sorted(
            (row for row in attached if _record_kind(row) == kind),
            key=lambda row: (_event_datetime(row), row["collector_first_seen_time"], row["event_cluster_id"]),
        )
        for kind in ATTACHMENT_KINDS
    }
    title = next(
        (str(row.get("primary_story_title_zh") or "").strip() for row in reversed(core)
         if str(row.get("primary_story_title_zh") or "").strip()),
        episode.replace("_", " "),
    )
    timeline = [_timeline_row(row, first=index == 0) for index, row in enumerate(core[-20:])]
    identity = hashlib.sha256(episode.encode()).hexdigest()[:16]
    role_order = ("OFFICIAL_PRIMARY", "SINGLE_RELIABLE", "INDEPENDENT_CONFIRMATION", "PHYSICAL_IMPACT", "MARKET_REACTION")
    covered = [role for role in role_order if role in roles]
    template_covered = [role for role in required_roles if role in roles]
    missing = [role for role in required_roles if role not in roles]
    organizations = sorted({organization for row in core for organization in row.get("source_organizations") or ()})
    archival = all(_is_archival(row) for row in core)
    market_narrative = _is_market_narrative(core)
    return {
        "storyline_id": f"story-{identity}", "episode_key": episode, "title": title,
        "policy_version": STORYLINE_POLICY_VERSION,
        "state": "ARCHIVAL_BACKFILL" if archival else _state(core, roles),
        "story_type": "MARKET_NARRATIVE_CANDIDATE" if market_narrative else "MATERIAL_EPISODE",
        "archival": archival,
        "event_count": len(core),
        "evidence_document_count": sum(int(row.get("evidence_document_count") or 1) for row in core),
        "reliable_event_count": sum(row["evidence_grade"] in {"PRIMARY", "CORROBORATED", "SINGLE_RELIABLE"} for row in core),
        "latest_change": core[-1]["canonical_headline"],
        "last_updated": max(row.get("last_evidence_seen_time") or row["collector_first_seen_time"] for row in core),
        "covered_roles": [{"key": role, "label": ROLE_LABELS[role]} for role in covered],
        "missing_roles": [{"key": role, "label": ROLE_LABELS[role]} for role in missing],
        "coverage_template": template_key,
        "coverage_count": len(template_covered),
        "coverage_total": len(required_roles),
        "independent_organization_count": len(organizations),
        "source_organizations": organizations,
        "independent_confirmation": "INDEPENDENT_CONFIRMATION" in roles,
        "timeline": timeline,
        "market_reactions": [_timeline_row(row, first=False) for row in by_kind["MARKET_REACTION"][-8:]],
        "commentary": [_timeline_row(row, first=False) for row in by_kind["COMMENTARY_FORECAST"][-8:]],
        "background": [_timeline_row(row, first=False) for row in by_kind["BACKGROUND"][-8:]],
        "model_permission": MODEL_PERMISSION,
    }


def storyline_rows(events: list[dict]) -> list[dict]:
    """Build strict stories; non-core records may attach but never create/update one."""
    core_groups: dict[str, list[dict]] = defaultdict(list)
    attachments: dict[str, list[dict]] = defaultdict(list)
    for event in events:
        episode = _episode_identity(event)
        if _is_core(event):
            core_groups[episode].append(event)
        elif episode and _record_kind(event) in ATTACHMENT_KINDS:
            attachments[episode].append(event)

    stories: list[dict] = []
    for episode, core in core_groups.items():
        core = _merge_event_evidence(core)
        attached = attachments.get(episode, [])
        if _is_active_story(core, attached):
            stories.append(_story_row(episode, core, attached))
    return sorted(stories, key=lambda row: row["last_updated"], reverse=True)


def event_candidate_rows(events: list[dict]) -> list[dict]:
    """Expose isolated core facts without pretending each one is a storyline."""
    grouped: dict[str, list[dict]] = defaultdict(list)
    attachments: dict[str, list[dict]] = defaultdict(list)
    for row in events:
        episode = _episode_identity(row)
        if _is_core(row):
            grouped[episode].append(row)
        elif episode and _record_kind(row) in ATTACHMENT_KINDS:
            attachments[episode].append(row)
    candidates = []
    for episode, rows in grouped.items():
        core = _merge_event_evidence(rows)
        if _is_active_story(core, attachments.get(episode, [])):
            continue
        row = core[-1]
        candidates.append({
            "candidate_id": f"candidate-{row['event_node_id']}", "episode_key": episode,
            "headline": row["canonical_headline"], "first_seen": row["collector_first_seen_time"], "event_time": _event_time(row),
            "evidence_documents": int(row.get("evidence_document_count") or 1),
            "independent_publishers": int(row.get("independent_publishers") or 0),
            "archival": _is_archival(row),
            "reason": "ARCHIVAL_BACKFILL" if _is_archival(row) else "WAITING_FOR_DISTINCT_EVENT",
            "model_permission": MODEL_PERMISSION,
        })
    return sorted(candidates, key=lambda row: row["first_seen"], reverse=True)


def market_reaction_stream_rows(events: list[dict]) -> list[dict]:
    """Aggregate price moves separately from factual geopolitical/policy episodes."""
    grouped: dict[str, list[dict]] = defaultdict(list)
    labels = {"gold": "黄金价格反应", "usd": "美元反应", "oil": "油价反应",
              "treasury_yields": "美债收益率反应", "stocks": "股票市场反应",
              "other_market": "其他市场反应"}
    for row in events:
        if _record_kind(row) != "MARKET_REACTION":
            continue
        text = _normal(" ".join(str(row.get(name) or "") for name in (
            "canonical_actor_id", "actor", "canonical_object_id", "object", "canonical_headline"
        )))
        actor = (
            "gold" if any(token in text for token in ("gold", "黄金", "金价")) else
            "usd" if any(token in text for token in ("usd", "dollar", "美元")) else
            "treasury_yields" if any(token in text for token in ("yield", "treasury", "美债", "收益率")) else
            "oil" if any(token in text for token in ("oil", "crude", "原油", "油价")) else
            "stocks" if any(token in text for token in ("stock", "equity", "s&p", "nasdaq", "dow", "股市", "股票")) else
            "other_market"
        )
        grouped[actor].append(row)
    streams = []
    for actor, members in grouped.items():
        members = sorted(members, key=lambda row: row["collector_first_seen_time"])
        streams.append({
            "stream_id": actor, "title": labels[actor],
            "item_count": len(members), "latest_headline": members[-1]["canonical_headline"],
            "last_updated": members[-1]["collector_first_seen_time"], "model_permission": MODEL_PERMISSION,
        })
    return sorted(streams, key=lambda row: row["last_updated"], reverse=True)


def theme_stream_rows(events: list[dict]) -> list[dict]:
    """Keep broad subjects visible without pretending they are one episode."""
    grouped: dict[str, list[dict]] = defaultdict(list)
    for event in events:
        category = str(event.get("primary_category") or "regulation_other")
        grouped[category if category in THEMES else "regulation_other"].append(event)
    rows = []
    for category, members in grouped.items():
        members = sorted(members, key=lambda row: row["collector_first_seen_time"])
        rows.append({
            "theme_id": category,
            "title": THEMES[category],
            "item_count": len(members),
            "last_updated": members[-1]["collector_first_seen_time"],
            "latest_headline": members[-1]["canonical_headline"],
            "model_permission": MODEL_PERMISSION,
        })
    return sorted(rows, key=lambda row: row["last_updated"], reverse=True)


def unassigned_event_rows(events: list[dict]) -> list[dict]:
    rows = []
    for event in events:
        if _episode_identity(event) is not None:
            continue
        reason = "LEGACY_ANNOTATION_NO_FACT_STRUCTURE" if not event.get("record_kind") else "INSUFFICIENT_STORY_MATCH"
        rows.append({
            "event_key": event["event_cluster_id"],
            "headline": event["canonical_headline"],
            "first_seen": event["collector_first_seen_time"],
            "record_kind": event.get("record_kind") or "UNSTRUCTURED",
            "reason": reason,
        })
    return sorted(rows, key=lambda row: row["first_seen"], reverse=True)


def temporal_event_graph(events: list[dict]) -> dict:
    all_stories = storyline_rows(events)
    stories = [row for row in all_stories if not row["archival"] and row["story_type"] == "MATERIAL_EPISODE"]
    market_narratives = [row for row in all_stories if not row["archival"] and row["story_type"] == "MARKET_NARRATIVE_CANDIDATE"]
    archives = [row for row in all_stories if row["archival"]]
    all_candidates = event_candidate_rows(events)
    candidates = [row for row in all_candidates if not row["archival"]]
    archived_candidates = [row for row in all_candidates if row["archival"]]
    themes = theme_stream_rows(events)
    unassigned = unassigned_event_rows(events)
    return {
        "policy_version": STORYLINE_POLICY_VERSION,
        "legacy_policy_status": LEGACY_POLICY_STATUS,
        "model_permission": MODEL_PERMISSION,
        "stories": stories,
        "market_narrative_candidates": market_narratives,
        "archived_storylines": archives,
        "event_candidates": candidates,
        "archived_event_candidates": archived_candidates,
        "market_reaction_streams": market_reaction_stream_rows(events),
        "theme_streams": themes,
        "unassigned_events": unassigned,
    }
