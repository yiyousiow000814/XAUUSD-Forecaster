"""Shared bounded-payload policies for dashboard producers and mirrors."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any


def bounded_evidence_window(
    rows: Sequence[Mapping[str, Any]], limit: int,
) -> list[Mapping[str, Any]]:
    """Keep current, used, and unused evidence inspectable in a bounded payload.

    Current model-eligible events are retained first because their headline
    count is an actionable dashboard state, not merely a historical total.
    Remaining capacity is split between used and unused evidence. Input order
    remains authoritative within every group.
    """
    if limit < 0:
        raise ValueError("evidence window limit must not be negative")
    if limit == 0:
        return []
    if len(rows) <= limit:
        return list(rows)

    indexed = list(enumerate(rows))
    current = [
        index for index, row in indexed if bool(row.get("broad_model_eligible"))
    ]
    selected = set(current[:limit])
    remaining_limit = limit - len(selected)
    if remaining_limit == 0:
        return [row for index, row in indexed if index in selected]

    seen = [
        index for index, row in indexed
        if index not in selected and bool(row.get("model_seen"))
    ]
    unseen = [
        index for index, row in indexed
        if index not in selected and not bool(row.get("model_seen"))
    ]

    seen_quota = min(len(seen), remaining_limit // 2)
    unseen_quota = min(len(unseen), remaining_limit - seen_quota)
    remaining = remaining_limit - seen_quota - unseen_quota
    if remaining:
        seen_quota += min(remaining, len(seen) - seen_quota)
        remaining = remaining_limit - seen_quota - unseen_quota
    if remaining:
        unseen_quota += min(remaining, len(unseen) - unseen_quota)

    selected.update(seen[:seen_quota])
    selected.update(unseen[:unseen_quota])
    return [row for index, row in indexed if index in selected]
