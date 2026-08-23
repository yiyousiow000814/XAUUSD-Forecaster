from __future__ import annotations

import copy
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from xauusd_forecaster.assistant.events import (
    MAX_ASSISTANT_ANSWER_DELTA_BYTES,
    AssistantEventBuilder,
    AssistantEventContractError,
    AssistantEventEnvelope,
    AssistantEventSequence,
    AssistantEventType,
    encode_assistant_sse,
)


FIXTURE = Path(__file__).parent / "fixtures" / "assistant_event_v1.json"


def _events() -> list[dict[str, object]]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_shared_v1_fixture_is_a_complete_bounded_terminal_sequence() -> None:
    sequence = AssistantEventSequence()
    for raw in _events():
        sequence.append(raw)

    assert sequence.terminal is True
    assert [event.sequence for event in sequence.events] == list(range(1, 12))
    assert sequence.events[-2].message_id == "message-assistant-1"
    assert sequence.events[-2].payload["evidence_ids"] == ["news:1", "news:2"]
    copied = sequence.events[-2].payload
    copied["evidence_ids"].clear()
    assert sequence.events[-2].payload["evidence_ids"] == ["news:1", "news:2"]
    exposed = sequence.events[-2]
    exposed._payload["evidence_ids"].clear()
    assert sequence.events[-2].payload["evidence_ids"] == ["news:1", "news:2"]


def test_sse_uses_sequence_resume_ids_and_one_valid_json_envelope() -> None:
    event = AssistantEventEnvelope.parse(_events()[7])

    encoded = encode_assistant_sse(event)

    assert encoded.startswith("id: 8\nevent: answer.delta\ndata: {")
    assert encoded.endswith("\n\n")
    assert "\r" not in encoded
    data = json.loads(encoded.split("data: ", 1)[1])
    assert data == event.receipt()
    assert "private reasoning" not in encoded


def test_builder_assigns_contiguous_events_without_exposing_reasoning_text() -> None:
    identifiers = iter(["event-a", "event-b", "event-c", "event-d"])
    builder = AssistantEventBuilder(
        "conversation-1",
        "turn-1",
        event_id_factory=lambda: next(identifiers),
        now=lambda: datetime(2026, 8, 15, 10, 0, tzinfo=UTC),
    )

    builder.emit(AssistantEventType.CONVERSATION_STARTED, {})
    reasoning = builder.emit(
        AssistantEventType.REASONING_STARTED,
        {"reasoning_class": "ANALYTICAL"},
    )
    builder.emit(AssistantEventType.ANSWER_STARTED, {})
    builder.emit(AssistantEventType.ERROR, {
        "code": "MODEL_UNAVAILABLE",
        "retryable": True,
        "recovery_key": "retry-later",
    })

    assert [event.sequence for event in builder.events] == [1, 2, 3, 4]
    assert reasoning.payload == {"reasoning_class": "ANALYTICAL"}
    assert set(reasoning.payload) == {"reasoning_class"}


@pytest.mark.parametrize(
    ("mutation", "error_code"),
    (
        (lambda item: item.update(protocol="assistant.event.v0"), "UNSUPPORTED_EVENT_PROTOCOL"),
        (lambda item: item.update(sequence=True), "INVALID_EVENT_SEQUENCE"),
        (lambda item: item.update(extra="hidden"), "INVALID_EVENT_ENVELOPE"),
        (lambda item: item["payload"].update(reasoning="private"), "INVALID_EVENT_PAYLOAD"),
        (lambda item: item.update(message_id="message-1"), "INVALID_EVENT_MESSAGE"),
    ),
)
def test_event_envelopes_fail_closed_on_contract_drift(mutation, error_code) -> None:
    raw = copy.deepcopy(_events()[1])
    mutation(raw)
    with pytest.raises(AssistantEventContractError) as captured:
        AssistantEventEnvelope.parse(raw)
    assert captured.value.error_code == error_code


def test_time_is_canonical_and_hand_built_envelopes_cannot_bypass_validation() -> None:
    raw = copy.deepcopy(_events()[0])
    raw["occurred_at"] = "2026-02-30T10:00:00.000Z"
    with pytest.raises(AssistantEventContractError) as invalid_time:
        AssistantEventEnvelope.parse(raw)
    assert invalid_time.value.error_code == "INVALID_EVENT_TIME"

    bypass = AssistantEventEnvelope(
        event_id="event-bypass",
        conversation_id="conversation-1",
        user_turn_id="turn-1",
        message_id=None,
        sequence=0,
        type=AssistantEventType.CONVERSATION_STARTED,
        occurred_at="2026-08-15T10:00:00.000Z",
        _payload={},
    )
    with pytest.raises(AssistantEventContractError) as invalid_sequence:
        AssistantEventSequence().append(bypass)
    assert invalid_sequence.value.error_code == "INVALID_EVENT_SEQUENCE"


def test_nonfinite_and_oversized_deltas_fail_before_sequence_mutation() -> None:
    raw = copy.deepcopy(_events()[7])
    raw["payload"] = {"text": float("nan")}
    with pytest.raises(AssistantEventContractError) as nonfinite:
        AssistantEventEnvelope.parse(raw)
    assert nonfinite.value.error_code == "INVALID_EVENT_PAYLOAD"

    raw["payload"] = {"text": "x" * (MAX_ASSISTANT_ANSWER_DELTA_BYTES + 1)}
    with pytest.raises(AssistantEventContractError) as oversized:
        AssistantEventEnvelope.parse(raw)
    assert oversized.value.error_code == "INVALID_EVENT_PAYLOAD"

    completion = copy.deepcopy(_events()[9])
    completion["payload"]["evidence_ids"] = [{"not": "an id"}]
    with pytest.raises(AssistantEventContractError) as malformed_evidence:
        AssistantEventEnvelope.parse(completion)
    assert malformed_evidence.value.error_code == "INVALID_EVENT_PAYLOAD"


def test_sequence_rejects_gaps_identity_changes_and_unfinished_tools() -> None:
    events = _events()
    gap = copy.deepcopy(events[0])
    gap["sequence"] = 2
    sequence = AssistantEventSequence()
    with pytest.raises(AssistantEventContractError) as gap_error:
        sequence.append(gap)
    assert gap_error.value.error_code == "INVALID_EVENT_SEQUENCE"

    sequence.append(events[0])
    changed = copy.deepcopy(events[1])
    changed["conversation_id"] = "conversation-2"
    with pytest.raises(AssistantEventContractError) as owner_error:
        sequence.append(changed)
    assert owner_error.value.error_code == "EVENT_OWNERSHIP_MISMATCH"

    sequence = AssistantEventSequence()
    for raw in events[:6]:
        if raw["type"] == "tool.completed":
            continue
        sequence.append({**raw, "sequence": len(sequence.events) + 1})
    answer_started = copy.deepcopy(events[6])
    answer_started["sequence"] = len(sequence.events) + 1
    with pytest.raises(AssistantEventContractError) as active_tool:
        sequence.append(answer_started)
    assert active_tool.value.error_code == "INVALID_EVENT_ORDER"


def test_terminal_events_reject_later_transport_data() -> None:
    events = _events()
    sequence = AssistantEventSequence()
    for raw in events:
        sequence.append(raw)
    replay = copy.deepcopy(events[7])
    replay["event_id"] = "event-after-terminal"
    replay["sequence"] = 12
    with pytest.raises(AssistantEventContractError) as captured:
        sequence.append(replay)
    assert captured.value.error_code == "EVENT_AFTER_TERMINAL"
