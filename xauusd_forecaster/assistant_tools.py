"""Versioned, read-only Assistant tools with bounded parallel execution."""

from __future__ import annotations

import copy
import hashlib
import json
import re
import time
from collections.abc import Callable, Mapping
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime
from enum import StrEnum
from typing import Any

from .news_qa import build_news_evidence_packet


ASSISTANT_TOOL_REGISTRY_VERSION = "assistant-tool-registry-v1"
NEWS_SEARCH_TOOL_NAME = "search_news_v1"
NEWS_SEARCH_TOOL_VERSION = "v1"
MAX_ASSISTANT_TOOL_DEFINITIONS = 16
MAX_ASSISTANT_TOOL_CALLS_PER_ROUND = 16
MAX_ASSISTANT_TOOL_ARGUMENT_BYTES = 8_192
MAX_ASSISTANT_TOOL_SCHEMA_BYTES = 32_768
MAX_ASSISTANT_TOOL_TIMEOUT_SECONDS = 30.0
MAX_ASSISTANT_TOOL_RESULT_TOKENS = 32_768


class AssistantToolCapability(StrEnum):
    NEWS_RETRIEVAL = "NEWS_RETRIEVAL"
    MARKET_DATA_READ = "MARKET_DATA_READ"
    CALENDAR_READ = "CALENDAR_READ"


class AssistantToolStatus(StrEnum):
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    REJECTED = "REJECTED"
    TIMED_OUT = "TIMED_OUT"


class AssistantToolPlanRejected(ValueError):
    """A complete model tool plan is unsafe to execute."""


class AssistantToolAdapterError(RuntimeError):
    """A controlled adapter failure that is safe to expose as a code."""

    def __init__(self, error_code: str) -> None:
        if not _ERROR_CODE.fullmatch(error_code):
            raise ValueError("Assistant tool error code is invalid")
        self.error_code = error_code
        super().__init__(error_code)


@dataclass(frozen=True)
class AssistantToolActor:
    actor_id: str
    work_id: str
    allowed_capabilities: frozenset[AssistantToolCapability]


@dataclass(frozen=True)
class AssistantToolCall:
    call_id: str
    name: str
    arguments: dict[str, object]


@dataclass(frozen=True)
class AssistantToolExecutionContext:
    actor: AssistantToolActor
    retrieval_cutoff: str
    deadline_monotonic: float
    result_token_budget: int
    max_retrieved_evidence: int
    monotonic_now: Callable[[], float] = field(repr=False, compare=False)

    def remaining_seconds(self) -> float:
        return max(0.0, self.deadline_monotonic - self.monotonic_now())


AssistantToolExecutor = Callable[
    [dict[str, object], AssistantToolExecutionContext], dict[str, object]
]


@dataclass(frozen=True)
class AssistantToolDefinition:
    name: str
    version: str
    description: str
    capability: AssistantToolCapability
    input_schema: dict[str, object]
    output_schema: dict[str, object]
    timeout_seconds: float
    max_result_tokens: int
    provenance_fields: tuple[str, ...] = ()
    executor: AssistantToolExecutor = field(repr=False, compare=False, default=None)  # type: ignore[assignment]


@dataclass(frozen=True)
class AssistantToolResult:
    call_id: str
    name: str
    tool_version: str | None
    status: AssistantToolStatus
    _output: dict[str, object] | None = field(repr=False, compare=False)
    error_code: str | None
    result_tokens: int
    result_sha256: str
    evidence_ids: tuple[str, ...]
    _provenance: dict[str, object] = field(repr=False, compare=False)
    started_at: str
    completed_at: str

    @property
    def output(self) -> dict[str, object] | None:
        return copy.deepcopy(self._output)

    @property
    def provenance(self) -> dict[str, object]:
        return copy.deepcopy(self._provenance)

    def model_response(self) -> dict[str, object]:
        response: dict[str, object] = {
            "status": self.status.value,
            "tool_version": self.tool_version,
        }
        if self.status is AssistantToolStatus.SUCCEEDED:
            response["result"] = copy.deepcopy(self._output)
        else:
            response["error"] = {"code": self.error_code}
        return response

    def receipt(self) -> dict[str, object]:
        return {
            "call_id": self.call_id,
            "name": self.name,
            "tool_version": self.tool_version,
            "status": self.status.value,
            "error_code": self.error_code,
            "result_tokens": self.result_tokens,
            "result_sha256": self.result_sha256,
            "evidence_ids": list(self.evidence_ids),
            "provenance": copy.deepcopy(self._provenance),
            "started_at": self.started_at,
            "completed_at": self.completed_at,
        }


