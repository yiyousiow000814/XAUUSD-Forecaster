"""Single source of truth for controlled news annotation semantics."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path


_SCHEMA_PATH = Path(__file__).resolve().parents[1] / "schemas" / "news_annotation.schema.json"


@lru_cache(maxsize=1)
def _schema() -> dict:
    return json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))


def _enum(property_name: str) -> frozenset[str]:
    return frozenset(_schema()["properties"][property_name]["enum"])


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


def effective_record_kind(annotation: dict) -> str:
    """Return only a schema-controlled model classification.

    Headline substring rules are deliberately excluded. The immutable Gemini
    annotation is the classification source; invalid values fail closed.
    """
    declared = str(annotation.get("record_kind") or "").upper()
    return declared if declared in RECORD_KINDS else "BACKGROUND"


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
