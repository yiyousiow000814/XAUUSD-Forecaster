"""Canonical runtime registry for AI quota surfaces."""

from __future__ import annotations

from dataclasses import dataclass

from .annotation import (
    DEFAULT_GEMINI_MODEL,
    DEFAULT_GEMMA_MODEL,
    FALLBACK_GEMINI_MODEL,
    GEMMA_REQUESTS_PER_DAY_PER_KEY,
)
from .gemini_quota import GEMINI_REQUESTS_PER_DAY_PER_KEY


@dataclass(frozen=True)
class AiQuotaSurface:
    payload_key: str
    model_families: tuple[str, ...]
    daily_limit: int


AI_QUOTA_SURFACES = (
    AiQuotaSurface(
        "gemini_quota", (DEFAULT_GEMINI_MODEL,),
        GEMINI_REQUESTS_PER_DAY_PER_KEY,
    ),
    AiQuotaSurface(
        "gemini_31_quota", (FALLBACK_GEMINI_MODEL,),
        GEMINI_REQUESTS_PER_DAY_PER_KEY,
    ),
    AiQuotaSurface(
        "gemma_quota", (DEFAULT_GEMMA_MODEL, "gemma-impact", "gemma-title"),
        GEMMA_REQUESTS_PER_DAY_PER_KEY,
    ),
)

AI_QUOTA_SURFACE_BY_KEY = {surface.payload_key: surface for surface in AI_QUOTA_SURFACES}
if len(AI_QUOTA_SURFACE_BY_KEY) != len(AI_QUOTA_SURFACES):
    raise RuntimeError("AI quota registry contains duplicate payload keys")
