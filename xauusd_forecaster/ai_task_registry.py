"""Compatibility shim for xauusd_forecaster.news.scheduler.task_registry."""

from xauusd_forecaster.news.scheduler.task_registry import (
    AI_TASK_ROUTES,
    AI_TASK_ROUTE_BY_TYPE,
    AiTaskRoute,
    route_for_task,
)

__all__ = [
    "AI_TASK_ROUTES",
    "AI_TASK_ROUTE_BY_TYPE",
    "AiTaskRoute",
    "route_for_task",
]
