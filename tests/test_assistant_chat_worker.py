from __future__ import annotations

import copy
import urllib.parse

import pytest

from xauusd_forecaster import assistant_chat_worker as worker
from xauusd_forecaster.assistant_agent import AssistantAgentResult
from xauusd_forecaster.assistant_capacity import AssistantCapacityUnavailable
from xauusd_forecaster.assistant_tools import (
    NEWS_SEARCH_TOOL_NAME,
    AssistantToolCall,
    AssistantToolCapability,
    AssistantToolStatus,
)


CUTOFF = "2026-08-16T01:00:00.000Z"
OWNER = "cloudflare-access:owner-1"


def _claim(**overrides) -> dict[str, object]:
    value: dict[str, object] = {
        "id": "turn-1",
        "owner_id": OWNER,
        "conversation_id": "conversation-1",
        "user_message_id": "message-1",
        "user_text": "请搜索并解释最新黄金新闻",
        "retrieval_cutoff": CUTOFF,
        "lease_token": "lease-token-1",
        "lease_expires_at": "2026-08-16T01:05:00.000Z",
        "attempt_count": 1,
        "event_sequence": 1,
    }
    value.update(overrides)
    return value


def _context(claim: dict[str, object], **current_overrides) -> dict[str, object]:
    current = {
        "id": claim["user_message_id"],
        "role": "USER",
        "content": claim["user_text"],
        "created_at": CUTOFF,
        **current_overrides,
    }
    return {
        "profile_id": "assistant-context-default-v1",
        "capacity_state": "GREEN",
        "estimated_tokens": 1_000,
        "context_limit_tokens": 32_768,
        "reserved_tokens": 4_096,
        "layers": [
            {"type": "PINNED_STATE", "token_estimate": 0, "items": []},
            {"type": "ROLLING_SUMMARY", "token_estimate": 0, "item": None},
            {"type": "HISTORICAL_MEMORY", "token_estimate": 0, "items": []},
            {"type": "RECENT_VERBATIM_TURNS", "token_estimate": 0, "items": []},
            {"type": "CURRENT_USER_MESSAGE", "token_estimate": 20, "item": current},
            {"type": "TOOL_EVIDENCE", "token_estimate": 0, "items": []},
        ],
    }


def _news_row(evidence_id: str) -> dict[str, object]:
    return {
        "evidence_id": evidence_id,
        "detail_key": evidence_id,
        "source_published_time": "2026-08-15T23:00:00.000Z",
        "collector_first_seen_time": "2026-08-15T23:01:00.000Z",
        "source": "Reuters",
        "headline": "Gold moves after a policy signal",
        "summary_zh": "政策信号发布后黄金波动。",
        "category": "MONETARY_POLICY",
        "impact_reason_zh": "美元与利率预期变化。",
        "body": "raw body must not reach the model",
    }


class RecordingTransport:
    def __init__(
        self,
        claim: dict[str, object],
        *,
        context: dict[str, object] | None = None,
    ) -> None:
        self.claim = claim
        self.context = context or _context(claim)
        self.claimed = False
        self.gets: list[tuple[str, float]] = []
        self.posts: list[tuple[str, dict[str, object]]] = []

    def get_json(self, url: str, timeout: float) -> dict[str, object]:
        self.gets.append((url, timeout))
        parsed = urllib.parse.urlsplit(url)
        query = urllib.parse.parse_qs(parsed.query)
        if parsed.path.endswith("/assistant-chat"):
            if self.claimed:
                return {"item": None}
            self.claimed = True
            return {"item": copy.deepcopy(self.claim)}
        if parsed.path.endswith("/news-search"):
            evidence_id = "a" * 64
            return {
                "items": [_news_row(evidence_id)],
                "query": query["q"][0],
                "source_mode": "D1_ARCHIVE",
                "archive_complete": True,
                "retrieval": {
                    "cutoff": query["received_to"][0],
                    "canonical_evidence_ids": [evidence_id],
                },
            }
        raise AssertionError(f"unexpected GET {url}")

    def post_json(
        self, url: str, payload: dict[str, object],
    ) -> dict[str, object]:
        self.posts.append((url, copy.deepcopy(payload)))
        if payload.get("action") == "BUILD_CONTEXT":
            return {"status": "OK", "item": copy.deepcopy(self.context)}
        if payload.get("action") == "EVENTS":
            return {"status": "OK", "item": [{"accepted": True}]}
        if payload.get("action") == "RENEW":
            return {"status": "OK", "item": {
                "id": self.claim["id"],
                "lease_token": self.claim["lease_token"],
                "lease_expires_at": "2026-08-16T01:06:00.000Z",
                "expires_at": "2026-08-16T01:30:00.000Z",
                "attempt_count": self.claim["attempt_count"],
            }}
        return {"status": "OK", "item": {"id": self.claim["id"]}}

    def value(self) -> worker.AssistantChatTransport:
        return worker.AssistantChatTransport(self.get_json, self.post_json)


