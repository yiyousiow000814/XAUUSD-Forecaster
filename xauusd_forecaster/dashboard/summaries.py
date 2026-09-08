"""Bounded read models for the critical dashboard heartbeat."""

from __future__ import annotations

import sqlite3

from xauusd_forecaster.evidence.projection_schema import (
    DASHBOARD_COUNT_TABLES,
    install_dashboard_summary_schema,
    install_dashboard_critical_activity_schema,
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




def dashboard_latest_activity(connection: sqlite3.Connection) -> dict[str, str | None]:
    activity_names = (
        "source_polls", "decision_events", "decision_time", "outcomes",
        "news_annotations",
    )
    return {
        str(row[0]): (str(row[1]) if row[1] else None)
        for row in connection.execute(
            """SELECT activity_name,activity_time
               FROM dashboard_latest_activity_v1
               WHERE activity_name IN (?,?,?,?,?)""",
            activity_names,
        ).fetchall()
    }


def dashboard_latest_macro(
    connection: sqlite3.Connection, series_ids: tuple[str, ...],
) -> dict[str, dict]:
    if not series_ids:
        return {}
    placeholders = ",".join("?" for _ in series_ids)
    return {
        str(row["series_id"]): dict(row)
        for row in connection.execute(
            f"""SELECT series_id,observation_period,value,unit
                FROM dashboard_macro_latest_v1
                WHERE series_id IN ({placeholders}) ORDER BY series_id""",
            series_ids,
        ).fetchall()
    }


def dashboard_collected_news_sources(
    connection: sqlite3.Connection, sources: tuple[str, ...],
) -> set[str]:
    if not sources:
        return set()
    placeholders = ",".join("?" for _ in sources)
    return {
        str(row[0]) for row in connection.execute(
            f"""SELECT source FROM dashboard_news_source_summary_v1
                WHERE source IN ({placeholders})""",
            sources,
        ).fetchall()
    }


def dashboard_total_brief_days(connection: sqlite3.Connection) -> int:
    row = connection.execute(
        "SELECT total_brief_days FROM dashboard_daily_brief_summary_v1 WHERE id=1"
    ).fetchone()
    return int(row[0])
