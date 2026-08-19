"""Bounded read models for the critical dashboard heartbeat."""

from __future__ import annotations

import sqlite3


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


def dashboard_table_counts(connection: sqlite3.Connection) -> dict[str, int]:
    rows = connection.execute(
        "SELECT table_name,row_count FROM dashboard_table_counts_v1"
    ).fetchall()
    counts = {str(row[0]): int(row[1]) for row in rows}
    missing = set(DASHBOARD_COUNT_TABLES) - set(counts)
    if missing:
        raise RuntimeError(
            "dashboard count summary is incomplete: " + ", ".join(sorted(missing))
        )
    return counts


def dashboard_valid_outcome_summary(connection: sqlite3.Connection) -> dict:
    row = connection.execute(
        """SELECT sample_count,long_return_sum,short_return_sum,quote_coverage_sum
           FROM dashboard_valid_outcome_summary_v1 WHERE id=1"""
    ).fetchone()
    if row is None:
        raise RuntimeError("dashboard outcome summary is unavailable")
    samples = int(row[0])
    return {
        "samples": samples,
        "avg_long": float(row[1]) / samples if samples else None,
        "avg_short": float(row[2]) / samples if samples else None,
        "avg_coverage": float(row[3]) / samples if samples else None,
    }


def dashboard_distinct_article_count(connection: sqlite3.Connection) -> int:
    row = connection.execute(
        """SELECT distinct_article_count
           FROM dashboard_news_article_summary_v1 WHERE id=1"""
    ).fetchone()
    if row is None:
        raise RuntimeError("dashboard article summary is unavailable")
    return int(row[0])


def dashboard_source_poll_summary(
    connection: sqlite3.Connection, source: str,
) -> dict:
    row = connection.execute(
        """SELECT total,ok_count,partial_count,error_count,last_success
           FROM dashboard_source_poll_summary_v1 WHERE source=?""",
        (source,),
    ).fetchone()
    if row is None:
        return {
            "total": 0, "ok_count": 0, "partial_count": 0,
            "error_count": 0, "last_success": None,
        }
    return {
        "total": int(row[0]), "ok_count": int(row[1]),
        "partial_count": int(row[2]), "error_count": int(row[3]),
        "last_success": row[4],
    }


def dashboard_news_source_summary(
    connection: sqlite3.Connection, sources: tuple[str, ...],
) -> dict:
    if not sources:
        return {
            "item_count": 0, "revision_count": 0, "full_text_count": 0,
            "latest_item_time": None,
        }
    placeholders = ",".join("?" for _ in sources)
    row = connection.execute(
        f"""SELECT COALESCE(sum(item_count),0),
                   COALESCE(sum(revision_count),0),
                   COALESCE(sum(full_text_count),0),max(latest_item_time)
            FROM dashboard_news_source_summary_v1
            WHERE source IN ({placeholders})""",
        sources,
    ).fetchone()
    return {
        "item_count": int(row[0]), "revision_count": int(row[1]),
        "full_text_count": int(row[2]), "latest_item_time": row[3],
    }


def dashboard_macro_source_summary(
    connection: sqlite3.Connection, source: str,
) -> dict:
    row = connection.execute(
        """SELECT item_count,revision_count,latest_item_time
           FROM dashboard_macro_source_summary_v1 WHERE source=?""",
        (source,),
    ).fetchone()
    if row is None:
        return {"item_count": 0, "revision_count": 0, "latest_item_time": None}
    return {
        "item_count": int(row[0]), "revision_count": int(row[1]),
        "latest_item_time": row[2],
    }
