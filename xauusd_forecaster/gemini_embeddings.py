"""Quota-accounted Gemini Embedding 2 client for news retrieval."""

from __future__ import annotations

from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
import hashlib
import json
import math
import sqlite3
import urllib.error
import uuid

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
    mark_account_request_attempted,
    record_account_request_outcome,
    record_account_vectors_committed,
    reserve_account_request,
)


GEMINI_EMBEDDING_DIMENSIONS = 768
GEMINI_EMBEDDING_PROFILE_DIGEST = hashlib.sha256(
    b"gemini-embedding-2|768|retrieval-document-query-asymmetric-v1"
).hexdigest()
_MAX_BATCH_REQUESTS = 50
_MAX_ESTIMATED_INPUT_TOKENS = 27_000


class GeminiEmbeddingFailure(RuntimeError):
    """Safe, structured failure at the embedding prerequisite boundary."""

    def __init__(
        self,
        message: str,
        *,
        failure_code: str,
        provider_http_status: int | None = None,
        retry_after_seconds: int | None = None,
        diagnostic: dict[str, object] | None = None,
    ) -> None:
        super().__init__(message)
        self.failure_code = failure_code
        self.provider_http_status = provider_http_status
        self.retry_after_seconds = retry_after_seconds
        self.diagnostic = diagnostic or {}
        self.next_retry_at: str | None = None


class GeminiEmbeddingCapacityDeferred(GeminiEmbeddingFailure):
    """No independent account currently has safe embedding capacity."""


def _bounded_text(value: object, limit: int = 120) -> str | None:
    text = " ".join(str(value or "").split())
    return text[:limit] if text else None


def _retry_after_seconds(value: object, *, requested_at: datetime) -> int | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        seconds = math.ceil(float(raw))
    except ValueError:
        try:
            target = parsedate_to_datetime(raw)
            if target.tzinfo is None:
                target = target.replace(tzinfo=UTC)
            seconds = math.ceil((target - requested_at).total_seconds())
        except (TypeError, ValueError, OverflowError):
            return None
    return min(86_400, max(1, seconds))


def _safe_quota_fields(payload: object) -> dict[str, object]:
    """Extract only bounded provider quota metadata, never the response body."""
    result: dict[str, object] = {}
    stack = [payload]
    while stack:
        item = stack.pop()
        if isinstance(item, list):
            stack.extend(item[:20])
            continue
        if not isinstance(item, dict):
            continue
        stack.extend(list(item.values())[:30])
        for key, value in item.items():
            normalized = str(key).replace("_", "").lower()
            target = {
                "status": "quota_reason",
                "reason": "quota_reason",
                "quotametric": "quota_metric",
                "quotaid": "quota_limit_name",
                "quotalimit": "quota_limit_name",
            }.get(normalized)
            if target and target not in result and not isinstance(value, (dict, list)):
                bounded = _bounded_text(value)
                if bounded:
                    result[target] = bounded
            if normalized == "quotadimensions" and isinstance(value, dict):
                safe_dimensions = {
                    str(name)[:40]: _bounded_text(dimension, 80)
                    for name, dimension in value.items()
                    if any(token in str(name).lower() for token in (
                        "project", "account", "consumer", "model", "location",
                    ))
                    and _bounded_text(dimension, 80)
                }
                if safe_dimensions and "quota_identifier" not in result:
                    result["quota_identifier"] = safe_dimensions
    return result


def _http_failure(
    error: urllib.error.HTTPError,
    *,
    requested_at: datetime,
    batch_item_count: int,
    estimated_input_tokens: int,
) -> GeminiEmbeddingFailure:
    retry_after = _retry_after_seconds(
        error.headers.get("Retry-After") if error.headers else None,
        requested_at=requested_at,
    )
    safe_fields: dict[str, object] = {}
    try:
        body = error.read(16_385)
        if len(body) <= 16_384:
            safe_fields = _safe_quota_fields(json.loads(body.decode("utf-8")))
    except (AttributeError, UnicodeDecodeError, json.JSONDecodeError, OSError):
        pass
    diagnostic = {
        "request_timestamp": requested_at.isoformat(timespec="microseconds"),
        "batch_item_count": batch_item_count,
        "estimated_input_tokens": estimated_input_tokens,
        **safe_fields,
    }
    if retry_after is not None:
        diagnostic["retry_after_seconds"] = retry_after
    throttled = int(error.code) == 429
    return GeminiEmbeddingFailure(
        "Gemini Embedding 2 provider throttled"
        if throttled else "Gemini Embedding 2 provider HTTP failure",
        failure_code=(
            "NEWS_EMBEDDING_PROVIDER_THROTTLED"
            if throttled else "NEWS_EMBEDDING_PROVIDER_TRANSPORT_FAILED"
        ),
        provider_http_status=int(error.code),
        retry_after_seconds=retry_after,
        diagnostic=diagnostic,
    )


