"""Compatibility shim for xauusd_forecaster.runtime.health."""

from xauusd_forecaster.runtime.health import (
    RuntimeHeartbeatPulse,
    write_runtime_heartbeat,
)

__all__ = [
    "RuntimeHeartbeatPulse",
    "write_runtime_heartbeat",
]