_TOOL_NAME = re.compile(r"^[a-z][a-z0-9_]{1,54}_v[1-9][0-9]*$")
_TOOL_VERSION = re.compile(r"^v[1-9][0-9]*$")
_CALL_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_ACTOR_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@/-]{0,127}$")
_ERROR_CODE = re.compile(r"^[A-Z][A-Z0-9_]{2,63}$")
_PROVENANCE_FIELD = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_DATE_ONLY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_SCHEMA_TYPES = {"object", "array", "string", "integer", "number", "boolean"}
_COMMON_SCHEMA_KEYS = {"type", "description", "enum"}
_TYPE_SCHEMA_KEYS = {
    "object": {"properties", "required", "additionalProperties", "minProperties", "maxProperties"},
    "array": {"items", "minItems", "maxItems", "uniqueItems"},
    "string": {"minLength", "maxLength", "pattern"},
    "integer": {"minimum", "maximum"},
    "number": {"minimum", "maximum"},
    "boolean": set(),
}


def _json_bytes(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _result_token_estimate(value: object) -> int:
    # UTF-8 bytes are a deliberately conservative provider-independent bound.
    return max(1, len(_json_bytes(value)))


def _iso_now(clock: Callable[[], datetime]) -> str:
    value = clock()
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Assistant tool clock must be timezone-aware")
    return value.astimezone(UTC).isoformat(timespec="microseconds")


def _canonical_cutoff(value: str) -> str:
    raw = str(value or "").strip()
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError("Assistant tool retrieval cutoff is invalid") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("Assistant tool retrieval cutoff must be timezone-aware")
    return parsed.astimezone(UTC).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z",
    )


def _validate_actor(actor: AssistantToolActor) -> AssistantToolActor:
    if not isinstance(actor, AssistantToolActor):
        raise ValueError("Assistant tool actor is invalid")
    if not _ACTOR_ID.fullmatch(actor.actor_id) or not _ACTOR_ID.fullmatch(actor.work_id):
        raise ValueError("Assistant tool actor identity is invalid")
    if any(
        not isinstance(capability, AssistantToolCapability)
        for capability in actor.allowed_capabilities
    ):
        raise ValueError("Assistant tool actor capabilities are invalid")
    return actor


def assistant_tool_actor_fingerprint(actor: AssistantToolActor) -> str:
    actor = _validate_actor(actor)
    material = f"{ASSISTANT_TOOL_REGISTRY_VERSION}:{actor.actor_id}:{actor.work_id}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]


def _bounded_integer(
    value: object, field_name: str, *, minimum: int, maximum: int,
) -> int:
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or not minimum <= value <= maximum
    ):
        raise ValueError(f"Assistant tool {field_name} is invalid")
    return value


def _validate_schema_definition(schema: object, *, path: str) -> None:
    if not isinstance(schema, dict):
        raise ValueError(f"Assistant tool {path} schema must be an object")
    schema_type = schema.get("type")
    if schema_type not in _SCHEMA_TYPES:
        raise ValueError(f"Assistant tool {path} schema type is invalid")
    unknown = set(schema) - _COMMON_SCHEMA_KEYS - _TYPE_SCHEMA_KEYS[str(schema_type)]
    if unknown:
        raise ValueError(f"Assistant tool {path} schema has unsupported keywords")
    description = schema.get("description")
    if description is not None and (
        not isinstance(description, str) or not 1 <= len(description) <= 500
    ):
        raise ValueError(f"Assistant tool {path} schema description is invalid")
    enum = schema.get("enum")
    if enum is not None and (
        not isinstance(enum, list) or not enum or len(enum) > 64
    ):
        raise ValueError(f"Assistant tool {path} schema enum is invalid")
    if schema_type == "object":
        properties = schema.get("properties")
        required = schema.get("required", [])
        if (
            not isinstance(properties, dict)
            or len(properties) > 32
            or not isinstance(required, list)
            or any(not isinstance(item, str) for item in required)
            or len(set(required)) != len(required)
            or any(item not in properties for item in required)
            or schema.get("additionalProperties") is not False
        ):
            raise ValueError(f"Assistant tool {path} object schema is invalid")
        for name, child in properties.items():
            if not _PROVENANCE_FIELD.fullmatch(str(name)):
                raise ValueError(f"Assistant tool {path} property name is invalid")
            _validate_schema_definition(child, path=f"{path}.{name}")
    elif schema_type == "array":
        if "items" not in schema:
            raise ValueError(f"Assistant tool {path} array schema lacks items")
        _validate_schema_definition(schema["items"], path=f"{path}[]")
    elif schema_type == "string" and "pattern" in schema:
        try:
            re.compile(str(schema["pattern"]))
        except re.error as error:
            raise ValueError(f"Assistant tool {path} pattern is invalid") from error
    for key in (
        "minProperties", "maxProperties", "minItems", "maxItems",
        "minLength", "maxLength",
    ):
        if key in schema:
            _bounded_integer(schema[key], f"schema {key}", minimum=0, maximum=100_000)
    for key in ("minimum", "maximum"):
        if key in schema and (
            not isinstance(schema[key], (int, float)) or isinstance(schema[key], bool)
        ):
            raise ValueError(f"Assistant tool {path} {key} is invalid")
    if "uniqueItems" in schema and not isinstance(schema["uniqueItems"], bool):
        raise ValueError(f"Assistant tool {path} uniqueItems is invalid")


