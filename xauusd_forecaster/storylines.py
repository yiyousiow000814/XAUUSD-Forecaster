"""Research-only temporal event graph and source coverage diagnostics."""

from __future__ import annotations

import hashlib
import re
from collections import Counter, defaultdict


STORYLINE_POLICY_VERSION = "temporal-event-graph-v2"

EVENT_FAMILIES = {
    "monetary_policy": {"topics": {"rates_fed"}, "label": "货币政策"},
    "macro_release": {"topics": {"inflation", "employment", "growth_economy"}, "label": "宏观数据"},
    "geopolitics": {"topics": {"war_geopolitics"}, "label": "战争与地缘"},
    "energy_supply": {"topics": {"oil_energy"}, "label": "能源供应"},
    "central_bank_gold": {"topics": {"central_bank_gold"}, "label": "央行购金"},
    "financial_stress": {"topics": {"usd_liquidity", "risk_sentiment"}, "label": "金融与流动性"},
}

# A source can perform more than one role. Roles, not source names, define coverage.
SOURCE_ROLE_REGISTRY = {
    "federal_reserve_monetary": ("OFFICIAL_PRIMARY", "POLICY_AUTHORITY"),
    "federal_reserve_press_all": ("OFFICIAL_PRIMARY", "REGULATOR"),
    "federal_reserve_speeches_testimony": ("OFFICIAL_PRIMARY", "POLICY_AUTHORITY"),
    "bea_economic_releases": ("OFFICIAL_PRIMARY", "STATISTICS_AUTHORITY"),
    "us_treasury_press_releases": ("OFFICIAL_PRIMARY", "FISCAL_AUTHORITY"),
    "eia_press_releases": ("OFFICIAL_PRIMARY", "ENERGY_AUTHORITY"),
    "eia_today_in_energy": ("OFFICIAL_PRIMARY", "ENERGY_AUTHORITY"),
    "ecb_press_releases": ("OFFICIAL_PRIMARY", "POLICY_AUTHORITY"),
    "world_gold_council_central_banks": ("SECTOR_AUTHORITY", "GOLD_FLOW_MONITOR"),
    "gdelt_gold_geopolitics": ("BROAD_DISCOVERY",),
    "google_news_gold_context": ("BROAD_DISCOVERY",),
}

COVERAGE_TEMPLATES = {
    "monetary_policy": ("POLICY_AUTHORITY", "INDEPENDENT_CONFIRMATION", "MARKET_REACTION"),
    "macro_release": ("STATISTICS_AUTHORITY", "INDEPENDENT_CONFIRMATION", "MARKET_REACTION"),
    "geopolitics": ("OFFICIAL_PRIMARY", "PHYSICAL_MONITOR", "INDEPENDENT_CONFIRMATION", "MARKET_REACTION"),
    "energy_supply": ("ENERGY_AUTHORITY", "PHYSICAL_MONITOR", "INDEPENDENT_CONFIRMATION", "MARKET_REACTION"),
    "central_bank_gold": ("GOLD_FLOW_MONITOR", "OFFICIAL_PRIMARY", "INDEPENDENT_CONFIRMATION"),
    "financial_stress": ("OFFICIAL_PRIMARY", "REGULATOR", "INDEPENDENT_CONFIRMATION", "MARKET_REACTION"),
}

ROLE_LABELS = {
    "OFFICIAL_PRIMARY": "官方一手",
    "POLICY_AUTHORITY": "政策机构",
    "STATISTICS_AUTHORITY": "统计机构",
    "FISCAL_AUTHORITY": "财政机构",
    "REGULATOR": "监管机构",
    "ENERGY_AUTHORITY": "能源机构",
    "SECTOR_AUTHORITY": "行业权威",
    "GOLD_FLOW_MONITOR": "黄金流向监测",
    "PHYSICAL_MONITOR": "实物/现场监测",
    "INDEPENDENT_CONFIRMATION": "独立媒体确认",
    "MARKET_REACTION": "市场反应确认",
    "BROAD_DISCOVERY": "广域发现",
}

_GENERIC_ENTITIES = {
    "gold", "xauusd", "market", "markets", "united states", "u.s.", "us",
    "黄金", "市场", "美国", "美联储", "federal reserve",
}
_TITLE_ALIASES = {
    "strait of hormuz": "霍尔木兹海峡", "hormuz": "霍尔木兹海峡", "霍尔木兹": "霍尔木兹海峡",
    "iran": "伊朗", "伊朗": "伊朗", "ukraine": "乌克兰", "乌克兰": "乌克兰",
    "russia": "俄罗斯", "俄罗斯": "俄罗斯", "fomc": "FOMC",
}
_BROAD_STORY_ANCHORS = {"伊朗", "乌克兰", "俄罗斯"}


