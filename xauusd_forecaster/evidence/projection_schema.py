"""SQLite projection schema shared by ledger startup and dashboard readers."""

from __future__ import annotations

import sqlite3

from datetime import UTC, datetime



DASHBOARD_COUNT_TABLES = (
    "decision_events",
    "outcomes",
    "news_revisions",
    "news_annotations",
    "news_title_translations",
    "macro_observations",
    "training_eligibility",
    "model_updates",
    "shadow_trade_intents",
    "shadow_trade_results",
    "repair_batches",
    "derived_market_snapshots",
    "derived_news_feature_snapshots",
    "derived_outcomes",
    "training_eligibility_v2",
    "model_updates_v2",
    "predictions_v2",
    "prediction_scores_v2",
    "news_decision_event_snapshots_v1",
)


def install_dashboard_summary_schema(connection: sqlite3.Connection) -> None:
    """Backfill once, then increment exact append-only dashboard summaries."""
    metadata_exists = connection.execute(
        """SELECT 1 FROM sqlite_master
           WHERE type='table' AND name='dashboard_summary_metadata_v1'"""
    ).fetchone()
    if metadata_exists and connection.execute(
        """SELECT 1 FROM dashboard_summary_metadata_v1
           WHERE key='append_only_backfill_v1'"""
    ).fetchone():
        return

    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS dashboard_summary_metadata_v1 (
            key TEXT PRIMARY KEY,
            installed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS dashboard_table_counts_v1 (
            table_name TEXT PRIMARY KEY,
            row_count INTEGER NOT NULL CHECK(row_count >= 0)
        );
        CREATE TABLE IF NOT EXISTS dashboard_valid_outcome_summary_v1 (
            id INTEGER PRIMARY KEY CHECK(id = 1),
            sample_count INTEGER NOT NULL,
            long_return_sum REAL NOT NULL,
            short_return_sum REAL NOT NULL,
            quote_coverage_sum REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS dashboard_news_article_summary_v1 (
            id INTEGER PRIMARY KEY CHECK(id = 1),
            distinct_article_count INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS dashboard_source_poll_summary_v1 (
            source TEXT PRIMARY KEY,
            total INTEGER NOT NULL,
            ok_count INTEGER NOT NULL,
            partial_count INTEGER NOT NULL,
            error_count INTEGER NOT NULL,
            last_success TEXT
        );
        CREATE TABLE IF NOT EXISTS dashboard_news_source_summary_v1 (
            source TEXT PRIMARY KEY,
            item_count INTEGER NOT NULL,
            revision_count INTEGER NOT NULL,
            full_text_count INTEGER NOT NULL,
            latest_item_time TEXT
        );
        CREATE TABLE IF NOT EXISTS dashboard_macro_source_summary_v1 (
            source TEXT PRIMARY KEY,
            item_count INTEGER NOT NULL,
            revision_count INTEGER NOT NULL,
            latest_item_time TEXT
        );
        """
    )
    with connection:
        for table in DASHBOARD_COUNT_TABLES:
            connection.execute(
                f"""INSERT OR IGNORE INTO dashboard_table_counts_v1
                     (table_name,row_count) SELECT ?,count(*) FROM {table}""",
                (table,),
            )
            connection.execute(
                f"""CREATE TRIGGER IF NOT EXISTS dashboard_count_{table}_insert_v1
                     AFTER INSERT ON {table} BEGIN
                       UPDATE dashboard_table_counts_v1
                       SET row_count=row_count+1 WHERE table_name='{table}';
                     END"""
            )
        connection.execute(
            """INSERT OR IGNORE INTO dashboard_valid_outcome_summary_v1
                 (id,sample_count,long_return_sum,short_return_sum,quote_coverage_sum)
               SELECT 1,count(*),COALESCE(sum(long_return),0),
                      COALESCE(sum(short_return),0),COALESCE(sum(quote_coverage),0)
               FROM outcomes WHERE outcome_status='VALID'"""
        )
        connection.execute(
            """CREATE TRIGGER IF NOT EXISTS dashboard_valid_outcome_insert_v1
               AFTER INSERT ON outcomes WHEN NEW.outcome_status='VALID' BEGIN
                 UPDATE dashboard_valid_outcome_summary_v1 SET
                   sample_count=sample_count+1,
                   long_return_sum=long_return_sum+NEW.long_return,
                   short_return_sum=short_return_sum+NEW.short_return,
                   quote_coverage_sum=quote_coverage_sum+NEW.quote_coverage
                 WHERE id=1;
               END"""
        )
        connection.execute(
            """INSERT OR IGNORE INTO dashboard_news_article_summary_v1
                 (id,distinct_article_count)
               SELECT 1,count(*) FROM (
                 SELECT 1 FROM news_revisions GROUP BY source,source_item_id
               )"""
        )
        connection.execute(
            """CREATE TRIGGER IF NOT EXISTS dashboard_news_article_insert_v1
               AFTER INSERT ON news_revisions
               WHEN (SELECT count(*) FROM news_revisions
                     WHERE source=NEW.source
                       AND source_item_id=NEW.source_item_id)=1 BEGIN
                 UPDATE dashboard_news_article_summary_v1
                 SET distinct_article_count=distinct_article_count+1 WHERE id=1;
               END"""
        )
        connection.execute(
            """INSERT OR IGNORE INTO dashboard_source_poll_summary_v1
                 (source,total,ok_count,partial_count,error_count,last_success)
               SELECT source,count(*),sum(status='OK'),sum(status='PARTIAL'),
                      sum(status='ERROR'),
                      max(CASE WHEN status='OK' THEN fetched_time END)
               FROM source_polls GROUP BY source"""
        )
        connection.execute(
            """CREATE TRIGGER IF NOT EXISTS dashboard_source_poll_insert_v1
               AFTER INSERT ON source_polls BEGIN
                 INSERT INTO dashboard_source_poll_summary_v1
                   (source,total,ok_count,partial_count,error_count,last_success)
                 VALUES (
                   NEW.source,1,NEW.status='OK',NEW.status='PARTIAL',
                   NEW.status='ERROR',CASE WHEN NEW.status='OK'
                                          THEN NEW.fetched_time END)
                 ON CONFLICT(source) DO UPDATE SET
                   total=total+1,
                   ok_count=ok_count+(NEW.status='OK'),
                   partial_count=partial_count+(NEW.status='PARTIAL'),
                   error_count=error_count+(NEW.status='ERROR'),
                   last_success=CASE WHEN NEW.status='OK'
                     AND (last_success IS NULL OR NEW.fetched_time>last_success)
                     THEN NEW.fetched_time ELSE last_success END;
               END"""
        )
        connection.execute(
            """INSERT OR IGNORE INTO dashboard_news_source_summary_v1
                 (source,item_count,revision_count,full_text_count,latest_item_time)
               SELECT source,count(DISTINCT source_item_id),count(*),
                      count(DISTINCT CASE WHEN body LIKE '[FULL_TEXT%'
                                         THEN source_item_id END),
                      max(collector_first_seen_time)
               FROM news_revisions GROUP BY source"""
        )
        connection.execute(
            """CREATE TRIGGER IF NOT EXISTS dashboard_news_source_insert_v1
               AFTER INSERT ON news_revisions BEGIN
                 INSERT INTO dashboard_news_source_summary_v1
                   (source,item_count,revision_count,full_text_count,latest_item_time)
                 VALUES (
                   NEW.source,
                   CASE WHEN (SELECT count(*) FROM news_revisions
                              WHERE source=NEW.source
                                AND source_item_id=NEW.source_item_id)=1
                        THEN 1 ELSE 0 END,
                   1,
                   CASE WHEN NEW.body LIKE '[FULL_TEXT%'
                         AND (SELECT count(*) FROM news_revisions
                              WHERE source=NEW.source
                                AND source_item_id=NEW.source_item_id
                                AND body LIKE '[FULL_TEXT%')=1
                        THEN 1 ELSE 0 END,
                   NEW.collector_first_seen_time)
                 ON CONFLICT(source) DO UPDATE SET
                   item_count=item_count+excluded.item_count,
                   revision_count=revision_count+1,
                   full_text_count=full_text_count+excluded.full_text_count,
                   latest_item_time=CASE
                     WHEN latest_item_time IS NULL
                       OR excluded.latest_item_time>latest_item_time
                     THEN excluded.latest_item_time ELSE latest_item_time END;
               END"""
        )
        connection.execute(
            """INSERT OR IGNORE INTO dashboard_macro_source_summary_v1
                 (source,item_count,revision_count,latest_item_time)
               SELECT source,count(DISTINCT series_id || ':' || observation_period),
                      count(*),max(collector_first_seen_time)
               FROM macro_observations GROUP BY source"""
        )
        connection.execute(
            """CREATE TRIGGER IF NOT EXISTS dashboard_macro_source_insert_v1
               AFTER INSERT ON macro_observations BEGIN
                 INSERT INTO dashboard_macro_source_summary_v1
                   (source,item_count,revision_count,latest_item_time)
                 VALUES (
                   NEW.source,
                   CASE WHEN (SELECT count(*) FROM macro_observations
                              WHERE source=NEW.source AND series_id=NEW.series_id
                                AND observation_period=NEW.observation_period)=1
                        THEN 1 ELSE 0 END,
                   1,NEW.collector_first_seen_time)
                 ON CONFLICT(source) DO UPDATE SET
                   item_count=item_count+excluded.item_count,
                   revision_count=revision_count+1,
                   latest_item_time=CASE
                     WHEN latest_item_time IS NULL
                       OR excluded.latest_item_time>latest_item_time
                     THEN excluded.latest_item_time ELSE latest_item_time END;
               END"""
        )
        connection.execute(
            """INSERT OR REPLACE INTO dashboard_summary_metadata_v1(key)
               VALUES ('append_only_backfill_v1')"""
        )


def install_dashboard_critical_activity_schema(
    connection: sqlite3.Connection,
) -> None:
    """Materialize the remaining fixed-cardinality critical activity state."""
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS dashboard_latest_activity_v1 (
            activity_name TEXT PRIMARY KEY,
            activity_time TEXT
        );
        CREATE TABLE IF NOT EXISTS dashboard_macro_latest_v1 (
            series_id TEXT PRIMARY KEY,
            source TEXT NOT NULL,
            observation_period TEXT NOT NULL,
            revision_number INTEGER NOT NULL,
            value REAL NOT NULL,
            unit TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS dashboard_daily_brief_summary_v1 (
            id INTEGER PRIMARY KEY CHECK(id=1),
            total_brief_days INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS dashboard_daily_brief_days_v1 (
            brief_date TEXT PRIMARY KEY
        );
        CREATE TRIGGER IF NOT EXISTS dashboard_latest_poll_insert_v1
        AFTER INSERT ON source_polls BEGIN
          INSERT INTO dashboard_latest_activity_v1 VALUES
            ('source_polls',NEW.fetched_time)
          ON CONFLICT(activity_name) DO UPDATE SET
            activity_time=CASE
              WHEN activity_time IS NULL OR excluded.activity_time>activity_time
              THEN excluded.activity_time ELSE activity_time END;
        END;
        CREATE TRIGGER IF NOT EXISTS dashboard_latest_decision_insert_v1
        AFTER INSERT ON decision_events BEGIN
          INSERT INTO dashboard_latest_activity_v1 VALUES
            ('decision_events',NEW.created_at)
          ON CONFLICT(activity_name) DO UPDATE SET
            activity_time=CASE
              WHEN activity_time IS NULL OR excluded.activity_time>activity_time
              THEN excluded.activity_time ELSE activity_time END;
          INSERT INTO dashboard_latest_activity_v1 VALUES
            ('decision_time',NEW.decision_time)
          ON CONFLICT(activity_name) DO UPDATE SET
            activity_time=CASE
              WHEN activity_time IS NULL OR excluded.activity_time>activity_time
              THEN excluded.activity_time ELSE activity_time END;
        END;
        CREATE TRIGGER IF NOT EXISTS dashboard_latest_outcome_insert_v1
        AFTER INSERT ON outcomes BEGIN
          INSERT INTO dashboard_latest_activity_v1 VALUES
            ('outcomes',NEW.appended_at)
          ON CONFLICT(activity_name) DO UPDATE SET
            activity_time=CASE
              WHEN activity_time IS NULL OR excluded.activity_time>activity_time
              THEN excluded.activity_time ELSE activity_time END;
        END;
        CREATE TRIGGER IF NOT EXISTS dashboard_latest_annotation_insert_v1
        AFTER INSERT ON news_annotations BEGIN
          INSERT INTO dashboard_latest_activity_v1 VALUES
            ('news_annotations',NEW.parsed_at)
          ON CONFLICT(activity_name) DO UPDATE SET
            activity_time=CASE
              WHEN activity_time IS NULL OR excluded.activity_time>activity_time
              THEN excluded.activity_time ELSE activity_time END;
        END;
        CREATE TRIGGER IF NOT EXISTS dashboard_macro_latest_insert_v1
        AFTER INSERT ON macro_observations BEGIN
          INSERT INTO dashboard_macro_latest_v1
            (series_id,source,observation_period,revision_number,value,unit)
          VALUES (NEW.series_id,NEW.source,NEW.observation_period,
                  NEW.revision_number,NEW.value,NEW.unit)
          ON CONFLICT(series_id) DO UPDATE SET
            source=excluded.source,
            observation_period=excluded.observation_period,
            revision_number=excluded.revision_number,
            value=excluded.value,
            unit=excluded.unit
          WHERE excluded.observation_period>observation_period
             OR (excluded.observation_period=observation_period
                 AND excluded.revision_number>revision_number);
        END;
        CREATE TRIGGER IF NOT EXISTS dashboard_daily_brief_insert_v1
        AFTER INSERT ON daily_news_briefs BEGIN
          INSERT OR IGNORE INTO dashboard_daily_brief_days_v1
            VALUES (NEW.brief_date);
          UPDATE dashboard_daily_brief_summary_v1
             SET total_brief_days=total_brief_days+changes() WHERE id=1;
        END;
        """
    )
    installed = connection.execute(
        """SELECT 1 FROM dashboard_summary_metadata_v1
           WHERE key='critical_activity_backfill_v1'"""
    ).fetchone()
    if installed:
        return
    with connection:
        activities = (
            ("source_polls", "SELECT max(fetched_time) FROM source_polls"),
            ("decision_events", "SELECT max(created_at) FROM decision_events"),
            ("decision_time", "SELECT max(decision_time) FROM decision_events"),
            ("outcomes", "SELECT max(appended_at) FROM outcomes"),
            ("news_annotations", "SELECT max(parsed_at) FROM news_annotations"),
        )
        for name, query in activities:
            connection.execute(
                "INSERT OR REPLACE INTO dashboard_latest_activity_v1 VALUES (?,?)",
                (name, connection.execute(query).fetchone()[0]),
            )
        connection.execute("DELETE FROM dashboard_macro_latest_v1")
        connection.execute(
            """INSERT INTO dashboard_macro_latest_v1
                 (series_id,source,observation_period,revision_number,value,unit)
               SELECT series_id,source,observation_period,revision_number,value,unit
               FROM (
                 SELECT *,row_number() OVER (
                   PARTITION BY series_id
                   ORDER BY observation_period DESC,revision_number DESC
                 ) AS current_rank
                 FROM macro_observations
               ) WHERE current_rank=1"""
        )
        connection.execute(
            """INSERT OR IGNORE INTO dashboard_daily_brief_days_v1
                 SELECT DISTINCT brief_date FROM daily_news_briefs"""
        )
        connection.execute(
            """INSERT OR REPLACE INTO dashboard_daily_brief_summary_v1
                 (id,total_brief_days)
               SELECT 1,count(*) FROM dashboard_daily_brief_days_v1"""
        )
        connection.execute(
            """INSERT INTO dashboard_summary_metadata_v1(key)
               VALUES ('critical_activity_backfill_v1')"""
        )


READ_MODEL_CONTRACTS = {
    "audit": "dashboard-audit-resources-v2",
    "learning": "dashboard-learning-pyramid-history-v3",
    "market_chart": "dashboard-market-chart-summary-v1",
}


_RESOURCE_SOURCE_TABLES = {
    "audit": (
        "daily_news_briefs", "decision_events", "news_annotations",
        "news_revisions", "news_title_translations", "outcomes",
    ),
    "learning": (
        "derived_outcomes", "execution_model_updates_v2",
        "execution_position_scores_v2", "execution_predictions_v2",
        "execution_training_examples_v2", "model_updates_v2",
        "prediction_scores_v2", "predictions_v2",
    ),
    "market_chart": (
        "decision_events", "derived_market_snapshots", "model_updates_v2",
        "prediction_scores_v2", "predictions_v2",
    ),
}


def install_dashboard_read_model_schema(connection: sqlite3.Connection) -> None:
    """Install local derived-state ownership and per-resource dirty tracking."""
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS dashboard_optional_read_models_v1 (
            resource TEXT PRIMARY KEY,
            contract_version TEXT NOT NULL,
            source_revision INTEGER NOT NULL CHECK(source_revision >= 0),
            generated_at TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            payload_hash TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS dashboard_optional_read_model_state_v1 (
            resource TEXT PRIMARY KEY,
            source_revision INTEGER NOT NULL DEFAULT 0,
            last_success_at TEXT,
            last_error TEXT,
            updated_at TEXT NOT NULL
        );
        """
    )
    existing_columns = {
        str(row[1]) for row in connection.execute(
            "PRAGMA table_info(dashboard_optional_read_models_v1)"
        )
    }
    for column, declaration in (
        ("snapshot_started_at", "TEXT"),
        ("snapshot_completed_at", "TEXT"),
        ("live_source_revision", "INTEGER"),
    ):
        if column not in existing_columns:
            connection.execute(
                f"ALTER TABLE dashboard_optional_read_models_v1 "
                f"ADD COLUMN {column} {declaration}"
            )
    with connection:
        for resource in READ_MODEL_CONTRACTS:
            connection.execute(
                """INSERT OR IGNORE INTO dashboard_optional_read_model_state_v1
                     (resource,source_revision,updated_at) VALUES (?,0,?)""",
                (resource, datetime.now(UTC).isoformat()),
            )
        for resource, tables in _RESOURCE_SOURCE_TABLES.items():
            for table in tables:
                for operation in ("INSERT", "UPDATE", "DELETE"):
                    trigger = f"dashboard_optional_{resource}_{table}_{operation.lower()}_v1"
                    connection.execute(
                        f"""CREATE TRIGGER IF NOT EXISTS {trigger}
                             AFTER {operation} ON {table} BEGIN
                               UPDATE dashboard_optional_read_model_state_v1
                                  SET source_revision=source_revision+1,
                                      updated_at=CURRENT_TIMESTAMP
                                WHERE resource='{resource}';
                             END"""
                    )