def _validate_json_value(value: object, schema: Mapping[str, object], *, path: str) -> None:
    schema_type = schema["type"]
    if schema_type == "object":
        if not isinstance(value, dict):
            raise ValueError(f"{path} must be an object")
        properties = schema["properties"]
        assert isinstance(properties, dict)
        required = schema.get("required", [])
        assert isinstance(required, list)
        if any(name not in value for name in required) or any(
            name not in properties for name in value
        ):
            raise ValueError(f"{path} has invalid properties")
        if len(value) < int(schema.get("minProperties", 0)) or len(value) > int(
            schema.get("maxProperties", len(properties))
        ):
            raise ValueError(f"{path} has an invalid property count")
        for name, item in value.items():
            _validate_json_value(item, properties[name], path=f"{path}.{name}")
    elif schema_type == "array":
        if not isinstance(value, list):
            raise ValueError(f"{path} must be an array")
        if len(value) < int(schema.get("minItems", 0)) or len(value) > int(
            schema.get("maxItems", 100_000)
        ):
            raise ValueError(f"{path} has an invalid item count")
        if schema.get("uniqueItems") and len({_json_bytes(item) for item in value}) != len(value):
            raise ValueError(f"{path} must contain unique items")
        for index, item in enumerate(value):
            _validate_json_value(item, schema["items"], path=f"{path}[{index}]")
    elif schema_type == "string":
        if not isinstance(value, str):
            raise ValueError(f"{path} must be a string")
        if len(value) < int(schema.get("minLength", 0)) or len(value) > int(
            schema.get("maxLength", 100_000)
        ):
            raise ValueError(f"{path} has an invalid length")
        if "pattern" in schema and re.fullmatch(str(schema["pattern"]), value) is None:
            raise ValueError(f"{path} has an invalid format")
    elif schema_type == "integer":
        if not isinstance(value, int) or isinstance(value, bool):
            raise ValueError(f"{path} must be an integer")
    elif schema_type == "number":
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise ValueError(f"{path} must be a number")
    elif schema_type == "boolean" and not isinstance(value, bool):
        raise ValueError(f"{path} must be boolean")
    if "enum" in schema and value not in schema["enum"]:
        raise ValueError(f"{path} is outside the allowed values")
    if schema_type in {"integer", "number"}:
        if "minimum" in schema and float(value) < float(schema["minimum"]):
            raise ValueError(f"{path} is below the minimum")
        if "maximum" in schema and float(value) > float(schema["maximum"]):
            raise ValueError(f"{path} exceeds the maximum")


def _validate_definition(definition: AssistantToolDefinition) -> None:
    if not isinstance(definition, AssistantToolDefinition):
        raise ValueError("Assistant tool definition is invalid")
    if not _TOOL_NAME.fullmatch(definition.name):
        raise ValueError("Assistant tool name is invalid")
    if not _TOOL_VERSION.fullmatch(definition.version) or not definition.name.endswith(
        f"_{definition.version}"
    ):
        raise ValueError("Assistant tool version is invalid")
    if not 1 <= len(definition.description.strip()) <= 500:
        raise ValueError("Assistant tool description is invalid")
    if not isinstance(definition.capability, AssistantToolCapability):
        raise ValueError("Assistant tool capability is not read-only")
    if not callable(definition.executor):
        raise ValueError("Assistant tool executor is required")
    if (
        not isinstance(definition.timeout_seconds, (int, float))
        or isinstance(definition.timeout_seconds, bool)
        or not 0.01 <= float(definition.timeout_seconds) <= MAX_ASSISTANT_TOOL_TIMEOUT_SECONDS
    ):
        raise ValueError("Assistant tool timeout is invalid")
    _bounded_integer(
        definition.max_result_tokens, "max_result_tokens",
        minimum=32, maximum=MAX_ASSISTANT_TOOL_RESULT_TOKENS,
    )
    if (
        len(set(definition.provenance_fields)) != len(definition.provenance_fields)
        or any(not _PROVENANCE_FIELD.fullmatch(item) for item in definition.provenance_fields)
    ):
        raise ValueError("Assistant tool provenance fields are invalid")
    _validate_schema_definition(definition.input_schema, path=f"{definition.name}.input")
    _validate_schema_definition(definition.output_schema, path=f"{definition.name}.output")
    if (
        len(_json_bytes(definition.input_schema)) > MAX_ASSISTANT_TOOL_SCHEMA_BYTES
        or len(_json_bytes(definition.output_schema)) > MAX_ASSISTANT_TOOL_SCHEMA_BYTES
    ):
        raise ValueError("Assistant tool schema is too large")


