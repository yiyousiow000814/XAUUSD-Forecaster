from __future__ import annotations

import copy
import threading
import time
from datetime import UTC, datetime

import pytest

from xauusd_forecaster.assistant_tools import (
    NEWS_SEARCH_TOOL_NAME,
    AssistantToolActor,
    AssistantToolCall,
    AssistantToolCapability,
    AssistantToolDefinition,
    AssistantToolPlanRejected,
    AssistantToolRegistry,
    AssistantToolStatus,
    build_news_search_tool,
)


NOW = datetime(2026, 8, 15, 12, 0, tzinfo=UTC)
INPUT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["value"],
    "properties": {"value": {"type": "string", "minLength": 1, "maxLength": 32}},
}
OUTPUT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["value"],
    "properties": {"value": {"type": "string", "minLength": 1, "maxLength": 1_000}},
}


def _definition(
    name: str,
    capability: AssistantToolCapability,
    executor,
    *,
    timeout: float = 1.0,
    max_result_tokens: int = 512,
) -> AssistantToolDefinition:
    return AssistantToolDefinition(
        name=name,
        version="v1",
        description=f"Read bounded {name} data.",
        capability=capability,
        input_schema=copy.deepcopy(INPUT_SCHEMA),
        output_schema=copy.deepcopy(OUTPUT_SCHEMA),
        timeout_seconds=timeout,
        max_result_tokens=max_result_tokens,
        executor=executor,
    )


def _actor(*capabilities: AssistantToolCapability) -> AssistantToolActor:
    return AssistantToolActor(
        actor_id="OWNER",
        work_id="assistant-work-1",
        allowed_capabilities=frozenset(capabilities),
    )


def _execute(
    registry: AssistantToolRegistry,
    calls: tuple[AssistantToolCall, ...],
    actor: AssistantToolActor,
    *,
    parallel: int | None = None,
):
    return registry.execute_batch(
        calls,
        actor=actor,
        retrieval_cutoff="2026-08-15T12:00:00Z",
        max_parallel_calls=parallel or len(calls),
        max_total_result_tokens=4_096,
        max_retrieved_evidence=20,
    )


def test_registry_accepts_only_versioned_declared_read_only_capabilities() -> None:
    with pytest.raises(ValueError, match="read-only"):
        AssistantToolRegistry((AssistantToolDefinition(
            name="place_order_v1",
            version="v1",
            description="Place an order.",
            capability="ORDER_EXECUTION",  # type: ignore[arg-type]
            input_schema=INPUT_SCHEMA,
            output_schema=OUTPUT_SCHEMA,
            timeout_seconds=1,
            max_result_tokens=512,
            executor=lambda _arguments, _context: {"value": "forbidden"},
        ),))

    definition = _definition(
        "market_price_v1",
        AssistantToolCapability.MARKET_DATA_READ,
        lambda arguments, _context: {"value": arguments["value"]},
    )
    registry = AssistantToolRegistry((definition,))
    assert registry.gemini_tools(_actor()) == []
    declaration = registry.gemini_tools(_actor(
        AssistantToolCapability.MARKET_DATA_READ,
    ))
    assert declaration[0]["functionDeclarations"][0] == {
        "name": "market_price_v1",
        "description": "Read bounded market_price_v1 data.",
        "parametersJsonSchema": INPUT_SCHEMA,
    }
    definition.input_schema["properties"] = {}
    registry.definitions[0].input_schema["properties"] = {}
    provider_declaration = registry.gemini_tools(_actor(
        AssistantToolCapability.MARKET_DATA_READ,
    ))[0]["functionDeclarations"][0]
    assert "parameters" not in provider_declaration
    assert provider_declaration["parametersJsonSchema"]["required"] == ["value"]
    assert provider_declaration["parametersJsonSchema"][
        "additionalProperties"
    ] is False


def test_independent_tool_calls_execute_in_parallel_and_return_in_plan_order() -> None:
    barrier = threading.Barrier(2, timeout=1)

    def execute(arguments, _context):
        barrier.wait()
        return {"value": arguments["value"]}

    registry = AssistantToolRegistry((
        _definition(
            "market_price_v1", AssistantToolCapability.MARKET_DATA_READ, execute,
        ),
        _definition(
            "calendar_events_v1", AssistantToolCapability.CALENDAR_READ, execute,
        ),
    ))
    calls = (
        AssistantToolCall("call-calendar", "calendar_events_v1", {"value": "second"}),
        AssistantToolCall("call-market", "market_price_v1", {"value": "first"}),
    )

    results = _execute(
        registry,
        calls,
        _actor(
            AssistantToolCapability.MARKET_DATA_READ,
            AssistantToolCapability.CALENDAR_READ,
        ),
    )

    assert [result.call_id for result in results] == ["call-calendar", "call-market"]
    assert [result.output["value"] for result in results] == ["second", "first"]
    assert all(result.status is AssistantToolStatus.SUCCEEDED for result in results)


