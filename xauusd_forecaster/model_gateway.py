"""Single metered network boundary for Google generative model requests."""

from __future__ import annotations

import hashlib
import json
import threading
import urllib.error
import urllib.parse
import urllib.request
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Callable, TypeVar


T = TypeVar("T")


class ModelGatewayCapacityExhausted(RuntimeError):
    """No metered request slot is available for this gateway batch."""


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


class ModelRequestAccountant(ABC):
    """Durable accounting boundary required before provider transport."""

    @abstractmethod
    def reserve(self, usage: ModelRequestUsage) -> bool:
        """Persist one attempted request when quota is available."""


class GeminiModelGateway:
    """Count, reserve, and send every Gemini or Gemma generation request."""

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

    def count_input_tokens(
        self, model: str, generate_content_request: dict[str, object],
    ) -> int:
        """Use the provider tokenizer without consuming a generation slot."""
        last_error: Exception | None = None
        for api_key in self.api_keys:
            try:
                envelope = self._post_json(
                    api_key,
                    model,
                    "countTokens",
                    {
                        "generateContentRequest": {
                            "model": f"models/{model}",
                            **generate_content_request,
                        },
                    },
                    timeout=30.0,
                )
                tokens = int(envelope["totalTokens"])
                if tokens <= 0:
                    raise ValueError("Gemini token count is not positive")
                return tokens
            except Exception as error:
                last_error = error
        raise RuntimeError("All configured keys failed provider token counting") from last_error

    def generate(
        self,
        start_index: int,
        *,
        model: str,
        purpose: str,
        payload: dict[str, object],
        input_tokens: int,
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
            )
            if not self._reserve(api_key, usage):
                continue
            try:
                envelope = self._post_json(
                    api_key, model, "generateContent", payload, timeout=120.0,
                )
                result = decode(envelope)
                return result, str(envelope.get("modelVersion") or model)
            except retryable_decode_errors as error:
                if getattr(error, "failure_evidence", None) is None:
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
                last_error = error
                if error.code not in retryable_http_codes:
                    raise
        if last_error is None:
            raise ModelGatewayCapacityExhausted(
                "Model request slots used; retained for the next batch"
            )
        if isinstance(last_error, urllib.error.HTTPError):
            raise last_error
        if isinstance(last_error, retryable_decode_errors):
            raise ModelGatewayResponseInvalid(last_error) from last_error
        raise RuntimeError("Metered model request failed") from last_error

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
        provider_methods = {
            "countTokens": "countTokens",
            "generateContent": "generateContent",
        }
        provider_method = provider_methods.get(method)
        if provider_method is None:
            raise ValueError(f"unsupported model provider method: {method}")
        encoded_model = urllib.parse.quote(model, safe="")
        request = urllib.request.Request(
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"{encoded_model}:{provider_method}",
            data=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
            headers={"Content-Type": "application/json", "x-goog-api-key": api_key},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=timeout) as response:
            envelope = json.loads(response.read())
        if not isinstance(envelope, dict):
            raise ValueError("model provider response is not a JSON object")
        return envelope
