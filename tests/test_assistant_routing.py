from __future__ import annotations

import json

import pytest

from xauusd_forecaster.assistant_routing import (
    ASSISTANT_ROUTING_POLICY_VERSION,
    GOOGLE_GENERATIVE_LANGUAGE,
    OLLAMA_LOCAL,
    AssistantModelRoutingUnavailable,
    AssistantTaskType,
    AssistantToolPolicy,
    ModelCapacityClass,
    ModelProfile,
    ModelRequirement,
    ReasoningClass,
    ThinkingLevel,
    apply_provider_thinking_level,
    classify_assistant_reasoning,
    classify_assistant_tool_policy,
    configured_assistant_model_profiles,
    plan_assistant_route,
    routing_provenance,
)


@pytest.mark.parametrize(
    ("text", "expected"),
    (
        ("你是谁", AssistantToolPolicy.DIRECT),
        ("你能做什么？", AssistantToolPolicy.DIRECT),
        ("你能做什么？请分成三点，简洁说明。", AssistantToolPolicy.DIRECT),
        ("介绍一下你自己，重点说明当前能力。", AssistantToolPolicy.DIRECT),
        ("What can you do? Answer in three bullets.", AssistantToolPolicy.DIRECT),
        ("你上一句说什么", AssistantToolPolicy.DIRECT),
        ("我刚才让你记住的测试短语是什么？", AssistantToolPolicy.DIRECT),
        ("请记住测试短语蓝鲸-73", AssistantToolPolicy.DIRECT),
        ("What did I just ask you to remember?", AssistantToolPolicy.DIRECT),
        ("为什么你要做新闻检索？", AssistantToolPolicy.DIRECT),
        ("昨天有什么影响黄金的新闻？", AssistantToolPolicy.AUTO),
        ("为什么 CPI 后黄金反常上涨？", AssistantToolPolicy.AUTO),
    ),
)
def test_tool_policy_separates_conversation_from_external_evidence(
    text: str,
    expected: AssistantToolPolicy,
) -> None:
    assert classify_assistant_tool_policy(text) is expected


def _profile(
    profile_id: str,
    capacity_class: ModelCapacityClass,
    *,
    context_limit: int = 32_768,
    thinking: bool = True,
    tools: bool = False,
) -> ModelProfile:
    return ModelProfile(
        profile_id=profile_id,
        model_id=f"model-{profile_id}",
        provider=GOOGLE_GENERATIVE_LANGUAGE,
        context_limit=context_limit,
        supports_thinking=thinking,
        supports_function_calling=tools,
        supports_streaming=False,
        capacity_class=capacity_class,
    )


SMALL = _profile("small", ModelCapacityClass.SMALL)
LARGE = _profile("large", ModelCapacityClass.LARGE)


@pytest.mark.parametrize(
    ("text", "tool_calls", "expected"),
    (
        ("列出最新三条美联储新闻", 1, ReasoningClass.SIMPLE),
        ("我刚才让你记住的测试短语是什么？", 0, ReasoningClass.SIMPLE),
        ("What did I just ask you to remember?", 0, ReasoningClass.SIMPLE),
        ("Why did gold rise after CPI?", 1, ReasoningClass.ANALYTICAL),
        ("比较昨天和今天，并解释证据冲突", 1, ReasoningClass.ANALYTICAL),
        ("查新闻、价格和日历", 3, ReasoningClass.TOOL_HEAVY),
        ("黄金怎么了", 1, ReasoningClass.ANALYTICAL),
    ),
)
def test_reasoning_policy_is_deterministic_and_request_local(
    text: str,
    tool_calls: int,
    expected: ReasoningClass,
) -> None:
    assert classify_assistant_reasoning(
        AssistantTaskType.ASSISTANT_CHAT,
        user_text=text,
        planned_tool_calls=tool_calls,
    ) is expected


