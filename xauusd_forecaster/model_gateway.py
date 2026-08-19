"""Single metered network boundary for Google generative model requests."""

from __future__ import annotations

import hashlib
import json
import threading
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Callable, TypeVar

from .ai_provider_registry import (
    google_embedding_endpoint_for_model,
    google_generation_endpoint_for_model,
)

T = TypeVar("T")
LOCAL_TOKEN_ESTIMATOR_VERSION = "multilingual-conservative-v1"


def _sanitized_usage_metadata(envelope: dict[str, object]) -> dict[str, int] | None:
    """Keep only bounded provider token counts, never request or response text."""
    raw = envelope.get("usageMetadata")
    if not isinstance(raw, dict):
        return None
    names = {
        "prompt_token_count": "promptTokenCount",
        "candidates_token_count": "candidatesTokenCount",
        "total_token_count": "totalTokenCount",
    }
    result: dict[str, int] = {}
    for target, source in names.items():
        value = raw.get(source)
        if isinstance(value, int) and 0 <= value <= 100_000_000:
            result[target] = value
    return result or None


def _sanitized_model_version(envelope: dict[str, object]) -> str | None:
    value = envelope.get("modelVersion")
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized[:200] if normalized else None


def _http_retry_after_seconds(error: Exception) -> int | None:
    """Parse a bounded Retry-After value without trusting provider input."""
    if not isinstance(error, urllib.error.HTTPError):
        return None
    value = error.headers.get("Retry-After") if error.headers else None
    if not value:
        return None
    try:
        return max(1, min(86_400, int(value)))
    except ValueError:
        try:
            retry_at = parsedate_to_datetime(value)
            if retry_at.tzinfo is None:
                retry_at = retry_at.replace(tzinfo=UTC)
            seconds = int((retry_at - datetime.now(UTC)).total_seconds())
            return max(1, min(86_400, seconds))
        except (TypeError, ValueError, OverflowError):
            return None


