"""Frozen Shadow execution-cost assumptions for cTrader XAUUSD."""

from __future__ import annotations

import math


COMMISSION_USD_PER_MILLION_USD_VOLUME = 30.0
COMMISSION_RATE_PER_SIDE = COMMISSION_USD_PER_MILLION_USD_VOLUME / 1_000_000.0
ROUND_TRIP_COMMISSION_LOG_COST = -math.log1p(-(2.0 * COMMISSION_RATE_PER_SIDE))
COMMISSION_STATUS = "CTRADER_USD30_PER_MILLION_EACH_SIDE"
SLIPPAGE_STATUS = "ZERO_ASSUMED_SHADOW"


def net_shadow_log_return(gross_quote_log_return: float) -> float:
    """Deduct the entry and exit commissions from a Bid/Ask log return.

    Bid/Ask already accounts for spread.  The configured commission is charged
    on USD volume on both executions.  At a flat price that is 0.006% round
    trip; the log equivalent keeps the existing additive return convention.
    """
    return float(gross_quote_log_return) - ROUND_TRIP_COMMISSION_LOG_COST


def round_trip_commission_usd(entry_notional_usd: float, exit_notional_usd: float) -> float:
    """Return the USD commission for two positive execution notionals."""
    if entry_notional_usd < 0 or exit_notional_usd < 0:
        raise ValueError("execution notionals must be non-negative")
    return (entry_notional_usd + exit_notional_usd) * COMMISSION_RATE_PER_SIDE
