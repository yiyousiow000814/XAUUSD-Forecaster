from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime

import pytest

import xauusd_forecaster.assistant_agent as assistant_agent_module
from xauusd_forecaster.assistant_agent import (
    DEFAULT_ASSISTANT_AGENT_BUDGETS,
    AssistantAgentContractError,
    AssistantAgentRequest,
    AssistantModelTurn,
    CapacityRoutedAssistantModelInvoker,
    RoutedAssistantModelTurn,
    configured_assistant_agent_budgets,
    decode_gemini_assistant_turn,
    run_bounded_assistant_agent,
)
from xauusd_forecaster.assistant_capacity import AssistantCapacityPolicy
from xauusd_forecaster.assistant_routing import (
    GOOGLE_GENERATIVE_LANGUAGE,
    ModelCapacityClass,
    ModelProfile,
)
from xauusd_forecaster.assistant_tools import (
    AssistantToolActor,
    AssistantToolCall,
    AssistantToolCapability,
    AssistantToolDefinition,
    AssistantToolRegistry,
)
from xauusd_forecaster.forward_ledger import ForwardLedger
from xauusd_forecaster.model_gateway import ModelRequestUsage
from xauusd_forecaster.news_scheduler import PREEMPTIBLE_POOL, ApiCredential


NOW = datetime(2026, 8, 15, 12, 0, tzinfo=UTC)
INPUT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["symbol"],
    "properties": {"symbol": {"type": "string", "enum": ["XAUUSD"]}},
}
OUTPUT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["value"],
    "properties": {"value": {"type": "string", "minLength": 1, "maxLength": 64}},
}


def _actor() -> AssistantToolActor:
    return AssistantToolActor(
        actor_id="OWNER",
        work_id="assistant-turn-1",
        allowed_capabilities=frozenset({
            AssistantToolCapability.MARKET_DATA_READ,
        }),
    )


def _request(*, active_context: dict[str, object] | None = None) -> AssistantAgentRequest:
    return AssistantAgentRequest(
        conversation_id="conversation-1",
        user_message_id="message-1",
        actor=_actor(),
        user_text="列出最新 XAUUSD 市场信息",
        active_context=active_context or {"recent_messages": []},
        retrieval_cutoff="2026-08-15T12:00:00Z",
    )


def _registry(executed: list[str] | None = None) -> AssistantToolRegistry:
    def execute(arguments, _context):
        if executed is not None:
            executed.append(arguments["symbol"])
        return {"value": "4321.00"}

    return AssistantToolRegistry((AssistantToolDefinition(
        name="market_price_v1",
        version="v1",
        description="Read a bounded XAUUSD market price snapshot.",
        capability=AssistantToolCapability.MARKET_DATA_READ,
        input_schema=INPUT_SCHEMA,
        output_schema=OUTPUT_SCHEMA,
        timeout_seconds=1,
        max_result_tokens=512,
        executor=execute,
    ),))


def _model_content(
    *,
    text: str | None = None,
    call_id: str | None = None,
    signature: str | None = None,
) -> dict[str, object]:
    if call_id is not None:
        part: dict[str, object] = {
            "functionCall": {
                "id": call_id,
                "name": "market_price_v1",
                "args": {"symbol": "XAUUSD"},
            },
        }
        if signature:
            part["thoughtSignature"] = signature
        parts = [part]
    else:
        parts = [{"text": text or ""}]
    return {"role": "model", "parts": parts}


def _routed(content: dict[str, object], turn_number: int) -> RoutedAssistantModelTurn:
    turn = decode_gemini_assistant_turn({
        "candidates": [{"content": content}],
    })
    return RoutedAssistantModelTurn(
        turn=turn,
        model_version="gemma-4-31b-it",
        routing={"turn": turn_number, "selected_model_id": "gemma-4-31b-it"},
    )