def _validate_call(call: AssistantToolCall) -> None:
    if not isinstance(call, AssistantToolCall):
        raise AssistantToolPlanRejected("Assistant tool call has an invalid type")
    if not _CALL_ID.fullmatch(call.call_id) or not _TOOL_NAME.fullmatch(call.name):
        raise AssistantToolPlanRejected("Assistant tool call identity is invalid")
    if not isinstance(call.arguments, dict):
        raise AssistantToolPlanRejected("Assistant tool arguments must be an object")
    if len(_json_bytes(call.arguments)) > MAX_ASSISTANT_TOOL_ARGUMENT_BYTES:
        raise AssistantToolPlanRejected("Assistant tool arguments exceed the bound")


def _public_provenance(
    definition: AssistantToolDefinition | None,
    output: dict[str, object] | None,
    *,
    actor: AssistantToolActor,
) -> dict[str, object]:
    result: dict[str, object] = {
        "registry_version": ASSISTANT_TOOL_REGISTRY_VERSION,
        "actor_fingerprint": assistant_tool_actor_fingerprint(actor),
    }
    if definition is None or output is None:
        return result
    raw = output.get("provenance")
    if not isinstance(raw, dict):
        return result
    for name in definition.provenance_fields:
        if name in raw:
            result[name] = raw[name]
    if len(_json_bytes(result)) > 8_192:
        raise ValueError("Assistant tool provenance exceeds the bound")
    return result


def _evidence_ids(output: dict[str, object] | None) -> tuple[str, ...]:
    if output is None or not isinstance(output.get("provenance"), dict):
        return ()
    raw = output["provenance"].get("canonical_evidence_ids")
    if raw is None:
        return ()
    if (
        not isinstance(raw, list)
        or len(raw) > 100
        or any(not isinstance(item, str) or not 1 <= len(item) <= 128 for item in raw)
    ):
        raise ValueError("Assistant tool evidence provenance is invalid")
    return tuple(dict.fromkeys(raw))


def _tool_result(
    call: AssistantToolCall,
    definition: AssistantToolDefinition | None,
    actor: AssistantToolActor,
    *,
    status: AssistantToolStatus,
    output: dict[str, object] | None,
    error_code: str | None,
    started_at: str,
    completed_at: str,
) -> AssistantToolResult:
    if status is AssistantToolStatus.SUCCEEDED:
        if output is None or error_code is not None:
            raise ValueError("Successful Assistant tool result is incomplete")
        result_tokens = _result_token_estimate(output)
    else:
        if output is not None or not error_code or not _ERROR_CODE.fullmatch(error_code):
            raise ValueError("Failed Assistant tool result is incomplete")
        result_tokens = 0
    evidence_ids = _evidence_ids(output)
    provenance = _public_provenance(definition, output, actor=actor)
    response: dict[str, object] = {
        "status": status.value,
        "tool_version": definition.version if definition else None,
    }
    if output is not None:
        response["result"] = output
    else:
        response["error"] = {"code": error_code}
    return AssistantToolResult(
        call_id=call.call_id,
        name=call.name,
        tool_version=definition.version if definition else None,
        status=status,
        _output=copy.deepcopy(output),
        error_code=error_code,
        result_tokens=result_tokens,
        result_sha256=hashlib.sha256(_json_bytes(response)).hexdigest(),
        evidence_ids=evidence_ids,
        _provenance=copy.deepcopy(provenance),
        started_at=started_at,
        completed_at=completed_at,
    )


