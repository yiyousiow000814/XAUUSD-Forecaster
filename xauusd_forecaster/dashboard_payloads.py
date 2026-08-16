"""Shared bounded-payload policies for dashboard producers and mirrors."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any


def bounded_evidence_window(
    rows: Sequence[Mapping[str, Any]], per_state_limit: int,
) -> list[Mapping[str, Any]]:
    """Keep an independent window for used and unused evidence.

    Current model-eligible events are retained first because their headline
    count is an actionable dashboard state, not merely a historical total. Each
    visibility state then receives up to ``per_state_limit`` rows instead of
    sharing one combined allowance. Input order remains authoritative within
    every group. A state may exceed its limit only when retaining every current
    event requires it.
    """
    if per_state_limit < 0:
        raise ValueError("evidence window limit must not be negative")

    indexed = list(enumerate(rows))
    current = [
        index for index, row in indexed if bool(row.get("broad_model_eligible"))
    ]
    selected = set(current)

    seen = [
        index for index, row in indexed
        if index not in selected and bool(row.get("model_seen"))
    ]
    unseen = [
        index for index, row in indexed
        if index not in selected and not bool(row.get("model_seen"))
    ]
    current_seen = sum(bool(rows[index].get("model_seen")) for index in current)
    current_unseen = len(current) - current_seen
    selected.update(seen[:max(0, per_state_limit - current_seen)])
    selected.update(unseen[:max(0, per_state_limit - current_unseen)])
    return [row for index, row in indexed if index in selected]
