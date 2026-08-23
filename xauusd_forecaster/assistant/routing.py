"""Deterministic Assistant reasoning policy and model-profile routing."""

from __future__ import annotations

import json
import os
import re
import unicodedata
from dataclasses import dataclass
from enum import StrEnum
from xauusd_forecaster.news.annotation.product import DEFAULT_GEMMA_MODEL


ASSISTANT_ROUTING_POLICY_VERSION = "assistant-routing-v2"
ASSISTANT_MODEL_PROFILES_ENV = "ASSISTANT_MODEL_PROFILES"
GOOGLE_GENERATIVE_LANGUAGE = "GOOGLE_GENERATIVE_LANGUAGE"
OLLAMA_LOCAL = "OLLAMA_LOCAL"
INSTALLED_ASSISTANT_PROVIDERS = frozenset({
    GOOGLE_GENERATIVE_LANGUAGE,
    OLLAMA_LOCAL,
})
ASSISTANT_REQUEST_ENVELOPE_RESERVE = 4_096
MAX_ASSISTANT_MODEL_CANDIDATES = 8


class AssistantTaskType(StrEnum):
    ASSISTANT_CHAT = "ASSISTANT_CHAT"
    NEWS_QA = "NEWS_QA"
    CONVERSATION_TITLE = "CONVERSATION_TITLE"
    CONTEXT_COMPACTION = "CONTEXT_COMPACTION"


class ReasoningClass(StrEnum):
    SIMPLE = "SIMPLE"
    ANALYTICAL = "ANALYTICAL"
    TOOL_HEAVY = "TOOL_HEAVY"


class AssistantToolPolicy(StrEnum):
    DIRECT = "DIRECT"
    AUTO = "AUTO"


class ThinkingLevel(StrEnum):
    MINIMAL = "MINIMAL"
    HIGH = "HIGH"


class ModelRequirement(StrEnum):
    SMALL_PREFERRED = "SMALL_PREFERRED"
    LARGE_REQUIRED = "LARGE_REQUIRED"


class ModelCapacityClass(StrEnum):
    SMALL = "SMALL"
    LARGE = "LARGE"


class AssistantModelRoutingUnavailable(RuntimeError):
    """No enabled model profile satisfies the task contract."""


@dataclass(frozen=True)
class ModelProfile:
    profile_id: str
    model_id: str
    provider: str
    context_limit: int
    supports_thinking: bool
    supports_function_calling: bool
    supports_streaming: bool
    capacity_class: ModelCapacityClass
    enabled: bool = True


@dataclass(frozen=True)
class AssistantRoutePlan:
    policy_version: str
    task_type: AssistantTaskType
    reasoning_class: ReasoningClass
    thinking_level: ThinkingLevel
    model_requirement: ModelRequirement
    estimated_input_tokens: int
    reserved_output_tokens: int
    planned_tool_calls: int
    candidate_profiles: tuple[ModelProfile, ...]

    @property
    def required_context_tokens(self) -> int:
        return self.estimated_input_tokens + self.reserved_output_tokens


DEFAULT_ASSISTANT_MODEL_PROFILES = (
    ModelProfile(
        profile_id="assistant-gemma-large-v1",
        model_id=DEFAULT_GEMMA_MODEL,
        provider=GOOGLE_GENERATIVE_LANGUAGE,
        context_limit=32_768,
        supports_thinking=True,
        supports_function_calling=True,
        supports_streaming=False,
        capacity_class=ModelCapacityClass.LARGE,
    ),
)


_PROFILE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,95}$")
_MODEL_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,159}$")
_PROVIDER = re.compile(r"^[A-Z][A-Z0-9_]{2,63}$")
_ANALYTICAL_MARKERS = (
    "为什么", "为何", "原因", "因果", "影响", "比较", "对比", "冲突",
    "矛盾", "反常", "多个时期", "多期", "why", "cause", "causal",
    "compare", "versus", "conflict", "contradict",
)
_CONVERSATION_REFERENCE_MARKERS = (
    "上一句", "上句话", "刚才说", "刚刚说", "刚才回答", "刚刚回答",
    "刚才让你", "刚刚让你", "之前让你", "前面让你", "我刚才说的",
    "我刚刚说的", "我之前说的", "我前面说的", "请记住", "让你记住的",
    "你记住的", "你还记得", "重复你", "重说", "previous message",
    "previous answer", "repeat that", "what did i just", "what did i ask",
    "what did i tell", "do you remember", "remember what i",
)
_SIMPLE_MARKERS = (
    "多少", "几条", "列出", "有哪些", "最新", "何时", "什么时候",
    "你是谁", "能做什么", "可以做什么",
    "count", "list", "show", "latest", "when", "how many",
    "who are you", "what can you do", "repeat",
) + _CONVERSATION_REFERENCE_MARKERS

