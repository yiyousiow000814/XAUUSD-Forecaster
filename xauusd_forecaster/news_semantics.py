"""Single source of truth for controlled news annotation semantics."""

from __future__ import annotations

import json
import math
import re
from functools import lru_cache
from pathlib import Path
from typing import Any


LEGACY_NEWS_PROMPT_VERSION = "news-json-v14-material-event-evidence"
PREVIOUS_NEWS_PROMPT_VERSION = "news-json-v15-ai-semantic-review"
CURRENT_NEWS_PROMPT_VERSION = "news-json-v16-xauusd-transmission-evidence"
LEGACY_INVALID_SEMANTIC_REASON_PREFIX = "语言或结构一致性检查未通过"
V1_NEWS_PROMPT_VERSIONS = (
    LEGACY_NEWS_PROMPT_VERSION,
    "news-json-v13-event-claims",
    "news-json-v12-gemini-story-identity",
    "news-json-v11-gemini-story-subjects",
    "news-json-v10-controlled-category-zh",
)
GENERATED_NEWS_PROMPT_VERSIONS = frozenset({CURRENT_NEWS_PROMPT_VERSION})
SEMANTIC_NEWS_PROMPT_VERSIONS = frozenset({
    PREVIOUS_NEWS_PROMPT_VERSION,
    CURRENT_NEWS_PROMPT_VERSION,
})
SUPPORTED_NEWS_PROMPT_VERSIONS = frozenset({
    *V1_NEWS_PROMPT_VERSIONS,
    PREVIOUS_NEWS_PROMPT_VERSION,
    CURRENT_NEWS_PROMPT_VERSION,
})

_SCHEMA_PATH = Path(__file__).with_name("news_annotation.schema.json")


def validated_annotation_predicate(alias: str) -> str:
    """One SQL contract for annotations that may drive downstream behavior."""
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", alias):
        raise ValueError("invalid SQL alias")
    return (
        f"COALESCE(json_extract({alias}.annotation_json, "
        "'$.semantic_reason_zh'), '') NOT LIKE "
        f"'{LEGACY_INVALID_SEMANTIC_REASON_PREFIX}%'"
    )


AI_SEMANTIC_FIELDS = {
    "xauusd_relevance": {
        "type": "string",
        "enum": ["DIRECT", "MACRO_DRIVER", "CONTEXT_ONLY", "IRRELEVANT"],
        "description": "Semantic relationship to XAUUSD, based on the complete source",
    },
    "review_priority": {
        "type": "string",
        "enum": ["IMMEDIATE", "FAST", "NORMAL", "BACKGROUND"],
        "description": "How quickly an independent semantic review is needed",
    },
    "material_change": {
        "type": "string",
        "enum": [
            "NEW_EVENT", "MATERIAL_UPDATE", "DUPLICATE_REPORT",
            "COMMENTARY", "HISTORICAL_CONTEXT",
        ],
        "description": "Whether the source adds a new real-world fact",
    },
    "time_sensitivity": {
        "type": "string",
        "enum": ["IMMEDIATE", "SAME_DAY", "MULTI_DAY", "ONGOING", "BACKGROUND"],
        "description": "Semantic urgency without using facts published later",
    },
    "semantic_reason_zh": {
        "type": "string", "minLength": 4, "maxLength": 240,
        "description": "Concise Simplified Chinese reason grounded in the source",
    },
    "supporting_evidence": {
        "type": "array", "minItems": 1, "maxItems": 3,
        "items": {"type": "string", "minLength": 4, "maxLength": 240},
        "uniqueItems": True,
        "description": "Exact short excerpts copied from the source",
    },
}


@lru_cache(maxsize=3)
def news_annotation_schema(
    prompt_version: str = CURRENT_NEWS_PROMPT_VERSION,
) -> dict:
    schema = json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))
    if prompt_version in SEMANTIC_NEWS_PROMPT_VERSIONS:
        schema["$id"] = (
            "xauusd.forward.news-annotation.v16"
            if prompt_version == CURRENT_NEWS_PROMPT_VERSION
            else "xauusd.forward.news-annotation.v15"
        )
        schema["required"].extend(AI_SEMANTIC_FIELDS)
        schema["properties"].update(json.loads(json.dumps(AI_SEMANTIC_FIELDS)))
        if prompt_version == CURRENT_NEWS_PROMPT_VERSION:
            evidence = schema["properties"]["supporting_evidence"]
            evidence["description"] = (
                "One to three contiguous source-language substrings copied "
                "verbatim from the supplied headline or full content; never "
                "translate, paraphrase, join clauses, or add ellipses"
            )
    elif prompt_version not in V1_NEWS_PROMPT_VERSIONS:
        raise ValueError(f"unsupported news prompt version: {prompt_version}")
    return schema