def test_tool_failures_are_typed_and_never_empty_successes() -> None:
    def crash(_arguments, _context):
        raise RuntimeError("private backend detail")

    def slow(arguments, _context):
        time.sleep(0.2)
        return {"value": arguments["value"]}

    registry = AssistantToolRegistry((
        _definition(
            "market_price_v1", AssistantToolCapability.MARKET_DATA_READ, crash,
        ),
        _definition(
            "calendar_events_v1", AssistantToolCapability.CALENDAR_READ,
            lambda arguments, _context: {"value": arguments["value"]},
        ),
        _definition(
            "slow_market_v1", AssistantToolCapability.MARKET_DATA_READ, slow,
            timeout=0.05,
        ),
    ))
    calls = (
        AssistantToolCall("unknown", "unknown_tool_v1", {"value": "x"}),
        AssistantToolCall("forbidden", "calendar_events_v1", {"value": "x"}),
        AssistantToolCall("invalid", "market_price_v1", {"extra": "x"}),
        AssistantToolCall("crash", "market_price_v1", {"value": "x"}),
        AssistantToolCall("timeout", "slow_market_v1", {"value": "x"}),
    )

    started = time.monotonic()
    results = _execute(
        registry,
        calls,
        _actor(AssistantToolCapability.MARKET_DATA_READ),
    )
    elapsed = time.monotonic() - started

    assert elapsed < 0.18
    assert [(result.status.value, result.error_code) for result in results] == [
        ("REJECTED", "UNKNOWN_TOOL"),
        ("REJECTED", "TOOL_NOT_AUTHORIZED"),
        ("REJECTED", "INVALID_TOOL_ARGUMENTS"),
        ("FAILED", "TOOL_EXECUTION_FAILED"),
        ("TIMED_OUT", "TOOL_TIMEOUT"),
    ]
    assert all(result.output is None for result in results)
    assert "private backend detail" not in str([result.model_response() for result in results])


def test_over_parallel_plan_executes_no_partial_subset() -> None:
    executed: list[str] = []

    def execute(arguments, _context):
        executed.append(arguments["value"])
        return {"value": arguments["value"]}

    registry = AssistantToolRegistry((
        _definition(
            "market_price_v1", AssistantToolCapability.MARKET_DATA_READ, execute,
        ),
    ))
    calls = (
        AssistantToolCall("one", "market_price_v1", {"value": "one"}),
        AssistantToolCall("two", "market_price_v1", {"value": "two"}),
    )
    with pytest.raises(AssistantToolPlanRejected, match="parallel"):
        _execute(
            registry,
            calls,
            _actor(AssistantToolCapability.MARKET_DATA_READ),
            parallel=1,
        )
    assert executed == []


def test_oversized_tool_output_becomes_a_typed_failure() -> None:
    registry = AssistantToolRegistry((
        _definition(
            "market_price_v1",
            AssistantToolCapability.MARKET_DATA_READ,
            lambda _arguments, _context: {"value": "x" * 500},
            max_result_tokens=64,
        ),
    ))
    result = _execute(
        registry,
        (AssistantToolCall("large", "market_price_v1", {"value": "x"}),),
        _actor(AssistantToolCapability.MARKET_DATA_READ),
    )[0]
    assert result.status is AssistantToolStatus.FAILED
    assert result.error_code == "TOOL_RESULT_BUDGET_EXCEEDED"
    assert result.output is None

    numeric_schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["value"],
        "properties": {"value": {"type": "number"}},
    }
    nonfinite = AssistantToolRegistry((AssistantToolDefinition(
        name="market_metric_v1",
        version="v1",
        description="Read one bounded market metric.",
        capability=AssistantToolCapability.MARKET_DATA_READ,
        input_schema=copy.deepcopy(INPUT_SCHEMA),
        output_schema=numeric_schema,
        timeout_seconds=1,
        max_result_tokens=512,
        executor=lambda _arguments, _context: {"value": float("nan")},
    ),))
    invalid = _execute(
        nonfinite,
        (AssistantToolCall("nan", "market_metric_v1", {"value": "x"}),),
        _actor(AssistantToolCapability.MARKET_DATA_READ),
    )[0]
    assert invalid.status is AssistantToolStatus.FAILED
    assert invalid.error_code == "INVALID_TOOL_RESULT"


