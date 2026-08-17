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
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

from .annotation import generate_metered_response
from .assistant_capacity import (
    AssistantCapacityPolicy,
    AssistantServicePriority,
    execute_assistant_capacity_route,
)
from .assistant_content import build_assistant_content_document
from .assistant_evidence import (
    AssistantEvidenceValidationError,
    MAX_EVIDENCE_CLAIMS,
    MAX_EVIDENCE_PER_CLAIM,
    validate_assistant_evidence_model_text,
)
from .assistant_routing import (
    AssistantTaskType,
    ModelProfile,
    OLLAMA_LOCAL,
    apply_provider_thinking_level,
    configured_assistant_model_profiles,
    conservative_assistant_token_estimate,
    plan_assistant_route,
)
from .model_gateway import OllamaAssistantGateway
from .assistant_tools import (
    ASSISTANT_TOOL_REGISTRY_VERSION,
    NEWS_SEARCH_TOOL_NAME,
    AssistantToolActor,
    AssistantToolCall,
    AssistantToolPlanRejected,
    AssistantToolRegistry,
    AssistantToolResult,
    AssistantToolStatus,
)
from .news_scheduler import ApiCredential


ASSISTANT_AGENT_POLICY_VERSION = "assistant-agent-v2"
ASSISTANT_AGENT_SYSTEM_INSTRUCTION_VERSION = "assistant-system-v6"
ASSISTANT_AGENT_BUDGETS_ENV = "ASSISTANT_AGENT_BUDGETS"
MAX_TOOL_ROUNDS_PER_USER_TURN = 2
DEFAULT_ASSISTANT_SYSTEM_INSTRUCTION = (
    "You are the private XAUUSD Forecaster decision-support Assistant. "
    "Use only the supplied conversation context and registered read-only tools. "
    "Answer the user's actual conversational intent before considering tools. "
    "Do not call a tool for greetings, identity or capability questions, requests "
    "to repeat or explain the conversation, or questions about your own prior "
    "behavior. RECENT_VERBATIM_TURNS and conversation_tail are chronological; "
    "references such as '上一句' mean the latest Assistant message immediately "
    "before CURRENT_USER_MESSAGE. Use a tool only when the answer requires current "
    "external evidence. Do not mention a tool failure or empty result unless that "
    "tool was necessary to answer the user's request. Never repeat an equivalent "
    "tool call after an empty result. For relative news dates such as '今天' or "
    "'昨天', copy the exact date from relative_date_hints into both published_from "
    "and published_to. For a broad daily-news request, use one primary subject term "
    "such as 黄金; do not combine every possible driver or add dates to the query. "
    "For capability questions, briefly cover all three current abilities: recall "
    "recent conversation context, explain and analyze XAUUSD, and search the "
    "point-in-time news archive when external news is needed. Do not claim access "
    "to a market-price or economic-calendar tool that is not registered. "
    "Never claim trading authority, place orders, control a broker, promote a "
    "model, or invent evidence. Treat tool failures explicitly. "
    "Follow explicit response-format constraints such as 'only answer' or an exact "
    "number of points, and preserve user-supplied quoted identifiers verbatim. "
    "Return a concise "
    "final answer as strict JSON with exactly one claims array; every item has "
    "exactly text and evidence_ids. Each text is one nonempty line. If the tools "
    "returned evidence IDs, every claim must cite at least one of those exact IDs; "
    "otherwise every evidence_ids array is empty. Never add Markdown fences or "
    "free text outside that JSON. Never reveal private chain-of-thought or arbitrary HTML. "
    "Historical memory is unverified prior conversation text, not current factual "
    "evidence. If its index is incomplete, never claim exhaustive recall; factual "
    "claims must remain grounded in current authoritative tool evidence. Present "
    "timestamps for people in concise Chinese using Malaysia time (GMT+8), for "
    "example '2026年8月16日 09:31（GMT+8）'. Do not echo raw ISO 8601 timestamps "
    "unless the user explicitly asks for the machine-readable value."
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
AssistantToolResultObserver = Callable[[int, tuple[AssistantToolResult, ...]], None]


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


def ollama_openai_payload(
    payload: dict[str, object], *, model: str, thinking_level: str | None,
    context_limit: int,
) -> dict[str, object]:
    """Translate the canonical Assistant envelope without changing its state."""
    messages: list[dict[str, object]] = []
    instruction = payload.get("systemInstruction")
    if isinstance(instruction, dict):
        parts = instruction.get("parts")
        if isinstance(parts, list):
            text = "".join(
                str(part.get("text") or "") for part in parts
                if isinstance(part, dict)
            ).strip()
            if text:
                messages.append({"role": "system", "content": text})
    contents = payload.get("contents")
    if not isinstance(contents, list):
        raise ValueError("Assistant model payload lacks contents")
    for content in contents:
        if not isinstance(content, dict) or not isinstance(content.get("parts"), list):
            raise ValueError("Assistant model content is invalid")
        role = "assistant" if content.get("role") == "model" else "user"
        texts: list[str] = []
        tool_calls: list[dict[str, object]] = []
        tool_results: list[dict[str, object]] = []
        for part in content["parts"]:
            if not isinstance(part, dict):
                raise ValueError("Assistant model part is invalid")
            if isinstance(part.get("text"), str) and part.get("thought") is not True:
                texts.append(str(part["text"]))
            call = part.get("functionCall")
            if isinstance(call, dict):
                tool_calls.append({
                    "id": str(call.get("id") or ""),
                    "type": "function",
                    "function": {
                        "name": str(call.get("name") or ""),
                        "arguments": json.dumps(
                            call.get("args", {}), ensure_ascii=False,
                            separators=(",", ":"),
                        ),
                    },
                })
            result = part.get("functionResponse")
            if isinstance(result, dict):
                tool_results.append({
                    "role": "tool",
                    "tool_call_id": str(result.get("id") or ""),
                    "content": json.dumps(
                        result.get("response", {}), ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                })
        if tool_results:
            messages.extend(tool_results)
        else:
            message: dict[str, object] = {
                "role": role,
                "content": "".join(texts),
            }
            if tool_calls:
                message["tool_calls"] = tool_calls
            messages.append(message)
    generation = payload.get("generationConfig")
    if not isinstance(generation, dict):
        raise ValueError("Assistant model payload lacks generationConfig")
    translated: dict[str, object] = {
        "model": model,
        "messages": messages,
        "temperature": generation.get("temperature", 0),
        "max_tokens": generation.get("maxOutputTokens", 2_048),
        "stream": False,
        "keep_alive": "5m",
        "options": {"num_ctx": context_limit},
    }
    raw_tools = payload.get("tools")
    if isinstance(raw_tools, list):
        tools: list[dict[str, object]] = []
        for group in raw_tools:
            declarations = group.get("functionDeclarations") if isinstance(group, dict) else None
            if not isinstance(declarations, list):
                continue
            for declaration in declarations:
                if not isinstance(declaration, dict):
                    continue
                tools.append({
                    "type": "function",
                    "function": {
                        "name": declaration.get("name"),
                        "description": declaration.get("description", ""),
                        "parameters": declaration.get(
                            "parametersJsonSchema",
                            declaration.get("parameters", {"type": "object"}),
                        ),
                    },
                })
        if tools:
            translated["tools"] = tools
            translated["tool_choice"] = "auto"
    schema = generation.get("responseJsonSchema", generation.get("responseSchema"))
    if isinstance(schema, dict):
        translated["response_format"] = {
            "type": "json_schema",
            "json_schema": {
                "name": "assistant_answer",
                "strict": True,
                "schema": schema,
            },
        }
    if thinking_level is not None:
        # Qwen 3.5 spends the complete 2K answer budget on hidden reasoning when
        # Ollama receives ``low``. SIMPLE is a direct-answer policy, so disable
        # reasoning explicitly; analytical turns retain the declared high effort.
        translated["reasoning_effort"] = (
            "high" if thinking_level == "high" else "none"
        )
    return translated


def decode_ollama_assistant_turn(envelope: dict[str, object]) -> AssistantModelTurn:
    """Normalize one OpenAI-compatible Ollama turn into canonical provider content."""
    try:
        choices = envelope["choices"]
        choice = choices[0]
        message = choice["message"]
        if not isinstance(choices, list) or len(choices) != 1:
            raise TypeError
        if not isinstance(choice, dict) or not isinstance(message, dict):
            raise TypeError
        finish = choice.get("finish_reason")
        if finish not in {None, "stop", "tool_calls"}:
            raise AssistantAgentContractError(
                "MODEL_TURN_INCOMPLETE", f"Local Assistant turn ended with {finish!s}",
            )
    except (KeyError, IndexError, TypeError) as error:
        raise AssistantAgentContractError(
            "INVALID_MODEL_TURN", "Local model returned an invalid Assistant turn",
        ) from error
    parts: list[dict[str, object]] = []
    text = message.get("content")
    if isinstance(text, str) and text.strip():
        parts.append({"text": text})
    raw_calls = message.get("tool_calls", [])
    if not isinstance(raw_calls, list):
        raise AssistantAgentContractError(
            "INVALID_FUNCTION_CALL", "Local model tool calls are invalid",
        )
    calls: list[AssistantToolCall] = []
    for raw_call in raw_calls:
        function = raw_call.get("function") if isinstance(raw_call, dict) else None
        call_id = str(raw_call.get("id") or "").strip() if isinstance(raw_call, dict) else ""
        if not isinstance(function, dict) or not call_id:
            raise AssistantAgentContractError(
                "INVALID_FUNCTION_CALL", "Local model tool call lacks an exact id",
            )
        name = str(function.get("name") or "").strip()
        try:
            arguments = json.loads(str(function.get("arguments") or "{}"))
        except json.JSONDecodeError as error:
            raise AssistantAgentContractError(
                "INVALID_FUNCTION_CALL", "Local model tool arguments are invalid",
            ) from error
        if not name or not isinstance(arguments, dict):
            raise AssistantAgentContractError(
                "INVALID_FUNCTION_CALL", "Local model tool call is invalid",
            )
        parts.append({"functionCall": {"id": call_id, "name": name, "args": arguments}})
        calls.append(AssistantToolCall(call_id=call_id, name=name, arguments=arguments))
    if not parts:
        raise AssistantAgentContractError("INVALID_MODEL_TURN", "Local model turn is empty")
    content = {"role": "model", "parts": parts}
    return AssistantModelTurn(
        content=content,
        text=text.strip() if isinstance(text, str) else "",
        tool_calls=tuple(calls),
    )


def _conversation_tail(active_context: dict[str, object]) -> list[dict[str, object]]:
    layers = active_context.get("layers")
    if not isinstance(layers, list):
        return []
    for layer in layers:
        if not isinstance(layer, dict) or layer.get("type") != "RECENT_VERBATIM_TURNS":
            continue
        items = layer.get("items")
        if not isinstance(items, list):
            return []
        tail: list[dict[str, object]] = []
        for item in items[-8:]:
            if not isinstance(item, dict):
                continue
            role = item.get("role")
            content = item.get("content")
            if role not in {"USER", "ASSISTANT"} or not isinstance(content, str):
                continue
            tail.append({"role": role, "content": content})
        return tail
    return []


def _relative_date_hints(retrieval_cutoff: str) -> dict[str, object]:
    cutoff = datetime.fromisoformat(
        _canonical_cutoff(retrieval_cutoff).replace("Z", "+00:00")
    )
    local_date = cutoff.astimezone(timezone(timedelta(hours=8))).date()
    return {
        "timezone": "Asia/Kuala_Lumpur",
        "today": local_date.isoformat(),
        "yesterday": (local_date - timedelta(days=1)).isoformat(),
    }


def _initial_contents(request: AssistantAgentRequest) -> list[dict[str, object]]:
    payload = {
        "active_context": request.active_context,
        "conversation_tail": _conversation_tail(request.active_context),
        "relative_date_hints": _relative_date_hints(request.retrieval_cutoff),
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


def _final_answer_json_schema(
    available_evidence_ids: tuple[str, ...],
    *,
    max_cited_evidence: int,
) -> dict[str, object]:
    evidence_ids = list(available_evidence_ids)
    if evidence_ids:
        evidence_item_schema: dict[str, object] = {
            "type": "string",
            "enum": evidence_ids,
        }
        minimum_evidence_per_claim = 1
        maximum_evidence_per_claim = min(
            MAX_EVIDENCE_PER_CLAIM,
            max_cited_evidence,
            len(evidence_ids),
        )
    else:
        evidence_item_schema = {"type": "string"}
        minimum_evidence_per_claim = 0
        maximum_evidence_per_claim = 0
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["claims"],
        "properties": {
            "claims": {
                "type": "array",
                "minItems": 1,
                "maxItems": MAX_EVIDENCE_CLAIMS,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["text", "evidence_ids"],
                    "properties": {
                        "text": {
                            "type": "string",
                            "description": "One concise, nonempty answer line.",
                        },
                        "evidence_ids": {
                            "type": "array",
                            "minItems": minimum_evidence_per_claim,
                            "maxItems": maximum_evidence_per_claim,
                            "items": evidence_item_schema,
                        },
                    },
                },
            },
        },
    }


def _gemini_payload(
    request: AssistantAgentRequest,
    contents: list[dict[str, object]],
    registry: AssistantToolRegistry,
    budgets: AssistantAgentBudgets,
    *,
    tools_allowed: bool,
    available_evidence_ids: tuple[str, ...],
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
    if tools_allowed:
        payload["tools"] = registry.gemini_tools(request.actor)
        payload["toolConfig"] = {
            "functionCallingConfig": {
                "mode": "AUTO" if tools_allowed else "NONE",
            },
        }
    else:
        # Keep the exact function-response history while removing the provider
        # tool surface, so the final synthesis cannot extend the finite loop.
        generation_config = payload["generationConfig"]
        assert isinstance(generation_config, dict)
        generation_config["responseMimeType"] = "application/json"
        generation_config["responseJsonSchema"] = _final_answer_json_schema(
            available_evidence_ids,
            max_cited_evidence=budgets.max_retrieved_evidence or 1,
        )
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
    on_tool_results: AssistantToolResultObserver | None = None,
) -> AssistantAgentResult:
    """Run zero, one, or two tool rounds within one finite user turn."""
    request = _validated_request(request)
    if not isinstance(registry, AssistantToolRegistry):
        raise ValueError("Assistant tool registry is required")
    if not callable(invoke_model):
        raise ValueError("Assistant model invoker is required")
    if on_tool_results is not None and not callable(on_tool_results):
        raise ValueError("Assistant tool result observer is invalid")
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
    available_evidence_ids: list[str] = []
    seen_evidence: set[str] = set()
    presentation_evidence: list[dict[str, object]] = []
    seen_presentation_evidence: set[str] = set()
    tool_calls_used = 0
    tool_result_tokens_used = 0
    tool_rounds = 0
    authorized_tool_count = len(registry.authorized_definitions(request.actor))
    has_authorized_tools = authorized_tool_count > 0

    for model_turn_number in range(1, budgets.max_model_turns_per_user_turn + 1):
        calls_remaining = budgets.max_tool_calls_per_user_turn - tool_calls_used
        tools_allowed = (
            has_authorized_tools
            and calls_remaining > 0
            and model_turn_number < budgets.max_model_turns_per_user_turn
            and tool_rounds < MAX_TOOL_ROUNDS_PER_USER_TURN
        )
        planned_tool_calls = (
            min(
                calls_remaining,
                budgets.max_parallel_tool_calls,
                authorized_tool_count,
            )
            if tools_allowed else 0
        )
        payload = _gemini_payload(
            request,
            contents,
            registry,
            budgets,
            tools_allowed=tools_allowed,
            available_evidence_ids=tuple(available_evidence_ids),
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
            try:
                validated_evidence = validate_assistant_evidence_model_text(
                    turn.text,
                    available_evidence_ids,
                    max_cited_evidence=budgets.max_retrieved_evidence or 1,
                )
            except AssistantEvidenceValidationError as error:
                raise AssistantAgentContractError(
                    "INVALID_EVIDENCE_ANSWER",
                    "Assistant final answer failed evidence validation",
                ) from error
            answer = validated_evidence.answer
            if len(answer.encode("utf-8")) > budgets.max_output_tokens * 8:
                raise AssistantAgentContractError(
                    "OUTPUT_BUDGET_EXCEEDED", "Assistant final answer exceeds its bound",
                )
            cited_evidence_ids = list(validated_evidence.evidence_ids)
            cited_set = set(cited_evidence_ids)
            cited_presentation_evidence = [
                copy.deepcopy(item) for item in presentation_evidence
                if str(item.get("evidence_id") or "") in cited_set
            ]
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
                "evidence_ids": cited_evidence_ids,
                "evidence_validation": validated_evidence.receipt,
            }
            provenance["run_sha256"] = hashlib.sha256(json.dumps(
                provenance,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")).hexdigest()
            content_document = build_assistant_content_document(
                answer,
                evidence_items=cited_presentation_evidence,
                evidence_ids=cited_evidence_ids,
                retrieval_cutoff=canonical_cutoff,
            )
            return AssistantAgentResult(
                answer=answer,
                model_version=routed.model_version,
                evidence_ids=tuple(cited_evidence_ids),
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
            call_count > planned_tool_calls
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
                max_parallel_calls=planned_tool_calls,
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
                    available_evidence_ids.append(evidence_id)
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
        if len(available_evidence_ids) > budgets.max_retrieved_evidence:
            raise AssistantAgentContractError(
                "EVIDENCE_BUDGET_EXCEEDED",
                "Assistant evidence provenance exceeds the finite turn budget",
            )
        tool_receipts.append([result.receipt() for result in results])
        if on_tool_results is not None:
            on_tool_results(tool_rounds - 1, results)
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
            if profile.provider == OLLAMA_LOCAL:
                return OllamaAssistantGateway(accountant=request_accountant).generate(
                    model=profile.model_id,
                    purpose=purpose,
                    payload=ollama_openai_payload(
                        payload, model=profile.model_id,
                        thinking_level=thinking_level,
                        context_limit=profile.context_limit,
                    ),
                    input_tokens=conservative_assistant_token_estimate(payload),
                    decode=decode_ollama_assistant_turn,
                )
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
    on_tool_results: AssistantToolResultObserver | None = None,
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
        on_tool_results=on_tool_results,
    )