_DIRECT_CHAT_EXACT = frozenset({
    "你好", "您好", "嗨", "谢谢", "多谢", "你是谁", "你能做什么",
    "你可以做什么", "你会做什么", "介绍一下你自己", "hello", "hi",
    "thanks", "thank you", "who are you", "what can you do",
})
_DIRECT_CHAT_MARKERS = _CONVERSATION_REFERENCE_MARKERS + (
    "你是谁", "你能做什么", "你可以做什么", "你会做什么",
    "介绍一下你自己", "who are you", "what can you do",
    "为什么你要做新闻检索", "为什么要做新闻检索", "为什么你要搜索",
    "为什么要搜索", "你attach的", "你 attach 的", "你附加的", "你附上的",
)


def _compact_dialogue_text(value: str) -> str:
    return "".join(
        character for character in value
        if not character.isspace()
        and not unicodedata.category(character).startswith("P")
    )


def classify_assistant_tool_policy(user_text: str) -> AssistantToolPolicy:
    """Disable external tools only for high-confidence conversational turns."""
    normalized = unicodedata.normalize("NFKC", str(user_text)).casefold().strip()
    compact = _compact_dialogue_text(normalized)
    exact = {_compact_dialogue_text(item) for item in _DIRECT_CHAT_EXACT}
    if compact in exact:
        return AssistantToolPolicy.DIRECT
    if len(normalized) <= 160 and any(
        marker in normalized for marker in _DIRECT_CHAT_MARKERS
    ):
        return AssistantToolPolicy.DIRECT
    return AssistantToolPolicy.AUTO


def _strict_bool(value: object, field: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"Assistant model profile {field} must be boolean")
    return value


def _strict_integer(
    value: object,
    field: str,
    *,
    minimum: int,
    maximum: int,
) -> int:
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or not minimum <= value <= maximum
    ):
        raise ValueError(f"Assistant routing {field} is invalid")
    return value


def _parse_profile(value: object) -> ModelProfile:
    if not isinstance(value, dict):
        raise ValueError("each Assistant model profile must be an object")
    allowed = {
        "profile_id", "model_id", "provider", "context_limit",
        "supports_thinking", "supports_function_calling", "supports_streaming",
        "capacity_class", "enabled",
    }
    unknown = set(value) - allowed
    if unknown:
        raise ValueError(
            "Assistant model profile contains unsupported fields: "
            + ", ".join(sorted(unknown))
        )
    profile_id = str(value.get("profile_id") or "").strip()
    model_id = str(value.get("model_id") or "").strip()
    provider = str(value.get("provider") or "").strip().upper()
    if not _PROFILE_ID.fullmatch(profile_id):
        raise ValueError("Assistant model profile_id is invalid")
    if not _MODEL_ID.fullmatch(model_id):
        raise ValueError("Assistant model model_id is invalid")
    if not _PROVIDER.fullmatch(provider):
        raise ValueError("Assistant model provider is invalid")
    if provider not in INSTALLED_ASSISTANT_PROVIDERS:
        raise ValueError("Assistant model provider has no installed gateway")
    context_limit = _strict_integer(
        value.get("context_limit"), "context_limit",
        minimum=1_024, maximum=1_000_000,
    )
    try:
        capacity_class = ModelCapacityClass(
            str(value.get("capacity_class") or "").strip().upper()
        )
    except ValueError as error:
        raise ValueError("Assistant model capacity_class is invalid") from error
    enabled = value.get("enabled", True)
    return ModelProfile(
        profile_id=profile_id,
        model_id=model_id,
        provider=provider,
        context_limit=context_limit,
        supports_thinking=_strict_bool(
            value.get("supports_thinking"), "supports_thinking",
        ),
        supports_function_calling=_strict_bool(
            value.get("supports_function_calling"), "supports_function_calling",
        ),
        supports_streaming=_strict_bool(
            value.get("supports_streaming"), "supports_streaming",
        ),
        capacity_class=capacity_class,
        enabled=_strict_bool(enabled, "enabled"),
    )