def _news_row(evidence_id: str) -> dict[str, object]:
    return {
        "evidence_id": evidence_id,
        "detail_key": evidence_id,
        "source_published_time": "2026-08-15T10:00:00.000Z",
        "collector_first_seen_time": "2026-08-15T10:01:00.000Z",
        "source": "Reuters",
        "headline": "Gold moves after policy signal",
        "summary_zh": "政策信号发布后黄金波动。",
        "category": "MONETARY_POLICY",
        "impact_reason_zh": "美元与利率预期变化。",
        "body": "raw body must never reach the model",
        "internal_metadata": {"secret": "must-not-leak"},
    }


def test_news_tool_reuses_authoritative_retrieval_with_server_fixed_cutoff() -> None:
    evidence_id = "a" * 64
    observed: list[tuple[dict[str, object], float]] = []

    def retrieve(params, timeout):
        observed.append((params, timeout))
        return {
            "items": [_news_row(evidence_id)],
            "query": "黄金",
            "source_mode": "D1_ARCHIVE",
            "archive_complete": True,
            "retrieval": {
                "cutoff": params["received_to"],
                "canonical_evidence_ids": [evidence_id],
            },
        }

    registry = AssistantToolRegistry((build_news_search_tool(retrieve),))
    result = registry.execute_batch(
        (AssistantToolCall("news-1", NEWS_SEARCH_TOOL_NAME, {"query": "黄金"}),),
        actor=_actor(AssistantToolCapability.NEWS_RETRIEVAL),
        retrieval_cutoff="2026-08-15T12:34:56Z",
        max_parallel_calls=1,
        max_total_result_tokens=8_192,
        max_retrieved_evidence=20,
    )[0]

    assert result.status is AssistantToolStatus.SUCCEEDED
    assert observed[0][0]["received_to"] == "2026-08-15T12:34:56.000Z"
    assert observed[0][0]["limit"] == 20
    assert observed[0][1] > 0
    assert list(result.output["items"][0]) == [
        "evidence_id", "published_at", "received_at", "source", "headline",
        "summary", "category", "impact",
    ]
    serialized = str(result.output)
    assert "raw body" not in serialized
    assert "internal_metadata" not in serialized
    assert result.evidence_ids == (evidence_id,)
    assert result.provenance["source_mode"] == "D1_ARCHIVE"
    assert len(result.provenance["actor_fingerprint"]) == 16
    response = result.model_response()
    response["result"]["items"].clear()
    receipt = result.receipt()
    receipt["provenance"]["source_mode"] = "MUTATED"
    assert len(result.model_response()["result"]["items"]) == 1
    assert result.receipt()["provenance"]["source_mode"] == "D1_ARCHIVE"
    assert result.result_sha256 == result.receipt()["result_sha256"]


def test_news_tool_rejects_model_cutoff_override_and_preview_fallback() -> None:
    calls = 0

    def retrieve(params, _timeout):
        nonlocal calls
        calls += 1
        return {
            "items": [],
            "query": params["q"],
            "source_mode": "IMMUTABLE_PREVIEW_SNAPSHOT",
            "archive_complete": False,
            "retrieval": {
                "cutoff": params["received_to"],
                "canonical_evidence_ids": [],
            },
        }

    registry = AssistantToolRegistry((build_news_search_tool(retrieve),))
    actor = _actor(AssistantToolCapability.NEWS_RETRIEVAL)
    invalid = _execute(
        registry,
        (AssistantToolCall(
            "override", NEWS_SEARCH_TOOL_NAME,
            {"query": "黄金", "received_to": "2099-01-01"},
        ),),
        actor,
    )[0]
    assert invalid.status is AssistantToolStatus.REJECTED
    assert invalid.error_code == "INVALID_TOOL_ARGUMENTS"
    assert calls == 0

    fallback = _execute(
        registry,
        (AssistantToolCall("preview", NEWS_SEARCH_TOOL_NAME, {"query": "黄金"}),),
        actor,
    )[0]
    assert fallback.status is AssistantToolStatus.FAILED
    assert fallback.error_code == "AUTHORITATIVE_NEWS_RETRIEVAL_REQUIRED"
    assert calls == 1