def _normal(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "").casefold()).strip()


def _family(event: dict) -> tuple[str, str] | None:
    category_family = {
        "rates_fed": "monetary_policy",
        "inflation_employment": "macro_release",
        "growth_economy": "macro_release",
        "war_geopolitics": "geopolitics",
        "oil_energy": "energy_supply",
        "central_bank_gold": "central_bank_gold",
        "usd_liquidity": "financial_stress",
        "risk_sentiment": "financial_stress",
    }
    selected = category_family.get(str(event.get("primary_category") or ""))
    if selected:
        return selected, str(EVENT_FAMILIES[selected]["label"])
    topics = set(event.get("topics") or ())
    for key, definition in EVENT_FAMILIES.items():
        if topics & definition["topics"]:
            return key, str(definition["label"])
    return None


def _story_entities(event: dict) -> tuple[str, ...]:
    values = {_normal(value) for value in event.get("entities") or ()}
    headline = _normal(event.get("canonical_headline") or event.get("headline"))
    for term in _TITLE_ALIASES:
        if term in headline:
            values.add(term)
    return tuple(sorted(value for value in values if len(value) >= 3 and value not in _GENERIC_ENTITIES))


def _display_entity(value: str) -> str:
    return _TITLE_ALIASES.get(value, value.title() if value.isascii() else value)


def _story_anchor(event: dict) -> str | None:
    headline = _normal(event.get("canonical_headline") or event.get("headline"))
    for term in _TITLE_ALIASES:
        if term in headline:
            canonical = _display_entity(term)
            if canonical not in _BROAD_STORY_ANCHORS:
                return canonical
    explicit = [entity for entity in _story_entities(event) if entity in headline]
    if not explicit:
        return None
    canonical = _display_entity(explicit[0])
    return None if canonical in _BROAD_STORY_ANCHORS else canonical


def _relation(previous: dict, current: dict) -> str:
    delta = float(current.get("geopolitical_risk") or 0) - float(previous.get("geopolitical_risk") or 0)
    if delta >= 0.15:
        return "ESCALATES"
    if delta <= -0.15:
        return "DEESCALATES"
    if current.get("evidence_grade") in {"PRIMARY", "CORROBORATED"} and previous.get("evidence_grade") not in {"PRIMARY", "CORROBORATED"}:
        return "CONFIRMS"
    return "FOLLOWED_BY"


def _source_roles(event: dict) -> set[str]:
    roles = {
        role for source in event.get("source_names") or ()
        for role in SOURCE_ROLE_REGISTRY.get(source, ())
    }
    if event.get("independent_publishers", 0) >= 1:
        roles.add("INDEPENDENT_CONFIRMATION")
    if {"risk_sentiment", "usd_liquidity"} & set(event.get("topics") or ()):
        roles.add("MARKET_REACTION")
    return roles


def _state(members: list[dict], covered_roles: set[str]) -> str:
    latest = members[-1]
    relations = [_relation(left, right) for left, right in zip(members, members[1:])]
    if relations and relations[-1] == "DEESCALATES":
        return "DEESCALATING"
    if relations and relations[-1] == "ESCALATES":
        return "ESCALATING"
    if "PHYSICAL_MONITOR" in covered_roles and latest.get("evidence_grade") in {"PRIMARY", "CORROBORATED"}:
        return "PHYSICAL_IMPACT_CONFIRMED"
    if latest.get("evidence_grade") == "PRIMARY":
        return "OFFICIALLY_CONFIRMED"
    if latest.get("evidence_grade") == "CORROBORATED":
        return "CORROBORATED"
    return "REPORTED" if len(members) >= 2 else "EMERGING"