def test_worker_builds_owner_context_runs_native_news_tool_and_completes(
    monkeypatch, tmp_path,
) -> None:
    claim = _claim()
    transport = RecordingTransport(claim)
    captured: dict[str, object] = {}

    def run_agent(connection, request, registry, credentials, **kwargs):
        captured.update({
            "connection": connection,
            "request": request,
            "registry": registry,
            "credentials": credentials,
            "kwargs": kwargs,
        })
        kwargs["before_model_attempt"]()
        result = registry.execute_batch(
            (AssistantToolCall(
                call_id="provider-call-1",
                name=NEWS_SEARCH_TOOL_NAME,
                arguments={"query": "黄金 政策"},
            ),),
            actor=request.actor,
            retrieval_cutoff=request.retrieval_cutoff,
            max_parallel_calls=1,
            max_total_result_tokens=8_192,
            max_retrieved_evidence=20,
        )[0]
        assert result.status is AssistantToolStatus.SUCCEEDED
        assert "raw body" not in str(result.output)
        return AssistantAgentResult(
            answer="新闻证据显示利率预期仍是黄金的主要驱动。",
            model_version="gemma-4-31b-it",
            evidence_ids=result.evidence_ids,
            provenance={
                "policy_version": "assistant-agent-v1",
                "tool_execution": [[result.receipt()]],
            },
        )

    monkeypatch.setattr(worker, "run_capacity_routed_assistant_agent", run_agent)
    outcome = worker.run_assistant_chat_worker(
        chat_url="https://example.test/api/assistant-chat",
        worker_id="dashboard-sync:test",
        database=tmp_path / "forward.sqlite3",
        credentials=(),
        transport=transport.value(),
    )

    assert outcome == worker.AssistantChatSyncResult(1, 1, 0, 0)
    request = captured["request"]
    assert request.conversation_id == claim["conversation_id"]
    assert request.user_message_id == claim["user_message_id"]
    assert request.actor.actor_id == OWNER
    assert request.actor.work_id == claim["id"]
    assert request.actor.allowed_capabilities == frozenset({
        AssistantToolCapability.NEWS_RETRIEVAL,
    })
    assert request.active_context["layers"][4]["item"]["id"] == "message-1"

    actions = [payload["action"] for _url, payload in transport.posts]
    assert actions == [
        "BUILD_CONTEXT", "EVENTS", "RENEW", "EVENTS", "COMPLETE",
    ]
    reasoning = transport.posts[1][1]["events"]
    assert reasoning == [{
        "idempotency_key": "chat-reasoning-attempt-1",
        "type": "reasoning.started",
        "payload": {"reasoning_class": "TOOL_HEAVY"},
    }]
    assert transport.posts[2][1] == {
        "action": "RENEW",
        "id": claim["id"],
        "lease_token": claim["lease_token"],
    }
    tool_events = transport.posts[3][1]["events"]
    assert [event["type"] for event in tool_events] == [
        "tool.started", "tool.completed",
    ]
    assert tool_events[0]["payload"]["call_id"].startswith("attempt-1-tool-")
    assert tool_events[1]["payload"]["evidence_count"] == 1
    complete = transport.posts[4][1]
    assert complete["answer"].startswith("新闻证据")
    assert complete["model_version"] == "gemma-4-31b-it"
    assert complete["provenance"]["tool_execution"][0][0]["call_id"] == (
        "provider-call-1"
    )

    claim_timeout = transport.gets[0][1]
    news_url, news_timeout = next(
        (url, timeout) for url, timeout in transport.gets if "/news-search?" in url
    )
    news_query = urllib.parse.parse_qs(urllib.parse.urlsplit(news_url).query)
    assert claim_timeout == worker.ASSISTANT_CHAT_REMOTE_TIMEOUT_SECONDS
    assert 0 < news_timeout <= 10
    assert news_query["received_to"] == [CUTOFF]
    assert news_query["limit"] == ["20"]


