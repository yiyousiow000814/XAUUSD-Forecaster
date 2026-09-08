"""Versioned, bounded presentation events for the private Assistant."""

from __future__ import annotations

import copy
import json
import re
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum


ASSISTANT_EVENT_PROTOCOL_VERSION = "assistant.event.v1"
MAX_ASSISTANT_EVENTS_PER_TURN = 256
MAX_ASSISTANT_EVENT_PAYLOAD_BYTES = 16_384
MAX_ASSISTANT_ANSWER_DELTA_BYTES = 4_096
MAX_ASSISTANT_PRESENTATION_BYTES = 65_536


class AssistantEventType(StrEnum):
    CONVERSATION_STARTED = "conversation.started"
    REASONING_STARTED = "reasoning.started"
    TOOL_STARTED = "tool.started"
    TOOL_COMPLETED = "tool.completed"
    TOOL_FAILED = "tool.failed"
    RETRIEVAL_STARTED = "retrieval.started"
    RETRIEVAL_COMPLETED = "retrieval.completed"
    ANSWER_STARTED = "answer.started"
    ANSWER_DELTA = "answer.delta"
    CONTENT_BLOCK = "content.block"
    ANSWER_COMPLETED = "answer.completed"
    CONVERSATION_COMPLETED = "conversation.completed"
    ERROR = "error"
    CANCELLED = "cancelled"


class AssistantEventContractError(ValueError):
    def __init__(self, error_code: str, message: str) -> None:
        if not re.fullmatch(r"^[A-Z][A-Z0-9_]{2,63}$", error_code):
            raise ValueError("Assistant event error code is invalid")
        self.error_code = error_code
        super().__init__(message)


_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9:._@/-]{0,127}$")
_TOOL_NAME = re.compile(r"^[a-z][a-z0-9_]{1,54}_v[1-9][0-9]*$")
_VERSION = re.compile(r"^[A-Za-z][A-Za-z0-9._-]{0,63}$")
_ERROR_CODE = re.compile(r"^[A-Z][A-Z0-9_]{2,63}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_CANONICAL_TIME = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{3}Z$"
)
_REASONING_CLASSES = frozenset({"SIMPLE", "ANALYTICAL", "TOOL_HEAVY"})
_TOOL_FAILURE_STATES = frozenset({"FAILED", "REJECTED", "TIMED_OUT"})


def _canonical_timestamp(value: object) -> str:
    if not isinstance(value, str) or not _CANONICAL_TIME.fullmatch(value):
        raise AssistantEventContractError(
            "INVALID_EVENT_TIME", "Assistant event time must be canonical UTC milliseconds",
        )
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise AssistantEventContractError(
            "INVALID_EVENT_TIME", "Assistant event time is invalid",
        ) from error
    canonical = parsed.astimezone(UTC).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z",
    )
    if canonical != value:
        raise AssistantEventContractError(
            "INVALID_EVENT_TIME", "Assistant event time is not canonical",
        )
    return canonical


def _strict_identifier(
    value: object, field_name: str, *, payload: bool = False,
) -> str:
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise AssistantEventContractError(
            "INVALID_EVENT_PAYLOAD" if payload else "INVALID_EVENT_ENVELOPE",
            f"Assistant event {field_name} is invalid",
        )
    return value


def _strict_object(value: object, expected: frozenset[str]) -> dict[str, object]:
    if not isinstance(value, Mapping) or set(value) != expected:
        raise AssistantEventContractError(
            "INVALID_EVENT_PAYLOAD", "Assistant event payload shape is invalid",
        )
    try:
        result = copy.deepcopy(dict(value))
        encoded = json.dumps(
            result,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, RecursionError) as error:
        raise AssistantEventContractError(
            "INVALID_EVENT_PAYLOAD", "Assistant event payload is not strict JSON",
        ) from error
    if len(encoded) > MAX_ASSISTANT_EVENT_PAYLOAD_BYTES:
        raise AssistantEventContractError(
            "EVENT_PAYLOAD_BUDGET_EXCEEDED", "Assistant event payload is too large",
        )
    return result


