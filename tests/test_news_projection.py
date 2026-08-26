import json
from pathlib import Path

import pytest

from xauusd_forecaster.news_projection import (
    NEWS_PROJECTION_CONTRACT_VERSION,
    split_news_rows,
    receipt_digest,
    receipt_payload_hash,
)


FIXTURE = Path(__file__).parent / "fixtures" / "news_projection_receipt_vectors.json"


def test_receipt_vectors_are_cross_runtime_canonical() -> None:
    vectors = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert vectors["contract_version"] == NEWS_PROJECTION_CONTRACT_VERSION
    for vector in vectors["payload_vectors"]:
        assert receipt_payload_hash(vector["value"]) == vector["expected_hash"]
    assert receipt_digest(
        [vectors["payload_vectors"][0]["value"]],
        [vectors["payload_vectors"][1]["value"]],
    ) == vectors["expected_receipt_digest"]


def test_receipt_numbers_follow_json_number_semantics() -> None:
    assert receipt_payload_hash({"value": 0}) == receipt_payload_hash({"value": 0.0})
    assert receipt_payload_hash({"value": 0}) == receipt_payload_hash({"value": -0.0})
    assert receipt_payload_hash({"value": 1}) == receipt_payload_hash({"value": 1.0})
    with pytest.raises(ValueError, match="safe-integer"):
        receipt_payload_hash({"value": 9_007_199_254_740_992})
    with pytest.raises(ValueError, match="finite"):
        receipt_payload_hash({"value": float("nan")})


def test_receipt_object_order_is_not_semantic() -> None:
    assert receipt_payload_hash({"z": 1, "a": 2}) == receipt_payload_hash({"a": 2, "z": 1})


def test_detail_identity_is_content_addressed_within_one_source_revision() -> None:
    base = {
        "source": "example", "source_item_id": "same", "revision_number": 1,
        "headline": "Original", "body": "immutable body",
    }
    changed = {**base, "body": "corrected immutable body"}

    first_index, first_details = split_news_rows([base])
    repeat_index, repeat_details = split_news_rows([dict(base)])
    changed_index, changed_details = split_news_rows([changed])

    assert first_index[0]["detail_key"] == first_details[0]["detail_key"]
    assert repeat_index[0]["detail_key"] == first_index[0]["detail_key"]
    assert repeat_details[0]["detail_hash"] == first_details[0]["detail_hash"]
    assert changed_index[0]["detail_key"] != first_index[0]["detail_key"]
    assert changed_details[0]["detail_hash"] != first_details[0]["detail_hash"]
