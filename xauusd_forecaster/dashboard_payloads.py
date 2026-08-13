"""Shared bounded-payload policies for dashboard producers and mirrors."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any


def bounded_evidence_window(
    rows: Sequence[Mapping[str, Any]], limit: int,
) -> list[Mapping[str, Any]]:
    """Keep used and unused evidence inspectable inside one bounded payload.

    Input order remains authoritative within each group. When truncation is
    required, half of the window is reserved for each visibility state and an
    undersubscribed side yields its unused capacity to the other side.
    """
    if limit < 0:
        raise ValueError("evidence window limit must not be negative")
    if limit == 0:
        return []
    if len(rows) <= limit:
        return list(rows)

    indexed = list(enumerate(rows))
    seen = [index for index, row in indexed if bool(row.get("model_seen"))]
    unseen = [index for index, row in indexed if not bool(row.get("model_seen"))]

    seen_quota = min(len(seen), limit // 2)
    unseen_quota = min(len(unseen), limit - seen_quota)
    remaining = limit - seen_quota - unseen_quota
    if remaining:
        seen_quota += min(remaining, len(seen) - seen_quota)
        remaining = limit - seen_quota - unseen_quota
    if remaining:
        unseen_quota += min(remaining, len(unseen) - unseen_quota)

    selected = set(seen[:seen_quota]) | set(unseen[:unseen_quota])
    return [row for index, row in indexed if index in selected]