def test_agent_budgets_are_exact_operational_config_and_always_finite() -> None:
    raw = json.dumps(DEFAULT_ASSISTANT_AGENT_BUDGETS.receipt())
    assert configured_assistant_agent_budgets(raw) == DEFAULT_ASSISTANT_AGENT_BUDGETS
    with pytest.raises(ValueError, match="exact budget set"):
        configured_assistant_agent_budgets(raw[:-1] + ', "UNBOUNDED": 1}')
    invalid = DEFAULT_ASSISTANT_AGENT_BUDGETS.receipt()
    invalid["MAX_MODEL_TURNS_PER_USER_TURN"] = True
    with pytest.raises(ValueError, match="MAX_MODEL_TURNS"):
        configured_assistant_agent_budgets(json.dumps(invalid))
    invalid = DEFAULT_ASSISTANT_AGENT_BUDGETS.receipt()
    invalid["MAX_TOOL_CALLS_PER_USER_TURN"] = 1
    invalid["MAX_PARALLEL_TOOL_CALLS"] = 2
    with pytest.raises(ValueError, match="parallel"):
        configured_assistant_agent_budgets(json.dumps(invalid))


def test_native_turn_parser_preserves_signature_and_hides_thought_text() -> None:
    content = {
        "role": "model",
        "parts": [
            {"thought": True, "text": "private reasoning", "thoughtSignature": "sig-0"},
            {
                "functionCall": {
                    "id": "call-1",
                    "name": "market_price_v1",
                    "args": {"symbol": "XAUUSD"},
                },
                "thoughtSignature": "sig-1",
            },
        ],
    }
    turn = decode_gemini_assistant_turn({"candidates": [{"content": content}]})
    assert turn.content == content
    assert turn.text == ""
    assert turn.tool_calls == (
        AssistantToolCall("call-1", "market_price_v1", {"symbol": "XAUUSD"}),
    )
    with pytest.raises(AssistantAgentContractError, match="exact id"):
        decode_gemini_assistant_turn({"candidates": [{"content": {
            "role": "model",
            "parts": [{"functionCall": {
                "name": "market_price_v1", "args": {"symbol": "XAUUSD"},
            }}],
        }}]})
    with pytest.raises(AssistantAgentContractError) as truncated:
        decode_gemini_assistant_turn({"candidates": [{
            "finishReason": "MAX_TOKENS",
            "content": _model_content(text="partial"),
        }]})
    assert truncated.value.error_code == "MODEL_TURN_INCOMPLETE"

    normalized = decode_gemini_assistant_turn({"candidates": [{"content": {
        "role": "model", "parts": [{"text": "第一行\r\n第二行\r"}],
    }}]})
    assert normalized.text == "第一行\n第二行"


def test_agent_may_answer_directly_without_executing_a_tool() -> None:
    payloads: list[dict[str, object]] = []

    def invoke(payload, **_kwargs):
        payloads.append(payload)
        return _routed(_model_content(text="直接回答。"), 1)

    result = run_bounded_assistant_agent(_request(), _registry(), invoke)

    assert result.answer == "直接回答。"
    assert [block["type"] for block in result.content_document["blocks"]] == [
        "markdown", "callout",
    ]
    assert result.provenance["model_turn_count"] == 1
    assert result.provenance["tool_call_count"] == 0
    assert result.provenance["system_instruction_version"] == "assistant-system-v2"
    assert "unverified prior conversation text" in (
        payloads[0]["systemInstruction"]["parts"][0]["text"]
    )
    assert len(result.provenance["system_instruction_sha256"]) == 64
    assert len(result.provenance["active_context_sha256"]) == 64
    assert result.provenance["retrieval_cutoff"] == "2026-08-15T12:00:00.000Z"
    assert payloads[0]["toolConfig"]["functionCallingConfig"]["mode"] == "AUTO"


def test_agent_rejects_nonfinite_context_before_model_transport() -> None:
    invoked = False

    def invoke(_payload, **_kwargs):
        nonlocal invoked
        invoked = True
        return _routed(_model_content(text="must not run"), 1)

    with pytest.raises(ValueError, match="not JSON"):
        run_bounded_assistant_agent(
            _request(active_context={"metric": float("nan")}),
            _registry(),
            invoke,
        )
    assert invoked is False