class AssistantToolRegistry:
    """Authorize and execute an explicit read-only tool set."""

    def __init__(
        self,
        definitions: tuple[AssistantToolDefinition, ...],
        *,
        utc_now: Callable[[], datetime] | None = None,
        monotonic_now: Callable[[], float] | None = None,
    ) -> None:
        if not definitions or len(definitions) > MAX_ASSISTANT_TOOL_DEFINITIONS:
            raise ValueError("Assistant tool definition count is invalid")
        snapshots: list[AssistantToolDefinition] = []
        for definition in definitions:
            _validate_definition(definition)
            snapshots.append(replace(
                definition,
                input_schema=copy.deepcopy(definition.input_schema),
                output_schema=copy.deepcopy(definition.output_schema),
            ))
        names = [definition.name for definition in snapshots]
        if len(set(names)) != len(names):
            raise ValueError("Assistant tool definitions contain duplicate names")
        self._definitions = tuple(snapshots)
        self._by_name = {
            definition.name: definition for definition in self._definitions
        }
        self._utc_now = utc_now or (lambda: datetime.now(UTC))
        self._monotonic_now = monotonic_now or time.monotonic

    @property
    def definitions(self) -> tuple[AssistantToolDefinition, ...]:
        return tuple(replace(
            definition,
            input_schema=copy.deepcopy(definition.input_schema),
            output_schema=copy.deepcopy(definition.output_schema),
        ) for definition in self._definitions)

    def _authorized_definitions(
        self, actor: AssistantToolActor,
    ) -> tuple[AssistantToolDefinition, ...]:
        actor = _validate_actor(actor)
        return tuple(
            definition for definition in self._definitions
            if definition.capability in actor.allowed_capabilities
        )

    def authorized_definitions(
        self, actor: AssistantToolActor,
    ) -> tuple[AssistantToolDefinition, ...]:
        return tuple(replace(
            definition,
            input_schema=copy.deepcopy(definition.input_schema),
            output_schema=copy.deepcopy(definition.output_schema),
        ) for definition in self._authorized_definitions(actor))

    def gemini_tools(self, actor: AssistantToolActor) -> list[dict[str, object]]:
        definitions = self._authorized_definitions(actor)
        if not definitions:
            return []
        return [{
            "functionDeclarations": [
                {
                    "name": definition.name,
                    "description": definition.description,
                    # The REST ``parameters`` field accepts only Gemini's
                    # protobuf Schema subset.  ``parametersJsonSchema`` keeps
                    # the registry's complete strict JSON Schema, including
                    # ``additionalProperties: false``.
                    "parametersJsonSchema": copy.deepcopy(
                        definition.input_schema,
                    ),
                }
                for definition in definitions
            ],
        }]

    def execute_batch(
        self,
        calls: tuple[AssistantToolCall, ...],
        *,
        actor: AssistantToolActor,
        retrieval_cutoff: str,
        max_parallel_calls: int,
        max_total_result_tokens: int,
        max_retrieved_evidence: int,
    ) -> tuple[AssistantToolResult, ...]:
        actor = _validate_actor(actor)
        cutoff = _canonical_cutoff(retrieval_cutoff)
        _bounded_integer(
            max_parallel_calls, "max_parallel_calls",
            minimum=1, maximum=MAX_ASSISTANT_TOOL_CALLS_PER_ROUND,
        )
        _bounded_integer(
            max_total_result_tokens, "max_total_result_tokens",
            minimum=32, maximum=MAX_ASSISTANT_TOOL_RESULT_TOKENS,
        )
        _bounded_integer(
            max_retrieved_evidence, "max_retrieved_evidence",
            minimum=0, maximum=100,
        )
        if not calls or len(calls) > MAX_ASSISTANT_TOOL_CALLS_PER_ROUND:
            raise AssistantToolPlanRejected("Assistant tool plan size is invalid")
        if len(calls) > max_parallel_calls:
            raise AssistantToolPlanRejected("Assistant tool plan exceeds parallel budget")
        for call in calls:
            _validate_call(call)
        call_ids = [call.call_id for call in calls]
        if len(set(call_ids)) != len(call_ids):
            raise AssistantToolPlanRejected("Assistant tool call ids must be unique")

        batch_started = self._monotonic_now()
        batch_timestamp = _iso_now(self._utc_now)
        prepared: list[
            tuple[int, AssistantToolCall, AssistantToolDefinition, int, int]
        ] = []
        results: dict[int, AssistantToolResult] = {}
        news_indexes = [
            index for index, call in enumerate(calls)
            if self._by_name.get(call.name) is not None
            and self._by_name[call.name].capability is AssistantToolCapability.NEWS_RETRIEVAL
        ]
        news_order = {index: order for order, index in enumerate(news_indexes)}
        valid_candidates: list[int] = []

        for index, call in enumerate(calls):
            definition = self._by_name.get(call.name)
            if definition is None:
                results[index] = _tool_result(
                    call, None, actor,
                    status=AssistantToolStatus.REJECTED,
                    output=None,
                    error_code="UNKNOWN_TOOL",
                    started_at=batch_timestamp,
                    completed_at=batch_timestamp,
                )
                continue
            if definition.capability not in actor.allowed_capabilities:
                results[index] = _tool_result(
                    call, definition, actor,
                    status=AssistantToolStatus.REJECTED,
                    output=None,
                    error_code="TOOL_NOT_AUTHORIZED",
                    started_at=batch_timestamp,
                    completed_at=batch_timestamp,
                )
                continue
            try:
                _validate_json_value(
                    call.arguments, definition.input_schema, path="arguments",
                )
            except ValueError:
                results[index] = _tool_result(
                    call, definition, actor,
                    status=AssistantToolStatus.REJECTED,
                    output=None,
                    error_code="INVALID_TOOL_ARGUMENTS",
                    started_at=batch_timestamp,
                    completed_at=batch_timestamp,
                )
                continue
            valid_candidates.append(index)

        for order, index in enumerate(valid_candidates):
            call = calls[index]
            definition = self._by_name[call.name]
            quotient, remainder = divmod(
                max_total_result_tokens, len(valid_candidates),
            )
            result_budget = min(
                definition.max_result_tokens,
                quotient + (1 if order < remainder else 0),
            )
            evidence_budget = 0
            if index in news_order and news_indexes:
                evidence_quotient, evidence_remainder = divmod(
                    max_retrieved_evidence, len(news_indexes),
                )
                evidence_budget = evidence_quotient + (
                    1 if news_order[index] < evidence_remainder else 0
                )
            prepared.append((index, call, definition, result_budget, evidence_budget))

        if not prepared:
            return tuple(results[index] for index in range(len(calls)))

        def execute_one(
            call: AssistantToolCall,
            definition: AssistantToolDefinition,
            result_budget: int,
            evidence_budget: int,
        ) -> AssistantToolResult:
            started_at = _iso_now(self._utc_now)
            context = AssistantToolExecutionContext(
                actor=actor,
                retrieval_cutoff=cutoff,
                deadline_monotonic=(
                    batch_started + float(definition.timeout_seconds)
                ),
                result_token_budget=result_budget,
                max_retrieved_evidence=evidence_budget,
                monotonic_now=self._monotonic_now,
            )
            try:
                output = copy.deepcopy(
                    definition.executor(copy.deepcopy(call.arguments), context)
                )
                _validate_json_value(
                    output, definition.output_schema, path="tool_result",
                )
                if _result_token_estimate(output) > result_budget:
                    raise AssistantToolAdapterError("TOOL_RESULT_BUDGET_EXCEEDED")
                return _tool_result(
                    call, definition, actor,
                    status=AssistantToolStatus.SUCCEEDED,
                    output=output,
                    error_code=None,
                    started_at=started_at,
                    completed_at=_iso_now(self._utc_now),
                )
            except AssistantToolAdapterError as error:
                code = error.error_code
            except ValueError:
                code = "INVALID_TOOL_RESULT"
            except Exception:
                code = "TOOL_EXECUTION_FAILED"
            return _tool_result(
                call, definition, actor,
                status=(
                    AssistantToolStatus.TIMED_OUT
                    if code == "TOOL_TIMEOUT" else AssistantToolStatus.FAILED
                ),
                output=None,
                error_code=code,
                started_at=started_at,
                completed_at=_iso_now(self._utc_now),
            )

        executor = ThreadPoolExecutor(
            max_workers=len(prepared), thread_name_prefix="assistant-tool",
        )
        pending: dict[Future[AssistantToolResult], tuple[int, AssistantToolCall, AssistantToolDefinition]] = {}
        for index, call, definition, result_budget, evidence_budget in prepared:
            future = executor.submit(
                execute_one, call, definition, result_budget, evidence_budget,
            )
            pending[future] = (index, call, definition)
        try:
            while pending:
                now = self._monotonic_now()
                nearest = min(
                    batch_started + float(definition.timeout_seconds)
                    for _index, _call, definition in pending.values()
                )
                done, _ = wait(
                    tuple(pending), timeout=max(0.0, nearest - now),
                    return_when=FIRST_COMPLETED,
                )
                for future in done:
                    index, _call, _definition = pending.pop(future)
                    results[index] = future.result()
                now = self._monotonic_now()
                expired = [
                    future for future, (_index, _call, definition) in pending.items()
                    if now >= batch_started + float(definition.timeout_seconds)
                ]
                for future in expired:
                    index, call, definition = pending.pop(future)
                    future.cancel()
                    results[index] = _tool_result(
                        call, definition, actor,
                        status=AssistantToolStatus.TIMED_OUT,
                        output=None,
                        error_code="TOOL_TIMEOUT",
                        started_at=batch_timestamp,
                        completed_at=_iso_now(self._utc_now),
                    )
        finally:
            # Registered adapters are trusted, bounded, read-only code and MUST
            # honor context.deadline_monotonic. Python cannot kill a bad thread;
            # the orchestrator stops waiting and never treats it as success.
            executor.shutdown(wait=False, cancel_futures=True)
        return tuple(results[index] for index in range(len(calls)))


