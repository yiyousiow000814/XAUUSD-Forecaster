"""Windows worker for durable private Assistant chat turns."""

from __future__ import annotations

import copy
import hashlib
import json
import math
import re
import urllib.error
import urllib.parse
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from .assistant_agent import (
    AssistantAgentBudgets,
    AssistantAgentContractError,
    AssistantAgentRequest,
    AssistantAgentResult,
    configured_assistant_agent_budgets,
    run_capacity_routed_assistant_agent,
)
from .assistant_capacity import (
    AssistantCapacityPolicy,
    AssistantCapacityUnavailable,
)
from .assistant_routing import (
    AssistantModelRoutingUnavailable,
    AssistantTaskType,
    ModelProfile,
    classify_assistant_reasoning,
)
from .assistant_tools import (
    AssistantToolActor,
    AssistantToolCapability,
    AssistantToolRegistry,
    build_news_search_tool,
)
from .forward_ledger import ForwardLedger
from .news_scheduler import ApiCredential


ASSISTANT_CHAT_MAX_CLAIMS_PER_SYNC = 3
ASSISTANT_CHAT_REMOTE_TIMEOUT_SECONDS = 30.0
ASSISTANT_CHAT_PROGRESS_BATCH_EVENTS = 16

_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9:._@/-]{0,127}$")
_WORKER_ID = re.compile(r"^[A-Za-z0-9._:-]{3,96}$")
_ERROR_CODE = re.compile(r"^[A-Z][A-Z0-9_]{2,63}$")
_TOOL_NAME = re.compile(r"^[a-z][a-z0-9_]{1,54}_v[1-9][0-9]*$")
_TOOL_VERSION = re.compile(r"^v[1-9][0-9]*$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_CONTEXT_LAYERS = (
    "PINNED_STATE",
    "ROLLING_SUMMARY",
    "HISTORICAL_MEMORY",
    "RECENT_VERBATIM_TURNS",
    "CURRENT_USER_MESSAGE",
    "TOOL_EVIDENCE",
)

AssistantChatGetJson = Callable[[str, float], dict[str, object]]
AssistantChatPostJson = Callable[[str, dict[str, object]], dict[str, object]]


class AssistantChatWorkerError(RuntimeError):
    """A remote or local worker boundary violated the chat contract."""

    def __init__(self, error_code: str, message: str) -> None:
        if not _ERROR_CODE.fullmatch(error_code):
            raise ValueError("Assistant chat worker error code is invalid")
        self.error_code = error_code
        super().__init__(message)


@dataclass(frozen=True)
class AssistantChatTransport:
    get_json: AssistantChatGetJson
    post_json: AssistantChatPostJson

    def __post_init__(self) -> None:
        if not callable(self.get_json) or not callable(self.post_json):
            raise ValueError("Assistant chat transport callbacks are required")


@dataclass(frozen=True)
class AssistantChatSyncResult:
    claimed: int
    answered: int
    deferred: int
    failed_attempts: int


def _canonical_time(value: object, field_name: str) -> str:
    if not isinstance(value, str):
        raise AssistantChatWorkerError(
            "INVALID_CHAT_CLAIM", f"Assistant chat {field_name} is invalid",
        )
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as error:
        raise AssistantChatWorkerError(
            "INVALID_CHAT_CLAIM", f"Assistant chat {field_name} is invalid",
        ) from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise AssistantChatWorkerError(
            "INVALID_CHAT_CLAIM", f"Assistant chat {field_name} lacks timezone",
        )
    return parsed.astimezone(UTC).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z",
    )


