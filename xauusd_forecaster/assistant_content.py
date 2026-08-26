"""Compatibility shim for xauusd_forecaster.assistant.content."""

from xauusd_forecaster.assistant.content import (
    ASSISTANT_CONTENT_PROTOCOL_VERSION,
    AssistantContentContractError,
    MAX_ASSISTANT_CONTENT_BLOCKS,
    MAX_ASSISTANT_CONTENT_BYTES,
    MAX_ASSISTANT_NEWS_CARDS,
    build_assistant_content_document,
    validate_assistant_content_document,
)

__all__ = [
    "ASSISTANT_CONTENT_PROTOCOL_VERSION",
    "AssistantContentContractError",
    "MAX_ASSISTANT_CONTENT_BLOCKS",
    "MAX_ASSISTANT_CONTENT_BYTES",
    "MAX_ASSISTANT_NEWS_CARDS",
    "build_assistant_content_document",
    "validate_assistant_content_document",
]