class GeminiEmbeddingClient:
    """Use independent-account quota admission before every provider batch."""

    def __init__(self, connection: sqlite3.Connection, *, timeout: float = 120.0) -> None:
        self.connection = connection
        self.timeout = timeout
        self._last_successful_usage_ids: tuple[str, ...] = ()

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
        self._last_successful_usage_ids = ()
        if not texts:
            return np.empty((0, GEMINI_EMBEDDING_DIMENSIONS), dtype=np.float32)
        formatted = [self._formatted(text, task_type) for text in texts]
        results: list[np.ndarray] = []
        successful_usage_ids: list[str] = []
        for batch in self._batches(formatted):
            estimated_input_tokens = sum(
                self._estimated_tokens(text) for text in batch
            )
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
                usage_id = str(uuid.uuid4())
                admitted = reserve_account_request(
                    self.connection,
                    account_id=credential.account_id,
                    model_family=GEMINI_EMBEDDING_MODEL,
                    daily_limit=GEMINI_EMBEDDING_REQUESTS_PER_DAY_PER_ACCOUNT,
                    requests_per_minute=GEMINI_EMBEDDING_REQUESTS_PER_MINUTE_PER_ACCOUNT,
                    request_count=len(batch),
                    input_tokens=estimated_input_tokens,
                    input_tokens_per_minute=(
                        GEMINI_EMBEDDING_INPUT_TOKENS_PER_MINUTE_PER_ACCOUNT
                    ),
                    usage_id=usage_id,
                )
                if not admitted:
                    continue
                requested_at = datetime.now(UTC)
                mark_account_request_attempted(
                    self.connection, usage_id, now=requested_at,
                )
                try:
                    result = self._request(
                        credential.api_key, batch, task_type,
                    )
                    record_account_request_outcome(
                        self.connection, usage_id,
                        outcome="PROVIDER_SUCCEEDED",
                    )
                    results.append(result)
                    successful_usage_ids.append(usage_id)
                    break
                except urllib.error.HTTPError as error:
                    record_account_request_outcome(
                        self.connection, usage_id,
                        outcome=(
                            "PROVIDER_THROTTLED"
                            if int(error.code) == 429 else "PROVIDER_FAILED"
                        ),
                        provider_http_status=int(error.code),
                    )
                    last_error = _http_failure(
                        error,
                        requested_at=requested_at,
                        batch_item_count=len(batch),
                        estimated_input_tokens=estimated_input_tokens,
                    )
                    # Configured account labels do not prove distinct Google
                    # quota domains. A 429 therefore cools the shared
                    # generation instead of immediately sending the same burst
                    # through every locally named account.
                    if int(error.code) == 429:
                        raise last_error
                except urllib.error.URLError as error:
                    record_account_request_outcome(
                        self.connection, usage_id, outcome="PROVIDER_FAILED",
                    )
                    last_error = GeminiEmbeddingFailure(
                        "Gemini Embedding 2 transport failed",
                        failure_code="NEWS_EMBEDDING_PROVIDER_TRANSPORT_FAILED",
                        diagnostic={
                            "request_timestamp": requested_at.isoformat(
                                timespec="microseconds"
                            ),
                            "batch_item_count": len(batch),
                            "estimated_input_tokens": estimated_input_tokens,
                            "transport_error_type": type(error).__name__,
                        },
                    )
                except ValueError as error:
                    record_account_request_outcome(
                        self.connection, usage_id, outcome="PROVIDER_FAILED",
                    )
                    last_error = GeminiEmbeddingFailure(
                        "Gemini Embedding 2 response was invalid",
                        failure_code="NEWS_EMBEDDING_PROVIDER_RESPONSE_INVALID",
                        diagnostic={
                            "request_timestamp": requested_at.isoformat(
                                timespec="microseconds"
                            ),
                            "batch_item_count": len(batch),
                            "estimated_input_tokens": estimated_input_tokens,
                            "response_error": _bounded_text(error, 160),
                        },
                    )
            else:
                if last_error is not None:
                    raise last_error
                raise GeminiEmbeddingCapacityDeferred(
                    "Gemini Embedding 2 capacity is temporarily unavailable",
                    failure_code="NEWS_EMBEDDING_CAPACITY_DEFERRED",
                    diagnostic={
                        "request_timestamp": datetime.now(UTC).isoformat(
                            timespec="microseconds"
                        ),
                        "batch_item_count": len(batch),
                        "estimated_input_tokens": estimated_input_tokens,
                    },
                )
        self._last_successful_usage_ids = tuple(successful_usage_ids)
        return np.concatenate(results, axis=0)

    def mark_last_vectors_committed(self) -> None:
        record_account_vectors_committed(
            self.connection, self._last_successful_usage_ids,
        )
        self._last_successful_usage_ids = ()

    def embed(self, texts: list[str], profile: EmbeddingProfile) -> np.ndarray:
        if profile != self.profile():
            raise ValueError("Gemini embedding profile does not match contract")
        return self._embed(texts, "RETRIEVAL_DOCUMENT")

    def embed_queries(self, texts: list[str], profile: EmbeddingProfile) -> np.ndarray:
        if profile != self.profile():
            raise ValueError("Gemini embedding profile does not match contract")
        return self._embed(texts, "RETRIEVAL_QUERY")
