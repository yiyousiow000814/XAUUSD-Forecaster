from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from xauusd_forecaster.assistant_memory_index import (
    ASSISTANT_MEMORY_INDEX_VERSION,
    build_assistant_memory_index_result,
    tokenize_assistant_memory,
)
from xauusd_forecaster.local_embeddings import (
    LOCAL_EMBEDDING_MODEL_DIGEST,
    EmbeddingProfile,
)


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "assistant_memory_tokenizer.json"


class FakeEmbeddingClient:
    def profile(self) -> EmbeddingProfile:
        return EmbeddingProfile("qwen3-embedding:0.6b", LOCAL_EMBEDDING_MODEL_DIGEST)

    def embed(self, texts, profile):
        import numpy as np
        result = np.zeros((len(texts), profile.dimensions), dtype=np.float32)
        result[:, 0] = 1.0
        return result


def test_memory_tokenizer_matches_shared_cross_runtime_fixture() -> None:
    fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    for case in fixture:
        assert list(tokenize_assistant_memory(
            case["text"], maximum_terms=case["maximum_terms"],
        )) == case["terms"]


def test_memory_index_result_is_content_derived_and_bounded() -> None:
    content = "美联储利率影响黄金 XAUUSD"
    result = build_assistant_memory_index_result({
        "id": "memory-index:message-1",
        "lease_token": "lease-memory-1",
        "source_message_id": "message-1",
        "index_version": ASSISTANT_MEMORY_INDEX_VERSION,
        "content": content,
    }, FakeEmbeddingClient())

    assert result == {
        "action": "COMPLETE_MEMORY_INDEX",
        "id": "memory-index:message-1",
        "lease_token": "lease-memory-1",
        "source_message_id": "message-1",
        "index_version": ASSISTANT_MEMORY_INDEX_VERSION,
        "source_content_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
        "terms": [
            "美联", "联储", "储利", "利率", "率影", "影响", "响黄", "黄金",
            "xauusd",
        ],
        "embedding_text_version": "assistant-message-embedding-v1",
        "embedding_model": "qwen3-embedding:0.6b",
        "embedding_model_digest": LOCAL_EMBEDDING_MODEL_DIGEST,
        "embedding_dimensions": 1024,
        "embedding": [1.0, *([0.0] * 1023)],
    }


@pytest.mark.parametrize("field,value", [
    ("index_version", "assistant-memory-lexical-v0"),
    ("content", None),
    ("lease_token", "bad lease"),
])
def test_memory_index_result_rejects_invalid_claims(field: str, value: object) -> None:
    claim = {
        "id": "memory-index:message-1",
        "lease_token": "lease-memory-1",
        "source_message_id": "message-1",
        "index_version": ASSISTANT_MEMORY_INDEX_VERSION,
        "content": "黄金",
    }
    claim[field] = value
    with pytest.raises(ValueError):
        build_assistant_memory_index_result(claim, FakeEmbeddingClient())