def _validated_profiles(
    profiles: tuple[ModelProfile, ...],
) -> tuple[ModelProfile, ...]:
    if not profiles or len(profiles) > 32:
        raise ValueError("Assistant model profile count is invalid")
    for profile in profiles:
        if not isinstance(profile, ModelProfile):
            raise ValueError("Assistant model profile has an invalid type")
        if not _PROFILE_ID.fullmatch(profile.profile_id):
            raise ValueError("Assistant model profile_id is invalid")
        if not _MODEL_ID.fullmatch(profile.model_id):
            raise ValueError("Assistant model model_id is invalid")
        if profile.provider not in INSTALLED_ASSISTANT_PROVIDERS:
            raise ValueError("Assistant model provider has no installed gateway")
        _strict_integer(
            profile.context_limit, "context_limit",
            minimum=1_024, maximum=1_000_000,
        )
        _strict_bool(profile.supports_thinking, "supports_thinking")
        _strict_bool(profile.supports_function_calling, "supports_function_calling")
        _strict_bool(profile.supports_streaming, "supports_streaming")
        _strict_bool(profile.enabled, "enabled")
        if not isinstance(profile.capacity_class, ModelCapacityClass):
            raise ValueError("Assistant model capacity_class is invalid")
    profile_ids = [profile.profile_id for profile in profiles]
    if len(set(profile_ids)) != len(profile_ids):
        raise ValueError("Assistant model profiles contain duplicate profile_id")
    enabled_model_ids = [profile.model_id for profile in profiles if profile.enabled]
    if len(set(enabled_model_ids)) != len(enabled_model_ids):
        raise ValueError("enabled Assistant profiles contain duplicate model_id")
    return profiles


def configured_assistant_model_profiles(
    raw_profiles: str | None = None,
) -> tuple[ModelProfile, ...]:
    """Load operational model profiles without embedding them in conversation state."""
    raw = (
        os.environ.get(ASSISTANT_MODEL_PROFILES_ENV, "")
        if raw_profiles is None else raw_profiles
    )
    if not raw.strip():
        return DEFAULT_ASSISTANT_MODEL_PROFILES
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as error:
        raise ValueError("ASSISTANT_MODEL_PROFILES is not valid JSON") from error
    if not isinstance(parsed, list) or not parsed:
        raise ValueError("ASSISTANT_MODEL_PROFILES must be a non-empty list")
    profiles = _validated_profiles(tuple(_parse_profile(value) for value in parsed))
    if not any(profile.enabled for profile in profiles):
        raise ValueError("Assistant model profiles enable no models")
    return profiles


def conservative_assistant_token_estimate(value: object) -> int:
    serialized = (
        value if isinstance(value, str)
        else json.dumps(
            value, ensure_ascii=False, separators=(",", ":"), allow_nan=False,
        )
    )
    return max(
        1,
        len(serialized.encode("utf-8")) + ASSISTANT_REQUEST_ENVELOPE_RESERVE,
    )


def classify_assistant_reasoning(
    task_type: AssistantTaskType | str,
    *,
    user_text: str = "",
    planned_tool_calls: int = 0,
) -> ReasoningClass:
    """Classify effort locally; this function never invokes a model."""
    try:
        task = AssistantTaskType(str(task_type))
    except ValueError as error:
        raise ValueError(f"unsupported Assistant task type: {task_type}") from error
    tool_calls = _strict_integer(
        planned_tool_calls, "planned_tool_calls", minimum=0, maximum=64,
    )
    if tool_calls > 1:
        return ReasoningClass.TOOL_HEAVY
    if task in {
        AssistantTaskType.CONVERSATION_TITLE,
        AssistantTaskType.CONTEXT_COMPACTION,
    }:
        return ReasoningClass.SIMPLE
    normalized = unicodedata.normalize("NFKC", user_text).casefold()
    if any(marker in normalized for marker in _ANALYTICAL_MARKERS):
        return ReasoningClass.ANALYTICAL
    if any(marker in normalized for marker in _SIMPLE_MARKERS):
        return ReasoningClass.SIMPLE
    return ReasoningClass.ANALYTICAL