NEWS_EVIDENCE_ITEM_SCHEMA: dict[str, object] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "evidence_id", "published_at", "received_at", "source", "headline",
        "summary", "category", "impact",
    ],
    "properties": {
        "evidence_id": {"type": "string", "minLength": 1, "maxLength": 128},
        "published_at": {"type": "string", "maxLength": 64},
        "received_at": {"type": "string", "maxLength": 64},
        "source": {"type": "string", "maxLength": 100},
        "headline": {"type": "string", "minLength": 1, "maxLength": 300},
        "summary": {"type": "string", "maxLength": 600},
        "category": {"type": "string", "maxLength": 80},
        "impact": {"type": "string", "maxLength": 600},
        "source_url": {
            "type": "string", "minLength": 1, "maxLength": 2_048,
            "pattern": "^https://[^\\s]+$",
        },
    },
}

NEWS_SEARCH_INPUT_SCHEMA: dict[str, object] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["query"],
    "properties": {
        "query": {
            "type": "string", "minLength": 1, "maxLength": 80,
            "description": (
                "One focused subject term for broad daily requests, or at most six "
                "terms for a specific event. Do not put dates in the query; use the "
                "time boundary fields. Prefer 黄金, 美联储, CPI, 就业, 美元, or 地缘政治."
            ),
        },
        "published_from": {
            "type": "string", "minLength": 10, "maxLength": 40,
            "description": "Inclusive publisher-time lower boundary in ISO 8601.",
        },
        "published_to": {
            "type": "string", "minLength": 10, "maxLength": 40,
            "description": "Inclusive publisher-time upper boundary in ISO 8601.",
        },
        "received_from": {"type": "string", "minLength": 10, "maxLength": 40},
        "evidence_id": {
            "type": "string", "minLength": 1, "maxLength": 128,
            "pattern": "^[A-Za-z0-9:._-]+$",
        },
        "source": {"type": "string", "minLength": 1, "maxLength": 80},
        "category": {"type": "string", "minLength": 1, "maxLength": 40},
    },
}

