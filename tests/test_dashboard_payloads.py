from __future__ import annotations

import pytest

from xauusd_forecaster.dashboard_payloads import bounded_evidence_window


@pytest.mark.parametrize(
    ("seen_count", "unseen_count", "limit", "expected_seen", "expected_unseen"),
    [
        (97, 105, 60, 60, 60),
        (97, 105, 100, 97, 100),
        (4, 100, 60, 4, 60),
        (100, 3, 60, 60, 3),
        (2, 2, 60, 2, 2),
    ],
)
def test_bounded_evidence_window_keeps_each_visibility_state_inspectable(
    seen_count: int,
    unseen_count: int,
    limit: int,
    expected_seen: int,
    expected_unseen: int,
) -> None:
    rows = [
        {"event_key": f"seen-{index}", "model_seen": True}
        for index in range(seen_count)
    ] + [
        {"event_key": f"unseen-{index}", "model_seen": False}
        for index in range(unseen_count)
    ]

    bounded = bounded_evidence_window(rows, limit)

    assert len(bounded) == expected_seen + expected_unseen
    assert sum(bool(row["model_seen"]) for row in bounded) == expected_seen
    assert sum(not bool(row["model_seen"]) for row in bounded) == expected_unseen
    assert [row["event_key"] for row in bounded if row["model_seen"]] == [
        f"seen-{index}" for index in range(expected_seen)
    ]
    assert [row["event_key"] for row in bounded if not row["model_seen"]] == [
        f"unseen-{index}" for index in range(expected_unseen)
    ]


def test_bounded_evidence_window_rejects_invalid_limit() -> None:
    with pytest.raises(ValueError, match="must not be negative"):
        bounded_evidence_window([], -1)


def test_bounded_evidence_window_retains_every_current_event_before_history(
) -> None:
    rows = [
        {
            "event_key": f"seen-{index}",
            "model_seen": True,
            "broad_model_eligible": False,
        }
        for index in range(100)
    ] + [
        {
            "event_key": f"unseen-{index}",
            "model_seen": False,
            "broad_model_eligible": 70 <= index < 86,
        }
        for index in range(100)
    ]

    bounded = bounded_evidence_window(rows, 60)

    assert len(bounded) == 120
    assert {
        row["event_key"] for row in bounded if row["broad_model_eligible"]
    } == {f"unseen-{index}" for index in range(70, 86)}
    assert sum(bool(row["model_seen"]) for row in bounded) == 60
    assert sum(not bool(row["model_seen"]) for row in bounded) == 60