def test_simple_route_prefers_small_and_declares_only_bounded_fallbacks() -> None:
    plan = plan_assistant_route(
        AssistantTaskType.NEWS_QA,
        estimated_input_tokens=1_000,
        reserved_output_tokens=500,
        user_text="列出最新新闻",
        planned_tool_calls=0,
        profiles=(LARGE, SMALL),
    )
    assert plan.reasoning_class is ReasoningClass.SIMPLE
    assert plan.thinking_level is ThinkingLevel.MINIMAL
    assert plan.model_requirement is ModelRequirement.SMALL_PREFERRED
    assert [profile.profile_id for profile in plan.candidate_profiles] == [
        "small", "large",
    ]
    selected = routing_provenance(plan, LARGE)
    assert selected["candidate_profile_ids"] == ["small", "large"]
    assert selected["selected_profile_id"] == "large"
    assert selected["policy_version"] == ASSISTANT_ROUTING_POLICY_VERSION
    assert selected["provider_thinking_level"] == "minimal"
    assert selected["supports_thinking"] is True


def test_candidate_fallback_list_is_bounded_and_preserves_declared_order() -> None:
    small_profiles = tuple(
        _profile(f"small-{index}", ModelCapacityClass.SMALL)
        for index in range(10)
    )
    plan = plan_assistant_route(
        AssistantTaskType.CONVERSATION_TITLE,
        estimated_input_tokens=1_000,
        reserved_output_tokens=80,
        profiles=small_profiles + (LARGE,),
    )

    assert [profile.profile_id for profile in plan.candidate_profiles] == [
        f"small-{index}" for index in range(8)
    ]


def test_local_assistant_profiles_use_the_same_fail_closed_route_contract() -> None:
    local = ModelProfile(
        profile_id="assistant-qwen-local",
        model_id="qwen3.5:9b-q4_K_M",
        provider=OLLAMA_LOCAL,
        context_limit=32_768,
        supports_thinking=True,
        supports_function_calling=True,
        supports_streaming=False,
        capacity_class=ModelCapacityClass.LARGE,
    )
    plan = plan_assistant_route(
        AssistantTaskType.ASSISTANT_CHAT,
        estimated_input_tokens=10_000,
        reserved_output_tokens=2_048,
        user_text="为什么黄金上涨？",
        planned_tool_calls=1,
        profiles=(local,),
    )

    assert plan.candidate_profiles == (local,)
    assert routing_provenance(plan, local)["provider"] == OLLAMA_LOCAL


def test_analytical_route_requires_thinking_large_and_never_downgrades() -> None:
    non_thinking_large = _profile(
        "large-no-thinking", ModelCapacityClass.LARGE, thinking=False,
    )
    plan = plan_assistant_route(
        AssistantTaskType.NEWS_QA,
        estimated_input_tokens=1_000,
        reserved_output_tokens=1_000,
        user_text="为什么 CPI 后黄金反常上涨？",
        profiles=(SMALL, non_thinking_large, LARGE),
    )

    assert plan.reasoning_class is ReasoningClass.ANALYTICAL
    assert plan.thinking_level is ThinkingLevel.HIGH
    assert plan.model_requirement is ModelRequirement.LARGE_REQUIRED
    assert plan.candidate_profiles == (LARGE,)