def _identifier(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise AssistantChatWorkerError(
            "INVALID_CHAT_CLAIM", f"Assistant chat {field_name} is invalid",
        )
    return value


def _validated_claim(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise AssistantChatWorkerError(
            "INVALID_CHAT_CLAIM", "Assistant chat claim is not an object",
        )
    required = {
        "id", "owner_id", "conversation_id", "user_message_id", "user_text",
        "retrieval_cutoff", "lease_token", "lease_expires_at", "attempt_count",
        "event_sequence",
    }
    if not required.issubset(value):
        raise AssistantChatWorkerError(
            "INVALID_CHAT_CLAIM", "Assistant chat claim is incomplete",
        )
    result = {
        name: _identifier(value[name], name)
        for name in (
            "id", "owner_id", "conversation_id", "user_message_id", "lease_token",
        )
    }
    user_text = value["user_text"]
    if (
        not isinstance(user_text, str)
        or not user_text.strip()
        or len(user_text.encode("utf-8")) > 16_000
    ):
        raise AssistantChatWorkerError(
            "INVALID_CHAT_CLAIM", "Assistant chat user text is invalid",
        )
    attempt_count = value["attempt_count"]
    event_sequence = value["event_sequence"]
    if (
        not isinstance(attempt_count, int)
        or isinstance(attempt_count, bool)
        or not 1 <= attempt_count <= 5
        or not isinstance(event_sequence, int)
        or isinstance(event_sequence, bool)
        or not 1 <= event_sequence <= 256
    ):
        raise AssistantChatWorkerError(
            "INVALID_CHAT_CLAIM", "Assistant chat claim counters are invalid",
        )
    result.update({
        "user_text": user_text.strip(),
        "retrieval_cutoff": _canonical_time(
            value["retrieval_cutoff"], "retrieval_cutoff",
        ),
        "lease_expires_at": _canonical_time(
            value["lease_expires_at"], "lease_expires_at",
        ),
        "attempt_count": attempt_count,
        "event_sequence": event_sequence,
    })
    return result


def _strict_json_copy(value: object, *, maximum_bytes: int) -> object:
    try:
        serialized = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, RecursionError) as error:
        raise AssistantChatWorkerError(
            "INVALID_ACTIVE_CONTEXT", "Assistant active context is not strict JSON",
        ) from error
    if len(serialized) > maximum_bytes:
        raise AssistantChatWorkerError(
            "INVALID_ACTIVE_CONTEXT", "Assistant active context is oversized",
        )
    return json.loads(serialized)


def _validated_context(
    response: object,
    claim: dict[str, object],
) -> dict[str, object]:
    if not isinstance(response, dict) or response.get("status") != "OK":
        raise AssistantChatWorkerError(
            "CONTEXT_BUILD_FAILED", "Assistant Context Builder did not succeed",
        )
    value = _strict_json_copy(response.get("item"), maximum_bytes=500_000)
    if not isinstance(value, dict):
        raise AssistantChatWorkerError(
            "INVALID_ACTIVE_CONTEXT", "Assistant active context is not an object",
        )
    layers = value.get("layers")
    if (
        not isinstance(layers, list)
        or tuple(
            layer.get("type") if isinstance(layer, dict) else None
            for layer in layers
        ) != _CONTEXT_LAYERS
    ):
        raise AssistantChatWorkerError(
            "INVALID_ACTIVE_CONTEXT", "Assistant context layers are invalid",
        )
    current_layer = layers[_CONTEXT_LAYERS.index("CURRENT_USER_MESSAGE")]
    current = current_layer.get("item") if isinstance(current_layer, dict) else None
    if (
        not isinstance(current, dict)
        or current.get("id") != claim["user_message_id"]
        or current.get("role") != "USER"
        or current.get("content") != claim["user_text"]
    ):
        raise AssistantChatWorkerError(
            "ACTIVE_CONTEXT_IDENTITY_MISMATCH",
            "Assistant context does not contain the claimed current message",
        )
    return value


def _checked_machine_response(response: object, action: str) -> object:
    if (
        not isinstance(response, dict)
        or response.get("status") != "OK"
        or response.get("item") is None
    ):
        raise AssistantChatWorkerError(
            "CHAT_MACHINE_WRITE_REJECTED",
            f"Assistant machine action {action} was not accepted",
        )
    return response["item"]


def _progress_key(
    attempt_count: int,
    round_index: int,
    call_index: int,
    kind: str,
    value: object,
) -> str:
    digest = hashlib.sha256(json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")).hexdigest()[:24]
    return (
        f"chat-progress-{attempt_count}-{round_index}-{call_index}-{kind}-{digest}"
    )


def assistant_tool_progress_batches(
    provenance: object,
    *,
    attempt_count: int,
) -> tuple[tuple[dict[str, object], ...], ...]:
    """Project public tool receipts into closed, idempotent progress batches."""
    if not isinstance(provenance, dict):
        raise AssistantChatWorkerError(
            "INVALID_AGENT_PROVENANCE", "Assistant agent provenance is invalid",
        )
    execution = provenance.get("tool_execution")
    if not isinstance(execution, list):
        raise AssistantChatWorkerError(
            "INVALID_AGENT_PROVENANCE", "Assistant tool provenance is invalid",
        )
    pairs: list[tuple[dict[str, object], dict[str, object]]] = []
    for round_index, round_receipts in enumerate(execution):
        if not isinstance(round_receipts, list):
            raise AssistantChatWorkerError(
                "INVALID_AGENT_PROVENANCE", "Assistant tool round is invalid",
            )
        for call_index, receipt in enumerate(round_receipts):
            if not isinstance(receipt, dict):
                raise AssistantChatWorkerError(
                    "INVALID_AGENT_PROVENANCE", "Assistant tool receipt is invalid",
                )
            name = receipt.get("name")
            version = receipt.get("tool_version")
            status = receipt.get("status")
            original_call_id = receipt.get("call_id")
            if version is None and status == "REJECTED":
                # An unknown tool has no authoritative version for tool.started.
                # The model receives the rejection, but presentation does not
                # fabricate a versioned operation.
                continue
            if (
                not isinstance(name, str)
                or not _TOOL_NAME.fullmatch(name)
                or not isinstance(version, str)
                or not _TOOL_VERSION.fullmatch(version)
                or not isinstance(original_call_id, str)
                or not _IDENTIFIER.fullmatch(original_call_id)
            ):
                raise AssistantChatWorkerError(
                    "INVALID_AGENT_PROVENANCE", "Assistant tool identity is invalid",
                )
            public_call_id = "attempt-{}-tool-{}".format(
                attempt_count,
                hashlib.sha256(
                    f"{round_index}:{call_index}:{original_call_id}".encode("utf-8"),
                ).hexdigest()[:20],
            )
            started = {
                "idempotency_key": _progress_key(
                    attempt_count, round_index, call_index, "started", receipt,
                ),
                "type": "tool.started",
                "payload": {
                    "call_id": public_call_id,
                    "tool_name": name,
                    "tool_version": version,
                },
            }
            if status == "SUCCEEDED":
                result_sha256 = receipt.get("result_sha256")
                evidence_ids = receipt.get("evidence_ids")
                if (
                    not isinstance(result_sha256, str)
                    or not _SHA256.fullmatch(result_sha256)
                    or not isinstance(evidence_ids, list)
                    or len(evidence_ids) > 100
                ):
                    raise AssistantChatWorkerError(
                        "INVALID_AGENT_PROVENANCE", "Assistant tool result is invalid",
                    )
                finished = {
                    "idempotency_key": _progress_key(
                        attempt_count, round_index, call_index, "completed", receipt,
                    ),
                    "type": "tool.completed",
                    "payload": {
                        "call_id": public_call_id,
                        "tool_name": name,
                        "status": "SUCCEEDED",
                        "result_sha256": result_sha256,
                        "evidence_count": len(evidence_ids),
                    },
                }
            else:
                error_code = receipt.get("error_code")
                if (
                    status not in {"FAILED", "REJECTED", "TIMED_OUT"}
                    or not isinstance(error_code, str)
                    or not _ERROR_CODE.fullmatch(error_code)
                ):
                    raise AssistantChatWorkerError(
                        "INVALID_AGENT_PROVENANCE", "Assistant tool failure is invalid",
                    )
                finished = {
                    "idempotency_key": _progress_key(
                        attempt_count, round_index, call_index, "failed", receipt,
                    ),
                    "type": "tool.failed",
                    "payload": {
                        "call_id": public_call_id,
                        "tool_name": name,
                        "status": status,
                        "error_code": error_code,
                    },
                }
            pairs.append((started, finished))

    maximum_pairs = ASSISTANT_CHAT_PROGRESS_BATCH_EVENTS // 2
    return tuple(
        tuple(event for pair in pairs[index:index + maximum_pairs] for event in pair)
        for index in range(0, len(pairs), maximum_pairs)
    )


def _failure_code(error: Exception) -> str:
    if isinstance(error, AssistantCapacityUnavailable):
        return "NO_MODEL_CAPACITY"
    if isinstance(error, AssistantModelRoutingUnavailable):
        return "NO_COMPATIBLE_MODEL"
    if isinstance(error, AssistantAgentContractError):
        return error.error_code
    if isinstance(error, AssistantChatWorkerError):
        return error.error_code
    if isinstance(error, (urllib.error.URLError, TimeoutError)):
        return "REMOTE_DEPENDENCY_UNAVAILABLE"
    if isinstance(error, ValueError):
        return "MODEL_OUTPUT_INVALID"
    return "WORKER_FAILURE"


def _machine_post(
    transport: AssistantChatTransport,
    url: str,
    payload: dict[str, object],
) -> object:
    return _checked_machine_response(
        transport.post_json(url, payload), str(payload.get("action") or "UNKNOWN"),
    )


def run_assistant_chat_worker(
    *,
    chat_url: str,
    worker_id: str,
    database: Path,
    credentials: tuple[ApiCredential, ...],
    transport: AssistantChatTransport,
    profiles: tuple[ModelProfile, ...] | None = None,
    policies: tuple[AssistantCapacityPolicy, ...] | None = None,
    budgets: AssistantAgentBudgets | None = None,
    max_claims: int = ASSISTANT_CHAT_MAX_CLAIMS_PER_SYNC,
) -> AssistantChatSyncResult:
    """Claim and process a finite number of owner-scoped chat turns."""
    if not isinstance(chat_url, str) or not chat_url.startswith("https://"):
        raise ValueError("Assistant chat URL must use HTTPS")
    chat_url = chat_url.rstrip("/")
    api_root = chat_url.rsplit("/", 1)[0]
    if not isinstance(worker_id, str) or not _WORKER_ID.fullmatch(worker_id):
        raise ValueError("Assistant chat worker identity is invalid")
    if (
        not isinstance(max_claims, int)
        or isinstance(max_claims, bool)
        or not 1 <= max_claims <= 10
    ):
        raise ValueError("Assistant chat claim bound is invalid")
    if not isinstance(database, Path):
        raise ValueError("Assistant chat database path is invalid")
    selected_budgets = (
        configured_assistant_agent_budgets() if budgets is None else budgets
    )
    machine_url = chat_url + "?mode=machine"
    conversation_url = api_root + "/assistant-conversations?mode=machine"
    news_url = api_root + "/news-search"
    ledger: ForwardLedger | None = None
    claimed = answered = deferred = failed_attempts = 0

    try:
        for _ in range(max_claims):
            claim_response = transport.get_json(
                chat_url + "?" + urllib.parse.urlencode({
                    "mode": "claim", "worker_id": worker_id,
                }),
                ASSISTANT_CHAT_REMOTE_TIMEOUT_SECONDS,
            )
            if not isinstance(claim_response, dict):
                raise AssistantChatWorkerError(
                    "INVALID_CHAT_CLAIM", "Assistant claim response is invalid",
                )
            raw_claim = claim_response.get("item")
            if raw_claim is None:
                break
            claim = _validated_claim(raw_claim)
            claimed += 1
            if ledger is None:
                ledger = ForwardLedger(database)
            try:
                context = _validated_context(transport.post_json(
                    conversation_url,
                    {
                        "action": "BUILD_CONTEXT",
                        "conversation_id": claim["conversation_id"],
                        "current_user_message_id": claim["user_message_id"],
                        "tool_evidence": [],
                    },
                ), claim)

                def retrieve_news(
                    params: dict[str, object], timeout_seconds: float,
                ) -> dict[str, object]:
                    if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
                        raise TimeoutError("Assistant news retrieval deadline expired")
                    url = news_url + "?" + urllib.parse.urlencode(params)
                    return transport.get_json(
                        url,
                        min(ASSISTANT_CHAT_REMOTE_TIMEOUT_SECONDS, timeout_seconds),
                    )

                registry = AssistantToolRegistry((build_news_search_tool(
                    retrieve_news,
                    timeout_seconds=10.0,
                    max_result_tokens=selected_budgets.max_tool_result_tokens,
                ),))
                actor = AssistantToolActor(
                    actor_id=str(claim["owner_id"]),
                    work_id=str(claim["id"]),
                    allowed_capabilities=frozenset({
                        AssistantToolCapability.NEWS_RETRIEVAL,
                    }),
                )
                request = AssistantAgentRequest(
                    conversation_id=str(claim["conversation_id"]),
                    user_message_id=str(claim["user_message_id"]),
                    actor=actor,
                    user_text=str(claim["user_text"]),
                    active_context=context,
                    retrieval_cutoff=str(claim["retrieval_cutoff"]),
                )

                def renew_model_lease() -> None:
                    renewed = _machine_post(transport, machine_url, {
                        "action": "RENEW",
                        "id": claim["id"],
                        "lease_token": claim["lease_token"],
                    })
                    if (
                        not isinstance(renewed, dict)
                        or renewed.get("id") != claim["id"]
                        or renewed.get("lease_token") != claim["lease_token"]
                        or renewed.get("attempt_count") != claim["attempt_count"]
                    ):
                        raise AssistantChatWorkerError(
                            "CHAT_LEASE_RENEWAL_REJECTED",
                            "Assistant lease renewal identity changed",
                        )
                    renewed_lease = _canonical_time(
                        renewed.get("lease_expires_at"), "renewed lease expiry",
                    )
                    turn_expiry = _canonical_time(
                        renewed.get("expires_at"), "turn expiry",
                    )
                    if renewed_lease > turn_expiry:
                        raise AssistantChatWorkerError(
                            "CHAT_LEASE_RENEWAL_REJECTED",
                            "Assistant renewed lease outlives the turn",
                        )

                if claim["event_sequence"] == 1:
                    planned_tool_calls = min(
                        selected_budgets.max_tool_calls_per_user_turn,
                        selected_budgets.max_parallel_tool_calls,
                    ) if registry.authorized_definitions(actor) else 0
                    reasoning = classify_assistant_reasoning(
                        AssistantTaskType.ASSISTANT_CHAT,
                        user_text=request.user_text,
                        planned_tool_calls=planned_tool_calls,
                    )
                    _machine_post(transport, machine_url, {
                        "action": "EVENTS",
                        "id": claim["id"],
                        "lease_token": claim["lease_token"],
                        "events": [{
                            "idempotency_key": (
                                f"chat-reasoning-attempt-{claim['attempt_count']}"
                            ),
                            "type": "reasoning.started",
                            "payload": {"reasoning_class": reasoning.value},
                        }],
                    })

                result = run_capacity_routed_assistant_agent(
                    ledger.connection,
                    request,
                    registry,
                    credentials,
                    budgets=selected_budgets,
                    profiles=profiles,
                    policies=policies,
                    before_model_attempt=renew_model_lease,
                )
                if not isinstance(result, AssistantAgentResult):
                    raise AssistantChatWorkerError(
                        "INVALID_AGENT_RESULT", "Assistant agent returned no result",
                    )
                for batch in assistant_tool_progress_batches(
                    result.provenance,
                    attempt_count=int(claim["attempt_count"]),
                ):
                    _machine_post(transport, machine_url, {
                        "action": "EVENTS",
                        "id": claim["id"],
                        "lease_token": claim["lease_token"],
                        "events": list(batch),
                    })
                _machine_post(transport, machine_url, {
                    "action": "COMPLETE",
                    "id": claim["id"],
                    "lease_token": claim["lease_token"],
                    "answer": result.answer,
                    "model_version": result.model_version,
                    "content_document": copy.deepcopy(result.content_document),
                    "provenance": copy.deepcopy(result.provenance),
                })
                answered += 1
            except Exception as error:
                capacity = isinstance(error, AssistantCapacityUnavailable)
                _machine_post(transport, machine_url, {
                    "action": "DEFER" if capacity else "FAIL",
                    "id": claim["id"],
                    "lease_token": claim["lease_token"],
                    "failure_code": _failure_code(error),
                })
                if capacity:
                    deferred += 1
                else:
                    failed_attempts += 1
    finally:
        if ledger is not None:
            ledger.close()
    return AssistantChatSyncResult(
        claimed=claimed,
        answered=answered,
        deferred=deferred,
        failed_attempts=failed_attempts,
    )
