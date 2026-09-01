"""Bounded incremental identity selection for the local Dashboard News archive."""

from __future__ import annotations

import json
import sqlite3

from xauusd_forecaster.news_identity import preferred_cluster_peer_predicate


def news_mirror_candidate_keys(
    connection: sqlite3.Connection,
    *,
    cutoff: str,
    after: str | None,
    limit: int,
) -> list[tuple[str, str, int, str]]:
    """Find changed reader keys before running the expensive detail joins."""
    cursor_clause = ""
    cursor_parameters: tuple[object, ...] = ()
    if after:
        cursor = json.loads(after)
        if not isinstance(cursor, list) or len(cursor) != 4:
            raise ValueError("invalid news archive cursor")
        cursor_clause = (
            "HAVING (max(changed_at),changes.source,changes.source_item_id,"
            "changes.revision_number) "
            "> (?,?,?,?)"
        )
        cursor_parameters = tuple(cursor)
    rows = connection.execute(
        f"""WITH changes AS (
              SELECT source,source_item_id,revision_number,
                     fetched_time AS changed_at
              FROM news_revisions WHERE fetched_time>=?
              UNION ALL
              SELECT source,source_item_id,revision_number,parsed_at
              FROM news_title_translations WHERE parsed_at>=?
              UNION ALL
              SELECT source,source_item_id,revision_number,parsed_at
              FROM news_annotations WHERE parsed_at>=?
              UNION ALL
              SELECT source,source_item_id,revision_number,assessed_at
              FROM news_impact_assessments_v1 WHERE assessed_at>=?
              UNION ALL
              SELECT source,source_item_id,revision_number,failed_at
              FROM news_llm_failures WHERE failed_at>=?
              UNION ALL
              SELECT source,source_item_id,revision_number,failed_at
              FROM news_content_failures WHERE failed_at>=?
              UNION ALL
              SELECT f.source,f.source_item_id,f.revision_number,r.authorized_at
              FROM news_ai_failure_recoveries_v1 r
              JOIN news_llm_failures f ON f.failure_id=r.failure_id
              WHERE r.authorized_at>=?
            )
            SELECT changes.source,changes.source_item_id,
                   changes.revision_number,max(changed_at)
            FROM changes
            JOIN news_revisions n
              ON n.source=changes.source
             AND n.source_item_id=changes.source_item_id
             AND n.revision_number=changes.revision_number
            WHERE NOT EXISTS (
              SELECT 1 FROM news_revisions newer
              WHERE newer.source=n.source
                AND newer.source_item_id=n.source_item_id
                AND newer.revision_number>n.revision_number)
              AND NOT EXISTS (
                SELECT 1 FROM news_revisions peer
                WHERE peer.cluster_id=n.cluster_id
                  AND NOT EXISTS (
                    SELECT 1 FROM news_revisions peer_newer
                    WHERE peer_newer.source=peer.source
                      AND peer_newer.source_item_id=peer.source_item_id
                      AND peer_newer.revision_number>peer.revision_number)
                  AND {preferred_cluster_peer_predicate('peer', 'n')})
              AND length(trim(COALESCE(n.body,'')))>=240
              AND COALESCE(n.source_published_time,
                           n.collector_first_seen_time)>=?
            GROUP BY changes.source,changes.source_item_id,
                     changes.revision_number
            {cursor_clause}
            ORDER BY max(changed_at),changes.source,
                     changes.source_item_id,changes.revision_number
            LIMIT ?""",
        (
            cutoff,
            cutoff,
            cutoff,
            cutoff,
            cutoff,
            cutoff,
            cutoff,
            cutoff,
            *cursor_parameters,
            limit,
        ),
    ).fetchall()
    return [
        (str(row[0]), str(row[1]), int(row[2]), str(row[3]))
        for row in rows
    ]
