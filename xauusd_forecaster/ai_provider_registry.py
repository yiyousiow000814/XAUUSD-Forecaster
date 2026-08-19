"""Canonical runtime registry for authorized AI provider surfaces."""

from __future__ import annotations

from dataclasses import dataclass

from .gemini_quota import GEMINI_REQUESTS_PER_DAY_PER_KEY
from .model_limits import (
    GEMMA_SAFE_INPUT_TOKENS_PER_MINUTE_PER_ACCOUNT,
    GEMMA_SAFE_REQUESTS_PER_MINUTE_PER_ACCOUNT,
    GEMINI_REQUESTS_PER_MINUTE_PER_KEY,
    GEMINI_SAFE_INPUT_TOKENS_PER_MINUTE_TOTAL,
)

DEFAULT_GEMINI_MODEL = "gemini-3.5-flash-lite"
FALLBACK_GEMINI_MODEL = "gemini-3.1-flash-lite"
DEFAULT_GEMMA_MODEL = "gemma-4-31b-it"
GEMINI_EMBEDDING_MODEL = "gemini-embedding-2"
GEMMA_REQUESTS_PER_DAY_PER_KEY = 15_000
GEMMA_SAFE_REQUESTS_PER_MINUTE_TOTAL = (
    GEMMA_SAFE_REQUESTS_PER_MINUTE_PER_ACCOUNT
)
GEMMA_SAFE_INPUT_TOKENS_PER_MINUTE_TOTAL = (
    GEMMA_SAFE_INPUT_TOKENS_PER_MINUTE_PER_ACCOUNT
)
GEMINI_EMBEDDING_REQUESTS_PER_DAY_PER_ACCOUNT = 1_000
GEMINI_EMBEDDING_REQUESTS_PER_MINUTE_PER_ACCOUNT = 100
GEMINI_EMBEDDING_INPUT_TOKENS_PER_MINUTE_PER_ACCOUNT = 30_000

_GOOGLE_GENERATION_ENDPOINTS = {
    DEFAULT_GEMINI_MODEL: (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        "gemini-3.5-flash-lite:generateContent"
    ),
    FALLBACK_GEMINI_MODEL: (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        "gemini-3.1-flash-lite:generateContent"
    ),
    DEFAULT_GEMMA_MODEL: (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        "gemma-4-31b-it:generateContent"
    ),
}
_GOOGLE_EMBEDDING_ENDPOINTS = {
    GEMINI_EMBEDDING_MODEL: (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        "gemini-embedding-2:batchEmbedContents"
    ),
}


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
    AiQuotaSurface(
        "gemini_embedding_quota", (GEMINI_EMBEDDING_MODEL,),
        GEMINI_EMBEDDING_REQUESTS_PER_DAY_PER_ACCOUNT,
        GEMINI_EMBEDDING_REQUESTS_PER_MINUTE_PER_ACCOUNT,
        GEMINI_EMBEDDING_INPUT_TOKENS_PER_MINUTE_PER_ACCOUNT,
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


def google_generation_endpoint_for_model(model: str) -> str:
    """Return a fixed authorized Google generation endpoint or fail closed."""
    try:
        return _GOOGLE_GENERATION_ENDPOINTS[model]
    except KeyError:
        raise ValueError("model is not authorized for Google generation") from None


def google_embedding_endpoint_for_model(model: str) -> str:
    """Return a fixed authorized Google embedding endpoint or fail closed."""
    try:
        return _GOOGLE_EMBEDDING_ENDPOINTS[model]
    except KeyError:
        raise ValueError("model is not authorized for Google embedding") from None
