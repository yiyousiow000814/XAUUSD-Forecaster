"""Bounded native function-calling loop for the private Assistant."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from .annotation import generate_metered_response
from .assistant_capacity import (
    AssistantCapacityPolicy,
    AssistantServicePriority,
    execute_assistant_capacity_route,
)
from .assistant_content import build_assistant_content_document
from .assistant_routing import (
    AssistantTaskType,
    ModelProfile,
    apply_provider_thinking_level,
    configured_assistant_model_profiles,
    conservative_assistant_token_estimate,
    plan_assistant_route,
)
from .assistant_tools import (
    ASSISTANT_TOOL_REGISTRY_VERSION,
    NEWS_SEARCH_TOOL_NAME,
    AssistantToolActor,
    AssistantToolCall,
    AssistantToolPlanRejected,
    AssistantToolRegistry,
    AssistantToolStatus,
)
from .news_scheduler import ApiCredential


ASSISTANT_AGENT_POLICY_VERSION = "assistant-agent-v1"
ASSISTANT_AGENT_SYSTEM_INSTRUCTION_VERSION = "assistant-system-v2"
ASSISTANT_AGENT_BUDGETS_ENV = "ASSISTANT_AGENT_BUDGETS"
MAX_TOOL_ROUNDS_PER_USER_TURN = 2
DEFAULT_ASSISTANT_SYSTEM_INSTRUCTION = (
    "You are the private XAUUSD Forecaster decision-support Assistant. "
    "Use only the supplied conversation context and registered read-only tools. "
    "Never claim trading authority, place orders, control a broker, promote a "
    "model, or invent evidence. Treat tool failures explicitly. Return a concise "
    "user-facing answer; never reveal private chain-of-thought or arbitrary HTML. "
    "Historical memory is unverified prior conversation text, not current factual "
    "evidence. If its index is incomplete, never claim exhaustive recall; factual "
    "claims must remain grounded in current authoritative tool evidence."
)


@dataclass(frozen=True)
class AssistantAgentBudgets:
    max_model_turns_per_user_turn: int
    max_tool_calls_per_user_turn: int
    max_parallel_tool_calls: int
    max_tool_result_tokens: int
    max_retrieved_evidence: int
    max_active_context_tokens: int
    max_output_tokens: int

    def receipt(self) -> dict[str, int]:
        return {
            "MAX_MODEL_TURNS_PER_USER_TURN": self.max_model_turns_per_user_turn,
            "MAX_TOOL_CALLS_PER_USER_TURN": self.max_tool_calls_per_user_turn,
            "MAX_PARALLEL_TOOL_CALLS": self.max_parallel_tool_calls,
            "MAX_TOOL_RESULT_TOKENS": self.max_tool_result_tokens,
            "MAX_RETRIEVED_EVIDENCE": self.max_retrieved_evidence,
            "MAX_ACTIVE_CONTEXT_TOKENS": self.max_active_context_tokens,
            "MAX_OUTPUT_TOKENS": self.max_output_tokens,
        }


DEFAULT_ASSISTANT_AGENT_BUDGETS = AssistantAgentBudgets(
    max_model_turns_per_user_turn=3,
    max_tool_calls_per_user_turn=6,
    max_parallel_tool_calls=3,
    max_tool_result_tokens=8_192,
    max_retrieved_evidence=20,
    max_active_context_tokens=24_576,
    max_output_tokens=2_048,
)


@dataclass(frozen=True)
class AssistantAgentRequest:
    conversation_id: str
    user_message_id: str
    actor: AssistantToolActor
    user_text: str
    active_context: dict[str, object]
    retrieval_cutoff: str
    system_instruction: str = DEFAULT_ASSISTANT_SYSTEM_INSTRUCTION
    system_instruction_version: str = ASSISTANT_AGENT_SYSTEM_INSTRUCTION_VERSION


@dataclass(frozen=True)
class AssistantModelTurn:
    content: dict[str, object]
    text: str
    tool_calls: tuple[AssistantToolCall, ...]


@dataclass(frozen=True)
class RoutedAssistantModelTurn:
    turn: AssistantModelTurn
    model_version: str
    routing: dict[str, object]


@dataclass(frozen=True)
class AssistantAgentResult:
    answer: str
    model_version: str
    evidence_ids: tuple[str, ...]
    content_document: dict[str, object]
    provenance: dict[str, object]


class AssistantAgentContractError(ValueError):
    """A bounded turn could not satisfy the public agent contract."""

    def __init__(self, error_code: str, message: str) -> None:
        if not re.fullmatch(r"^[A-Z][A-Z0-9_]{2,63}$", error_code):
            raise ValueError("Assistant agent error code is invalid")
        self.error_code = error_code
        super().__init__(message)


AssistantModelTurnInvoker = Callable[..., RoutedAssistantModelTurn]


_BUDGET_FIELDS = {
    "MAX_MODEL_TURNS_PER_USER_TURN": (
        "max_model_turns_per_user_turn", 1, 3,
    ),
    "MAX_TOOL_CALLS_PER_USER_TURN": (
        "max_tool_calls_per_user_turn", 0, 32,
    ),
    "MAX_PARALLEL_TOOL_CALLS": ("max_parallel_tool_calls", 1, 16),
    "MAX_TOOL_RESULT_TOKENS": ("max_tool_result_tokens", 32, 32_768),
    "MAX_RETRIEVED_EVIDENCE": ("max_retrieved_evidence", 0, 100),
    "MAX_ACTIVE_CONTEXT_TOKENS": (
        "max_active_context_tokens", 1_024, 1_000_000,
    ),
    "MAX_OUTPUT_TOKENS": ("max_output_tokens", 32, 32_768),
}
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@/-]{0,127}$")
_MODEL_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,159}$")


def _strict_budget_integer(
    value: object, field_name: str, minimum: int, maximum: int,
) -> int:
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or not minimum <= value <= maximum
    ):
        raise ValueError(f"Assistant agent {field_name} is invalid")
    return value


def _validated_budgets(budgets: AssistantAgentBudgets) -> AssistantAgentBudgets:
    if not isinstance(budgets, AssistantAgentBudgets):
        raise ValueError("Assistant agent budgets are invalid")
    values = budgets.receipt()
    for field_name, (_attribute, minimum, maximum) in _BUDGET_FIELDS.items():
        _strict_budget_integer(values[field_name], field_name, minimum, maximum)
    if budgets.max_tool_calls_per_user_turn < budgets.max_parallel_tool_calls:
        # A direct-only profile may set total calls to zero. Otherwise the
        # per-round budget cannot exceed the complete turn budget.
        if budgets.max_tool_calls_per_user_turn != 0:
            raise ValueError("Assistant agent parallel tool budget exceeds total calls")
    if budgets.max_output_tokens >= budgets.max_active_context_tokens:
        raise ValueError("Assistant agent output reserves the complete context")
    return budgets


def configured_assistant_agent_budgets(
    raw_budgets: str | None = None,
) -> AssistantAgentBudgets:
    raw = (
        os.environ.get(ASSISTANT_AGENT_BUDGETS_ENV, "")
        if raw_budgets is None else raw_budgets
    )
    if not raw.strip():
        return _validated_budgets(DEFAULT_ASSISTANT_AGENT_BUDGETS)
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as error:
        raise ValueError("ASSISTANT_AGENT_BUDGETS is not valid JSON") from error
    if not isinstance(parsed, dict) or set(parsed) != set(_BUDGET_FIELDS):
        raise ValueError("ASSISTANT_AGENT_BUDGETS must declare the exact budget set")
    values: dict[str, int] = {}
    for field_name, (attribute, minimum, maximum) in _BUDGET_FIELDS.items():
        values[attribute] = _strict_budget_integer(
            parsed[field_name], field_name, minimum, maximum,
        )
    return _validated_budgets(AssistantAgentBudgets(**values))


def _canonical_cutoff(value: str) -> str:
    try:
        parsed = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError("Assistant agent retrieval cutoff is invalid") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("Assistant agent retrieval cutoff must be timezone-aware")
    return parsed.astimezone(UTC).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z",
    )


def _validated_request(request: AssistantAgentRequest) -> AssistantAgentRequest:
    if not isinstance(request, AssistantAgentRequest):
        raise ValueError("Assistant agent request is invalid")
    if (
        not isinstance(request.conversation_id, str)
        or not isinstance(request.user_message_id, str)
        or not _IDENTIFIER.fullmatch(request.conversation_id)
        or not _IDENTIFIER.fullmatch(request.user_message_id)
    ):
        raise ValueError("Assistant agent canonical message identity is invalid")
    if not isinstance(request.actor, AssistantToolActor):
        raise ValueError("Assistant agent actor is invalid")
    if not isinstance(request.user_text, str):
        raise ValueError("Assistant agent user text is invalid")
    user_text = request.user_text.strip()
    if not user_text or len(user_text) > 16_000:
        raise ValueError("Assistant agent user text is invalid")
    if not isinstance(request.active_context, dict):
        raise ValueError("Assistant agent active context must be an object")
    try:
        context_bytes = json.dumps(
            request.active_context,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise ValueError("Assistant agent active context is not JSON") from error
    if len(context_bytes) > 500_000:
        raise ValueError("Assistant agent active context exceeds the hard bound")
    if (
        not isinstance(request.system_instruction, str)
        or not request.system_instruction.strip()
        or len(request.system_instruction) > 8_000
        or not isinstance(request.system_instruction_version, str)
        or not _IDENTIFIER.fullmatch(request.system_instruction_version)
    ):
        raise ValueError("Assistant agent system instruction is invalid")
    if not isinstance(request.retrieval_cutoff, str):
        raise ValueError("Assistant agent retrieval cutoff is invalid")
    _canonical_cutoff(request.retrieval_cutoff)
    return request


def decode_gemini_assistant_turn(envelope: dict[str, object]) -> AssistantModelTurn:
    """Parse one native GenerateContent model turn without losing signatures."""
    try:
        candidates = envelope["candidates"]
        if not isinstance(candidates, list) or len(candidates) != 1:
            raise TypeError
        candidate = candidates[0]
        if not isinstance(candidate, dict):
            raise TypeError
        finish_reason = candidate.get("finishReason")
        if finish_reason is not None and finish_reason != "STOP":
            raise AssistantAgentContractError(
                "MODEL_TURN_INCOMPLETE",
                f"Gemma Assistant turn ended with {finish_reason!s}",
            )
        raw_content = candidate["content"]
        if not isinstance(raw_content, dict):
            raise TypeError
        content = copy.deepcopy(raw_content)
        if content.get("role") != "model":
            raise TypeError
        parts = content.get("parts")
        if not isinstance(parts, list) or not 1 <= len(parts) <= 64:
            raise TypeError
    except (KeyError, IndexError, TypeError) as error:
        raise AssistantAgentContractError(
            "INVALID_MODEL_TURN", "Gemma returned an invalid Assistant turn",
        ) from error
    try:
        json.dumps(
            content, ensure_ascii=False, separators=(",", ":"), allow_nan=False,
        )
    except (TypeError, ValueError) as error:
        raise AssistantAgentContractError(
            "INVALID_MODEL_TURN", "Gemma turn is not a JSON provider envelope",
        ) from error

    texts: list[str] = []
    calls: list[AssistantToolCall] = []
    for part in parts:
        if not isinstance(part, dict):
            raise AssistantAgentContractError(
                "INVALID_MODEL_TURN", "Gemma turn contains an invalid part",
            )
        function_call = part.get("functionCall")
        if function_call is not None:
            if not isinstance(function_call, dict):
                raise AssistantAgentContractError(
                    "INVALID_FUNCTION_CALL", "Gemma function call is invalid",
                )
            call_id = str(function_call.get("id") or "").strip()
            name = str(function_call.get("name") or "").strip()
            arguments = function_call.get("args", {})
            if not call_id or not name or not isinstance(arguments, dict):
                raise AssistantAgentContractError(
                    "INVALID_FUNCTION_CALL",
                    "Gemma function call lacks an exact id, name, or arguments",
                )
            calls.append(AssistantToolCall(
                call_id=call_id,
                name=name,
                arguments=copy.deepcopy(arguments),
            ))
        text = part.get("text")
        if text is not None and part.get("thought") is not True:
            if not isinstance(text, str):
                raise AssistantAgentContractError(
                    "INVALID_MODEL_TURN", "Gemma text part is invalid",
                )
            texts.append(text)
    return AssistantModelTurn(
        content=content,
        text="".join(texts).replace("\r\n", "\n").replace("\r", "\n").strip(),
        tool_calls=tuple(calls),
    )


def _initial_contents(request: AssistantAgentRequest) -> list[dict[str, object]]:
    payload = {
        "active_context": request.active_context,
        "retrieval_cutoff": _canonical_cutoff(request.retrieval_cutoff),
        "current_user_message": request.user_text.strip(),
    }
    return [{
        "role": "user",
        "parts": [{
            "text": json.dumps(
                payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                allow_nan=False,
            ),
        }],
    }]


def _gemini_payload(
    request: AssistantAgentRequest,
    contents: list[dict[str, object]],
    registry: AssistantToolRegistry,
    budgets: AssistantAgentBudgets,
    *,
    tools_allowed: bool,
    include_tool_definitions: bool,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "systemInstruction": {
            "parts": [{"text": request.system_instruction.strip()}],
        },
        "contents": copy.deepcopy(contents),
        "generationConfig": {
            "candidateCount": 1,
            "temperature": 0,
            "maxOutputTokens": budgets.max_output_tokens,
        },
    }
    if include_tool_definitions:
        payload["tools"] = registry.gemini_tools(request.actor)
        payload["toolConfig"] = {
            "functionCallingConfig": {
                "mode": "AUTO" if tools_allowed else "NONE",
            },
        }
    return payload


def _function_response_content(
    calls: tuple[AssistantToolCall, ...],
    results: tuple[Any, ...],
) -> dict[str, object]:
    if len(calls) != len(results):
        raise ValueError("Assistant tool call/result count mismatch")
    return {
        "role": "user",
        "parts": [
            {
                "functionResponse": {
                    "id": call.call_id,
                    "name": call.name,
                    "response": result.model_response(),
                },
            }
            for call, result in zip(calls, results, strict=True)
        ],
    }


def run_bounded_assistant_agent(
    request: AssistantAgentRequest,
    registry: AssistantToolRegistry,
    invoke_model: AssistantModelTurnInvoker,
    *,
    budgets: AssistantAgentBudgets | None = None,
) -> AssistantAgentResult:
    """Run zero, one, or two tool rounds within one finite user turn."""
    request = _validated_request(request)
    if not isinstance(registry, AssistantToolRegistry):
        raise ValueError("Assistant tool registry is required")
    if not callable(invoke_model):
        raise ValueError("Assistant model invoker is required")
    budgets = _validated_budgets(
        configured_assistant_agent_budgets() if budgets is None else budgets
    )
    canonical_cutoff = _canonical_cutoff(request.retrieval_cutoff)
    system_instruction = request.system_instruction.strip()
    active_context_bytes = json.dumps(
        request.active_context,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    contents = _initial_contents(request)
    model_receipts: list[dict[str, object]] = []
    tool_receipts: list[list[dict[str, object]]] = []
    model_versions: list[str] = []
    evidence_ids: list[str] = []
    seen_evidence: set[str] = set()
    presentation_evidence: list[dict[str, object]] = []
    seen_presentation_evidence: set[str] = set()
    tool_calls_used = 0
    tool_result_tokens_used = 0
    tool_rounds = 0
    has_authorized_tools = bool(registry.authorized_definitions(request.actor))

    for model_turn_number in range(1, budgets.max_model_turns_per_user_turn + 1):
        calls_remaining = budgets.max_tool_calls_per_user_turn - tool_calls_used
        tools_allowed = (
            has_authorized_tools
            and calls_remaining > 0
            and model_turn_number < budgets.max_model_turns_per_user_turn
            and tool_rounds < MAX_TOOL_ROUNDS_PER_USER_TURN
        )
        planned_tool_calls = (
            min(calls_remaining, budgets.max_parallel_tool_calls)
            if tools_allowed else 0
        )
        payload = _gemini_payload(
            request,
            contents,
            registry,
            budgets,
            tools_allowed=tools_allowed,
            include_tool_definitions=tools_allowed or tool_rounds > 0,
        )
        estimated_input_tokens = conservative_assistant_token_estimate(payload)
        if (
            estimated_input_tokens + budgets.max_output_tokens
            > budgets.max_active_context_tokens
        ):
            raise AssistantAgentContractError(
                "ACTIVE_CONTEXT_BUDGET_EXCEEDED",
                "Assistant active context cannot preserve the configured reserves",
            )
        routed = invoke_model(
            payload,
            user_text=request.user_text,
            planned_tool_calls=planned_tool_calls,
        )
        if not isinstance(routed, RoutedAssistantModelTurn):
            raise AssistantAgentContractError(
                "INVALID_MODEL_TURN", "Assistant model invoker returned no route receipt",
            )
        if (
            not isinstance(routed.turn, AssistantModelTurn)
            or not isinstance(routed.model_version, str)
            or not _MODEL_IDENTIFIER.fullmatch(routed.model_version)
            or not isinstance(routed.routing, dict)
        ):
            raise AssistantAgentContractError(
                "INVALID_MODEL_TURN", "Assistant model route receipt is invalid",
            )
        try:
            routing_bytes = json.dumps(
                routed.routing,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        except (TypeError, ValueError) as error:
            raise AssistantAgentContractError(
                "INVALID_MODEL_TURN", "Assistant model route receipt is not JSON",
            ) from error
        if len(routing_bytes) > 100_000:
            raise AssistantAgentContractError(
                "INVALID_MODEL_TURN", "Assistant model route receipt exceeds its bound",
            )
        turn = routed.turn
        model_versions.append(routed.model_version)
        model_receipts.append(copy.deepcopy(routed.routing))

        if not turn.tool_calls:
            if not turn.text:
                raise AssistantAgentContractError(
                    "EMPTY_FINAL_ANSWER", "Assistant final answer is empty",
                )
            if len(turn.text.encode("utf-8")) > budgets.max_output_tokens * 8:
                raise AssistantAgentContractError(
                    "OUTPUT_BUDGET_EXCEEDED", "Assistant final answer exceeds its bound",
                )
            provenance: dict[str, object] = {
                "policy_version": ASSISTANT_AGENT_POLICY_VERSION,
                "tool_registry_version": ASSISTANT_TOOL_REGISTRY_VERSION,
                "conversation_id": request.conversation_id,
                "user_message_id": request.user_message_id,
                "system_instruction_version": request.system_instruction_version,
                "system_instruction_sha256": hashlib.sha256(
                    system_instruction.encode("utf-8")
                ).hexdigest(),
                "active_context_sha256": hashlib.sha256(
                    active_context_bytes
                ).hexdigest(),
                "retrieval_cutoff": canonical_cutoff,
                "budgets": budgets.receipt(),
                "model_turn_count": len(model_receipts),
                "tool_round_count": len(tool_receipts),
                "tool_call_count": tool_calls_used,
                "tool_result_tokens": tool_result_tokens_used,
                "model_versions": model_versions,
                "model_routing": model_receipts,
                "tool_execution": tool_receipts,
                "evidence_ids": evidence_ids,
            }
            provenance["run_sha256"] = hashlib.sha256(json.dumps(
                provenance,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")).hexdigest()
            content_document = build_assistant_content_document(
                turn.text,
                evidence_items=presentation_evidence,
                evidence_ids=evidence_ids,
                retrieval_cutoff=canonical_cutoff,
            )
            return AssistantAgentResult(
                answer=turn.text,
                model_version=routed.model_version,
                evidence_ids=tuple(evidence_ids),
                content_document=content_document,
                provenance=provenance,
            )

        if not tools_allowed:
            raise AssistantAgentContractError(
                "TOOL_CALL_OUTSIDE_BUDGET",
                "Gemma requested a tool after function calling was disabled",
            )
        call_count = len(turn.tool_calls)
        if (
            call_count > budgets.max_parallel_tool_calls
            or tool_calls_used + call_count > budgets.max_tool_calls_per_user_turn
        ):
            # Reject the complete plan before executing any subset.
            raise AssistantAgentContractError(
                "TOOL_PLAN_BUDGET_EXCEEDED",
                "Gemma tool plan exceeds the finite turn budget",
            )
        remaining_result_tokens = (
            budgets.max_tool_result_tokens - tool_result_tokens_used
        )
        remaining_evidence = budgets.max_retrieved_evidence - len(seen_evidence)
        if remaining_result_tokens < 32:
            raise AssistantAgentContractError(
                "TOOL_RESULT_BUDGET_EXCEEDED",
                "Assistant tool result budget is exhausted",
            )
        try:
            results = registry.execute_batch(
                turn.tool_calls,
                actor=request.actor,
                retrieval_cutoff=canonical_cutoff,
                max_parallel_calls=budgets.max_parallel_tool_calls,
                max_total_result_tokens=remaining_result_tokens,
                max_retrieved_evidence=max(0, remaining_evidence),
            )
        except AssistantToolPlanRejected as error:
            raise AssistantAgentContractError(
                "INVALID_TOOL_PLAN", str(error),
            ) from error
        tool_calls_used += call_count
        tool_rounds += 1
        tool_result_tokens_used += sum(result.result_tokens for result in results)
        if tool_result_tokens_used > budgets.max_tool_result_tokens:
            raise AssistantAgentContractError(
                "TOOL_RESULT_BUDGET_EXCEEDED",
                "Assistant tool results exceed the finite turn budget",
            )
        for result in results:
            for evidence_id in result.evidence_ids:
                if evidence_id not in seen_evidence:
                    seen_evidence.add(evidence_id)
                    evidence_ids.append(evidence_id)
            output = result.output
            if (
                result.name == NEWS_SEARCH_TOOL_NAME
                and result.status is AssistantToolStatus.SUCCEEDED
                and isinstance(output, dict)
                and isinstance(output.get("items"), list)
            ):
                for item in output["items"]:
                    if not isinstance(item, dict):
                        continue
                    evidence_id = str(item.get("evidence_id") or "")
                    if evidence_id and evidence_id not in seen_presentation_evidence:
                        seen_presentation_evidence.add(evidence_id)
                        presentation_evidence.append(copy.deepcopy(item))
        if len(evidence_ids) > budgets.max_retrieved_evidence:
            raise AssistantAgentContractError(
                "EVIDENCE_BUDGET_EXCEEDED",
                "Assistant evidence provenance exceeds the finite turn budget",
            )
        tool_receipts.append([result.receipt() for result in results])
        # Preserve the provider model content exactly, including any required
        # thoughtSignature, then match every functionResponse to its exact id.
        contents.append(copy.deepcopy(turn.content))
        contents.append(_function_response_content(turn.tool_calls, results))

    raise AssistantAgentContractError(
        "MODEL_TURN_BUDGET_EXCEEDED",
        "Assistant exhausted its finite model-turn budget without a final answer",
    )


class CapacityRoutedAssistantModelInvoker:
    """Route every model turn through fixed model policy and durable capacity."""

    def __init__(
        self,
        connection: sqlite3.Connection,
        credentials: tuple[ApiCredential, ...],
        *,
        profiles: tuple[ModelProfile, ...] | None = None,
        policies: tuple[AssistantCapacityPolicy, ...] | None = None,
        before_model_attempt: Callable[[], None] | None = None,
        now: datetime | None = None,
    ) -> None:
        self.connection = connection
        self.credentials = credentials
        self.profiles = (
            configured_assistant_model_profiles() if profiles is None else profiles
        )
        self.policies = policies
        self.before_model_attempt = before_model_attempt
        self.now = now
        self.locked_profile: ModelProfile | None = None
        self.locked_planned_tool_calls: int | None = None
        self.turn_count = 0

    def __call__(
        self,
        payload: dict[str, object],
        *,
        user_text: str,
        planned_tool_calls: int,
    ) -> RoutedAssistantModelTurn:
        generation = payload.get("generationConfig")
        if not isinstance(generation, dict):
            raise ValueError("Assistant model payload lacks generationConfig")
        reserved_output_tokens = generation.get("maxOutputTokens")
        if not isinstance(reserved_output_tokens, int):
            raise ValueError("Assistant model output budget is invalid")
        candidates = (
            (self.locked_profile,) if self.locked_profile is not None
            else self.profiles
        )
        if self.locked_planned_tool_calls is None:
            self.locked_planned_tool_calls = planned_tool_calls
        route_tool_calls = self.locked_planned_tool_calls
        plan = plan_assistant_route(
            AssistantTaskType.ASSISTANT_CHAT,
            estimated_input_tokens=conservative_assistant_token_estimate(payload),
            reserved_output_tokens=reserved_output_tokens,
            user_text=user_text,
            # Reasoning effort is selected once for the complete user turn.
            # Later provider calls may disable tools but must not silently
            # downgrade the task policy while synthesizing prior tool results.
            planned_tool_calls=route_tool_calls,
            profiles=candidates,
        )
        purpose = f"assistant-agent-turn-{self.turn_count + 1}"

        def invoke(profile, credential, thinking_level, request_accountant):
            return generate_metered_response(
                credential.api_key,
                model=profile.model_id,
                purpose=purpose,
                payload=apply_provider_thinking_level(payload, thinking_level),
                decode=decode_gemini_assistant_turn,
                request_accountant=request_accountant,
            )

        routed = execute_assistant_capacity_route(
            self.connection,
            plan,
            self.credentials,
            service_priority=AssistantServicePriority.INTERACTIVE,
            policies=self.policies,
            invoke=invoke,
            before_invoke=self.before_model_attempt,
            now=self.now,
        )
        turn, exact_model = routed.value
        if self.locked_profile is None:
            self.locked_profile = routed.profile
        elif routed.profile != self.locked_profile:
            raise AssistantAgentContractError(
                "MODEL_CHANGED_DURING_TOOL_LOOP",
                "Assistant model changed inside one native function-call sequence",
            )
        self.turn_count += 1
        return RoutedAssistantModelTurn(
            turn=turn,
            model_version=exact_model,
            routing=routed.routing,
        )


def run_capacity_routed_assistant_agent(
    connection: sqlite3.Connection,
    request: AssistantAgentRequest,
    registry: AssistantToolRegistry,
    credentials: tuple[ApiCredential, ...],
    *,
    budgets: AssistantAgentBudgets | None = None,
    profiles: tuple[ModelProfile, ...] | None = None,
    policies: tuple[AssistantCapacityPolicy, ...] | None = None,
    before_model_attempt: Callable[[], None] | None = None,
    now: datetime | None = None,
) -> AssistantAgentResult:
    invoker = CapacityRoutedAssistantModelInvoker(
        connection,
        credentials,
        profiles=profiles,
        policies=policies,
        before_model_attempt=before_model_attempt,
        now=now,
    )
    return run_bounded_assistant_agent(
        request,
        registry,
        invoker,
        budgets=budgets,
    )
