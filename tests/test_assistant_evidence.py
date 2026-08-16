from __future__ import annotations

import json
from pathlib import Path

import pytest

from xauusd_forecaster.assistant_evidence import (
    AssistantEvidenceValidationError,
    validate_assistant_evidence_claims,
    validate_assistant_evidence_model_text,
)


FIXTURE = Path(__file__).parent / "fixtures" / "assistant_evidence_validation.json"


def test_python_evidence_receipts_match_the_cross_runtime_contract() -> None:
    cases = json.loads(FIXTURE.read_text(encoding="utf-8"))["cases"]
    for case in cases:
        result = validate_assistant_evidence_claims(
            case["input"],
            case["available_evidence_ids"],
            mode=case["mode"],
            max_cited_evidence=case["max_cited_evidence"],
        )
        assert result.answer == case["answer"]
        assert list(result.evidence_ids) == case["evidence_ids"]
        assert result.receipt == case["receipt"]


@pytest.mark.parametrize(
    "claims,available",
    [
        (
            {"claims": [{"text": "未知引用。", "evidence_ids": ["invented"]}]},
            ["known"],
        ),
        (
            {"claims": [{"text": "缺少引用。", "evidence_ids": []}]},
            ["known"],
        ),
        (
            {
                "claims": [{"text": "有效。", "evidence_ids": ["known"]}],
                "answer": "不允许的自由文本",
            },
            ["known"],
        ),
    ],
)
def test_evidence_contract_rejects_unknown_uncited_and_free_text(
    claims: object,
    available: list[str],
) -> None:
    with pytest.raises(AssistantEvidenceValidationError):
        validate_assistant_evidence_claims(claims, available)


def test_available_packet_and_cited_subset_have_independent_bounds() -> None:
    available = [f"evidence-{index}" for index in range(20)]
    result = validate_assistant_evidence_claims(
        {"claims": [{"text": "只引用一项。", "evidence_ids": [available[0]]}]},
        available,
        max_cited_evidence=12,
    )
    assert result.evidence_ids == (available[0],)
    assert result.receipt["available_evidence_ids"] == available


@pytest.mark.parametrize("opening_fence", ["```json", "```JSON", "```"])
def test_model_output_accepts_one_json_fence_without_changing_validation(
    opening_fence: str,
) -> None:
    raw = json.dumps({
        "claims": [{"text": "有效回答。", "evidence_ids": ["known"]}],
    }, ensure_ascii=False)

    direct = validate_assistant_evidence_model_text(raw, ["known"])
    fenced = validate_assistant_evidence_model_text(
        f"{opening_fence}\n{raw}\n```",
        ["known"],
    )

    assert fenced == direct


@pytest.mark.parametrize(
    "model_text",
    [
        "not json",
        'prefix\n```json\n{"claims": []}\n```',
        '```python\n{"claims": []}\n```',
        '```json\n{"claims": []}\n```\ntrailing',
    ],
)
def test_model_output_rejects_non_json_or_non_wrapper_text(model_text: str) -> None:
    with pytest.raises(AssistantEvidenceValidationError, match="strict JSON"):
        validate_assistant_evidence_model_text(model_text, [])


@pytest.mark.parametrize("mode", ["UNKNOWN", ""])
def test_evidence_validation_rejects_an_unknown_runtime_mode(mode: str) -> None:
    with pytest.raises(AssistantEvidenceValidationError, match="mode"):
        validate_assistant_evidence_claims(
            {"claims": [{"text": "回答。", "evidence_ids": []}]},
            [],
            mode=mode,  # type: ignore[arg-type]
        )
