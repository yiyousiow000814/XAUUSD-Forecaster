"""Executable fixed-horizon counterfactual labels."""

from __future__ import annotations

import math
from datetime import datetime, timedelta
from typing import Iterable

from .models import OutcomeLabel
from .quotes import Quote


def build_fixed_horizon_label(
    *,
    decision_id: str,
    decision_time: datetime,
    quotes: Iterable[Quote],
    u5: float,
    commission_round_trip_log: float,
    signal_expiry_seconds: int = 20,
    hold_minutes: int = 30,
    maximum_healthy_gap_seconds: int = 60,
) -> OutcomeLabel | None:
    """Build Long and Short labels from one shared executable quote path.

    The entry is the first quote at or after the decision event and before the
    signal expires. The exit is the first quote at or after 30 minutes from the
    actual entry. Returning ``None`` means the quote path cannot support the
    frozen label contract.
    """
    if u5 <= 0 or not math.isfinite(u5):
        raise ValueError("u5 must be positive and finite")
    if commission_round_trip_log < 0 or not math.isfinite(
        commission_round_trip_log
    ):
        raise ValueError("commission_round_trip_log must be finite and non-negative")
    if signal_expiry_seconds <= 0 or hold_minutes <= 0:
        raise ValueError("expiry and hold duration must be positive")
    if maximum_healthy_gap_seconds <= 0:
        raise ValueError("maximum_healthy_gap_seconds must be positive")

    expiry = decision_time + timedelta(seconds=signal_expiry_seconds)
    entry: Quote | None = None
    path: list[Quote] = []
    terminal_time: datetime | None = None

    for quote in quotes:
        if quote.timestamp <= decision_time:
            continue
        if entry is None:
            if quote.timestamp > expiry:
                return None
            entry = quote
            terminal_time = entry.timestamp + timedelta(minutes=hold_minutes)
        path.append(quote)
        if quote.timestamp >= terminal_time:
            break

    if entry is None or not path or path[-1].timestamp < terminal_time:
        return None

    long_values = [
        math.log(quote.bid / entry.ask) - commission_round_trip_log
        for quote in path
    ]
    short_values = [
        math.log(entry.bid / quote.ask) - commission_round_trip_log
        for quote in path
    ]
    maximum_spread = max(quote.ask - quote.bid for quote in path)

    span_seconds = (path[-1].timestamp - entry.timestamp).total_seconds()
    covered_seconds = 0.0
    for previous, current in zip(path, path[1:]):
        gap = (current.timestamp - previous.timestamp).total_seconds()
        if gap < 0:
            raise ValueError("quotes must be chronological")
        covered_seconds += min(gap, maximum_healthy_gap_seconds)
    coverage = min(1.0, covered_seconds / span_seconds) if span_seconds else 0.0
    ambiguity = "NONE" if coverage >= 0.99 else "QUOTE_GAPS"

    return OutcomeLabel(
        decision_id=decision_id,
        label_time=path[-1].timestamp,
        label_contract_version="executable-fixed-30m-v2",
        long_return_u5=long_values[-1] / u5,
        short_return_u5=short_values[-1] / u5,
        mfe_long_u5=max(long_values) / u5,
        mae_long_u5=min(long_values) / u5,
        mfe_short_u5=max(short_values) / u5,
        mae_short_u5=min(short_values) / u5,
        maximum_spread=maximum_spread,
        quote_coverage=coverage,
        ambiguity_state=ambiguity,
    )
