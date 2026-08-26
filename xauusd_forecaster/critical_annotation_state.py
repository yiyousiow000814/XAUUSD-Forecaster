"""Compatibility shim for xauusd_forecaster.news.semantics.critical_state."""

from xauusd_forecaster.news.semantics.critical_state import (
    AnnotationMaterializationContract,
    INSTALL_VERSION,
    RETIRED_ERROR,
    annotation_materialization_contract,
    annotation_queue_snapshot,
    install_annotation_job_count_schema,
    install_critical_annotation_state_schema,
    news_current_counts,
    record_annotation_completion,
    refresh_news_cluster_state,
    refresh_news_revision_state,
    scheduler_state_counts,
)

__all__ = [
    "AnnotationMaterializationContract",
    "INSTALL_VERSION",
    "RETIRED_ERROR",
    "annotation_materialization_contract",
    "annotation_queue_snapshot",
    "install_annotation_job_count_schema",
    "install_critical_annotation_state_schema",
    "news_current_counts",
    "record_annotation_completion",
    "refresh_news_cluster_state",
    "refresh_news_revision_state",
    "scheduler_state_counts",
]
