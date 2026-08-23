"""Compose co-resident owner schemas for the shared local SQLite store."""


def install_shared_store_owner_schemas(connection) -> None:
    """Install owner schemas in the historical ForwardLedger order."""
    from xauusd_forecaster.assistant.capacity import install_assistant_capacity_schema
    from xauusd_forecaster.dashboard.read_models import install_dashboard_read_model_schema
    from xauusd_forecaster.dashboard.summaries import (
        install_dashboard_critical_activity_schema,
        install_dashboard_summary_schema,
    )
    from xauusd_forecaster.evidence.schema import install_v2_schema
    from xauusd_forecaster.news.scheduler.state import install_scheduler_schema

    install_v2_schema(connection)
    install_scheduler_schema(connection)
    install_assistant_capacity_schema(connection)
    install_dashboard_summary_schema(connection)
    install_dashboard_critical_activity_schema(connection)
    install_dashboard_read_model_schema(connection)


def install_shared_store_post_metadata_schema(connection) -> None:
    """Install the critical-annotation schema after runtime metadata exists."""
    from xauusd_forecaster.news.semantics.critical_state import (
        install_critical_annotation_state_schema,
    )

    install_critical_annotation_state_schema(connection)
