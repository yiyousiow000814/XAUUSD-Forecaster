"""Compatibility shim for xauusd_forecaster.dashboard.summaries."""

from xauusd_forecaster.dashboard.summaries import (
    DASHBOARD_COUNT_TABLES,
    dashboard_collected_news_sources,
    dashboard_distinct_article_count,
    dashboard_latest_activity,
    dashboard_latest_macro,
    dashboard_macro_source_summary,
    dashboard_news_source_summary,
    dashboard_source_poll_summary,
    dashboard_table_counts,
    dashboard_total_brief_days,
    dashboard_valid_outcome_summary,
    install_dashboard_critical_activity_schema,
    install_dashboard_summary_schema,
)

__all__ = [
    "DASHBOARD_COUNT_TABLES",
    "dashboard_collected_news_sources",
    "dashboard_distinct_article_count",
    "dashboard_latest_activity",
    "dashboard_latest_macro",
    "dashboard_macro_source_summary",
    "dashboard_news_source_summary",
    "dashboard_source_poll_summary",
    "dashboard_table_counts",
    "dashboard_total_brief_days",
    "dashboard_valid_outcome_summary",
    "install_dashboard_critical_activity_schema",
    "install_dashboard_summary_schema",
]