def post_gemini_batch_embeddings(
    api_key: str,
    model: str,
    payload: dict[str, object],
    *,
    timeout: float,
) -> dict[str, object]:
    """Send an already-accounted embedding batch through the Google boundary."""
    request = urllib.request.Request(
        google_embedding_endpoint_for_model(model),
        data=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
        headers={"Content-Type": "application/json", "x-goog-api-key": api_key},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        envelope = json.loads(response.read())
    if not isinstance(envelope, dict):
        raise ValueError("embedding provider response is not a JSON object")
    return envelope


class ModelGatewayCapacityExhausted(RuntimeError):
    """No metered request slot is available for this gateway batch."""

    def __init__(
        self,
        message: str,
        *,
        failure_code: str = "MODEL_CAPACITY_DEFERRED",
        next_retry_at: str | None = None,
        failure_evidence: dict[str, object] | None = None,
    ) -> None:
        self.failure_code = failure_code
        self.next_retry_at = next_retry_at
        self.failure_evidence = failure_evidence
        super().__init__(message)


class ModelGatewayRequestFailed(RuntimeError):
    """Provider transport ended without a trustworthy model response."""

    failure_code = "MODEL_REQUEST_FAILED"

    def __init__(self, error: Exception) -> None:
        self.transport_error_type = type(error).__name__
        self.provider_http_status = None
        super().__init__(
            f"Model provider request failed: {self.transport_error_type}"
        )


class ModelGatewayResponseInvalid(RuntimeError):
    """A metered provider response could not satisfy the requested contract."""

    def __init__(self, error: Exception) -> None:
        self.cause_type = type(error).__name__
        self.cause_message = str(error)
        self.failure_evidence = getattr(error, "failure_evidence", None)
        super().__init__(
            f"Model response failed validation: {self.cause_type}: "
            f"{self.cause_message}"
        )


@dataclass(frozen=True)
class ModelRequestUsage:
    model: str
    purpose: str
    input_tokens: int
    prompt_contract: str = "unspecified"
    estimator_version: str = LOCAL_TOKEN_ESTIMATOR_VERSION


class ModelRequestAccountant(ABC):
    """Durable accounting boundary required before provider transport."""

    @abstractmethod
    def reserve(self, usage: ModelRequestUsage) -> bool:
        """Persist one attempted request when quota is available."""

    def effective_base_input_token_budget(
        self,
        usage: ModelRequestUsage,
        *,
        input_tokens_per_minute: int,
    ) -> int:
        """Return the largest base estimate that can fit one TPM admission."""
        del usage
        return max(0, int(input_tokens_per_minute))

    def mark_provider_attempted(self) -> None:
        """Mark the reserved request immediately before provider transport."""

    def record_provider_outcome(
        self, outcome: str, *, retry_after_seconds: int | None = None,
        usage_metadata: dict[str, int] | None = None,
        provider_model_version: str | None = None,
    ) -> None:
        """Update optional adaptive provider pacing after transport."""
        del outcome, retry_after_seconds, usage_metadata, provider_model_version

    @property
    def next_retry_at(self) -> str | None:
        return None

    @property
    def failure_code(self) -> str | None:
        return None

    @property
    def failure_evidence(self) -> dict[str, object] | None:
        return None

class GeminiModelGateway:
    """Reserve and send every Gemini or Gemma generation request."""

    def __init__(
        self,
        api_keys: tuple[str, ...],
        *,
        requests_per_key: int,
        accountant: ModelRequestAccountant,
        batch_limit: int | None = None,
    ) -> None:
        if not api_keys:
            raise ValueError("model gateway requires at least one API key")
        if not isinstance(accountant, ModelRequestAccountant):
            raise ValueError("model gateway requires metered request accounting")
        self.api_keys = api_keys
        self.requests_per_key = max(1, int(requests_per_key))
        self.batch_limit = batch_limit
        self.accountant = accountant
        self._batch_counts = {key: 0 for key in api_keys}
        self._batch_total = 0
        self._lock = threading.Lock()

    def available_batch_capacity(self) -> int:
        capacity = sum(
            max(0, self.requests_per_key - count)
            for count in self._batch_counts.values()
        )
        if self.batch_limit is not None:
            capacity = min(capacity, max(0, self.batch_limit - self._batch_total))
        return capacity

    def generate(
        self,
        start_index: int,
        *,
        model: str,
        purpose: str,
        payload: dict[str, object],
        input_tokens: int,
        prompt_contract: str | None = None,
        estimator_version: str = LOCAL_TOKEN_ESTIMATOR_VERSION,
        decode: Callable[[dict[str, object]], T],
        retryable_http_codes: frozenset[int],
        retryable_decode_errors: tuple[type[Exception], ...] = (),
    ) -> tuple[T, str]:
        """Reserve before each attempt, then decode a provider response."""
        if not purpose.strip():
            raise ValueError("model request purpose is required")
        last_error: Exception | None = None
        for offset in range(len(self.api_keys)):
            api_key = self.api_keys[(start_index + offset) % len(self.api_keys)]
            usage = ModelRequestUsage(
                model=model,
                purpose=purpose,
                input_tokens=max(0, int(input_tokens)),
                prompt_contract=(prompt_contract or purpose).strip(),
                estimator_version=estimator_version.strip(),
            )
            if not self._reserve(api_key, usage):
                continue
            envelope: dict[str, object] | None = None
            provider_attempted = False
            try:
                self.accountant.mark_provider_attempted()
                provider_attempted = True
                envelope = self._post_json(
                    api_key, model, "generateContent", payload, timeout=120.0,
                )
                if not isinstance(envelope, dict):
                    raise ValueError("provider response is not a JSON object")
                provider_model_version = _sanitized_model_version(envelope)
                result = decode(envelope)
            except retryable_decode_errors as error:
                if provider_attempted:
                    self.accountant.record_provider_outcome("PROVIDER_FAILED")
                if getattr(error, "failure_evidence", None) is None:
                    if envelope is None:
                        raw_output = f"{type(error).__name__}: {error}"
                    else:
                        try:
                            raw_output = str(
                                envelope["candidates"][0]["content"]["parts"][0]["text"]
                            )
                        except (KeyError, IndexError, TypeError):
                            raw_output = json.dumps(
                                envelope, ensure_ascii=False, sort_keys=True,
                                separators=(",", ":"), default=str,
                            )
                    error.failure_evidence = {
                        "failure_code": "MODEL_OUTPUT_INVALID",
                        "failure_stage": "RESPONSE_DECODE",
                        "response_hash": hashlib.sha256(
                            raw_output.encode("utf-8")
                        ).hexdigest(),
                        "selected_output": {
                            "bounded_response_prefix": raw_output[:500],
                        },
                        "cause_type": type(error).__name__,
                        "cause": str(error)[:500],
                    }
                last_error = error
            except urllib.error.HTTPError as error:
                if provider_attempted:
                    self.accountant.record_provider_outcome(
                        (
                            "PROVIDER_THROTTLED"
                            if int(error.code) == 429 else "PROVIDER_FAILED"
                        ),
                        retry_after_seconds=_http_retry_after_seconds(error),
                    )
                last_error = error
                if error.code not in retryable_http_codes:
                    raise
            except (
                urllib.error.URLError, TimeoutError, ConnectionError,
            ) as error:
                if provider_attempted:
                    self.accountant.record_provider_outcome("PROVIDER_FAILED")
                last_error = error
            except Exception:
                if provider_attempted:
                    self.accountant.record_provider_outcome("PROVIDER_FAILED")
                raise
            else:
                self.accountant.record_provider_outcome(
                    "PROVIDER_SUCCEEDED",
                    usage_metadata=_sanitized_usage_metadata(envelope),
                    provider_model_version=provider_model_version,
                )
                return result, provider_model_version or model
        if last_error is None:
            raise ModelGatewayCapacityExhausted(
                "Model request slots used; retained for the next batch",
                failure_code=(self.accountant.failure_code
                              or "MODEL_CAPACITY_DEFERRED"),
                next_retry_at=self.accountant.next_retry_at,
                failure_evidence=self.accountant.failure_evidence,
            )
        if isinstance(last_error, urllib.error.HTTPError):
            raise last_error
        if isinstance(last_error, retryable_decode_errors):
            raise ModelGatewayResponseInvalid(last_error) from last_error
        raise ModelGatewayRequestFailed(last_error) from last_error

    def _reserve(self, api_key: str, usage: ModelRequestUsage) -> bool:
        with self._lock:
            if self.batch_limit is not None and self._batch_total >= self.batch_limit:
                return False
            if self._batch_counts[api_key] >= self.requests_per_key:
                return False
            if not self.accountant.reserve(usage):
                return False
            self._batch_counts[api_key] += 1
            self._batch_total += 1
            return True

    @staticmethod
    def _post_json(
        api_key: str,
        model: str,
        method: str,
        payload: dict[str, object],
        *,
        timeout: float,
    ) -> dict[str, object]:
        if method != "generateContent":
            raise ValueError(f"unsupported model provider method: {method}")
        request = urllib.request.Request(
            google_generation_endpoint_for_model(model),
            data=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
            headers={"Content-Type": "application/json", "x-goog-api-key": api_key},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=timeout) as response:
            envelope = json.loads(response.read())
        if not isinstance(envelope, dict):
            raise ValueError("model provider response is not a JSON object")
        return envelope


class OllamaAssistantGateway:
    """Metered loopback-only OpenAI-compatible transport for Assistant turns."""

    def __init__(
        self,
        *,
        accountant: ModelRequestAccountant,
        endpoint: str = "http://127.0.0.1:11434/v1/chat/completions",
    ) -> None:
        if not isinstance(accountant, ModelRequestAccountant):
            raise ValueError("Ollama gateway requires metered request accounting")
        if endpoint != "http://127.0.0.1:11434/v1/chat/completions":
            raise ValueError("Ollama Assistant endpoint must remain loopback-only")
        self.accountant = accountant
        self.endpoint = endpoint

    def generate(
        self,
        *,
        model: str,
        purpose: str,
        payload: dict[str, object],
        input_tokens: int,
        decode: Callable[[dict[str, object]], T],
    ) -> tuple[T, str]:
        usage = ModelRequestUsage(
            model=model,
            purpose=purpose,
            input_tokens=max(1, int(input_tokens)),
        )
        if not self.accountant.reserve(usage):
            raise ModelGatewayCapacityExhausted(
                "Local Assistant request exceeded its reserved capacity"
            )
        request = urllib.request.Request(
            self.endpoint,
            data=json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode(
                "utf-8"
            ),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=180.0) as response:
            envelope = json.loads(response.read())
        if not isinstance(envelope, dict):
            raise ValueError("Ollama response is not a JSON object")
        return decode(envelope), str(envelope.get("model") or model)

    def generate_structured(
        self,
        *,
        model: str,
        purpose: str,
        payload: dict[str, object],
        input_tokens: int,
        decode: Callable[[dict[str, object]], T],
    ) -> tuple[T, str]:
        """Use Ollama's native schema-constrained chat endpoint."""
        usage = ModelRequestUsage(
            model=model,
            purpose=purpose,
            input_tokens=max(1, int(input_tokens)),
        )
        if not self.accountant.reserve(usage):
            raise ModelGatewayCapacityExhausted(
                "Local Assistant request exceeded its reserved capacity"
            )
        request = urllib.request.Request(
            "http://127.0.0.1:11434/api/chat",
            data=json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode(
                "utf-8"
            ),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=180.0) as response:
            envelope = json.loads(response.read())
        if not isinstance(envelope, dict):
            raise ValueError("Ollama response is not a JSON object")
        return decode(envelope), str(envelope.get("model") or model)
