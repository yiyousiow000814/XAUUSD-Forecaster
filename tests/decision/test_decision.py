from datetime import datetime, timedelta, timezone

from xauusd_forecaster import (
    Action,
    DataHealth,
    Forecast,
    ShadowDecisionGate,
    select_recommended_action,
)
from xauusd_forecaster.decision.selection import select_post_cost_ev_action


UTC = timezone.utc


def test_post_cost_ev_action_uses_frozen_ev() -> None:
    assert select_post_cost_ev_action(0.109, -0.129) is Action.LONG


def forecast(at: datetime, **overrides: object) -> Forecast:
    values = {
        "decision_id": f"XAU-{at.isoformat()}",
        "decision_time": at,
        "model_version": "test-v1",
        "feature_snapshot_hash": "abc123",
        "ev_long_u5": 0.14,
        "ev_short_u5": -0.08,
        "lcb_long_u5": 0.03,
        "lcb_short_u5": -0.17,
        "uncertainty_long_u5": 0.05,
        "uncertainty_short_u5": 0.05,
        "estimated_cost_long_u5": 0.01,
        "estimated_cost_short_u5": 0.01,
        "data_health": DataHealth.OK,
    }
    values.update(overrides)
    return Forecast(**values)


def test_selects_best_positive_post_cost_ev_without_lcb_gate() -> None:
    at = datetime(2026, 8, 5, 10, 0, tzinfo=UTC)
    assert select_recommended_action(forecast(at))[0] is Action.LONG
    assert (
        select_recommended_action(
            forecast(
                at,
                ev_long_u5=-0.1,
                ev_short_u5=0.2,
                lcb_short_u5=0.01,
            )
        )[0]
        is Action.SHORT
    )
    assert select_recommended_action(forecast(at, lcb_long_u5=-1.0))[0] is Action.LONG
    assert select_recommended_action(
        forecast(at, ev_long_u5=-0.1, ev_short_u5=-0.2)
    )[0] is Action.WAIT


def test_unhealthy_data_and_tied_ev_fail_closed() -> None:
    at = datetime(2026, 8, 5, 10, 0, tzinfo=UTC)
    assert (
        select_recommended_action(forecast(at, data_health=DataHealth.STALE))[0]
        is Action.WAIT
    )
    assert (
        select_recommended_action(
            forecast(at, ev_long_u5=0.1, ev_short_u5=0.1)
        )[0]
        is Action.WAIT
    )


def test_shadow_lock_preserves_recommendation_but_blocks_effective_signal() -> None:
    start = datetime(2026, 8, 5, 10, 0, tzinfo=UTC)
    gate = ShadowDecisionGate()

    first = gate.decide(forecast(start))
    blocked = gate.decide(forecast(start + timedelta(minutes=5)))
    reopened = gate.decide(forecast(start + timedelta(minutes=30)))

    assert first.effective_action is Action.LONG
    assert first.active_until == start + timedelta(minutes=30)
    assert blocked.recommended_action is Action.LONG
    assert blocked.effective_action is Action.WAIT
    assert blocked.decision_reason == "ACTIVE_SIGNAL"
    assert reopened.effective_action is Action.LONG
