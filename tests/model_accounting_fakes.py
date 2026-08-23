from __future__ import annotations

from collections.abc import Callable

from xauusd_forecaster.ai.model_gateway import ModelRequestAccountant, ModelRequestUsage


class CallbackModelAccountant(ModelRequestAccountant):
    """Test-only adapter for observing or accepting model reservations."""

    def __init__(self, callback: Callable[[ModelRequestUsage], bool]) -> None:
        self.callback = callback

    def reserve(self, usage: ModelRequestUsage) -> bool:
        return self.callback(usage)
