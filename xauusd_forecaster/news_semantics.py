"""Single source of truth for controlled news annotation semantics."""

from __future__ import annotations

import json
import math
import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any


LEGACY_NEWS_PROMPT_VERSION = "news-json-v14-material-event-evidence"
LEGACY_SEMANTIC_NEWS_PROMPT_VERSION = "news-json-v15-ai-semantic-review"
PREVIOUS_NEWS_PROMPT_VERSION = "news-json-v16-xauusd-transmission-evidence"
CURRENT_NEWS_PROMPT_VERSION = "news-json-v17-structured-named-references"
NAMED_REFERENCE_MAX_CHARACTERS = 512
NAMED_REFERENCE_MAX_ITEMS = 24
NAMED_REFERENCE_CONNECTORS = frozenset({
    "a", "an", "and", "as", "at", "by", "for", "from", "in", "of",
    "on", "or", "the", "to", "vs", "with",
})
NAMED_REFERENCE_CLAUSE_WORDS = frozenset({
    "am", "are", "be", "been", "being", "can", "could", "did", "do",
    "does", "expect", "expected", "expects", "fell", "had", "has", "have",
    "increase", "increased", "increases", "is", "may", "might", "must",
    "reported", "reports", "rise", "rises", "rose", "said", "says", "shall",
    "should", "was", "were", "will", "worried", "would",
})
NAMED_REFERENCE_DELIMITERS = (
    ("《", "》"), ("“", "”"), ("‘", "’"), ("「", "」"), ("『", "』"),
    ('"', '"'),
)
_NAMED_REFERENCE_TOKEN_PATTERN = re.compile(
    r"[A-Za-z0-9]+(?:[.'&+/-][A-Za-z0-9]+)*"
)
_NAMING_CONTEXT_PATTERN = re.compile(
    r"\b(?:called|entitled|known\s+as|named|refers?\s+to|referred\s+to\s+as|"
    r"titled)\b",
    re.IGNORECASE,
)
LEGACY_INVALID_SEMANTIC_REASON_PREFIX = "语言或结构一致性检查未通过"
DISPLAY_AUDIT_FALLBACK_REASON_PREFIX = "语义已完成，但中文展示未通过校验"
V1_NEWS_PROMPT_VERSIONS = (
    LEGACY_NEWS_PROMPT_VERSION,
    "news-json-v13-event-claims",
    "news-json-v12-gemini-story-identity",
    "news-json-v11-gemini-story-subjects",
    "news-json-v10-controlled-category-zh",
)
GENERATED_NEWS_PROMPT_VERSIONS = frozenset({CURRENT_NEWS_PROMPT_VERSION})
SEMANTIC_NEWS_PROMPT_VERSIONS = frozenset({
    LEGACY_SEMANTIC_NEWS_PROMPT_VERSION,
    PREVIOUS_NEWS_PROMPT_VERSION,
    CURRENT_NEWS_PROMPT_VERSION,
})
SUPPORTED_NEWS_PROMPT_VERSIONS = frozenset({
    *V1_NEWS_PROMPT_VERSIONS,
    LEGACY_SEMANTIC_NEWS_PROMPT_VERSION,
    PREVIOUS_NEWS_PROMPT_VERSION,
    CURRENT_NEWS_PROMPT_VERSION,
})

_SCHEMA_PATH = Path(__file__).with_name("news_annotation.schema.json")


@dataclass(frozen=True)
class StructuredNamedReferenceProof:
    exact_text: str
    source_start: int
    source_end: int
    evidence: tuple[str, ...]


def model_usable_annotation_predicate(alias: str) -> str:
    """SQL contract for annotations permitted to drive downstream behavior."""
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", alias):
        raise ValueError("invalid SQL alias")
    return (
        f"COALESCE(json_extract({alias}.annotation_json, "
        "'$.semantic_reason_zh'), '') NOT LIKE "
        f"'{LEGACY_INVALID_SEMANTIC_REASON_PREFIX}%' AND "
        f"COALESCE(json_extract({alias}.annotation_json, "
        "'$.semantic_reason_zh'), '') NOT LIKE "
        f"'{DISPLAY_AUDIT_FALLBACK_REASON_PREFIX}%'"
    )