def test_one_tool_round_preserves_model_content_and_exact_function_response_id() -> None:
    payloads: list[dict[str, object]] = []

    def invoke(payload, **_kwargs):
        payloads.append(payload)
        if len(payloads) == 1:
            return _routed(
                _model_content(call_id="call-1", signature="signature-1"), 1,
            )
        assert payload["contents"][-2] == _model_content(
            call_id="call-1", signature="signature-1",
        )
        function_response = payload["contents"][-1]["parts"][0]["functionResponse"]
        assert function_response["id"] == "call-1"
        assert function_response["name"] == "market_price_v1"
        assert function_response["response"]["status"] == "SUCCEEDED"
        return _routed(_model_content(text="当前价格为 4321.00。"), 2)

    result = run_bounded_assistant_agent(_request(), _registry(), invoke)

    assert result.answer == "当前价格为 4321.00。"
    assert result.content_document["blocks"][0]["data"]["text"] == result.answer
    assert result.provenance["model_turn_count"] == 2
    assert result.provenance["tool_round_count"] == 1
    assert result.provenance["tool_call_count"] == 1
    assert len(result.provenance["run_sha256"]) == 64


def test_agent_allows_at_most_a_bounded_second_tool_round_then_disables_tools() -> None:
    modes: list[str] = []

    def invoke(payload, **_kwargs):
        modes.append(payload["toolConfig"]["functionCallingConfig"]["mode"])
        if len(modes) == 1:
            return _routed(_model_content(call_id="call-1"), 1)
        if len(modes) == 2:
            return _routed(_model_content(call_id="call-2"), 2)
        return _routed(_model_content(text="两轮工具后完成。"), 3)

    result = run_bounded_assistant_agent(_request(), _registry(), invoke)

    assert result.answer == "两轮工具后完成。"
    assert modes == ["AUTO", "AUTO", "NONE"]
    assert result.provenance["model_turn_count"] == 3
    assert result.provenance["tool_round_count"] == 2


def test_tool_call_on_final_only_turn_fails_closed() -> None:
    calls = 0

    def invoke(_payload, **_kwargs):
        nonlocal calls
        calls += 1
        return _routed(_model_content(call_id=f"call-{calls}"), calls)

    budgets = replace(
        DEFAULT_ASSISTANT_AGENT_BUDGETS,
        max_model_turns_per_user_turn=2,
        max_tool_calls_per_user_turn=1,
        max_parallel_tool_calls=1,
    )
    with pytest.raises(AssistantAgentContractError) as captured:
        run_bounded_assistant_agent(
            _request(), _registry(), invoke, budgets=budgets,
        )
    assert captured.value.error_code == "TOOL_CALL_OUTSIDE_BUDGET"


def test_over_budget_tool_plan_executes_no_partial_calls() -> None:
    executed: list[str] = []

    def invoke(_payload, **_kwargs):
        content = {
            "role": "model",
            "parts": [
                {"functionCall": {
                    "id": f"call-{index}",
                    "name": "market_price_v1",
                    "args": {"symbol": "XAUUSD"},
                }}
                for index in range(3)
            ],
        }
        return _routed(content, 1)

    budgets = replace(
        DEFAULT_ASSISTANT_AGENT_BUDGETS,
        max_model_turns_per_user_turn=2,
        max_tool_calls_per_user_turn=2,
        max_parallel_tool_calls=2,
    )
    with pytest.raises(AssistantAgentContractError) as captured:
        run_bounded_assistant_agent(
            _request(), _registry(executed), invoke, budgets=budgets,
        )
    assert captured.value.error_code == "TOOL_PLAN_BUDGET_EXCEEDED"
    assert executed == []


def test_active_context_budget_fails_before_model_transport() -> None:
    invoked = False

    def invoke(_payload, **_kwargs):
        nonlocal invoked
        invoked = True
        return _routed(_model_content(text="must not run"), 1)

    budgets = replace(
        DEFAULT_ASSISTANT_AGENT_BUDGETS,
        max_active_context_tokens=1_024,
        max_output_tokens=32,
    )
    with pytest.raises(AssistantAgentContractError) as captured:
        run_bounded_assistant_agent(
            _request(active_context={"summary": "x" * 500}),
            _registry(),
            invoke,
            budgets=budgets,
        )
    assert captured.value.error_code == "ACTIVE_CONTEXT_BUDGET_EXCEEDED"
    assert invoked is False


