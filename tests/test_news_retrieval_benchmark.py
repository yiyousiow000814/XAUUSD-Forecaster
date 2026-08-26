import json
from pathlib import Path

import pytest

from xauusd_forecaster.news.retrieval.benchmark import (
    BENCHMARK_SCHEMA_VERSION,
    load_benchmark_manifest,
    score_candidate_rankings,
)


FIXTURE = Path(__file__).parent / "fixtures" / "news_candidate_retrieval_benchmark.json"


def _case(index: int, *, positive: bool) -> dict:
    payload = {
        "case_id": f"{'positive' if positive else 'negative'}-{index:03d}",
        "current_annotation_id": f"current-{index:03d}",
        "current_content_hash": "a" * 64,
        "prior_content_hash": "b" * 64,
        "label_basis": (
            "same_verifiable_fact" if positive else "different_occurrence"
        ),
        "reviewed": True,
    }
    if positive:
        payload.update({
            "expected_prior_annotation_id": f"prior-{index:03d}",
            "relation": "SAME_EVENT",
        })
    else:
        payload["forbidden_prior_annotation_id"] = f"prior-{index:03d}"
    return payload


def test_manifest_requires_the_frozen_positive_and_negative_population(tmp_path):
    path = tmp_path / "benchmark.json"
    payload = {
        "schema_version": BENCHMARK_SCHEMA_VERSION,
        "positive_cases": [_case(index, positive=True) for index in range(100)],
        "negative_cases": [_case(index, positive=False) for index in range(100)],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")

    loaded = load_benchmark_manifest(path)

    assert len(loaded["positive_cases"]) == 100
    assert len(loaded["negative_cases"]) == 100

    payload["positive_cases"].pop()
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="100 positives"):
        load_benchmark_manifest(path)


def test_frozen_historical_manifest_has_independent_and_cross_cluster_labels():
    manifest = load_benchmark_manifest(FIXTURE)
    notes = [case["label_note"] for case in manifest["positive_cases"]]

    assert sum(note.startswith("independent collector cluster") for note in notes) == 68
    assert sum(note.startswith("manual cross-cluster evidence review") for note in notes) == 32
    assert {
        case["label_basis"] for case in manifest["negative_cases"]
    } == {"different_release_series"}


def test_candidate_metrics_separate_recall_from_hard_negative_collisions():
    positives = [
        {
            "case_id": "same-event", "current_annotation_id": "current-a",
            "expected_prior_annotation_id": "expected-a",
            "relation": "SAME_EVENT",
        },
        {
            "case_id": "same-episode", "current_annotation_id": "current-b",
            "expected_prior_annotation_id": "expected-b",
            "relation": "SAME_EPISODE",
        },
    ]
    negatives = [
        {
            "case_id": "negative-a", "current_annotation_id": "current-a",
            "forbidden_prior_annotation_id": "forbidden-a",
        },
        {
            "case_id": "negative-b", "current_annotation_id": "current-b",
            "forbidden_prior_annotation_id": "forbidden-b",
        },
    ]
    rankings = {
        "current-a": ("expected-a", "forbidden-a"),
        "current-b": ("other", "expected-b"),
    }

    result = score_candidate_rankings(
        positives, negatives, rankings, top_k=5,
    )

    assert result["recall_at_1"] == 0.5
    assert result["recall_at_5"] == 1.0
    assert result["mrr_at_5"] == 0.75
    assert result["positive_empty_candidate_rate"] == 0.0
    assert result["hard_negative_collision_rate_at_5"] == 0.5
    assert result["by_relation"]["SAME_EVENT"]["recall_at_5"] == 1.0
    assert result["by_relation"]["SAME_EPISODE"]["recall_at_5"] == 1.0
