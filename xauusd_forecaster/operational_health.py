"""Compatibility shim for xauusd_forecaster.runtime.operational_health."""

from xauusd_forecaster.runtime.operational_health import (
    CAPACITY_DEFERRED_THRESHOLD,
    CAPACITY_FAILURE_CODES,
    ERROR_COUNT_THRESHOLD,
    MONITOR_WINDOW,
    RETRY_LOOP_THRESHOLD,
    SEVERITY_ORDER,
    TASK_LABELS,
    TASK_QUEUE_SLA,
    extend_with_component_alerts,
    scheduler_health_snapshot,
)

__all__ = [
    "CAPACITY_DEFERRED_THRESHOLD",
    "CAPACITY_FAILURE_CODES",
    "ERROR_COUNT_THRESHOLD",
    "MONITOR_WINDOW",
    "RETRY_LOOP_THRESHOLD",
    "SEVERITY_ORDER",
    "TASK_LABELS",
    "TASK_QUEUE_SLA",
    "extend_with_component_alerts",
    "scheduler_health_snapshot",
]
