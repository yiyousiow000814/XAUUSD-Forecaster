"""Compatibility shim for xauusd_forecaster.news.retrieval.gemini_embeddings."""

from xauusd_forecaster.news.retrieval.gemini_embeddings import (
    GEMINI_EMBEDDING_DIMENSIONS,
    GEMINI_EMBEDDING_FAILURE_CODES,
    GEMINI_EMBEDDING_PROFILE_DIGEST,
    GeminiEmbeddingCapacityDeferred,
    GeminiEmbeddingClient,
    GeminiEmbeddingDispatchDeferred,
    GeminiEmbeddingFailure,
)

__all__ = [
    "GEMINI_EMBEDDING_DIMENSIONS",
    "GEMINI_EMBEDDING_FAILURE_CODES",
    "GEMINI_EMBEDDING_PROFILE_DIGEST",
    "GeminiEmbeddingCapacityDeferred",
    "GeminiEmbeddingClient",
    "GeminiEmbeddingDispatchDeferred",
    "GeminiEmbeddingFailure",
]
