"""Canonical model routes for scheduler-owned news AI tasks."""

from __future__ import annotations

from dataclasses import dataclass

from .annotation import (
    DEFAULT_GEMINI_MODEL,
    FALLBACK_GEMINI_MODEL,
    TITLE_TRANSLATION_MODELS,
)
from .news_impact import IMPACT_MODEL


@dataclass(frozen=True)
class AiTaskRoute:
    task_type: str
    models: tuple[str, ...]
    semantic_owner: str
    priority_reserve_models: tuple[str, ...] = ()


AI_TASK_ROUTES = (
    AiTaskRoute(
        "ACTIVE_ANNOTATION",
        (DEFAULT_GEMINI_MODEL, FALLBACK_GEMINI_MODEL),
        "NEWS_SEMANTICS",
        (DEFAULT_GEMINI_MODEL,),
    ),
    AiTaskRoute("ACTIVE_IMPACT", (IMPACT_MODEL,), "NEWS_EVENT_IDENTITY"),
    AiTaskRoute("TITLE_TRANSLATION", TITLE_TRANSLATION_MODELS, "DISPLAY_ONLY"),
)

AI_TASK_ROUTE_BY_TYPE = {route.task_type: route for route in AI_TASK_ROUTES}
if len(AI_TASK_ROUTE_BY_TYPE) != len(AI_TASK_ROUTES):
    raise RuntimeError("AI task registry contains duplicate task types")


def route_for_task(task_type: str) -> AiTaskRoute:
    try:
        return AI_TASK_ROUTE_BY_TYPE[task_type]
    except KeyError as error:
        raise ValueError(f"scheduler task has no model route: {task_type}") from error
