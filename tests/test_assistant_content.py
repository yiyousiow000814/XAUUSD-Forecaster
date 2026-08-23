from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from xauusd_forecaster.assistant.content import (
    ASSISTANT_CONTENT_PROTOCOL_VERSION,
    AssistantContentContractError,
    build_assistant_content_document,
    validate_assistant_content_document,
)


FIXTURE = Path(__file__).parent / "fixtures" / "assistant_content_v1.json"
EVIDENCE_IDS = ("preview-evidence-1", "preview-evidence-2")


def _fixture() -> dict[str, object]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _answer(document: dict[str, object]) -> str:
    blocks = document["blocks"]
    assert isinstance(blocks, list)
    return str(blocks[0]["data"]["text"])


def test_shared_content_fixture_validates_every_initial_block_type() -> None:
    document = _fixture()

    parsed = validate_assistant_content_document(
        document, answer=_answer(document), evidence_ids=EVIDENCE_IDS,
    )

    assert parsed["protocol"] == ASSISTANT_CONTENT_PROTOCOL_VERSION
    assert [block["type"] for block in parsed["blocks"]] == [
        "markdown", "metric", "news_card", "news_card", "table", "callout",
    ]
    parsed["blocks"][0]["data"]["text"] = "detached"
    assert _answer(document) != "detached"


def test_builder_is_deterministic_and_binds_cards_to_authoritative_evidence() -> None:
    fixture = _fixture()
    cards = [block["data"] for block in fixture["blocks"] if block["type"] == "news_card"]

    first = build_assistant_content_document(
        _answer(fixture),
        evidence_items=cards,
        evidence_ids=EVIDENCE_IDS,
        retrieval_cutoff="2026-08-15T10:00:00.000Z",
    )
    second = build_assistant_content_document(
        _answer(fixture),
        evidence_items=cards,
        evidence_ids=EVIDENCE_IDS,
        retrieval_cutoff="2026-08-15T10:00:00.000Z",
    )

    assert first == second
    assert [block["type"] for block in first["blocks"]] == [
        "markdown", "metric", "news_card", "news_card", "table",
    ]
    assert first["blocks"] == fixture["blocks"][:-1]


@pytest.mark.parametrize(
    "mutate",
    [
        lambda value: value["blocks"][0]["data"].update(text="changed"),
        lambda value: value["blocks"][2]["data"].update(
            evidence_id="not-in-provenance",
        ),
        lambda value: value["blocks"][2]["data"].update(
            source_url="javascript:alert(1)",
        ),
        lambda value: value["blocks"][4]["data"]["rows"][0].append("extra"),
        lambda value: value["blocks"][5].update(component="UnsafeWidget"),
    ],
)
def test_content_tampering_and_unsafe_render_inputs_fail_closed(mutate) -> None:
    document = _fixture()
    answer = _answer(document)
    mutate(document)

    with pytest.raises(AssistantContentContractError):
        validate_assistant_content_document(
            document, answer=answer, evidence_ids=EVIDENCE_IDS,
        )


def test_text_only_output_remains_structured_without_inventing_evidence() -> None:
    document = build_assistant_content_document("当前问题不需要外部新闻检索。")

    assert [block["type"] for block in document["blocks"]] == ["markdown"]
    assert all(block["type"] != "news_card" for block in document["blocks"])

    forged = copy.deepcopy(document)
    forged["blocks"][0]["data"]["text"] = "<script>alert(1)</script>"
    with pytest.raises(AssistantContentContractError, match="hash"):
        validate_assistant_content_document(forged, answer="当前问题不需要外部新闻检索。")