def _strict_bounded_text(
    value: object, field_name: str, *, maximum: int, allow_empty: bool = False,
) -> str:
    if not isinstance(value, str):
        raise AssistantEventContractError(
            "INVALID_EVENT_PAYLOAD", f"Assistant event {field_name} is invalid",
        )
    normalized = value.strip() if field_name != "text" else value
    if (not allow_empty and not normalized) or len(normalized.encode("utf-8")) > maximum:
        raise AssistantEventContractError(
            "INVALID_EVENT_PAYLOAD", f"Assistant event {field_name} is invalid",
        )
    return normalized


def _strict_count(value: object, field_name: str, maximum: int) -> int:
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or not 0 <= value <= maximum
    ):
        raise AssistantEventContractError(
            "INVALID_EVENT_PAYLOAD", f"Assistant event {field_name} is invalid",
        )
    return value


def _validated_payload(
    event_type: AssistantEventType, value: object,
) -> dict[str, object]:
    if event_type in {
        AssistantEventType.CONVERSATION_STARTED,
        AssistantEventType.ANSWER_STARTED,
        AssistantEventType.CONVERSATION_COMPLETED,
    }:
        return _strict_object(value, frozenset())
    if event_type is AssistantEventType.REASONING_STARTED:
        payload = _strict_object(value, frozenset({"reasoning_class"}))
        if payload["reasoning_class"] not in _REASONING_CLASSES:
            raise AssistantEventContractError(
                "INVALID_EVENT_PAYLOAD", "Assistant reasoning class is invalid",
            )
        return payload
    if event_type is AssistantEventType.TOOL_STARTED:
        payload = _strict_object(
            value, frozenset({"call_id", "tool_name", "tool_version"}),
        )
        _strict_identifier(payload["call_id"], "call_id", payload=True)
        if not isinstance(payload["tool_name"], str) or not _TOOL_NAME.fullmatch(
            payload["tool_name"],
        ):
            raise AssistantEventContractError(
                "INVALID_EVENT_PAYLOAD", "Assistant event tool_name is invalid",
            )
        if not isinstance(payload["tool_version"], str) or not re.fullmatch(
            r"^v[1-9][0-9]*$", payload["tool_version"],
        ):
            raise AssistantEventContractError(
                "INVALID_EVENT_PAYLOAD", "Assistant event tool_version is invalid",
            )
        return payload
    if event_type is AssistantEventType.TOOL_COMPLETED:
        payload = _strict_object(value, frozenset({
            "call_id", "tool_name", "status", "result_sha256", "evidence_count",
        }))
        _validate_tool_identity(payload)
        if payload["status"] != "SUCCEEDED" or not isinstance(
            payload["result_sha256"], str,
        ) or not _SHA256.fullmatch(payload["result_sha256"]):
            raise AssistantEventContractError(
                "INVALID_EVENT_PAYLOAD", "Assistant tool completion is invalid",
            )
        _strict_count(payload["evidence_count"], "evidence_count", 100)
        return payload
    if event_type is AssistantEventType.TOOL_FAILED:
        payload = _strict_object(
            value, frozenset({"call_id", "tool_name", "status", "error_code"}),
        )
        _validate_tool_identity(payload)
        if payload["status"] not in _TOOL_FAILURE_STATES or not isinstance(
            payload["error_code"], str,
        ) or not _ERROR_CODE.fullmatch(payload["error_code"]):
            raise AssistantEventContractError(
                "INVALID_EVENT_PAYLOAD", "Assistant tool failure is invalid",
            )
        return payload
    if event_type is AssistantEventType.RETRIEVAL_STARTED:
        payload = _strict_object(value, frozenset({"operation_id", "tool_name"}))
        _strict_identifier(payload["operation_id"], "operation_id", payload=True)
        if not isinstance(payload["tool_name"], str) or not _TOOL_NAME.fullmatch(
            payload["tool_name"],
        ):
            raise AssistantEventContractError(
                "INVALID_EVENT_PAYLOAD", "Assistant retrieval tool_name is invalid",
            )
        return payload
    if event_type is AssistantEventType.RETRIEVAL_COMPLETED:
        payload = _strict_object(value, frozenset({
            "operation_id", "evidence_count", "source_mode", "result_sha256",
        }))
        _strict_identifier(payload["operation_id"], "operation_id", payload=True)
        _strict_count(payload["evidence_count"], "evidence_count", 100)
        if not isinstance(payload["source_mode"], str) or not _VERSION.fullmatch(
            payload["source_mode"],
        ) or not isinstance(payload["result_sha256"], str) or not _SHA256.fullmatch(
            payload["result_sha256"],
        ):
            raise AssistantEventContractError(
                "INVALID_EVENT_PAYLOAD", "Assistant retrieval completion is invalid",
            )
        return payload
    if event_type is AssistantEventType.ANSWER_DELTA:
        payload = _strict_object(value, frozenset({"text"}))
        _strict_bounded_text(
            payload["text"], "text", maximum=MAX_ASSISTANT_ANSWER_DELTA_BYTES,
        )
        return payload
    if event_type is AssistantEventType.CONTENT_BLOCK:
        payload = _strict_object(value, frozenset({
            "block_id", "block_type", "block_version", "content_sha256",
        }))
        _strict_identifier(payload["block_id"], "block_id", payload=True)
        for name in ("block_type", "block_version"):
            if not isinstance(payload[name], str) or not _VERSION.fullmatch(payload[name]):
                raise AssistantEventContractError(
                    "INVALID_EVENT_PAYLOAD", f"Assistant event {name} is invalid",
                )
        if not isinstance(payload["content_sha256"], str) or not _SHA256.fullmatch(
            payload["content_sha256"],
        ):
            raise AssistantEventContractError(
                "INVALID_EVENT_PAYLOAD", "Assistant content block hash is invalid",
            )
        return payload
    if event_type is AssistantEventType.ANSWER_COMPLETED:
        payload = _strict_object(
            value, frozenset({"content_sha256", "evidence_ids"}),
        )
        if not isinstance(payload["content_sha256"], str) or not _SHA256.fullmatch(
            payload["content_sha256"],
        ) or not isinstance(payload["evidence_ids"], list):
            raise AssistantEventContractError(
                "INVALID_EVENT_PAYLOAD", "Assistant answer completion is invalid",
            )
        evidence_ids = payload["evidence_ids"]
        if (
            len(evidence_ids) > 20
            or any(not isinstance(item, str) or not _IDENTIFIER.fullmatch(item)
                   for item in evidence_ids)
            or len(set(evidence_ids)) != len(evidence_ids)
        ):
            raise AssistantEventContractError(
                "INVALID_EVENT_PAYLOAD", "Assistant answer evidence is invalid",
            )
        return payload
    if event_type is AssistantEventType.ERROR:
        payload = _strict_object(
            value, frozenset({"code", "retryable", "recovery_key"}),
        )
        if not isinstance(payload["code"], str) or not _ERROR_CODE.fullmatch(
            payload["code"],
        ) or not isinstance(payload["retryable"], bool):
            raise AssistantEventContractError(
                "INVALID_EVENT_PAYLOAD", "Assistant public error is invalid",
            )
        if payload["recovery_key"] is not None:
            _strict_identifier(payload["recovery_key"], "recovery_key", payload=True)
        return payload
    if event_type is AssistantEventType.CANCELLED:
        payload = _strict_object(value, frozenset({"code"}))
        if not isinstance(payload["code"], str) or not _ERROR_CODE.fullmatch(
            payload["code"],
        ):
            raise AssistantEventContractError(
                "INVALID_EVENT_PAYLOAD", "Assistant cancellation is invalid",
            )
        return payload
    raise AssertionError(f"unhandled Assistant event type: {event_type}")


