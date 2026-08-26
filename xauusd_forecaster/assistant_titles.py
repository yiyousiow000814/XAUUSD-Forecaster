"""Compatibility shim for xauusd_forecaster.assistant.titles."""

from xauusd_forecaster.assistant.titles import (
    ASSISTANT_TITLE_MAX_OUTPUT_TOKENS,
    ASSISTANT_TITLE_PROMPT_VERSION,
    MAX_TITLE_INPUT_CHARACTERS,
    MAX_TITLE_RESPONSE_CHARACTERS,
    generate_assistant_title,
)

__all__ = [
    "ASSISTANT_TITLE_MAX_OUTPUT_TOKENS",
    "ASSISTANT_TITLE_PROMPT_VERSION",
    "MAX_TITLE_INPUT_CHARACTERS",
    "MAX_TITLE_RESPONSE_CHARACTERS",
    "generate_assistant_title",
]
