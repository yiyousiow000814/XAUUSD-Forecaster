"""Canonical model routes for scheduler-owned news AI tasks."""

from __future__ import annotations

from dataclasses import dataclass

from xauusd_forecaster.news.annotation.product import (
    DEFAULT_GEMINI_MODEL,
    DEFAULT_GEMMA_MODEL,
    FALLBACK_GEMINI_MODEL,
    TITLE_TRANSLATION_MODELS,
)
from xauusd_forecaster.news.annotation.impact import IMPACT_MODEL
from xauusd_forecaster.ai.provider_registry import GEMINI_EMBEDDING_MODEL


@dataclass(frozen=True)
class AiTaskRoute:
    task_type: str
    models: tuple[str, ...]
    semantic_owner: str
    priority_reserve_models: tuple[str, ...] = ()
    provenance_source: str = "INHERENT_LIVE"
    quota_pressure_tasks: tuple[str, ...] = ()


AI_TASK_ROUTES = (
    AiTaskRoute(
        task_type="ACTIVE_ANNOTATION",
        models=(DEFAULT_GEMINI_MODEL, FALLBACK_GEMINI_MODEL),
        semantic_owner="NEWS_SEMANTICS",
        priority_reserve_models=(DEFAULT_GEMINI_MODEL,),
        provenance_source="SOURCE_RECEIPT_TIME",
        quota_pressure_tasks=("ACTIVE_ANNOTATION",),
    ),
    AiTaskRoute(
        task_type="ACTIVE_IMPACT", models=(IMPACT_MODEL,),
        semantic_owner="NEWS_EVENT_IDENTITY",
        provenance_source="ACTIVE_ANNOTATION",
        quota_pressure_tasks=("ACTIVE_IMPACT",),
    ),
    AiTaskRoute(
        task_type="TITLE_TRANSLATION", models=TITLE_TRANSLATION_MODELS,
        semantic_owner="DISPLAY_ONLY",
        provenance_source="ACTIVE_ANNOTATION",
        quota_pressure_tasks=("TITLE_TRANSLATION",),
    ),
    AiTaskRoute(
        task_type="DAILY_BRIEF", models=(DEFAULT_GEMMA_MODEL,),
        semantic_owner="DISPLAY_ONLY", provenance_source="INHERENT_LIVE",
    ),
    AiTaskRoute(
        task_type="NEWS_EMBEDDING", models=(GEMINI_EMBEDDING_MODEL,),
        semantic_owner="NEWS_IDENTITY_RETRIEVAL",
        provenance_source="CALLER_OPERATION",
        quota_pressure_tasks=("ACTIVE_IMPACT",),
    ),
)

AI_TASK_ROUTE_BY_TYPE = {route.task_type: route for route in AI_TASK_ROUTES}
if len(AI_TASK_ROUTE_BY_TYPE) != len(AI_TASK_ROUTES):
    raise RuntimeError("AI task registry contains duplicate task types")


def route_for_task(task_type: str) -> AiTaskRoute:
    try:
        return AI_TASK_ROUTE_BY_TYPE[task_type]
    except KeyError as error:
        raise ValueError(f"scheduler task has no model route: {task_type}") from error
