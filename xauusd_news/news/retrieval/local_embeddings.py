"""Strict local embedding boundary shared by retrieval systems."""

from __future__ import annotations

from dataclasses import dataclass
import json
import urllib.request

import numpy as np


LOCAL_EMBEDDING_MODEL = "qwen3-embedding:0.6b"
LOCAL_EMBEDDING_MODEL_DIGEST = (
    "ac6da0dfba84a81fdbfbaf330198c33cd77c4cdfc53e8bc50eb581914a15621d"
)
LOCAL_EMBEDDING_DIMENSIONS = 1024
_OLLAMA_ENDPOINT = "http://127.0.0.1:11434"


@dataclass(frozen=True)
class EmbeddingProfile:
    model_name: str
    model_digest: str
    dimensions: int = LOCAL_EMBEDDING_DIMENSIONS


class OllamaEmbeddingClient:
    """Small strict client for Ollama's local embedding endpoint."""

    def __init__(
        self,
        *,
        model: str = LOCAL_EMBEDDING_MODEL,
        endpoint: str = _OLLAMA_ENDPOINT,
        timeout: float = 120.0,
    ) -> None:
        self.model = model
        self.endpoint = endpoint.rstrip("/")
        self.timeout = timeout

    def _request(self, path: str, payload: dict | None = None) -> dict:
        request = urllib.request.Request(
            f"{self.endpoint}{path}",
            data=(
                json.dumps(payload, separators=(",", ":")).encode("utf-8")
                if payload is not None else None
            ),
            headers={"Content-Type": "application/json"},
            method="POST" if payload is not None else "GET",
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            result = json.loads(response.read())
        if not isinstance(result, dict):
            raise ValueError("Ollama embedding response is invalid")
        return result

    def profile(self) -> EmbeddingProfile:
        tags = self._request("/api/tags").get("models")
        if not isinstance(tags, list):
            raise ValueError("Ollama model inventory is unavailable")
        requested = self.model.lower()
        match = next(
            (
                model for model in tags
                if str(model.get("name") or "").lower() == requested
                or str(model.get("model") or "").lower() == requested
            ),
            None,
        )
        if match is None:
            raise ValueError(f"required embedding model is missing: {self.model}")
        digest = str(match.get("digest") or "").strip()
        if len(digest) != 64:
            raise ValueError("Ollama embedding model digest is invalid")
        if digest != LOCAL_EMBEDDING_MODEL_DIGEST:
            raise ValueError("Ollama embedding model digest does not match contract")
        return EmbeddingProfile(self.model, digest)

    def embed(self, texts: list[str], profile: EmbeddingProfile) -> np.ndarray:
        if not texts:
            return np.empty((0, profile.dimensions), dtype=np.float32)
        response = self._request(
            "/api/embed",
            {"model": profile.model_name, "input": texts, "truncate": False},
        )
        vectors = np.asarray(response.get("embeddings"), dtype=np.float32)
        if vectors.shape != (len(texts), profile.dimensions):
            raise ValueError("Ollama embedding dimensions do not match contract")
        norms = np.linalg.norm(vectors, axis=1, keepdims=True)
        if np.any(~np.isfinite(vectors)) or np.any(norms <= 0):
            raise ValueError("Ollama returned an invalid embedding")
        return vectors / norms
