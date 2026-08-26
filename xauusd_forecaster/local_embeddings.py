"""Compatibility shim for xauusd_forecaster.news.retrieval.local_embeddings."""

from xauusd_forecaster.news.retrieval.local_embeddings import (
    EmbeddingProfile,
    LOCAL_EMBEDDING_DIMENSIONS,
    LOCAL_EMBEDDING_MODEL,
    LOCAL_EMBEDDING_MODEL_DIGEST,
    OllamaEmbeddingClient,
)

__all__ = [
    "EmbeddingProfile",
    "LOCAL_EMBEDDING_DIMENSIONS",
    "LOCAL_EMBEDDING_MODEL",
    "LOCAL_EMBEDDING_MODEL_DIGEST",
    "OllamaEmbeddingClient",
]
