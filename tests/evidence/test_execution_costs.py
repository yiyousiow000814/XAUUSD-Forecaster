import math

import pytest

from xauusd_forecaster.execution_costs import (
    ROUND_TRIP_COMMISSION_LOG_COST,
    net_shadow_log_return,
    round_trip_commission_usd,
)


def test_ctrader_commission_is_charged_on_both_execution_sides() -> None:
    assert round_trip_commission_usd(427_000.0, 427_000.0) == pytest.approx(25.62)
    assert math.exp(-ROUND_TRIP_COMMISSION_LOG_COST) - 1.0 == pytest.approx(-0.00006)
    assert net_shadow_log_return(0.0) == pytest.approx(math.log(0.99994))


def test_negative_notional_is_rejected() -> None:
    with pytest.raises(ValueError):
        round_trip_commission_usd(-1.0, 1.0)
