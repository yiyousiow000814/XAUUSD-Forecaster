"""Compatibility shim for xauusd_forecaster.assistant.memory_index."""

from xauusd_forecaster.assistant.memory_index import (
    ASSISTANT_MEMORY_EMBEDDING_TEXT_VERSION,
    ASSISTANT_MEMORY_INDEX_VERSION,
    ASSISTANT_MEMORY_MAX_CLAIMS_PER_SYNC,
    ASSISTANT_MEMORY_MAX_INDEX_TERMS,
    ASSISTANT_MEMORY_MAX_QUERY_TERMS,
    build_assistant_memory_index_result,
    build_assistant_query_embedding,
    tokenize_assistant_memory,
)

__all__ = [
    "ASSISTANT_MEMORY_EMBEDDING_TEXT_VERSION",
    "ASSISTANT_MEMORY_INDEX_VERSION",
    "ASSISTANT_MEMORY_MAX_CLAIMS_PER_SYNC",
    "ASSISTANT_MEMORY_MAX_INDEX_TERMS",
    "ASSISTANT_MEMORY_MAX_QUERY_TERMS",
    "build_assistant_memory_index_result",
    "build_assistant_query_embedding",
    "tokenize_assistant_memory",
]