def test_context_and_tool_capabilities_fail_closed_before_transport() -> None:
    with pytest.raises(AssistantModelRoutingUnavailable, match="LARGE_REQUIRED"):
        plan_assistant_route(
            AssistantTaskType.ASSISTANT_CHAT,
            estimated_input_tokens=31_000,
            reserved_output_tokens=2_000,
            user_text="为什么？",
            profiles=(SMALL, _profile(
                "short-large", ModelCapacityClass.LARGE, context_limit=32_000,
            )),
        )

    with pytest.raises(AssistantModelRoutingUnavailable, match="LARGE_REQUIRED"):
        plan_assistant_route(
            AssistantTaskType.ASSISTANT_CHAT,
            estimated_input_tokens=1_000,
            reserved_output_tokens=2_000,
            user_text="分析三个来源",
            planned_tool_calls=3,
            profiles=(LARGE,),
        )

    with pytest.raises(AssistantModelRoutingUnavailable, match="SMALL_PREFERRED"):
        plan_assistant_route(
            AssistantTaskType.ASSISTANT_CHAT,
            estimated_input_tokens=1_000,
            reserved_output_tokens=500,
            user_text="列出最新新闻",
            planned_tool_calls=1,
            profiles=(SMALL, LARGE),
        )

    tool_large = _profile(
        "tool-large", ModelCapacityClass.LARGE, tools=True,
    )
    plan = plan_assistant_route(
        AssistantTaskType.ASSISTANT_CHAT,
        estimated_input_tokens=1_000,
        reserved_output_tokens=2_000,
        user_text="分析三个来源",
        planned_tool_calls=3,
        profiles=(SMALL, tool_large),
    )
    assert plan.candidate_profiles == (tool_large,)

    tool_small = _profile(
        "tool-small", ModelCapacityClass.SMALL, tools=True,
    )
    single_tool_plan = plan_assistant_route(
        AssistantTaskType.ASSISTANT_CHAT,
        estimated_input_tokens=1_000,
        reserved_output_tokens=500,
        user_text="列出最新新闻",
        planned_tool_calls=1,
        profiles=(SMALL, tool_small, tool_large),
    )
    assert single_tool_plan.candidate_profiles == (tool_small, tool_large)


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("estimated_input_tokens", -1),
        ("estimated_input_tokens", 1.5),
        ("reserved_output_tokens", 0),
        ("planned_tool_calls", -1),
        ("planned_tool_calls", 65),
    ),
)
def test_routing_budgets_reject_invalid_values_instead_of_coercing(
    field: str,
    value: object,
) -> None:
    arguments = {
        "estimated_input_tokens": 1_000,
        "reserved_output_tokens": 500,
        "planned_tool_calls": 1,
    }
    arguments[field] = value
    with pytest.raises(ValueError, match=field):
        plan_assistant_route(
            AssistantTaskType.ASSISTANT_CHAT,
            user_text="列出最新新闻",
            profiles=(SMALL, LARGE),
            **arguments,
        )


def test_provider_thinking_level_is_added_without_mutating_the_payload() -> None:
    payload = {"generationConfig": {"temperature": 0}, "contents": []}

    routed = apply_provider_thinking_level(payload, "high")

    assert routed["generationConfig"] == {
        "temperature": 0,
        "thinkingConfig": {"thinkingLevel": "high"},
    }
    assert payload == {"generationConfig": {"temperature": 0}, "contents": []}
    with pytest.raises(ValueError, match="thinking level"):
        apply_provider_thinking_level(payload, "unbounded")


def test_operational_profiles_are_strict_and_do_not_contain_credentials() -> None:
    assert configured_assistant_model_profiles()[0].supports_function_calling is True
    raw = json.dumps([
        {
            "profile_id": "small-v1",
            "model_id": "gemma-small",
            "provider": GOOGLE_GENERATIVE_LANGUAGE,
            "context_limit": 16_384,
            "supports_thinking": True,
            "supports_function_calling": False,
            "supports_streaming": True,
            "capacity_class": "SMALL",
            "enabled": True,
        },
        {
            "profile_id": "large-v1",
            "model_id": "gemma-large",
            "provider": GOOGLE_GENERATIVE_LANGUAGE,
            "context_limit": 65_536,
            "supports_thinking": True,
            "supports_function_calling": True,
            "supports_streaming": True,
            "capacity_class": "LARGE",
            "enabled": True,
        },
    ])

    profiles = configured_assistant_model_profiles(raw)

    assert [profile.capacity_class for profile in profiles] == [
        ModelCapacityClass.SMALL, ModelCapacityClass.LARGE,
    ]
    with pytest.raises(ValueError, match="unsupported fields"):
        configured_assistant_model_profiles(raw.replace(
            '"enabled": true', '"api_key": "secret", "enabled": true', 1,
        ))
    with pytest.raises(ValueError, match="installed gateway"):
        configured_assistant_model_profiles(raw.replace(
            GOOGLE_GENERATIVE_LANGUAGE, "UNSUPPORTED_PROVIDER", 1,
        ))
    with pytest.raises(ValueError, match="installed gateway"):
        plan_assistant_route(
            AssistantTaskType.NEWS_QA,
            estimated_input_tokens=1_000,
            reserved_output_tokens=500,
            user_text="列出最新新闻",
            profiles=(ModelProfile(
                profile_id="bad-provider",
                model_id="model-bad",
                provider="UNSUPPORTED_PROVIDER",
                context_limit=32_768,
                supports_thinking=True,
                supports_function_calling=False,
                supports_streaming=False,
                capacity_class=ModelCapacityClass.SMALL,
            ),),
        )
