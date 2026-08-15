"""Canonical runtime registry for AI quota surfaces."""

from __future__ import annotations

from dataclasses import dataclass

from .annotation import (
    DEFAULT_GEMINI_MODEL,
    DEFAULT_GEMMA_MODEL,
    FALLBACK_GEMINI_MODEL,
    GEMMA_REQUESTS_PER_DAY_PER_KEY,
    GEMMA_SAFE_INPUT_TOKENS_PER_MINUTE_TOTAL,
    GEMMA_SAFE_REQUESTS_PER_MINUTE_TOTAL,
)
from .gemini_quota import GEMINI_REQUESTS_PER_DAY_PER_KEY
from .model_limits import (
    GEMINI_REQUESTS_PER_MINUTE_PER_KEY,
    GEMINI_SAFE_INPUT_TOKENS_PER_MINUTE_TOTAL,
)


@dataclass(frozen=True)
class AiQuotaSurface:
    payload_key: str
    model_families: tuple[str, ...]
    daily_limit: int
    requests_per_minute: int
    input_tokens_per_minute: int
    share_minute_across_accounts: bool = False


AI_QUOTA_SURFACES = (
    AiQuotaSurface(
        "gemini_quota", (DEFAULT_GEMINI_MODEL,),
        GEMINI_REQUESTS_PER_DAY_PER_KEY,
        GEMINI_REQUESTS_PER_MINUTE_PER_KEY,
        GEMINI_SAFE_INPUT_TOKENS_PER_MINUTE_TOTAL,
    ),
    AiQuotaSurface(
        "gemini_31_quota", (FALLBACK_GEMINI_MODEL,),
        GEMINI_REQUESTS_PER_DAY_PER_KEY,
        GEMINI_REQUESTS_PER_MINUTE_PER_KEY,
        GEMINI_SAFE_INPUT_TOKENS_PER_MINUTE_TOTAL,
    ),
    AiQuotaSurface(
        "gemma_quota", (DEFAULT_GEMMA_MODEL, "gemma-impact", "gemma-title"),
        GEMMA_REQUESTS_PER_DAY_PER_KEY,
        GEMMA_SAFE_REQUESTS_PER_MINUTE_TOTAL,
        GEMMA_SAFE_INPUT_TOKENS_PER_MINUTE_TOTAL,
    ),
)

AI_QUOTA_SURFACE_BY_KEY = {surface.payload_key: surface for surface in AI_QUOTA_SURFACES}
if len(AI_QUOTA_SURFACE_BY_KEY) != len(AI_QUOTA_SURFACES):
    raise RuntimeError("AI quota registry contains duplicate payload keys")


def quota_surface_for_model(model: str) -> AiQuotaSurface:
    """Resolve quota policy from the model actually sent to the provider."""
    matches = [surface for surface in AI_QUOTA_SURFACES if model in surface.model_families]
    if len(matches) != 1:
        raise ValueError(f"model has no unique quota policy: {model}")
    return matches[0]