def display_repair_checkpoint_predicate(alias: str) -> str:
    """SQL contract for saved semantics whose Chinese display still needs repair."""
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", alias):
        raise ValueError("invalid SQL alias")
    return (
        f"COALESCE(json_extract({alias}.annotation_json, "
        "'$.semantic_reason_zh'), '') LIKE "
        f"'{DISPLAY_AUDIT_FALLBACK_REASON_PREFIX}%'"
    )


def validated_annotation_predicate(alias: str) -> str:
    """Compatibility name for the model-usable annotation contract."""
    return model_usable_annotation_predicate(alias)


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


@lru_cache(maxsize=4)
def news_annotation_schema(
    prompt_version: str = CURRENT_NEWS_PROMPT_VERSION,
) -> dict:
    schema = json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))
    if prompt_version in SEMANTIC_NEWS_PROMPT_VERSIONS:
        schema["$id"] = {
            LEGACY_SEMANTIC_NEWS_PROMPT_VERSION:
                "xauusd.forward.news-annotation.v15",
            PREVIOUS_NEWS_PROMPT_VERSION:
                "xauusd.forward.news-annotation.v16",
            CURRENT_NEWS_PROMPT_VERSION:
                "xauusd.forward.news-annotation.v17",
        }[prompt_version]
        schema["required"].extend(AI_SEMANTIC_FIELDS)
        schema["properties"].update(json.loads(json.dumps(AI_SEMANTIC_FIELDS)))
        if prompt_version in {
            PREVIOUS_NEWS_PROMPT_VERSION, CURRENT_NEWS_PROMPT_VERSION,
        }:
            evidence = schema["properties"]["supporting_evidence"]
            evidence["description"] = (
                "One to three contiguous source-language substrings copied "
                "verbatim from the supplied headline or full content; never "
                "translate, paraphrase, join clauses, or add ellipses"
            )
        if prompt_version == CURRENT_NEWS_PROMPT_VERSION:
            schema["required"].append("named_references")
            schema["properties"]["named_references"] = {
                "type": "array",
                "maxItems": NAMED_REFERENCE_MAX_ITEMS,
                "uniqueItems": True,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["exact_text"],
                    "properties": {
                        "exact_text": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": NAMED_REFERENCE_MAX_CHARACTERS,
                            "description": (
                                "Exact contiguous named-reference text copied "
                                "from the supplied immutable source"
                            ),
                        },
                    },
                },
                "description": (
                    "Category-neutral named references copied exactly from the "
                    "source; local code derives and verifies all coordinates"
                ),
            }
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


def canonical_annotation_source_text(headline: str, body: str) -> str:
    """Return the only coordinate space used for Annotation source spans."""
    return f"{headline}\n{body}"


def _is_han(character: str) -> bool:
    codepoint = ord(character)
    return (
        0x3400 <= codepoint <= 0x4DBF
        or 0x4E00 <= codepoint <= 0x9FFF
        or 0xF900 <= codepoint <= 0xFAFF
        or 0x20000 <= codepoint <= 0x2FA1F
    )


def _is_latin_letter(character: str) -> bool:
    return character.isalpha() and "LATIN" in unicodedata.name(character, "")


def _normalized_reference_tokens(value: str) -> tuple[str, ...]:
    return tuple(
        normalized for match in re.finditer(
            r"[A-Za-z0-9]+(?:[._'/-][A-Za-z0-9]+)*", value,
        )
        if (normalized := re.sub(
            r"[^a-z0-9]", "", match.group(0).casefold(),
        ))
    )


def _strong_identifier_shape(value: str) -> bool:
    for token in _NAMED_REFERENCE_TOKEN_PATTERN.findall(value):
        letters = "".join(
            character for character in token if _is_latin_letter(character)
        )
        if (
            any(character.isdigit() for character in token)
            or any(character in ".&+/-" for character in token)
            or (letters and letters.isupper())
            or any(character.isupper() for character in letters[1:])
        ):
            return True
    return False


def _structured_name_shape(value: str) -> bool:
    tokens = _NAMED_REFERENCE_TOKEN_PATTERN.findall(value)
    latin_tokens = [
        token for token in tokens
        if any(_is_latin_letter(character) for character in token)
    ]
    if not latin_tokens:
        return False
    for token in latin_tokens:
        word = re.sub(r"[^a-z]", "", token.casefold())
        if word in NAMED_REFERENCE_CONNECTORS:
            continue
        letters = "".join(
            character for character in token if _is_latin_letter(character)
        )
        if not letters or not (
            letters[0].isupper() or _strong_identifier_shape(token)
        ):
            return False
    return True


