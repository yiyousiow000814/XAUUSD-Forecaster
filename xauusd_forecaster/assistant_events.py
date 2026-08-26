"""Compatibility shim for xauusd_forecaster.assistant.events."""

from xauusd_forecaster.assistant.events import (
    ASSISTANT_EVENT_PROTOCOL_VERSION,
    AssistantEventBuilder,
    AssistantEventContractError,
    AssistantEventEnvelope,
    AssistantEventSequence,
    AssistantEventType,
    MAX_ASSISTANT_ANSWER_DELTA_BYTES,
    MAX_ASSISTANT_EVENTS_PER_TURN,
    MAX_ASSISTANT_EVENT_PAYLOAD_BYTES,
    MAX_ASSISTANT_PRESENTATION_BYTES,
    encode_assistant_sse,
)

__all__ = [
    "ASSISTANT_EVENT_PROTOCOL_VERSION",
    "AssistantEventBuilder",
    "AssistantEventContractError",
    "AssistantEventEnvelope",
    "AssistantEventSequence",
    "AssistantEventType",
    "MAX_ASSISTANT_ANSWER_DELTA_BYTES",
    "MAX_ASSISTANT_EVENTS_PER_TURN",
    "MAX_ASSISTANT_EVENT_PAYLOAD_BYTES",
    "MAX_ASSISTANT_PRESENTATION_BYTES",
    "encode_assistant_sse",
]