def test_capacity_failure_defers_under_the_same_turn_lease(
    monkeypatch, tmp_path,
) -> None:
    claim = _claim()
    transport = RecordingTransport(claim)

    def no_capacity(*_args, **_kwargs):
        raise AssistantCapacityUnavailable("no safe credential-model pair")

    monkeypatch.setattr(
        worker, "run_capacity_routed_assistant_agent", no_capacity,
    )
    outcome = worker.run_assistant_chat_worker(
        chat_url="https://example.test/api/assistant-chat",
        worker_id="dashboard-sync:test",
        database=tmp_path / "forward.sqlite3",
        credentials=(),
        transport=transport.value(),
        max_claims=1,
    )

    assert outcome == worker.AssistantChatSyncResult(1, 0, 1, 0)
    assert [payload["action"] for _url, payload in transport.posts] == [
        "BUILD_CONTEXT", "EVENTS", "DEFER",
    ]
    assert transport.posts[-1][1]["failure_code"] == "NO_MODEL_CAPACITY"


def test_context_identity_mismatch_fails_before_model_or_tool_execution(
    monkeypatch, tmp_path,
) -> None:
    claim = _claim()
    transport = RecordingTransport(
        claim,
        context=_context(claim, id="foreign-message"),
    )
    model_called = False

    def must_not_run(*_args, **_kwargs):
        nonlocal model_called
        model_called = True
        raise AssertionError("model must not run")

    monkeypatch.setattr(
        worker, "run_capacity_routed_assistant_agent", must_not_run,
    )
    outcome = worker.run_assistant_chat_worker(
        chat_url="https://example.test/api/assistant-chat",
        worker_id="dashboard-sync:test",
        database=tmp_path / "forward.sqlite3",
        credentials=(),
        transport=transport.value(),
        max_claims=1,
    )

    assert model_called is False
    assert outcome == worker.AssistantChatSyncResult(1, 0, 0, 1)
    assert [payload["action"] for _url, payload in transport.posts] == [
        "BUILD_CONTEXT", "FAIL",
    ]
    assert transport.posts[-1][1]["failure_code"] == (
        "ACTIVE_CONTEXT_IDENTITY_MISMATCH"
    )


def test_tool_progress_is_closed_bounded_stable_and_omits_unknown_versions() -> None:
    receipts = []
    for index in range(10):
        success = index % 2 == 0
        receipts.append({
            "call_id": f"provider-call-{index}",
            "name": "search_news_v1",
            "tool_version": "v1",
            "status": "SUCCEEDED" if success else "FAILED",
            "error_code": None if success else "TOOL_EXECUTION_FAILED",
            "result_sha256": "a" * 64,
            "evidence_ids": [f"evidence-{index}"] if success else [],
        })
    receipts.append({
        "call_id": "unknown-call",
        "name": "unknown_tool_v1",
        "tool_version": None,
        "status": "REJECTED",
        "error_code": "UNKNOWN_TOOL",
        "result_sha256": "b" * 64,
        "evidence_ids": [],
    })
    provenance = {"tool_execution": [receipts]}

    first = worker.assistant_tool_progress_batches(provenance, attempt_count=2)
    second = worker.assistant_tool_progress_batches(provenance, attempt_count=2)

    assert first == second
    assert [len(batch) for batch in first] == [16, 4]
    flat = [event for batch in first for event in batch]
    assert len(flat) == 20
    assert all(
        flat[index]["type"] == "tool.started"
        and flat[index + 1]["type"] in {"tool.completed", "tool.failed"}
        and flat[index]["payload"]["call_id"]
        == flat[index + 1]["payload"]["call_id"]
        for index in range(0, len(flat), 2)
    )
    assert len({event["idempotency_key"] for event in flat}) == len(flat)


@pytest.mark.parametrize("field", ["conversation_id", "owner_id", "lease_token"])
def test_invalid_claim_identity_never_opens_the_local_ledger(
    monkeypatch, tmp_path, field,
) -> None:
    claim = _claim(**{field: "invalid value"})
    transport = RecordingTransport(claim)
    opened = False

    class ForbiddenLedger:
        def __init__(self, _path):
            nonlocal opened
            opened = True

    monkeypatch.setattr(worker, "ForwardLedger", ForbiddenLedger)
    with pytest.raises(worker.AssistantChatWorkerError, match="claim|invalid"):
        worker.run_assistant_chat_worker(
            chat_url="https://example.test/api/assistant-chat",
            worker_id="dashboard-sync:test",
            database=tmp_path / "forward.sqlite3",
            credentials=(),
            transport=transport.value(),
            max_claims=1,
        )
    assert opened is False