def _reference_has_clause_syntax(value: str) -> bool:
    if "\n" in value or "\r" in value or re.search(r"[;!?。！？]", value):
        return True
    if re.search(r"(?<![A-Z])\.\s+\S", value):
        return True
    words = [
        re.sub(r"[^a-z]", "", token.casefold())
        for token in _NAMED_REFERENCE_TOKEN_PATTERN.findall(value)
    ]
    return any(word in NAMED_REFERENCE_CLAUSE_WORDS for word in words)


def source_span_within_reference_delimiters(
    value: str, start: int, end: int, *,
    max_span_characters: int = NAMED_REFERENCE_MAX_CHARACTERS,
) -> bool:
    for opening, closing in NAMED_REFERENCE_DELIMITERS:
        if opening == closing:
            if value[:start].count(opening) % 2 == 0:
                continue
            right = value.find(
                closing, end, min(len(value), end + max_span_characters + 8),
            )
            if right >= 0:
                return True
            continue
        left = value.rfind(opening, max(0, start - max_span_characters - 8), start)
        prior_close = value.rfind(
            closing, max(0, start - max_span_characters - 8), start,
        )
        if left < 0 or left < prior_close:
            continue
        search_end = min(len(value), end + max_span_characters + 8)
        right = value.find(closing, end, search_end)
        next_open = value.find(opening, end, search_end)
        if (
            right >= 0
            and (next_open < 0 or right < next_open)
            and right - left <= max_span_characters + 8
        ):
            return True
    return False


def _source_span_has_naming_context(source: str, start: int, end: int) -> bool:
    prefix = source[max(0, start - 96):start]
    for cue in _NAMING_CONTEXT_PATTERN.finditer(prefix):
        if re.fullmatch(r"[\s:,-]*[\"“'‘]?", prefix[cue.end():]):
            return True
    suffix = source[end:min(len(source), end + 96)]
    return bool(re.match(
        r"^[\s\"”'’]*(?:is|was)?\s*(?:the\s+)?(?:name|title)\b",
        suffix,
        re.IGNORECASE,
    ))


def _exact_reference_matches(
    source: str, exact_text: str,
) -> tuple[tuple[int, int], ...]:
    found = []
    cursor = 0
    while (start := source.find(exact_text, cursor)) >= 0:
        end = start + len(exact_text)
        left_ok = start == 0 or not (
            source[start - 1].isdigit() or _is_latin_letter(source[start - 1])
        )
        right_ok = end == len(source) or not (
            source[end].isdigit() or _is_latin_letter(source[end])
        )
        if left_ok and right_ok:
            found.append((start, end))
        cursor = start + max(1, len(exact_text))
    return tuple(found)


