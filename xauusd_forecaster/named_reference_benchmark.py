"""Compatibility shim for xauusd_forecaster.news.retrieval.named_reference_benchmark."""

from xauusd_forecaster.news.retrieval.named_reference_benchmark import (
    DECISIONS,
    EXPECTED_HARD_GUARD_CASES,
    EXPECTED_REVIEW_CASES,
    MAXIMUM_DISAGREEMENT_RATE,
    MAXIMUM_FALSE_ACCEPT_RATE,
    MINIMUM_PRECISION,
    MINIMUM_RECALL,
    NamedReferenceBenchmarkCase,
    REVIEW_CONTRACT_VERSION,
    SCHEMA_VERSION,
    benchmark_manifest_sha256,
    decode_named_reference_review,
    load_named_reference_benchmark,
    named_reference_review_payload,
    score_named_reference_runs,
)

__all__ = [
    "DECISIONS",
    "EXPECTED_HARD_GUARD_CASES",
    "EXPECTED_REVIEW_CASES",
    "MAXIMUM_DISAGREEMENT_RATE",
    "MAXIMUM_FALSE_ACCEPT_RATE",
    "MINIMUM_PRECISION",
    "MINIMUM_RECALL",
    "NamedReferenceBenchmarkCase",
    "REVIEW_CONTRACT_VERSION",
    "SCHEMA_VERSION",
    "benchmark_manifest_sha256",
    "decode_named_reference_review",
    "load_named_reference_benchmark",
    "named_reference_review_payload",
    "score_named_reference_runs",
]