def _validate_tool_identity(payload: Mapping[str, object]) -> None:
    _strict_identifier(payload["call_id"], "call_id", payload=True)
    if not isinstance(payload["tool_name"], str) or not _TOOL_NAME.fullmatch(
        payload["tool_name"],
    ):
        raise AssistantEventContractError(
            "INVALID_EVENT_PAYLOAD", "Assistant event tool_name is invalid",
        )


@dataclass(frozen=True)
class AssistantEventEnvelope:
    event_id: str
    conversation_id: str
    user_turn_id: str
    message_id: str | None
    sequence: int
    type: AssistantEventType
    occurred_at: str
    _payload: dict[str, object] = field(repr=False, compare=False)
    protocol: str = ASSISTANT_EVENT_PROTOCOL_VERSION

    @property
    def payload(self) -> dict[str, object]:
        try:
            return copy.deepcopy(self._payload)
        except (TypeError, ValueError, RecursionError) as error:
            raise AssistantEventContractError(
                "INVALID_EVENT_PAYLOAD", "Assistant event payload cannot be copied",
            ) from error

    def receipt(self) -> dict[str, object]:
        payload = self.payload
        return {
            "protocol": self.protocol,
            "event_id": self.event_id,
            "conversation_id": self.conversation_id,
            "user_turn_id": self.user_turn_id,
            "message_id": self.message_id,
            "sequence": self.sequence,
            "type": self.type.value,
            "occurred_at": self.occurred_at,
            "payload": payload,
        }

    @classmethod
    def parse(cls, value: object) -> AssistantEventEnvelope:
        expected = {
            "protocol", "event_id", "conversation_id", "user_turn_id",
            "message_id", "sequence", "type", "occurred_at", "payload",
        }
        if not isinstance(value, Mapping) or set(value) != expected:
            raise AssistantEventContractError(
                "INVALID_EVENT_ENVELOPE", "Assistant event envelope shape is invalid",
            )
        if value["protocol"] != ASSISTANT_EVENT_PROTOCOL_VERSION:
            raise AssistantEventContractError(
                "UNSUPPORTED_EVENT_PROTOCOL", "Assistant event protocol is unsupported",
            )
        event_id = _strict_identifier(value["event_id"], "event_id")
        conversation_id = _strict_identifier(value["conversation_id"], "conversation_id")
        user_turn_id = _strict_identifier(value["user_turn_id"], "user_turn_id")
        message_id = value["message_id"]
        if message_id is not None:
            message_id = _strict_identifier(message_id, "message_id")
        sequence = value["sequence"]
        if (
            not isinstance(sequence, int)
            or isinstance(sequence, bool)
            or not 1 <= sequence <= MAX_ASSISTANT_EVENTS_PER_TURN
        ):
            raise AssistantEventContractError(
                "INVALID_EVENT_SEQUENCE", "Assistant event sequence is invalid",
            )
        try:
            event_type = AssistantEventType(value["type"])
        except (TypeError, ValueError) as error:
            raise AssistantEventContractError(
                "INVALID_EVENT_TYPE", "Assistant event type is invalid",
            ) from error
        payload = _validated_payload(event_type, value["payload"])
        if (event_type is AssistantEventType.ANSWER_COMPLETED) != (message_id is not None):
            raise AssistantEventContractError(
                "INVALID_EVENT_MESSAGE", "Only answer.completed names a canonical message",
            )
        return cls(
            event_id=event_id,
            conversation_id=conversation_id,
            user_turn_id=user_turn_id,
            message_id=message_id,
            sequence=sequence,
            type=event_type,
            occurred_at=_canonical_timestamp(value["occurred_at"]),
            _payload=payload,
        )