def resolve_structured_named_reference(
    exact_text: str, source_text: str, declared_identities: tuple[str, ...],
) -> StructuredNamedReferenceProof | None:
    """Derive one auditable source span for a locally proven declaration."""
    if (
        exact_text != exact_text.strip()
        or not exact_text
        or len(exact_text) > NAMED_REFERENCE_MAX_CHARACTERS
        or not any(_is_latin_letter(character) for character in exact_text)
        or any(_is_han(character) for character in exact_text)
        or any(
            character.isalpha()
            and not _is_han(character)
            and not _is_latin_letter(character)
            for character in exact_text
        )
    ):
        return None
    source_matches = _exact_reference_matches(source_text, exact_text)
    if not source_matches:
        return None
    exact_tokens = _normalized_reference_tokens(exact_text)
    declared_tokens = {
        _normalized_reference_tokens(identity)
        for identity in declared_identities
        if identity.strip() and identity.strip().casefold() != "n/a"
    }
    evidence: list[str] = []
    if exact_tokens and exact_tokens in declared_tokens:
        evidence.append("DECLARED_SEMANTIC_IDENTITY")
    name_shape = _structured_name_shape(exact_text)
    reference_tokens = _NAMED_REFERENCE_TOKEN_PATTERN.findall(exact_text)
    if _strong_identifier_shape(exact_text) and (
        len(reference_tokens) == 1 or name_shape
    ):
        evidence.append("STRONG_IDENTIFIER")
    if name_shape:
        evidence.append("PROPER_NAME_SHAPE")
    delimited_matches = tuple(
        match for match in source_matches
        if source_span_within_reference_delimiters(
            source_text, match[0], match[1],
        )
    )
    if delimited_matches:
        evidence.append("SOURCE_DELIMITED_REFERENCE")
    context_matches = tuple(
        match for match in source_matches
        if _source_span_has_naming_context(source_text, match[0], match[1])
    )
    if context_matches:
        evidence.append("SOURCE_REFERENCE_CONTEXT")
    if len(source_matches) > 1:
        evidence.append("REPEATED_SOURCE_REFERENCE")

    if _reference_has_clause_syntax(exact_text):
        shared = [match for match in delimited_matches if match in context_matches]
        if not shared:
            return None
        chosen = shared[0]
    else:
        locally_referential = {
            "DECLARED_SEMANTIC_IDENTITY", "STRONG_IDENTIFIER",
            "PROPER_NAME_SHAPE", "SOURCE_REFERENCE_CONTEXT",
        }
        if locally_referential.isdisjoint(evidence):
            return None
        chosen = (
            context_matches[0] if context_matches
            else delimited_matches[0] if delimited_matches
            else source_matches[0]
        )
    return StructuredNamedReferenceProof(
        exact_text=exact_text,
        source_start=chosen[0],
        source_end=chosen[1],
        evidence=tuple(evidence),
    )


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
                if item_rule.get("type") == "object":
                    if not isinstance(item, dict):
                        raise ValueError(f"annotation {name} contains a non-object")
                    item_properties = item_rule.get("properties", {})
                    item_required = set(item_rule.get("required", ()))
                    missing_item = item_required - set(item)
                    if missing_item:
                        raise ValueError(
                            f"annotation {name} item is missing fields: "
                            + ", ".join(sorted(missing_item))
                        )
                    if item_rule.get("additionalProperties") is False:
                        additional_item = set(item) - set(item_properties)
                        if additional_item:
                            raise ValueError(
                                f"annotation {name} item has unknown fields: "
                                + ", ".join(sorted(additional_item))
                            )
                    for item_name, item_value in item.items():
                        child_rule = item_properties[item_name]
                        if child_rule.get("type") == "string":
                            if not isinstance(item_value, str):
                                raise ValueError(
                                    f"annotation {name}.{item_name} is not a string"
                                )
                            if len(item_value) < int(child_rule.get("minLength", 0)):
                                raise ValueError(
                                    f"annotation {name}.{item_name} is too short"
                                )
                            if len(item_value) > int(
                                child_rule.get("maxLength", len(item_value))
                            ):
                                raise ValueError(
                                    f"annotation {name}.{item_name} is too long"
                                )
                    continue
                if len(str(item)) < int(item_rule.get("minLength", 0)):
                    raise ValueError(f"annotation {name} contains a short item")
                if len(str(item)) > int(item_rule.get("maxLength", len(str(item)))):
                    raise ValueError(f"annotation {name} contains a long item")
                if "enum" in item_rule and item not in item_rule["enum"]:
                    raise ValueError(f"annotation {name} contains an uncontrolled item")
            if rule.get("uniqueItems"):
                fingerprints = [
                    json.dumps(item, ensure_ascii=False, sort_keys=True)
                    if isinstance(item, (dict, list)) else repr(item)
                    for item in value
                ]
                if len(fingerprints) != len(set(fingerprints)):
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
        if prompt_version == CURRENT_NEWS_PROMPT_VERSION:
            declared_identities = (
                str(annotation.get("actor") or ""),
                str(annotation.get("object") or ""),
                *tuple(str(item) for item in annotation.get("entities") or ()),
            )
            for reference in annotation["named_references"]:
                if reference["exact_text"] not in source_text:
                    raise ValueError(
                        "annotation named reference is absent from source"
                    )
                if resolve_structured_named_reference(
                    reference["exact_text"], source_text, declared_identities,
                ) is None:
                    raise ValueError(
                        "UNPROVEN_NAMED_REFERENCE: annotation named reference "
                        "lacks deterministic local proof"
                    )


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
