"""One received-time executable label authority for repair and live settling."""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from typing import Iterable

from .market import MarketObservation


@dataclass(frozen=True)
class ExecutableLabelV2:
    outcome_status: str
    reason_codes: tuple[str, ...]
    entry_event_time: datetime | None = None
    entry_received_time: datetime | None = None
    entry_receipt_delay_seconds: float | None = None
    exit_event_time: datetime | None = None
    exit_received_time: datetime | None = None
    exit_receipt_delay_seconds: float | None = None
    maximum_event_gap: float | None = None
    maximum_receipt_gap: float | None = None
    quote_coverage: float | None = None
    ambiguity_state: str = "UNREPAIRABLE"
    gross_midpoint_direction_move: float | None = None
    long_quote_return: float | None = None
    short_quote_return: float | None = None
    spread_quote_cost: float | None = None
    long_mfe: float | None = None
    long_mae: float | None = None
    short_mfe: float | None = None
    short_mae: float | None = None
    maximum_spread: float | None = None
    break_even_commission_long: float | None = None
    break_even_commission_short: float | None = None
    commission_status: str = "UNCONFIGURED"
    slippage_status: str = "UNAVAILABLE_SHADOW"

    def payload(self) -> dict:
        return asdict(self)


def _invalid(*reasons: str) -> ExecutableLabelV2:
    return ExecutableLabelV2(
        outcome_status="UNREPAIRABLE",
        reason_codes=tuple(dict.fromkeys(reasons)),
    )


def build_executable_label_v2(
    *,
    decision_time: datetime,
    quotes: Iterable[MarketObservation],
    signal_expiry_seconds: int = 20,
    hold_minutes: int = 30,
    maximum_healthy_gap_seconds: int = 60,
    maximum_clock_skew_seconds: float = 20.0,
    maximum_quote_freshness_seconds: float = 20.0,
) -> ExecutableLabelV2:
    """Build one label using only the order in which quotes were received.

    Entry and exit eligibility are both receipt-clock decisions.  Event time is
    retained for clock, freshness, and path diagnostics but never makes a late
    quote executable retroactively.  The clock tolerance is aligned with quote
    freshness because cTrader server time can lead the local receipt clock by
    several seconds without exposing the quote before it was received.
    """
    rows = sorted(quotes, key=lambda q: (q.received_time, q.event_time))
    expiry = decision_time + timedelta(seconds=signal_expiry_seconds)
    candidates = [
        quote for quote in rows
        if decision_time < quote.received_time <= expiry
    ]
    if not candidates:
        return _invalid("NO_ENTRY_RECEIVED_WITHIN_EXPIRY")
    entry = candidates[0]
    entry_delay = (entry.received_time - entry.event_time).total_seconds()
    entry_freshness = abs((entry.received_time - entry.event_time).total_seconds())
    if entry.event_time - entry.received_time > timedelta(seconds=maximum_clock_skew_seconds):
        return _invalid("ENTRY_EVENT_CLOCK_AHEAD")
    if entry_freshness > maximum_quote_freshness_seconds:
        return _invalid("ENTRY_QUOTE_STALE_ON_RECEIPT")

    target = entry.received_time + timedelta(minutes=hold_minutes)
    exits = [quote for quote in rows if quote.received_time >= target]
    if not exits:
        return _invalid("NO_EXIT_RECEIVED_AFTER_HORIZON")
    exit_quote = exits[0]
    exit_delay = (exit_quote.received_time - exit_quote.event_time).total_seconds()
    if exit_quote.event_time - exit_quote.received_time > timedelta(seconds=maximum_clock_skew_seconds):
        return _invalid("EXIT_EVENT_CLOCK_AHEAD")
    if abs(exit_delay) > maximum_quote_freshness_seconds:
        return _invalid("EXIT_QUOTE_STALE_ON_RECEIPT")

    path = [
        quote for quote in rows
        if entry.received_time <= quote.received_time <= exit_quote.received_time
    ]
    if not path:
        return _invalid("EMPTY_RECEIPT_PATH")
    receipt_gaps = [
        (right.received_time - left.received_time).total_seconds()
        for left, right in zip(path, path[1:])
    ]
    event_path = sorted(path, key=lambda q: (q.event_time, q.received_time))
    event_gaps = [
        (right.event_time - left.event_time).total_seconds()
        for left, right in zip(event_path, event_path[1:])
    ]
    maximum_receipt_gap = max(receipt_gaps, default=0.0)
    maximum_event_gap = max(event_gaps, default=0.0)
    span = max(1.0, (exit_quote.received_time - entry.received_time).total_seconds())
    covered = sum(min(maximum_healthy_gap_seconds, max(0.0, gap)) for gap in receipt_gaps)
    coverage = min(1.0, covered / span)
    ambiguity = "NONE" if maximum_receipt_gap <= maximum_healthy_gap_seconds else "QUOTE_GAPS"

    entry_mid = (entry.bid + entry.ask) / 2.0
    exit_mid = (exit_quote.bid + exit_quote.ask) / 2.0
    midpoint_move = math.log(exit_mid / entry_mid)
    long_path = [math.log(quote.bid / entry.ask) for quote in path]
    short_path = [math.log(entry.bid / quote.ask) for quote in path]
    long_return = long_path[-1]
    short_return = short_path[-1]
    quote_cost = -(long_return + short_return) / 2.0
    return ExecutableLabelV2(
        outcome_status="VALID",
        reason_codes=(),
        entry_event_time=entry.event_time,
        entry_received_time=entry.received_time,
        entry_receipt_delay_seconds=entry_delay,
        exit_event_time=exit_quote.event_time,
        exit_received_time=exit_quote.received_time,
        exit_receipt_delay_seconds=exit_delay,
        maximum_event_gap=maximum_event_gap,
        maximum_receipt_gap=maximum_receipt_gap,
        quote_coverage=coverage,
        ambiguity_state=ambiguity,
        gross_midpoint_direction_move=midpoint_move,
        long_quote_return=long_return,
        short_quote_return=short_return,
        spread_quote_cost=quote_cost,
        long_mfe=max(long_path),
        long_mae=min(long_path),
        short_mfe=max(short_path),
        short_mae=min(short_path),
        maximum_spread=max(quote.ask - quote.bid for quote in path),
        break_even_commission_long=max(0.0, long_return),
        break_even_commission_short=max(0.0, short_return),
    )
