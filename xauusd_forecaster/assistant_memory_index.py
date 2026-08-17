"""Deterministic lexical and local-vector indexing for Assistant messages."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from typing import Any

from .local_embeddings import (
    LOCAL_EMBEDDING_DIMENSIONS,
    LOCAL_EMBEDDING_MODEL,
    OllamaEmbeddingClient,
)


ASSISTANT_MEMORY_INDEX_VERSION = "assistant-memory-hybrid-v2"
ASSISTANT_MEMORY_EMBEDDING_TEXT_VERSION = "assistant-message-embedding-v1"
ASSISTANT_MEMORY_MAX_INDEX_TERMS = 64
ASSISTANT_MEMORY_MAX_QUERY_TERMS = 16
ASSISTANT_MEMORY_MAX_CLAIMS_PER_SYNC = 20

_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9:._@/-]{0,255}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _is_han(character: str) -> bool:
    codepoint = ord(character)
    return (
        0x3400 <= codepoint <= 0x4DBF
        or 0x4E00 <= codepoint <= 0x9FFF
        or 0xF900 <= codepoint <= 0xFAFF
    )


def tokenize_assistant_memory(
    value: str,
    *,
    maximum_terms: int = ASSISTANT_MEMORY_MAX_INDEX_TERMS,
) -> tuple[str, ...]:
    """Return ordered unique ASCII tokens and adjacent Han bigrams."""
    if not isinstance(value, str):
        raise ValueError("Assistant memory source text must be a string")
    if (
        not isinstance(maximum_terms, int)
        or isinstance(maximum_terms, bool)
        or not 1 <= maximum_terms <= ASSISTANT_MEMORY_MAX_INDEX_TERMS
    ):
        raise ValueError("Assistant memory term bound is invalid")
    normalized = unicodedata.normalize("NFKC", value)
    terms: list[str] = []
    seen: set[str] = set()

    def add(term: str) -> None:
        if term and len(term) <= 64 and term not in seen and len(terms) < maximum_terms:
            seen.add(term)
            terms.append(term)

    index = 0
    while index < len(normalized) and len(terms) < maximum_terms:
        character = normalized[index]
        if character.isascii() and character.isalnum():
            end = index + 1
            while (
                end < len(normalized)
                and normalized[end].isascii()
                and normalized[end].isalnum()
            ):
                end += 1
            add(normalized[index:end].lower())
            index = end
            continue
        if _is_han(character):
            end = index + 1
            while end < len(normalized) and _is_han(normalized[end]):
                end += 1
            run = normalized[index:end]
            if len(run) == 1:
                add(run)
            else:
                for offset in range(len(run) - 1):
                    add(run[offset:offset + 2])
                    if len(terms) >= maximum_terms:
                        break
            index = end
            continue
        index += 1
    return tuple(terms)


def _embedding_payload(content: str, client: OllamaEmbeddingClient) -> dict[str, Any]:
    profile = client.profile()
    if profile.model_name != LOCAL_EMBEDDING_MODEL:
        raise ValueError("Assistant memory embedding model is invalid")
    vector = client.embed([content], profile)[0]
    return {
        "embedding_text_version": ASSISTANT_MEMORY_EMBEDDING_TEXT_VERSION,
        "embedding_model": profile.model_name,
        "embedding_model_digest": profile.model_digest,
        "embedding_dimensions": profile.dimensions,
        "embedding": [float(value) for value in vector],
    }


def build_assistant_query_embedding(
    content: str,
    embedding_client: OllamaEmbeddingClient | None = None,
) -> dict[str, Any]:
    if not isinstance(content, str) or not content.strip():
        raise ValueError("Assistant memory query text is invalid")
    return {
        **_embedding_payload(content, embedding_client or OllamaEmbeddingClient()),
        "query_content_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
    }


def build_assistant_memory_index_result(
    item: object,
    embedding_client: OllamaEmbeddingClient | None = None,
) -> dict[str, Any]:
    if not isinstance(item, dict):
        raise ValueError("Assistant memory index claim is invalid")
    identifier = str(item.get("id") or "")
    lease_token = str(item.get("lease_token") or "")
    source_message_id = str(item.get("source_message_id") or "")
    index_version = str(item.get("index_version") or "")
    content = item.get("content")
    if (
        not _IDENTIFIER.fullmatch(identifier)
        or not _IDENTIFIER.fullmatch(lease_token)
        or not _IDENTIFIER.fullmatch(source_message_id)
        or index_version != ASSISTANT_MEMORY_INDEX_VERSION
        or not isinstance(content, str)
    ):
        raise ValueError("Assistant memory index claim identity is invalid")
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    if not _SHA256.fullmatch(digest):
        raise AssertionError("Assistant memory source digest is invalid")
    return {
        "action": "COMPLETE_MEMORY_INDEX",
        "id": identifier,
        "lease_token": lease_token,
        "source_message_id": source_message_id,
        "index_version": index_version,
        "source_content_sha256": digest,
        "terms": list(tokenize_assistant_memory(content)),
        **_embedding_payload(content, embedding_client or OllamaEmbeddingClient()),
    }
