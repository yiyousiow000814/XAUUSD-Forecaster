"""Single source of truth for controlled news annotation semantics."""

from __future__ import annotations

import json
import math
import re
from functools import lru_cache
from pathlib import Path
from typing import Any


CURRENT_NEWS_PROMPT_VERSION = "news-json-v14-material-event-evidence"
V1_NEWS_PROMPT_VERSIONS = (
    CURRENT_NEWS_PROMPT_VERSION,
    "news-json-v13-event-claims",
    "news-json-v12-gemini-story-identity",
    "news-json-v11-gemini-story-subjects",
    "news-json-v10-controlled-category-zh",
)

_SCHEMA_PATH = Path(__file__).with_name("news_annotation.schema.json")


@lru_cache(maxsize=1)
def news_annotation_schema() -> dict:
    return json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))


def _enum(property_name: str) -> frozenset[str]:
    return frozenset(news_annotation_schema()["properties"][property_name]["enum"])


NEWS_CATEGORIES = _enum("primary_category")
RECORD_KINDS = _enum("record_kind")
DOCUMENT_KINDS = _enum("document_kind")
EVIDENCE_ROLES = _enum("evidence_role")

ACTIONABLE_CATEGORIES = NEWS_CATEGORIES - {"regulation_other"}
ACTIONABLE_RECORD_KINDS = frozenset({"FACT_EVENT", "OFFICIAL_CLAIM"})

_MARKET_INSTRUMENT_TERMS = frozenset({
    "gold", "bullion", "dollar", "yield", "treasury", "stock", "shares",
    "futures", "oil", "黄金", "金价", "美元", "收益率", "美债", "股市",
    "股指", "期货", "油价", "原油",
})
_MARKET_MOVEMENT_TERMS = frozenset({
    "rise", "rises", "rose", "higher", "fall", "falls", "fell", "drop",
    "gain", "gains", "climb", "steady", "surge", "slip", "breakout",
    "上涨", "下跌", "走高", "走低", "攀升", "回落", "持稳", "突破",
})

CATEGORY_TOPICS: dict[str, tuple[str, ...]] = {
    "rates_fed": ("rates_fed",),
    "inflation_employment": ("inflation", "employment"),
    "growth_economy": ("growth_economy",),
    "usd_liquidity": ("usd_liquidity",),
    "oil_energy": ("oil_energy",),
    "war_geopolitics": ("war_geopolitics",),
    "central_bank_gold": ("central_bank_gold",),
    "risk_sentiment": ("risk_sentiment",),
    "regulation_other": ("other",),
}


def _contains_term(text: str, terms: frozenset[str]) -> bool:
    """Match English words and explicit CJK phrases without substring leaks."""
    return any(
        term in text if any("\u3400" <= char <= "\u9fff" for char in term)
        else re.search(rf"(?<![a-z]){re.escape(term)}(?![a-z])", text) is not None
        for term in terms
    )


def effective_record_kind(annotation: dict, headline: str = "") -> str:
    """Return the model kind after narrow, fail-closed consistency checks.

    Gemini remains the classifier and its immutable annotation is preserved.
    This admission view only prevents an internally contradictory annotation,
    or an explicit market-price narration, from becoming a core fact event.
    """
    declared = str(annotation.get("record_kind") or "").upper()
    if declared not in RECORD_KINDS:
        return "BACKGROUND"
    if declared not in ACTIONABLE_RECORD_KINDS:
        return declared
    evidence_role = str(annotation.get("evidence_role") or "").upper()
    if evidence_role == "MARKET_REACTION":
        return "MARKET_REACTION"
    if evidence_role == "COMMENTARY":
        return "COMMENTARY_FORECAST"
    if evidence_role == "BACKGROUND":
        return "BACKGROUND"
    if str(annotation.get("relation_to_prior") or "").upper() == "MARKET_REACTS_TO":
        return "MARKET_REACTION"
    document_kind = str(annotation.get("document_kind") or "").upper()
    if document_kind == "ANALYSIS":
        return "COMMENTARY_FORECAST"
    if document_kind == "BACKGROUND":
        return "BACKGROUND"
    normalized = re.sub(
        r"\s+", " ",
        str(headline or annotation.get("headline_zh") or "").casefold(),
    )
    if (
        _contains_term(normalized, _MARKET_INSTRUMENT_TERMS)
        and _contains_term(normalized, _MARKET_MOVEMENT_TERMS)
    ):
        return "MARKET_REACTION"
    return declared


def validate_news_annotation(annotation: dict[str, Any]) -> None:
    """Validate the complete current annotation against the packaged schema."""
    schema = news_annotation_schema()
    properties = schema["properties"]
    required = set(schema["required"])
    keys = set(annotation)
    missing = required - keys
    additional = keys - set(properties)
    if missing:
        raise ValueError(
            "annotation missing schema fields: " + ", ".join(sorted(missing))
        )
    if additional:
        raise ValueError(
            "annotation has unknown schema fields: " + ", ".join(sorted(additional))
        )
    for name, rule in properties.items():
        value = annotation[name]
        expected = rule.get("type")
        if expected == "string":
            if not isinstance(value, str):
                raise ValueError(f"annotation {name} is not a string")
            if len(value) < int(rule.get("minLength", 0)):
                raise ValueError(f"annotation {name} is too short")
            if len(value) > int(rule.get("maxLength", len(value))):
                raise ValueError(f"annotation {name} is too long")
            if "enum" in rule and value not in rule["enum"]:
                raise ValueError(f"annotation {name} is not controlled")
        elif expected == "number":
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"annotation {name} is not numeric")
            if not math.isfinite(value):
                raise ValueError(f"annotation {name} is not finite")
            if value < rule.get("minimum", value) or value > rule.get("maximum", value):
                raise ValueError(f"annotation {name} is outside its allowed range")
        elif expected == "array":
            if not isinstance(value, list):
                raise ValueError(f"annotation {name} is not an array")
            if len(value) > int(rule.get("maxItems", len(value))):
                raise ValueError(f"annotation {name} exceeds its item limit")
            item_rule = rule.get("items", {})
            for item in value:
                if item_rule.get("type") == "string" and not isinstance(item, str):
                    raise ValueError(f"annotation {name} contains a non-string")
                if len(str(item)) < int(item_rule.get("minLength", 0)):
                    raise ValueError(f"annotation {name} contains a short item")
                if len(str(item)) > int(item_rule.get("maxLength", len(str(item)))):
                    raise ValueError(f"annotation {name} contains a long item")
                if "enum" in item_rule and item not in item_rule["enum"]:
                    raise ValueError(f"annotation {name} contains an uncontrolled item")
            if rule.get("uniqueItems") and len(value) != len(set(value)):
                raise ValueError(f"annotation {name} contains duplicates")
    secondary = annotation["secondary_categories"]
    if annotation["primary_category"] in secondary:
        raise ValueError("annotation category cannot be both primary and secondary")


def annotation_topics(annotation: dict) -> tuple[str, ...]:
    """Map Gemini's controlled categories to stable model topic features."""
    categories = (
        annotation.get("primary_category"),
        *(annotation.get("secondary_categories") or []),
    )
    topics: list[str] = []
    for category in categories:
        topics.extend(CATEGORY_TOPICS.get(str(category), ()))
    return tuple(dict.fromkeys(topics or ("other",)))