def plan_assistant_route(
    task_type: AssistantTaskType | str,
    *,
    estimated_input_tokens: int,
    reserved_output_tokens: int,
    user_text: str = "",
    planned_tool_calls: int = 0,
    profiles: tuple[ModelProfile, ...] | None = None,
) -> AssistantRoutePlan:
    try:
        task = AssistantTaskType(str(task_type))
    except ValueError as error:
        raise ValueError(f"unsupported Assistant task type: {task_type}") from error
    input_tokens = _strict_integer(
        estimated_input_tokens, "estimated_input_tokens",
        minimum=1, maximum=1_000_000,
    )
    output_tokens = _strict_integer(
        reserved_output_tokens, "reserved_output_tokens",
        minimum=1, maximum=1_000_000,
    )
    tool_calls = _strict_integer(
        planned_tool_calls, "planned_tool_calls", minimum=0, maximum=64,
    )
    reasoning = classify_assistant_reasoning(
        task, user_text=user_text, planned_tool_calls=tool_calls,
    )
    thinking = (
        ThinkingLevel.MINIMAL
        if reasoning is ReasoningClass.SIMPLE else ThinkingLevel.HIGH
    )
    requirement = (
        ModelRequirement.SMALL_PREFERRED
        if reasoning is ReasoningClass.SIMPLE else ModelRequirement.LARGE_REQUIRED
    )
    configured_profiles = (
        configured_assistant_model_profiles() if profiles is None else profiles
    )
    configured_profiles = _validated_profiles(configured_profiles)
    available = tuple(
        profile for profile in configured_profiles
        if profile.enabled and profile.context_limit >= input_tokens + output_tokens
    )
    if tool_calls > 0:
        available = tuple(
            profile for profile in available if profile.supports_function_calling
        )
    if thinking is ThinkingLevel.HIGH:
        available = tuple(profile for profile in available if profile.supports_thinking)
    if requirement is ModelRequirement.LARGE_REQUIRED:
        candidates = tuple(
            profile for profile in available
            if profile.capacity_class is ModelCapacityClass.LARGE
        )
    else:
        candidates = tuple(
            profile for profile in available
            if profile.capacity_class is ModelCapacityClass.SMALL
        ) + tuple(
            profile for profile in available
            if profile.capacity_class is ModelCapacityClass.LARGE
        )
    candidates = candidates[:MAX_ASSISTANT_MODEL_CANDIDATES]
    if not candidates:
        raise AssistantModelRoutingUnavailable(
            f"no model profile satisfies {task.value}/{requirement.value} "
            f"for {input_tokens + output_tokens} tokens"
        )
    return AssistantRoutePlan(
        policy_version=ASSISTANT_ROUTING_POLICY_VERSION,
        task_type=task,
        reasoning_class=reasoning,
        thinking_level=thinking,
        model_requirement=requirement,
        estimated_input_tokens=input_tokens,
        reserved_output_tokens=output_tokens,
        planned_tool_calls=tool_calls,
        candidate_profiles=candidates,
    )


def provider_thinking_level(
    plan: AssistantRoutePlan,
    profile: ModelProfile,
) -> str | None:
    if profile not in plan.candidate_profiles:
        raise ValueError("selected model profile is outside the route plan")
    if not profile.supports_thinking:
        return None
    return plan.thinking_level.value.lower()


def routing_provenance(
    plan: AssistantRoutePlan,
    profile: ModelProfile,
) -> dict[str, object]:
    if profile not in plan.candidate_profiles:
        raise ValueError("selected model profile is outside the route plan")
    return {
        "policy_version": plan.policy_version,
        "task_type": plan.task_type.value,
        "reasoning_class": plan.reasoning_class.value,
        "thinking_level": plan.thinking_level.value,
        "provider_thinking_level": provider_thinking_level(plan, profile),
        "model_requirement": plan.model_requirement.value,
        "estimated_input_tokens": plan.estimated_input_tokens,
        "reserved_output_tokens": plan.reserved_output_tokens,
        "required_context_tokens": plan.required_context_tokens,
        "planned_tool_calls": plan.planned_tool_calls,
        "candidate_profile_ids": [
            candidate.profile_id for candidate in plan.candidate_profiles
        ],
        "selected_profile_id": profile.profile_id,
        "selected_model_id": profile.model_id,
        "provider": profile.provider,
        "capacity_class": profile.capacity_class.value,
        "context_limit": profile.context_limit,
        "supports_thinking": profile.supports_thinking,
        "supports_function_calling": profile.supports_function_calling,
        "supports_streaming": profile.supports_streaming,
    }


def apply_provider_thinking_level(
    payload: dict[str, object],
    thinking_level: str | None,
) -> dict[str, object]:
    """Return a request copy with a validated GenerateContent thinking level."""
    if thinking_level is None:
        return payload
    normalized = str(thinking_level).strip().lower()
    if normalized not in {"minimal", "high"}:
        raise ValueError("Assistant provider thinking level is invalid")
    generation = payload.get("generationConfig")
    if not isinstance(generation, dict):
        raise ValueError("Assistant model payload lacks generationConfig")
    return {
        **payload,
        "generationConfig": {
            **generation,
            "thinkingConfig": {"thinkingLevel": normalized},
        },
    }