def _profile() -> ModelProfile:
    return ModelProfile(
        profile_id="assistant-gemma-large-v1",
        model_id="gemma-4-31b-it",
        provider=GOOGLE_GENERATIVE_LANGUAGE,
        context_limit=32_768,
        supports_thinking=True,
        supports_function_calling=True,
        supports_streaming=False,
        capacity_class=ModelCapacityClass.LARGE,
    )


def _policy() -> AssistantCapacityPolicy:
    return AssistantCapacityPolicy(
        credential_pool_id="pool-a",
        provider=GOOGLE_GENERATIVE_LANGUAGE,
        model_id="gemma-4-31b-it",
        shared_model_ids=("gemma-4-31b-it",),
        rpd_limit=10_000,
        rpm_limit=1_000,
        tpm_limit=1_000_000,
        soft_cap_basis_points=8_000,
        max_in_flight=2,
        reservation_ttl_seconds=180,
        cooldown_seconds=60,
        failure_cooldown_threshold=2,
        enabled=True,
        source="CONFIGURED",
    )


def test_every_native_model_turn_uses_capacity_and_locks_the_selected_model(
    tmp_path,
    monkeypatch,
) -> None:
    envelopes = [
        {"candidates": [{"content": _model_content(call_id="call-1")}],
         "modelVersion": "gemma-4-31b-it-exact"},
        {"candidates": [{"content": _model_content(text="容量路由完成。")}],
         "modelVersion": "gemma-4-31b-it-exact"},
    ]

    def fake_generate(
        _api_key,
        *,
        model,
        purpose,
        payload,
        decode,
        request_accountant,
    ):
        assert request_accountant.reserve(ModelRequestUsage(
            model=model,
            purpose=purpose,
            input_tokens=1_000,
        ))
        envelope = envelopes.pop(0)
        assert payload["generationConfig"]["thinkingConfig"]["thinkingLevel"] == "minimal"
        return decode(envelope), envelope["modelVersion"]

    monkeypatch.setattr(
        assistant_agent_module, "generate_metered_response", fake_generate,
    )
    ledger = ForwardLedger(tmp_path / "forward.sqlite3", now=NOW)
    credential = ApiCredential(
        account_id="pool-a",
        pool=PREEMPTIBLE_POOL,
        api_key="secret-key-material",
        credential_id="credential-fingerprint-a",
    )
    lease_renewals: list[int] = []
    invoker = CapacityRoutedAssistantModelInvoker(
        ledger.connection,
        (credential,),
        profiles=(_profile(),),
        policies=(_policy(),),
        before_model_attempt=lambda: lease_renewals.append(len(envelopes)),
        now=NOW,
    )
    budgets = replace(
        DEFAULT_ASSISTANT_AGENT_BUDGETS,
        max_model_turns_per_user_turn=2,
        max_tool_calls_per_user_turn=1,
        max_parallel_tool_calls=1,
        max_tool_result_tokens=1_024,
        max_retrieved_evidence=0,
        max_output_tokens=512,
    )

    result = run_bounded_assistant_agent(
        _request(), _registry(), invoker, budgets=budgets,
    )

    reservations = ledger.connection.execute(
        "SELECT model_id,state FROM assistant_capacity_reservations_v1 "
        "ORDER BY created_at,reservation_id"
    ).fetchall()
    assert [(row["model_id"], row["state"]) for row in reservations] == [
        ("gemma-4-31b-it", "SUCCEEDED"),
        ("gemma-4-31b-it", "SUCCEEDED"),
    ]
    routes = result.provenance["model_routing"]
    assert [route["selected_model_id"] for route in routes] == [
        "gemma-4-31b-it", "gemma-4-31b-it",
    ]
    assert [route["planned_tool_calls"] for route in routes] == [1, 1]
    assert result.model_version == "gemma-4-31b-it-exact"
    assert "secret-key-material" not in json.dumps(result.provenance)
    assert invoker.locked_profile == _profile()
    assert lease_renewals == [2, 1]
    assert envelopes == []
    ledger.close()