def _candidate_sources(members: list[dict], missing_roles: list[str]) -> list[dict]:
    domains = Counter(
        domain for event in members for domain in event.get("publisher_domains") or () if domain
    )
    candidates = []
    for domain, count in domains.most_common(4):
        candidates.append({
            "candidate": domain,
            "suggested_role": "INDEPENDENT_CONFIRMATION",
            "reason": f"已在本故事出现 {count} 次，等待可靠性与正文链路验证",
            "status": "PROBATION",
            "adapter": "ARTICLE_BODY",
        })
    for role in missing_roles:
        if role in {"PHYSICAL_MONITOR", "MARKET_REACTION", "OFFICIAL_PRIMARY"}:
            candidates.append({
                "candidate": f"待发现：{ROLE_LABELS[role]}",
                "suggested_role": role,
                "reason": "覆盖模板发现缺口；系统只提出角色需求，不自动授予来源权限",
                "status": "NEEDS_DISCOVERY",
                "adapter": "RSS / ATOM / JSON API / HTML LIST / PDF RELEASE",
            })
    return candidates[:6]


def storyline_rows(events: list[dict]) -> list[dict]:
    """Build generic display-only stories from event family, entities and first-seen time."""
    grouped: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for event in events:
        family = _family(event)
        anchor = _story_anchor(event)
        if family and anchor:
            grouped[(family[0], anchor)].append(event)

    stories = []
    for (family_key, anchor), members in grouped.items():
        members = sorted(members, key=lambda row: (row["collector_first_seen_time"], row["event_cluster_id"]))
        unique_members = {}
        for member in members:
            unique_members.setdefault(_normal(member.get("canonical_headline")), member)
        members = list(unique_members.values())
        if len(members) < 2:
            continue
        entity_counts = Counter(entity for member in members for entity in _story_entities(member))
        entities = tuple(entity for entity, count in entity_counts.most_common() if count >= 2)[:2]
        if not entities:
            entities = (anchor,)
        covered_roles = set().union(*(_source_roles(member) for member in members))
        required_roles = list(COVERAGE_TEMPLATES[family_key])
        missing_roles = [role for role in required_roles if role not in covered_roles]
        title = "—".join(dict.fromkeys(_display_entity(value) for value in entities))
        headline_blob = " ".join(_normal(member.get("canonical_headline")) for member in members)
        if ("iran" in headline_blob or "伊朗" in headline_blob) and ("hormuz" in headline_blob or "霍尔木兹" in headline_blob):
            title = "伊朗—霍尔木兹海峡"
        timeline = []
        previous = None
        for event in members[-16:]:
            timeline.append({
                "event_key": event["event_cluster_id"],
                "first_seen": event["collector_first_seen_time"],
                "headline": event["canonical_headline"],
                "evidence_grade": event["evidence_grade"],
                "independent_publishers": event["independent_publishers"],
                "relation": "STARTS" if previous is None else _relation(previous, event),
                "topics": list(event["topics"]),
            })
            previous = event
        reliable = sum(row["evidence_grade"] in {"PRIMARY", "CORROBORATED"} for row in members)
        identity = hashlib.sha256(f"{family_key}|{'|'.join(entities)}".encode()).hexdigest()[:16]
        stories.append({
            "storyline_id": f"story-{identity}",
            "title": title,
            "family": family_key,
            "family_label": EVENT_FAMILIES[family_key]["label"],
            "policy_version": STORYLINE_POLICY_VERSION,
            "state": _state(members, covered_roles),
            "event_count": len(members),
            "reliable_event_count": reliable,
            "latest_change": members[-1]["canonical_headline"],
            "last_updated": members[-1]["collector_first_seen_time"],
            "topics": sorted({topic for row in members for topic in row["topics"]}),
            "covered_roles": [{"key": role, "label": ROLE_LABELS[role]} for role in required_roles if role in covered_roles],
            "missing_roles": [{"key": role, "label": ROLE_LABELS[role]} for role in missing_roles],
            "coverage_count": len(required_roles) - len(missing_roles),
            "coverage_total": len(required_roles),
            "candidate_sources": _candidate_sources(members, missing_roles),
            "state_deltas": {
                "official_confirmation_delta": int("OFFICIAL_PRIMARY" in covered_roles),
                "independent_confirmation_delta": int("INDEPENDENT_CONFIRMATION" in covered_roles),
                "physical_impact_delta": int("PHYSICAL_MONITOR" in covered_roles),
                "escalation_delta": sum(item["relation"] == "ESCALATES" for item in timeline) - sum(item["relation"] == "DEESCALATES" for item in timeline),
                "source_diversity_delta": len({source for row in members for source in row.get("source_names") or ()}),
            },
            "timeline": timeline,
            "model_permission": "DISPLAY_ONLY",
        })
    return sorted(stories, key=lambda row: row["last_updated"], reverse=True)