NEWS_SEARCH_OUTPUT_SCHEMA: dict[str, object] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["items", "provenance"],
    "properties": {
        "items": {
            "type": "array", "maxItems": 20, "items": NEWS_EVIDENCE_ITEM_SCHEMA,
        },
        "provenance": {
            "type": "object",
            "additionalProperties": False,
            "required": [
                "source_mode", "archive_complete", "retrieval_cutoff", "query",
                "retrieved_evidence_count", "canonical_evidence_ids",
            ],
            "properties": {
                "source_mode": {"type": "string", "enum": ["D1_ARCHIVE"]},
                "archive_complete": {"type": "boolean", "enum": [True]},
                "retrieval_cutoff": {"type": "string", "minLength": 20, "maxLength": 40},
                "query": {"type": "string", "maxLength": 80},
                "retrieved_evidence_count": {"type": "integer", "minimum": 0, "maximum": 20},
                "canonical_evidence_ids": {
                    "type": "array", "maxItems": 20, "uniqueItems": True,
                    "items": {"type": "string", "minLength": 1, "maxLength": 128},
                },
            },
        },
    },
}


NewsRetrievalCallback = Callable[[dict[str, object], float], dict[str, object]]


def _validate_optional_boundary(value: object) -> str:
    raw = str(value or "").strip()
    if _DATE_ONLY.fullmatch(raw):
        try:
            date.fromisoformat(raw)
        except ValueError as error:
            raise AssistantToolAdapterError("INVALID_NEWS_TIME_BOUNDARY") from error
        return raw
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as error:
        raise AssistantToolAdapterError("INVALID_NEWS_TIME_BOUNDARY") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise AssistantToolAdapterError("INVALID_NEWS_TIME_BOUNDARY")
    return parsed.astimezone(UTC).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z",
    )


