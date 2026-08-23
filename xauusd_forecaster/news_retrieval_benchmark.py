"""Compatibility shim for xauusd_forecaster.news.retrieval.benchmark."""

from xauusd_forecaster.news.retrieval.benchmark import (
    BENCHMARK_NEGATIVE_CASES,
    BENCHMARK_POSITIVE_CASES,
    BENCHMARK_SCHEMA_VERSION,
    BENCHMARK_TOP_K,
    evaluate_candidate_retrieval,
    load_benchmark_manifest,
    score_candidate_rankings,
)

__all__ = [
    "BENCHMARK_NEGATIVE_CASES",
    "BENCHMARK_POSITIVE_CASES",
    "BENCHMARK_SCHEMA_VERSION",
    "BENCHMARK_TOP_K",
    "evaluate_candidate_retrieval",
    "load_benchmark_manifest",
    "score_candidate_rankings",
]
