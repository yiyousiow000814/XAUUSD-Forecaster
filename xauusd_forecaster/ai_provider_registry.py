"""Compatibility shim for xauusd_forecaster.ai.provider_registry."""

from xauusd_forecaster.ai.provider_registry import (
    AI_QUOTA_SURFACES,
    AI_QUOTA_SURFACE_BY_KEY,
    AiQuotaSurface,
    DEFAULT_GEMINI_MODEL,
    DEFAULT_GEMMA_MODEL,
    FALLBACK_GEMINI_MODEL,
    GEMINI_EMBEDDING_INPUT_TOKENS_PER_MINUTE_PER_ACCOUNT,
    GEMINI_EMBEDDING_MODEL,
    GEMINI_EMBEDDING_REQUESTS_PER_DAY_PER_ACCOUNT,
    GEMINI_EMBEDDING_REQUESTS_PER_MINUTE_PER_ACCOUNT,
    GEMMA_REQUESTS_PER_DAY_PER_KEY,
    GEMMA_SAFE_INPUT_TOKENS_PER_MINUTE_TOTAL,
    GEMMA_SAFE_REQUESTS_PER_MINUTE_TOTAL,
    google_embedding_endpoint_for_model,
    google_generation_endpoint_for_model,
    quota_surface_for_model,
)

__all__ = [
    "AI_QUOTA_SURFACES",
    "AI_QUOTA_SURFACE_BY_KEY",
    "AiQuotaSurface",
    "DEFAULT_GEMINI_MODEL",
    "DEFAULT_GEMMA_MODEL",
    "FALLBACK_GEMINI_MODEL",
    "GEMINI_EMBEDDING_INPUT_TOKENS_PER_MINUTE_PER_ACCOUNT",
    "GEMINI_EMBEDDING_MODEL",
    "GEMINI_EMBEDDING_REQUESTS_PER_DAY_PER_ACCOUNT",
    "GEMINI_EMBEDDING_REQUESTS_PER_MINUTE_PER_ACCOUNT",
    "GEMMA_REQUESTS_PER_DAY_PER_KEY",
    "GEMMA_SAFE_INPUT_TOKENS_PER_MINUTE_TOTAL",
    "GEMMA_SAFE_REQUESTS_PER_MINUTE_TOTAL",
    "google_embedding_endpoint_for_model",
    "google_generation_endpoint_for_model",
    "quota_surface_for_model",
]
