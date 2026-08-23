"""Compatibility shim for xauusd_forecaster.news.collection.pruning."""

from xauusd_forecaster.news.collection.pruning import (
    NewsPrunePlan,
    build_news_prune_plan,
    prune_unused_news,
)

__all__ = [
    "NewsPrunePlan",
    "build_news_prune_plan",
    "prune_unused_news",
]