class _StreamPhase(StrEnum):
    NEW = "NEW"
    OPEN = "OPEN"
    ANSWERING = "ANSWERING"
    ANSWERED = "ANSWERED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class AssistantEventSequence:
    """Validate ordered events without treating them as canonical messages."""

    def __init__(self) -> None:
        self._events: list[AssistantEventEnvelope] = []
        self._phase = _StreamPhase.NEW
        self._event_ids: set[str] = set()
        self._tool_calls: dict[str, tuple[str, bool]] = {}
        self._retrievals: dict[str, bool] = {}
        self._reasoning_started = False
        self._presentation_bytes = 0

    @property
    def events(self) -> tuple[AssistantEventEnvelope, ...]:
        return tuple(
            AssistantEventEnvelope.parse(event.receipt()) for event in self._events
        )

    @property
    def terminal(self) -> bool:
        return self._phase in {
            _StreamPhase.COMPLETED, _StreamPhase.FAILED, _StreamPhase.CANCELLED,
        }

    def append(self, event: AssistantEventEnvelope | Mapping[str, object]) -> None:
        envelope = AssistantEventEnvelope.parse(
            event.receipt() if isinstance(event, AssistantEventEnvelope) else event
        )
        if len(self._events) >= MAX_ASSISTANT_EVENTS_PER_TURN:
            raise AssistantEventContractError(
                "EVENT_COUNT_BUDGET_EXCEEDED", "Assistant event count is exhausted",
            )
        if envelope.sequence != len(self._events) + 1:
            raise AssistantEventContractError(
                "INVALID_EVENT_SEQUENCE", "Assistant event sequence is not contiguous",
            )
        if envelope.event_id in self._event_ids:
            raise AssistantEventContractError(
                "DUPLICATE_EVENT_ID", "Assistant event ID is duplicated",
            )
        if self._events and (
            envelope.conversation_id != self._events[0].conversation_id
            or envelope.user_turn_id != self._events[0].user_turn_id
        ):
            raise AssistantEventContractError(
                "EVENT_OWNERSHIP_MISMATCH", "Assistant event stream identity changed",
            )
        self._advance(envelope)
        self._events.append(envelope)
        self._event_ids.add(envelope.event_id)

    def _advance(self, event: AssistantEventEnvelope) -> None:
        event_type = event.type
        payload = event.payload
        if self.terminal:
            raise AssistantEventContractError(
                "EVENT_AFTER_TERMINAL", "Assistant event follows a terminal event",
            )
        if self._phase is _StreamPhase.NEW:
            if event_type is not AssistantEventType.CONVERSATION_STARTED:
                raise AssistantEventContractError(
                    "INVALID_EVENT_ORDER", "Assistant stream must start with conversation.started",
                )
            self._phase = _StreamPhase.OPEN
            return
        if event_type is AssistantEventType.CONVERSATION_STARTED:
            raise AssistantEventContractError(
                "INVALID_EVENT_ORDER", "Assistant conversation.started is duplicated",
            )
        if event_type is AssistantEventType.ERROR:
            if self._phase is _StreamPhase.ANSWERED:
                raise AssistantEventContractError(
                    "INVALID_EVENT_ORDER", "A persisted answer cannot become a stream error",
                )
            self._phase = _StreamPhase.FAILED
            return
        if event_type is AssistantEventType.CANCELLED:
            if self._phase is _StreamPhase.ANSWERED:
                raise AssistantEventContractError(
                    "INVALID_EVENT_ORDER", "A persisted answer cannot be cancelled",
                )
            self._phase = _StreamPhase.CANCELLED
            return
        if self._phase is _StreamPhase.OPEN:
            self._advance_open(event_type, payload)
            return
        if self._phase is _StreamPhase.ANSWERING:
            if event_type is AssistantEventType.ANSWER_DELTA:
                self._add_presentation_bytes(len(str(payload["text"]).encode("utf-8")))
                return
            if event_type is AssistantEventType.CONTENT_BLOCK:
                self._add_presentation_bytes(len(json.dumps(
                    payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False,
                ).encode("utf-8")))
                return
            if event_type is AssistantEventType.ANSWER_COMPLETED:
                self._phase = _StreamPhase.ANSWERED
                return
            raise AssistantEventContractError(
                "INVALID_EVENT_ORDER", "Assistant answer event order is invalid",
            )
        if self._phase is _StreamPhase.ANSWERED:
            if event_type is not AssistantEventType.CONVERSATION_COMPLETED:
                raise AssistantEventContractError(
                    "INVALID_EVENT_ORDER", "Assistant answer must end the conversation stream",
                )
            self._phase = _StreamPhase.COMPLETED
            return
        raise AssertionError(f"unhandled Assistant event phase: {self._phase}")

    def _advance_open(
        self, event_type: AssistantEventType, payload: Mapping[str, object],
    ) -> None:
        if event_type is AssistantEventType.REASONING_STARTED:
            if self._reasoning_started:
                raise AssistantEventContractError(
                    "INVALID_EVENT_ORDER", "Assistant reasoning.started is duplicated",
                )
            self._reasoning_started = True
            return
        if event_type is AssistantEventType.TOOL_STARTED:
            call_id = str(payload["call_id"])
            if call_id in self._tool_calls:
                raise AssistantEventContractError(
                    "INVALID_EVENT_ORDER", "Assistant tool call is duplicated",
                )
            self._tool_calls[call_id] = (str(payload["tool_name"]), False)
            return
        if event_type in {AssistantEventType.TOOL_COMPLETED, AssistantEventType.TOOL_FAILED}:
            call_id = str(payload["call_id"])
            expected = self._tool_calls.get(call_id)
            if expected is None or expected[0] != payload["tool_name"] or expected[1]:
                raise AssistantEventContractError(
                    "INVALID_EVENT_ORDER", "Assistant tool completion has no active call",
                )
            self._tool_calls[call_id] = (expected[0], True)
            return
        if event_type is AssistantEventType.RETRIEVAL_STARTED:
            operation_id = str(payload["operation_id"])
            if operation_id in self._retrievals:
                raise AssistantEventContractError(
                    "INVALID_EVENT_ORDER", "Assistant retrieval is duplicated",
                )
            self._retrievals[operation_id] = False
            return
        if event_type is AssistantEventType.RETRIEVAL_COMPLETED:
            operation_id = str(payload["operation_id"])
            if self._retrievals.get(operation_id) is not False:
                raise AssistantEventContractError(
                    "INVALID_EVENT_ORDER", "Assistant retrieval has no active operation",
                )
            self._retrievals[operation_id] = True
            return
        if event_type is AssistantEventType.ANSWER_STARTED:
            if (
                any(not completed for _name, completed in self._tool_calls.values())
                or any(not completed for completed in self._retrievals.values())
            ):
                raise AssistantEventContractError(
                    "INVALID_EVENT_ORDER", "Assistant answer started before progress completed",
                )
            self._phase = _StreamPhase.ANSWERING
            return
        raise AssistantEventContractError(
            "INVALID_EVENT_ORDER", "Assistant progress event order is invalid",
        )

    def _add_presentation_bytes(self, amount: int) -> None:
        self._presentation_bytes += amount
        if self._presentation_bytes > MAX_ASSISTANT_PRESENTATION_BYTES:
            raise AssistantEventContractError(
                "PRESENTATION_BUDGET_EXCEEDED", "Assistant presentation stream is too large",
            )


