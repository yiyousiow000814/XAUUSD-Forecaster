import json
from pathlib import Path

import pytest

from xauusd_forecaster.named_reference_benchmark import (
    EXPECTED_HARD_GUARD_CASES,
    EXPECTED_REVIEW_CASES,
    benchmark_manifest_sha256,
    decode_named_reference_review,
    load_named_reference_benchmark,
    named_reference_review_payload,
    score_named_reference_runs,
)


FIXTURE = Path(__file__).parent / "fixtures" / "news_named_reference_benchmark.json"


def test_frozen_named_reference_benchmark_is_balanced_and_grounded():
    manifest = load_named_reference_benchmark(FIXTURE)
    cases = manifest["review_cases"]

    assert len(cases) == EXPECTED_REVIEW_CASES
    assert sum(case.expected == "NAMED_REFERENCE" for case in cases) == 210
    assert sum(case.expected == "PROSE" for case in cases) == 210
    assert len(manifest["hard_guard_cases"]) == EXPECTED_HARD_GUARD_CASES
    assert all(case.candidate in case.source for case in cases)
    assert len(benchmark_manifest_sha256(manifest)) == 64
    assert {
        case.family for case in cases if case.expected == "NAMED_REFERENCE"
    } >= {
        "person", "organization", "product", "acronym", "meeting", "paper",
        "legal_reference", "movie", "book", "song", "game", "episode",
    }


def test_review_payload_and_decoder_require_one_decision_per_case():
    cases = load_named_reference_benchmark(FIXTURE)["review_cases"][:2]
    payload = named_reference_review_payload(cases)
    ids = tuple(case.case_id for case in cases)
    envelope = {"candidates": [{"content": {"parts": [{"text": json.dumps({
        "decisions": ["NAMED_REFERENCE", "PROSE"],
    })}]}}]}

    assert decode_named_reference_review(envelope, ids) == {
        ids[0]: "NAMED_REFERENCE", ids[1]: "PROSE",
    }

    envelope["candidates"][0]["content"]["parts"][0]["text"] = json.dumps({
        "decisions": ["NAMED_REFERENCE"],
    })
    with pytest.raises(ValueError, match="incomplete"):
        decode_named_reference_review(envelope, ids)


def test_gate_counts_false_accepts_false_rejects_and_instability():
    cases = load_named_reference_benchmark(FIXTURE)["review_cases"]
    perfect = {case.case_id: case.expected for case in cases}
    second = dict(perfect)

    passed = score_named_reference_runs(cases, (perfect, second))

    assert passed["gate"] == "PASS"
    assert passed["positive_precision"] == 1.0
    assert passed["positive_recall"] == 1.0
    assert passed["false_accept_count"] == 0
    assert passed["repeated_run_disagreement_count"] == 0

    negative = next(case for case in cases if case.expected == "PROSE")
    positive = next(case for case in cases if case.expected == "NAMED_REFERENCE")
    failed_run = dict(perfect)
    failed_run[negative.case_id] = "NAMED_REFERENCE"
    failed_run[positive.case_id] = "AMBIGUOUS"
    failed = score_named_reference_runs(cases, (failed_run, perfect))

    assert failed["gate"] == "FAIL"
    assert failed["false_accept_count"] == 1
    assert failed["false_reject_count"] == 1
    assert failed["repeated_run_disagreement_count"] == 2
