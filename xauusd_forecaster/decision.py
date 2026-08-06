"""Deterministic action gate and non-overlapping shadow-signal lock."""

from __future__ import annotations

from datetime import datetime, timedelta

from .models import Action, DataHealth, Decision, Forecast


def select_recommended_action(forecast: Forecast) -> tuple[Action, str]:
    """Choose the positive post-cost EV direction; keep LCB as diagnostics."""
    if forecast.data_health is not DataHealth.OK:
        return Action.WAIT, f"DATA_{forecast.data_health.value}"

    if forecast.ev_long_u5 == forecast.ev_short_u5:
        return Action.WAIT, "TIED_DIRECTIONAL_EV"

    if forecast.ev_long_u5 > forecast.ev_short_u5:
        if forecast.ev_long_u5 > 0:
            return Action.LONG, "LONG_BEST_POSITIVE_POST_COST_EV"
        return Action.WAIT, "NO_POSITIVE_POST_COST_EV"

    if forecast.ev_short_u5 > 0:
        return Action.SHORT, "SHORT_BEST_POSITIVE_POST_COST_EV"
    return Action.WAIT, "NO_POSITIVE_POST_COST_EV"


class ShadowDecisionGate:
    """Allow at most one user-facing directional signal per 30 minutes."""

    def __init__(self, hold_minutes: int = 30) -> None:
        if hold_minutes <= 0:
            raise ValueError("hold_minutes must be positive")
        self._hold = timedelta(minutes=hold_minutes)
        self._active_until: datetime | None = None

    @property
    def active_until(self) -> datetime | None:
        return self._active_until

    def decide(self, forecast: Forecast) -> Decision:
        recommendation, reason = select_recommended_action(forecast)
        active = (
            self._active_until is not None
            and forecast.decision_time < self._active_until
        )
        if active:
            return Decision(
                forecast=forecast,
                recommended_action=recommendation,
                effective_action=Action.WAIT,
                decision_reason="ACTIVE_SIGNAL",
                active_until=self._active_until,
            )

        if recommendation is Action.WAIT:
            self._active_until = None
            return Decision(
                forecast=forecast,
                recommended_action=recommendation,
                effective_action=Action.WAIT,
                decision_reason=reason,
                active_until=None,
            )

        self._active_until = forecast.decision_time + self._hold
        return Decision(
            forecast=forecast,
            recommended_action=recommendation,
            effective_action=recommendation,
            decision_reason=reason,
            active_until=self._active_until,
        )
