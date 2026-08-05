"""Compatibility adapter to the single received-time executable label authority."""

from __future__ import annotations

from datetime import datetime
import math
from typing import Iterable

from .executable_label import build_executable_label_v2
from .market import MarketObservation
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
    """Adapt timestamp-only archives to the common V2 builder.

    Historical XAUTK002 rows contain one observable timestamp, so it is used as
    both event and receipt time.  Live Forward labels call the same builder with
    their independently recorded timestamps.
    """
    if not math.isfinite(u5) or u5 <= 0:
        raise ValueError("u5 must be positive and finite")
    if not math.isfinite(commission_round_trip_log) or commission_round_trip_log < 0:
        raise ValueError("commission_round_trip_log must be finite and non-negative")
    observations = [
        MarketObservation(quote.timestamp, quote.timestamp, quote.bid, quote.ask)
        for quote in quotes
    ]
    label = build_executable_label_v2(
        decision_time=decision_time, quotes=observations,
        signal_expiry_seconds=signal_expiry_seconds, hold_minutes=hold_minutes,
        maximum_healthy_gap_seconds=maximum_healthy_gap_seconds,
    )
    if label.outcome_status != "VALID":
        return None
    return OutcomeLabel(
        decision_id=decision_id,
        label_time=label.exit_received_time,
        label_contract_version="received-time-executable-30m-v2",
        long_return_u5=(label.long_quote_return - commission_round_trip_log) / u5,
        short_return_u5=(label.short_quote_return - commission_round_trip_log) / u5,
        mfe_long_u5=(label.long_mfe - commission_round_trip_log) / u5,
        mae_long_u5=(label.long_mae - commission_round_trip_log) / u5,
        mfe_short_u5=(label.short_mfe - commission_round_trip_log) / u5,
        mae_short_u5=(label.short_mae - commission_round_trip_log) / u5,
        maximum_spread=label.maximum_spread,
        quote_coverage=label.quote_coverage,
        ambiguity_state=label.ambiguity_state,
    )