class AssistantEventBuilder:
    def __init__(
        self,
        conversation_id: str,
        user_turn_id: str,
        *,
        event_id_factory: Callable[[], str] | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.conversation_id = _strict_identifier(conversation_id, "conversation_id")
        self.user_turn_id = _strict_identifier(user_turn_id, "user_turn_id")
        self._event_id_factory = event_id_factory or (lambda: str(uuid.uuid4()))
        self._now = now or (lambda: datetime.now(UTC))
        self._sequence = AssistantEventSequence()

    @property
    def events(self) -> tuple[AssistantEventEnvelope, ...]:
        return self._sequence.events

    def emit(
        self,
        event_type: AssistantEventType | str,
        payload: Mapping[str, object],
        *,
        message_id: str | None = None,
    ) -> AssistantEventEnvelope:
        try:
            normalized_type = AssistantEventType(event_type)
        except (TypeError, ValueError) as error:
            raise AssistantEventContractError(
                "INVALID_EVENT_TYPE", "Assistant event type is invalid",
            ) from error
        instant = self._now()
        if not isinstance(instant, datetime):
            raise AssistantEventContractError(
                "INVALID_EVENT_TIME", "Assistant event clock is invalid",
            )
        envelope = AssistantEventEnvelope.parse({
            "protocol": ASSISTANT_EVENT_PROTOCOL_VERSION,
            "event_id": self._event_id_factory(),
            "conversation_id": self.conversation_id,
            "user_turn_id": self.user_turn_id,
            "message_id": message_id,
            "sequence": len(self.events) + 1,
            "type": normalized_type.value,
            "occurred_at": _canonical_timestamp(instant.isoformat(
                timespec="milliseconds",
            ).replace("+00:00", "Z")),
            "payload": dict(payload),
        })
        self._sequence.append(envelope)
        return envelope


def encode_assistant_sse(event: AssistantEventEnvelope | Mapping[str, object]) -> str:
    envelope = AssistantEventEnvelope.parse(
        event.receipt() if isinstance(event, AssistantEventEnvelope) else event
    )
    data = json.dumps(
        envelope.receipt(),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return f"id: {envelope.sequence}\nevent: {envelope.type.value}\ndata: {data}\n\n"
