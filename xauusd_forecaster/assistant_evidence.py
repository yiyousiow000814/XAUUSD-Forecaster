"""Compatibility shim for xauusd_forecaster.assistant.evidence."""

from xauusd_forecaster.assistant.evidence import (
    ASSISTANT_EVIDENCE_PROTOCOL,
    ASSISTANT_EVIDENCE_VALIDATOR_VERSION,
    AssistantEvidenceValidationError,
    EvidenceValidationMode,
    MAX_CLAIM_CHARACTERS,
    MAX_EVIDENCE_CLAIMS,
    MAX_EVIDENCE_PER_CLAIM,
    ValidatedAssistantEvidence,
    insufficient_evidence_validation,
    validate_assistant_evidence_claims,
    validate_assistant_evidence_model_text,
)

__all__ = [
    "ASSISTANT_EVIDENCE_PROTOCOL",
    "ASSISTANT_EVIDENCE_VALIDATOR_VERSION",
    "AssistantEvidenceValidationError",
    "EvidenceValidationMode",
    "MAX_CLAIM_CHARACTERS",
    "MAX_EVIDENCE_CLAIMS",
    "MAX_EVIDENCE_PER_CLAIM",
    "ValidatedAssistantEvidence",
    "insufficient_evidence_validation",
    "validate_assistant_evidence_claims",
    "validate_assistant_evidence_model_text",
]
