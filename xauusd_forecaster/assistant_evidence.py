"""Deterministic claim-to-citation coverage for Assistant answers."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Literal


ASSISTANT_EVIDENCE_PROTOCOL = "assistant.evidence.v1"
ASSISTANT_EVIDENCE_VALIDATOR_VERSION = "assistant-evidence-validator-v1"
MAX_EVIDENCE_CLAIMS = 12
MAX_CLAIM_CHARACTERS = 4_000
MAX_EVIDENCE_PER_CLAIM = 8

EvidenceValidationMode = Literal[
    "CITATION_COVERAGE",
    "NO_CITABLE_EVIDENCE",
    "INSUFFICIENT_EVIDENCE",
]

_EVIDENCE_ID = re.compile(r"^[A-Za-z0-9:._-]{1,128}$")


class AssistantEvidenceValidationError(ValueError):
    """A model answer could not satisfy the evidence coverage contract."""


@dataclass(frozen=True)
class ValidatedAssistantEvidence:
    answer: str
    evidence_ids: tuple[str, ...]
    receipt: dict[str, Any]


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _ordered_evidence_ids(value: object, *, maximum: int) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)) or len(value) > maximum:
        raise AssistantEvidenceValidationError("Evidence IDs are invalid")
    result: list[str] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, str) or not _EVIDENCE_ID.fullmatch(item):
            raise AssistantEvidenceValidationError("Evidence ID is invalid")
        if item in seen:
            raise AssistantEvidenceValidationError("Evidence IDs must be unique")
        seen.add(item)
        result.append(item)
    return tuple(result)


def _claim_text(value: object) -> str:
    if not isinstance(value, str):
        raise AssistantEvidenceValidationError("Evidence claim text is invalid")
    normalized = unicodedata.normalize("NFC", value).strip()
    if any(ord(character) < 32 or ord(character) == 127 for character in normalized):
        raise AssistantEvidenceValidationError("Evidence claim must be one safe line")
    normalized = " ".join(normalized.split())
    if not normalized or len(normalized) > MAX_CLAIM_CHARACTERS:
        raise AssistantEvidenceValidationError("Evidence claim text is out of bounds")
    return normalized


def validate_assistant_evidence_claims(
    value: object,
    available_evidence_ids: object,
    *,
    mode: EvidenceValidationMode | None = None,
    max_cited_evidence: int = 20,
) -> ValidatedAssistantEvidence:
    if (
        not isinstance(max_cited_evidence, int)
        or isinstance(max_cited_evidence, bool)
        or not 1 <= max_cited_evidence <= 100
    ):
        raise ValueError("Evidence citation bound is invalid")
    available = _ordered_evidence_ids(available_evidence_ids, maximum=100)
    if not isinstance(value, dict) or set(value) != {"claims"}:
        raise AssistantEvidenceValidationError("Evidence answer envelope is invalid")
    raw_claims = value.get("claims")
    if (
        not isinstance(raw_claims, list)
        or not 1 <= len(raw_claims) <= MAX_EVIDENCE_CLAIMS
    ):
        raise AssistantEvidenceValidationError("Evidence claim count is invalid")
    selected_mode: EvidenceValidationMode = mode or (
        "CITATION_COVERAGE" if available else "NO_CITABLE_EVIDENCE"
    )
    if selected_mode == "CITATION_COVERAGE" and not available:
        raise AssistantEvidenceValidationError("Citation coverage requires evidence")
    if selected_mode in {"NO_CITABLE_EVIDENCE", "INSUFFICIENT_EVIDENCE"} and available:
        raise AssistantEvidenceValidationError("Uncited mode cannot hide available evidence")

    allowed = set(available)
    cited: list[str] = []
    cited_seen: set[str] = set()
    claims: list[dict[str, object]] = []
    texts: list[str] = []
    for index, raw_claim in enumerate(raw_claims, 1):
        if not isinstance(raw_claim, dict) or set(raw_claim) != {"text", "evidence_ids"}:
            raise AssistantEvidenceValidationError("Evidence claim fields are invalid")
        text = _claim_text(raw_claim.get("text"))
        refs = _ordered_evidence_ids(
            raw_claim.get("evidence_ids"), maximum=MAX_EVIDENCE_PER_CLAIM,
        )
        if selected_mode == "CITATION_COVERAGE":
            if not refs or any(item not in allowed for item in refs):
                raise AssistantEvidenceValidationError(
                    "Every evidence-backed claim needs retrieved citations",
                )
        elif refs:
            raise AssistantEvidenceValidationError("Uncited answer mode contains citations")
        for evidence_id in refs:
            if evidence_id not in cited_seen:
                cited_seen.add(evidence_id)
                cited.append(evidence_id)
        if len(cited) > max_cited_evidence:
            raise AssistantEvidenceValidationError("Cited evidence exceeds its bound")
        texts.append(text)
        claims.append({
            "claim_id": f"claim-{index}",
            "line_index": index - 1,
            "text_sha256": _sha256(text),
            "evidence_ids": list(refs),
        })

    answer = "\n".join(texts)
    receipt: dict[str, Any] = {
        "protocol": ASSISTANT_EVIDENCE_PROTOCOL,
        "validator_version": ASSISTANT_EVIDENCE_VALIDATOR_VERSION,
        "mode": selected_mode,
        "claim_count": len(claims),
        "citation_count": sum(len(item["evidence_ids"]) for item in claims),
        "available_evidence_ids": list(available),
        "cited_evidence_ids": cited,
        "claims": claims,
        "coverage_complete": selected_mode == "CITATION_COVERAGE",
        "entailment_status": "NOT_VERIFIED",
        "answer_sha256": _sha256(answer),
    }
    receipt["receipt_sha256"] = _sha256(_canonical_json(receipt))
    return ValidatedAssistantEvidence(answer, tuple(cited), receipt)


def validate_assistant_evidence_model_text(
    value: str,
    available_evidence_ids: object,
    *,
    max_cited_evidence: int = 20,
) -> ValidatedAssistantEvidence:
    if not isinstance(value, str):
        raise AssistantEvidenceValidationError("Evidence model output is invalid")
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as error:
        raise AssistantEvidenceValidationError(
            "Evidence model output must be strict JSON",
        ) from error
    return validate_assistant_evidence_claims(
        parsed,
        available_evidence_ids,
        max_cited_evidence=max_cited_evidence,
    )


def insufficient_evidence_validation(answer: str) -> ValidatedAssistantEvidence:
    return validate_assistant_evidence_claims(
        {"claims": [{"text": answer, "evidence_ids": []}]},
        [],
        mode="INSUFFICIENT_EVIDENCE",
    )