def build_news_search_tool(
    retrieve: NewsRetrievalCallback,
    *,
    timeout_seconds: float = 10.0,
    max_result_tokens: int = 8_192,
) -> AssistantToolDefinition:
    """Adapt the existing shared `/news-search` service as one typed tool."""
    if not callable(retrieve):
        raise ValueError("Assistant news retrieval callback is required")

    def execute(
        arguments: dict[str, object],
        context: AssistantToolExecutionContext,
    ) -> dict[str, object]:
        query = " ".join(str(arguments["query"]).split())
        if not query or len(query.split(" ")) > 6:
            raise AssistantToolAdapterError("INVALID_NEWS_QUERY")
        if context.max_retrieved_evidence < 1:
            raise AssistantToolAdapterError("EVIDENCE_BUDGET_EXHAUSTED")
        params: dict[str, object] = {
            "q": query,
            "received_to": context.retrieval_cutoff,
            "page": 1,
            "limit": min(20, context.max_retrieved_evidence),
        }
        for name in (
            "published_from", "published_to", "received_from",
            "evidence_id", "source", "category",
        ):
            if name not in arguments:
                continue
            params[name] = (
                _validate_optional_boundary(arguments[name])
                if name.endswith("_from") or name.endswith("_to")
                else " ".join(str(arguments[name]).split())
            )
        remaining = context.remaining_seconds()
        if remaining <= 0:
            raise AssistantToolAdapterError("TOOL_TIMEOUT")
        payload = retrieve(params, remaining)
        if not isinstance(payload, dict):
            raise AssistantToolAdapterError("NEWS_RETRIEVAL_INVALID")
        rows = payload.get("items")
        retrieval = payload.get("retrieval")
        if (
            not isinstance(rows, list)
            or len(rows) > int(params["limit"])
            or any(not isinstance(row, dict) for row in rows)
            or not isinstance(retrieval, dict)
            or payload.get("source_mode") != "D1_ARCHIVE"
            or payload.get("archive_complete") is not True
        ):
            raise AssistantToolAdapterError("AUTHORITATIVE_NEWS_RETRIEVAL_REQUIRED")
        canonical_ids = [str(row.get("evidence_id") or "") for row in rows]
        if canonical_ids != list(retrieval.get("canonical_evidence_ids") or []):
            raise AssistantToolAdapterError("NEWS_RETRIEVAL_PROVENANCE_MISMATCH")
        try:
            returned_cutoff = _canonical_cutoff(str(retrieval.get("cutoff") or ""))
        except ValueError as error:
            raise AssistantToolAdapterError("NEWS_RETRIEVAL_PROVENANCE_MISMATCH") from error
        if returned_cutoff != context.retrieval_cutoff:
            raise AssistantToolAdapterError("NEWS_RETRIEVAL_PROVENANCE_MISMATCH")
        packet = build_news_evidence_packet([dict(row) for row in rows])
        if [str(row["evidence_id"]) for row in packet] != canonical_ids:
            raise AssistantToolAdapterError("NEWS_RETRIEVAL_PROVENANCE_MISMATCH")
        normalized_query = " ".join(str(payload.get("query") or query).split())[:80]

        def output_for(items: list[dict[str, object]]) -> dict[str, object]:
            evidence_ids = [str(item["evidence_id"]) for item in items]
            return {
                "items": items,
                "provenance": {
                    "source_mode": "D1_ARCHIVE",
                    "archive_complete": True,
                    "retrieval_cutoff": context.retrieval_cutoff,
                    "query": normalized_query,
                    "retrieved_evidence_count": len(canonical_ids),
                    "canonical_evidence_ids": evidence_ids,
                },
            }

        result = output_for(packet)
        while packet and _result_token_estimate(result) > context.result_token_budget:
            packet = packet[:-1]
            result = output_for(packet)
        if canonical_ids and not packet:
            raise AssistantToolAdapterError("TOOL_RESULT_BUDGET_EXCEEDED")
        return result

    definition = AssistantToolDefinition(
        name=NEWS_SEARCH_TOOL_NAME,
        version=NEWS_SEARCH_TOOL_VERSION,
        description=(
            "Search the authoritative point-in-time news archive. The server "
            "fixes the received-time cutoff and returns compact evidence only. "
            "Use this only when the user needs external news facts, not for "
            "conversation recall, greetings, identity, capabilities, or explaining "
            "the Assistant's previous behavior. Resolve relative dates into the "
            "published time fields and keep dates out of query terms."
        ),
        capability=AssistantToolCapability.NEWS_RETRIEVAL,
        input_schema=NEWS_SEARCH_INPUT_SCHEMA,
        output_schema=NEWS_SEARCH_OUTPUT_SCHEMA,
        timeout_seconds=timeout_seconds,
        max_result_tokens=max_result_tokens,
        provenance_fields=(
            "source_mode", "archive_complete", "retrieval_cutoff", "query",
            "retrieved_evidence_count", "canonical_evidence_ids",
        ),
        executor=execute,
    )
    _validate_definition(definition)
    return definition