def canonicalize_active_annotation(
    annotation: dict[str, Any], *, source_text: str, max_length: int = 240,
) -> None:
    """Apply lossless active-generation cleanup before fail-closed validation.

    Repeated or primary secondary categories carry no additional meaning. The
    evidence repair is deliberately narrow: after removing punctuation and
    spacing, the complete excerpt must have exactly one contiguous match in the
    source. Changed words, numbers, ordering, joined clauses, and ambiguous
    matches stay untouched so the normal evidence validator rejects them.
    """
    secondary = annotation.get("secondary_categories")
    primary = annotation.get("primary_category")
    if isinstance(secondary, list):
        deduplicated: list[object] = []
        for item in secondary:
            if item == primary or item in deduplicated:
                continue
            deduplicated.append(item)
        annotation["secondary_categories"] = deduplicated
    evidence = annotation.get("supporting_evidence")
    if not isinstance(evidence, list):
        return
    normalized_source = " ".join(source_text.split()).casefold()
    folded_source, source_positions = _alphanumeric_fold(source_text)
    canonical: list[object] = []
    for excerpt in evidence:
        if not isinstance(excerpt, str):
            canonical.append(excerpt)
            continue
        direct_match = " ".join(excerpt.split()).casefold() in normalized_source
        folded_excerpt, _ = _alphanumeric_fold(excerpt)
        if len(folded_excerpt) < 16:
            canonical.append(excerpt[:max_length])
            continue
        start = folded_source.find(folded_excerpt)
        if start < 0 or folded_source.find(folded_excerpt, start + 1) >= 0:
            canonical.append(excerpt[:max_length] if direct_match else excerpt)
            continue
        end = start + len(folded_excerpt) - 1
        source_excerpt = source_text[
            source_positions[start]:source_positions[end] + 1
        ]
        canonical.append(source_excerpt[:max_length])
    annotation["supporting_evidence"] = canonical


def _alphanumeric_fold(value: str) -> tuple[str, list[int]]:
    characters: list[str] = []
    positions: list[int] = []
    for index, character in enumerate(value):
        if not character.isalnum():
            continue
        for folded in character.casefold():
            characters.append(folded)
            positions.append(index)
    return "".join(characters), positions


def _enum(property_name: str) -> frozenset[str]:
    return frozenset(news_annotation_schema()["properties"][property_name]["enum"])


NEWS_CATEGORIES = _enum("primary_category")
RECORD_KINDS = _enum("record_kind")
DOCUMENT_KINDS = _enum("document_kind")
EVIDENCE_ROLES = _enum("evidence_role")

ACTIONABLE_CATEGORIES = NEWS_CATEGORIES - {"regulation_other"}
ACTIONABLE_RECORD_KINDS = frozenset({"FACT_EVENT", "OFFICIAL_CLAIM"})

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


def effective_record_kind(annotation: dict, headline: str = "") -> str:
    """Return Gemini's controlled classification without a keyword override.

    ``headline`` remains temporarily accepted for callers created before v15,
    but it never changes the semantic result.  Contract validation, not a
    second hidden classifier, rejects malformed target annotations.
    """
    del headline
    declared = str(annotation.get("record_kind") or "").upper()
    if declared not in RECORD_KINDS:
        return "BACKGROUND"
    return declared


def validate_news_annotation(
    annotation: dict[str, Any],
    *,
    prompt_version: str = CURRENT_NEWS_PROMPT_VERSION,
    source_text: str | None = None,
) -> None:
    """Validate the complete current annotation against the packaged schema."""
    schema = news_annotation_schema(prompt_version)
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
            if len(value) < int(rule.get("minItems", 0)):
                raise ValueError(f"annotation {name} has too few items")
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
    if prompt_version in SEMANTIC_NEWS_PROMPT_VERSIONS:
        if source_text is None:
            raise ValueError("semantic annotation validation requires source text")
        normalized_source = " ".join(source_text.split()).casefold()
        for excerpt in annotation["supporting_evidence"]:
            if " ".join(excerpt.split()).casefold() not in normalized_source:
                raise ValueError("annotation supporting evidence is absent from source")


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
