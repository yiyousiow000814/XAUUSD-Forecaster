"""Quota-accounted Gemini Embedding 2 client for news retrieval."""

from __future__ import annotations

import hashlib
import sqlite3
import urllib.error

import numpy as np

from .ai_provider_registry import (
    GEMINI_EMBEDDING_INPUT_TOKENS_PER_MINUTE_PER_ACCOUNT,
    GEMINI_EMBEDDING_MODEL,
    GEMINI_EMBEDDING_REQUESTS_PER_DAY_PER_ACCOUNT,
    GEMINI_EMBEDDING_REQUESTS_PER_MINUTE_PER_ACCOUNT,
)
from .local_embeddings import EmbeddingProfile
from .model_gateway import post_gemini_batch_embeddings
from .news_scheduler import (
    configured_api_credentials,
    credentials_for_background_task,
    reserve_account_request,
)


GEMINI_EMBEDDING_DIMENSIONS = 768
GEMINI_EMBEDDING_PROFILE_DIGEST = hashlib.sha256(
    b"gemini-embedding-2|768|retrieval-document-query-asymmetric-v1"
).hexdigest()
_MAX_BATCH_REQUESTS = 50
_MAX_ESTIMATED_INPUT_TOKENS = 27_000


class GeminiEmbeddingCapacityDeferred(RuntimeError):
    """No independent account currently has safe embedding capacity."""


class GeminiEmbeddingClient:
    """Use independent-account quota admission before every provider batch."""

    def __init__(self, connection: sqlite3.Connection, *, timeout: float = 120.0) -> None:
        self.connection = connection
        self.timeout = timeout

    def profile(self) -> EmbeddingProfile:
        return EmbeddingProfile(
            GEMINI_EMBEDDING_MODEL,
            GEMINI_EMBEDDING_PROFILE_DIGEST,
            GEMINI_EMBEDDING_DIMENSIONS,
        )

    @staticmethod
    def _formatted(text: str, task_type: str) -> str:
        prefix = "title: news identity | text: " if task_type == "RETRIEVAL_DOCUMENT" \
            else "task: find the same news event | query: "
        return prefix + text

    @staticmethod
    def _estimated_tokens(text: str) -> int:
        # UTF-8 bytes are a deliberately conservative admission estimate for
        # mixed Chinese/English news. Provider usage can only be lower.
        return len(text.encode("utf-8"))

    def _batches(self, texts: list[str]) -> list[list[str]]:
        batches: list[list[str]] = []
        current: list[str] = []
        tokens = 0
        for text in texts:
            estimate = self._estimated_tokens(text)
            if estimate > _MAX_ESTIMATED_INPUT_TOKENS:
                raise ValueError("one embedding input exceeds the safe TPM envelope")
            if current and (
                len(current) >= _MAX_BATCH_REQUESTS
                or tokens + estimate > _MAX_ESTIMATED_INPUT_TOKENS
            ):
                batches.append(current)
                current = []
                tokens = 0
            current.append(text)
            tokens += estimate
        if current:
            batches.append(current)
        return batches

    def _request(self, api_key: str, texts: list[str], task_type: str) -> np.ndarray:
        model_path = f"models/{GEMINI_EMBEDDING_MODEL}"
        payload = {"requests": [{
            "model": model_path,
            "content": {"parts": [{"text": text}]},
            "taskType": task_type,
            "outputDimensionality": GEMINI_EMBEDDING_DIMENSIONS,
        } for text in texts]}
        result = post_gemini_batch_embeddings(
            api_key, GEMINI_EMBEDDING_MODEL, payload, timeout=self.timeout,
        )
        vectors = np.asarray(
            [item.get("values") for item in result.get("embeddings", [])],
            dtype=np.float32,
        )
        if vectors.shape != (len(texts), GEMINI_EMBEDDING_DIMENSIONS):
            raise ValueError("Gemini embedding dimensions do not match contract")
        norms = np.linalg.norm(vectors, axis=1, keepdims=True)
        if np.any(~np.isfinite(vectors)) or np.any(norms <= 0):
            raise ValueError("Gemini returned an invalid embedding")
        return vectors / norms

    def _embed(self, texts: list[str], task_type: str) -> np.ndarray:
        if not texts:
            return np.empty((0, GEMINI_EMBEDDING_DIMENSIONS), dtype=np.float32)
        formatted = [self._formatted(text, task_type) for text in texts]
        results: list[np.ndarray] = []
        for batch in self._batches(formatted):
            credentials = credentials_for_background_task(
                self.connection, configured_api_credentials(),
                task_type="NEWS_EMBEDDING",
            )
            # Multiple keys on one account are transport redundancy, not more
            # provider quota. Try at most one credential per account per batch.
            independent = []
            seen_accounts: set[str] = set()
            for credential in credentials:
                if credential.account_id not in seen_accounts:
                    independent.append(credential)
                    seen_accounts.add(credential.account_id)
            last_error: Exception | None = None
            for credential in independent:
                admitted = reserve_account_request(
                    self.connection,
                    account_id=credential.account_id,
                    model_family=GEMINI_EMBEDDING_MODEL,
                    daily_limit=GEMINI_EMBEDDING_REQUESTS_PER_DAY_PER_ACCOUNT,
                    requests_per_minute=GEMINI_EMBEDDING_REQUESTS_PER_MINUTE_PER_ACCOUNT,
                    request_count=len(batch),
                    input_tokens=sum(self._estimated_tokens(text) for text in batch),
                    input_tokens_per_minute=(
                        GEMINI_EMBEDDING_INPUT_TOKENS_PER_MINUTE_PER_ACCOUNT
                    ),
                )
                if not admitted:
                    continue
                try:
                    results.append(self._request(
                        credential.api_key, batch, task_type,
                    ))
                    break
                except (urllib.error.URLError, ValueError) as error:
                    last_error = error
            else:
                if last_error is not None:
                    raise last_error
                raise GeminiEmbeddingCapacityDeferred(
                    "Gemini Embedding 2 capacity is temporarily unavailable"
                )
        return np.concatenate(results, axis=0)

    def embed(self, texts: list[str], profile: EmbeddingProfile) -> np.ndarray:
        if profile != self.profile():
            raise ValueError("Gemini embedding profile does not match contract")
        return self._embed(texts, "RETRIEVAL_DOCUMENT")

    def embed_queries(self, texts: list[str], profile: EmbeddingProfile) -> np.ndarray:
        if profile != self.profile():
            raise ValueError("Gemini embedding profile does not match contract")
        return self._embed(texts, "RETRIEVAL_QUERY")
