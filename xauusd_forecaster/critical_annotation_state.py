"""Bounded current-state read models for critical annotation status."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime

from .news_identity import (
    NEWS_CURRENT_REPRESENTATIVE_CONTRACT_VERSION,
    preferred_cluster_peer_predicate,
)
from .news_semantics import (
    NEWS_ANNOTATION_USABILITY_CONTRACT_VERSION,
    display_repair_checkpoint_predicate,
    model_usable_annotation_predicate,
)


INSTALL_VERSION = "critical-annotation-state-v1"
RETIRED_ERROR = "CURRENT_EVIDENCE_NO_LONGER_ELIGIBLE"


@dataclass(frozen=True)
class AnnotationMaterializationContract:
    fingerprint: str
    components_json: str


def annotation_materialization_contract() -> AnnotationMaterializationContract:
    """Fingerprint every authority that can change current annotation state."""
    from .annotation import (
        ANNOTATION_BODY_MIN_CHARACTERS,
        PROMPT_VERSION,
        SUPPORTED_GEMINI_MODELS,
    )
    from .news_time import NEWS_SEMANTIC_ELIGIBILITY_CONTRACT_VERSION

    components = {
        "annotation_body_min_characters": ANNOTATION_BODY_MIN_CHARACTERS,
        "annotation_usability": NEWS_ANNOTATION_USABILITY_CONTRACT_VERSION,
        "current_prompt": PROMPT_VERSION,
        "materialization_schema": INSTALL_VERSION,
        "representative_policy": NEWS_CURRENT_REPRESENTATIVE_CONTRACT_VERSION,
        "semantic_eligibility": NEWS_SEMANTIC_ELIGIBILITY_CONTRACT_VERSION,
        "supported_models": sorted(SUPPORTED_GEMINI_MODELS),
    }
    encoded = json.dumps(
        components, ensure_ascii=True, sort_keys=True, separators=(",", ":"),
    )
    return AnnotationMaterializationContract(
        fingerprint=hashlib.sha256(encoded.encode("utf-8")).hexdigest(),
        components_json=encoded,
    )


def install_annotation_job_count_schema(connection: sqlite3.Connection) -> None:
    """Install the scheduler-owned exact state-count read model."""
    connection.executescript(
        f"""
        CREATE TABLE IF NOT EXISTS dashboard_annotation_job_counts_v1 (
            task_type TEXT NOT NULL,
            prompt_version TEXT NOT NULL,
            work_lane TEXT NOT NULL,
            state TEXT NOT NULL,
            retired INTEGER NOT NULL CHECK(retired IN (0,1)),
            job_count INTEGER NOT NULL CHECK(job_count >= 0),
            PRIMARY KEY(task_type,prompt_version,work_lane,state,retired)
        );
        CREATE TABLE IF NOT EXISTS dashboard_job_count_metadata_v1 (
            version TEXT PRIMARY KEY
        );
        CREATE TRIGGER IF NOT EXISTS dashboard_job_count_insert_v1
        AFTER INSERT ON news_ai_jobs_v1 BEGIN
          INSERT INTO dashboard_annotation_job_counts_v1
            (task_type,prompt_version,work_lane,state,retired,job_count)
          VALUES (NEW.task_type,NEW.prompt_version,NEW.work_lane,NEW.state,
            CASE WHEN NEW.last_error='{RETIRED_ERROR}' THEN 1 ELSE 0 END,1)
          ON CONFLICT(task_type,prompt_version,work_lane,state,retired)
          DO UPDATE SET job_count=job_count+1;
        END;
        CREATE TRIGGER IF NOT EXISTS dashboard_job_count_update_v1
        AFTER UPDATE OF task_type,prompt_version,work_lane,state,last_error
        ON news_ai_jobs_v1 BEGIN
          UPDATE dashboard_annotation_job_counts_v1 SET job_count=job_count-1
           WHERE task_type=OLD.task_type AND prompt_version=OLD.prompt_version
             AND work_lane=OLD.work_lane
             AND state=OLD.state AND retired=CASE
               WHEN OLD.last_error='{RETIRED_ERROR}' THEN 1 ELSE 0 END;
          INSERT INTO dashboard_annotation_job_counts_v1
            (task_type,prompt_version,work_lane,state,retired,job_count)
          VALUES (NEW.task_type,NEW.prompt_version,NEW.work_lane,NEW.state,
            CASE WHEN NEW.last_error='{RETIRED_ERROR}' THEN 1 ELSE 0 END,1)
          ON CONFLICT(task_type,prompt_version,work_lane,state,retired)
          DO UPDATE SET job_count=job_count+1;
        END;
        CREATE TRIGGER IF NOT EXISTS dashboard_job_count_delete_v1
        AFTER DELETE ON news_ai_jobs_v1 BEGIN
          UPDATE dashboard_annotation_job_counts_v1 SET job_count=job_count-1
           WHERE task_type=OLD.task_type AND prompt_version=OLD.prompt_version
             AND work_lane=OLD.work_lane
             AND state=OLD.state AND retired=CASE
               WHEN OLD.last_error='{RETIRED_ERROR}' THEN 1 ELSE 0 END;
        END;
        """
    )
    count_columns = {
        str(row[1]) for row in connection.execute(
            "PRAGMA table_info(dashboard_annotation_job_counts_v1)"
        ).fetchall()
    }
    if "work_lane" not in count_columns:
        with connection:
            connection.executescript(
                """DROP TRIGGER IF EXISTS dashboard_job_count_insert_v1;
                   DROP TRIGGER IF EXISTS dashboard_job_count_update_v1;
                   DROP TRIGGER IF EXISTS dashboard_job_count_delete_v1;
                   DROP TABLE dashboard_annotation_job_counts_v1;"""
            )
        return install_annotation_job_count_schema(connection)
    marker = "annotation-job-counts-v2-lanes"
    if connection.execute(
        "SELECT 1 FROM dashboard_job_count_metadata_v1 WHERE version=?",
        (marker,),
    ).fetchone():
        return
    with connection:
        connection.execute("DELETE FROM dashboard_annotation_job_counts_v1")
        connection.execute(
            f"""INSERT INTO dashboard_annotation_job_counts_v1
                  (task_type,prompt_version,work_lane,state,retired,job_count)
                SELECT task_type,prompt_version,work_lane,state,
                  CASE WHEN last_error='{RETIRED_ERROR}' THEN 1 ELSE 0 END,
                  count(*)
                FROM news_ai_jobs_v1
                GROUP BY task_type,prompt_version,work_lane,state,
                  CASE WHEN last_error='{RETIRED_ERROR}' THEN 1 ELSE 0 END"""
        )
        connection.execute(
            "INSERT INTO dashboard_job_count_metadata_v1 VALUES (?)", (marker,),
        )


def record_annotation_completion(
    connection: sqlite3.Connection,
    *,
    source: str,
    source_item_id: str,
    revision_number: int,
    prompt_version: str,
    completed_at: str,
) -> None:
    """Represent a completion that did not already arrive through a leased job."""
    usable = connection.execute(
        f"""SELECT 1 FROM news_annotations a
            WHERE a.source=? AND a.source_item_id=? AND a.revision_number=?
              AND a.prompt_version=?
              AND {model_usable_annotation_predicate('a')}
            LIMIT 1""",
        (source, source_item_id, revision_number, prompt_version),
    ).fetchone()
    if usable is None:
        return
    identity = "|".join((
        "ACTIVE_ANNOTATION", source, source_item_id, str(revision_number), "",
        prompt_version,
    ))
    job_id = hashlib.sha256(identity.encode("utf-8")).hexdigest()
    connection.execute(
        """INSERT INTO news_ai_jobs_v1
           (job_id,task_type,source,source_item_id,revision_number,annotation_id,
            prompt_version,priority,state,available_at,lease_owner,
            lease_expires_at,attempt_count,last_error,created_at,updated_at,
            completed_at,work_lane,lane_classified) VALUES
           (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(job_id) DO UPDATE SET
             state='COMPLETED',available_at=excluded.available_at,
             lease_owner=NULL,lease_expires_at=NULL,last_error=NULL,
             updated_at=excluded.updated_at,completed_at=excluded.completed_at
           WHERE news_ai_jobs_v1.state<>'LEASED'""",
        (
            job_id, "ACTIVE_ANNOTATION", source, source_item_id,
            revision_number, "", prompt_version, "NORMAL", "COMPLETED",
            completed_at, None, None, 0, None, completed_at, completed_at,
            completed_at, "LIVE", 1,
        ),
    )


def _preferred_cluster_row(
    connection: sqlite3.Connection, cluster_id: str,
) -> sqlite3.Row | None:
    return connection.execute(
        f"""SELECT n.source,n.source_item_id,n.revision_number,n.cluster_id,n.body,
                   n.source_published_time,n.collector_first_seen_time
            FROM news_revisions n
            WHERE n.cluster_id=?
              AND NOT EXISTS (
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
            LIMIT 1""",
        (cluster_id,),
    ).fetchone()


def _content_state(connection: sqlite3.Connection, row: sqlite3.Row) -> str:
    from .annotation import ANNOTATION_BODY_MIN_CHARACTERS

    if len(str(row["body"] or "").strip()) >= ANNOTATION_BODY_MIN_CHARACTERS:
        return "AVAILABLE"
    failure = connection.execute(
        """SELECT is_terminal FROM news_content_failures
           WHERE source=? AND source_item_id=? AND revision_number=?
           ORDER BY attempt_number DESC LIMIT 1""",
        (row["source"], row["source_item_id"], row["revision_number"]),
    ).fetchone()
    return "UNAVAILABLE" if failure and int(failure[0]) else "WAITING"


def _annotation_state(connection: sqlite3.Connection, row: sqlite3.Row) -> str:
    from .annotation import (
        ANNOTATION_BODY_MIN_CHARACTERS,
        PROMPT_VERSION,
        SUPPORTED_GEMINI_MODELS,
    )
    if len(str(row["body"] or "").strip()) < ANNOTATION_BODY_MIN_CHARACTERS:
        return "NONE"
    from .news_time import assess_news_semantic_eligibility

    epoch = connection.execute(
        "SELECT value FROM runtime_metadata WHERE key='FORWARD_EPOCH'"
    ).fetchone()
    if epoch is None or not assess_news_semantic_eligibility(
        row, forward_epoch=datetime.fromisoformat(str(epoch[0])),
    ).eligible:
        return "NONE"
    usable = connection.execute(
        f"""SELECT 1 FROM news_annotations a
            WHERE a.source=? AND a.source_item_id=? AND a.revision_number=?
              AND a.llm_model_version IN (?,?) AND a.prompt_version=?
              AND {model_usable_annotation_predicate('a')} LIMIT 1""",
        (
            row["source"], row["source_item_id"], row["revision_number"],
            *SUPPORTED_GEMINI_MODELS, PROMPT_VERSION,
        ),
    ).fetchone()
    return "READY" if usable else "PENDING"


def _has_invalid_display(
    connection: sqlite3.Connection, row: sqlite3.Row,
) -> int:
    invalid = connection.execute(
        f"""SELECT 1 FROM news_annotations fallback
            WHERE fallback.source=? AND fallback.source_item_id=?
              AND fallback.revision_number=?
              AND {display_repair_checkpoint_predicate('fallback')}
              AND COALESCE(json_extract(
                    fallback.annotation_json,'$.xauusd_relevance'),'IRRELEVANT')
                    <> 'IRRELEVANT'
              AND NOT EXISTS (
                SELECT 1 FROM news_annotations repaired
                WHERE repaired.source=fallback.source
                  AND repaired.source_item_id=fallback.source_item_id
                  AND repaired.revision_number=fallback.revision_number
                  AND repaired.prompt_version=fallback.prompt_version
                  AND repaired.parsed_at>fallback.parsed_at
                  AND {model_usable_annotation_predicate('repaired')})
            LIMIT 1""",
        (row["source"], row["source_item_id"], row["revision_number"]),
    ).fetchone()
    return int(invalid is not None)


def refresh_news_cluster_state(
    connection: sqlite3.Connection, cluster_id: str | None,
) -> None:
    """Refresh one affected current cluster after an authoritative transition."""
    if not cluster_id:
        return
    row = _preferred_cluster_row(connection, cluster_id)
    if row is None:
        connection.execute(
            "DELETE FROM dashboard_news_current_state_v1 WHERE cluster_id=?",
            (cluster_id,),
        )
        return
    connection.execute(
        """INSERT INTO dashboard_news_current_state_v1
             (cluster_id,source,source_item_id,revision_number,
              content_state,invalid_display,annotation_state)
           VALUES (?,?,?,?,?,?,?)
           ON CONFLICT(cluster_id) DO UPDATE SET
             source=excluded.source,
             source_item_id=excluded.source_item_id,
             revision_number=excluded.revision_number,
             content_state=excluded.content_state,
             invalid_display=excluded.invalid_display,
             annotation_state=excluded.annotation_state""",
        (
            cluster_id, row["source"], row["source_item_id"],
            row["revision_number"], _content_state(connection, row),
            _has_invalid_display(connection, row), _annotation_state(connection, row),
        ),
    )


def refresh_news_revision_state(
    connection: sqlite3.Connection,
    source: str,
    source_item_id: str,
    revision_number: int,
) -> None:
    row = connection.execute(
        """SELECT cluster_id FROM news_revisions
           WHERE source=? AND source_item_id=? AND revision_number=?""",
        (source, source_item_id, revision_number),
    ).fetchone()
    if row is not None:
        refresh_news_cluster_state(connection, str(row[0]))


def _backfill_current_annotation_completions(
    connection: sqlite3.Connection,
) -> None:
    """Bridge pre-scheduler evidence into the current scheduler contract once."""
    from .annotation import (
        ANNOTATION_BODY_MIN_CHARACTERS,
        PROMPT_VERSION,
        SUPPORTED_GEMINI_MODELS,
    )
    from .news_time import (
        register_news_semantic_eligibility_sql,
        semantic_eligibility_sql_predicate,
    )

    register_news_semantic_eligibility_sql(connection)
    forward_epoch = str(connection.execute(
        "SELECT value FROM runtime_metadata WHERE key='FORWARD_EPOCH'"
    ).fetchone()[0])
    rows = connection.execute(
        f"""SELECT n.source,n.source_item_id,n.revision_number,
                   max(a.parsed_at) AS completed_at
            FROM news_revisions n
            JOIN news_annotations a
              ON a.source=n.source AND a.source_item_id=n.source_item_id
             AND a.revision_number=n.revision_number
             AND a.llm_model_version IN (?,?) AND a.prompt_version=?
             AND {model_usable_annotation_predicate('a')}
            WHERE length(trim(COALESCE(n.body,'')))>=
              {ANNOTATION_BODY_MIN_CHARACTERS}
              AND {semantic_eligibility_sql_predicate('n')}
              AND NOT EXISTS (
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
                  AND {semantic_eligibility_sql_predicate('peer')}
                  AND {preferred_cluster_peer_predicate('peer', 'n')})
            GROUP BY n.source,n.source_item_id,n.revision_number""",
        (*SUPPORTED_GEMINI_MODELS, PROMPT_VERSION, forward_epoch, forward_epoch),
    ).fetchall()
    for row in rows:
        record_annotation_completion(
            connection, source=str(row[0]), source_item_id=str(row[1]),
            revision_number=int(row[2]), prompt_version=PROMPT_VERSION,
            completed_at=str(row[3]),
        )


def install_critical_annotation_state_schema(
    connection: sqlite3.Connection,
) -> None:
    """Install once, backfill once, then maintain exact current-state summaries."""
    install_annotation_job_count_schema(connection)
    connection.executescript(
        f"""
        CREATE TABLE IF NOT EXISTS dashboard_critical_state_metadata_v1 (
            version TEXT PRIMARY KEY,
            contract_fingerprint TEXT,
            contract_json TEXT,
            installed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS dashboard_news_current_state_v1 (
            cluster_id TEXT PRIMARY KEY,
            source TEXT NOT NULL,
            source_item_id TEXT NOT NULL,
            revision_number INTEGER NOT NULL,
            content_state TEXT NOT NULL CHECK(content_state IN (
                'AVAILABLE','WAITING','UNAVAILABLE')),
            invalid_display INTEGER NOT NULL CHECK(invalid_display IN (0,1)),
            annotation_state TEXT NOT NULL CHECK(annotation_state IN (
                'READY','PENDING','NONE'))
        );
        CREATE TABLE IF NOT EXISTS dashboard_news_current_counts_v1 (
            id INTEGER PRIMARY KEY CHECK(id=1),
            waiting_content INTEGER NOT NULL,
            unavailable_content INTEGER NOT NULL,
            invalid_display INTEGER NOT NULL,
            ready_annotations INTEGER NOT NULL,
            pending_annotations INTEGER NOT NULL
        );
        INSERT OR IGNORE INTO dashboard_news_current_counts_v1
          VALUES (1,0,0,0,0,0);
        CREATE INDEX IF NOT EXISTS news_ai_jobs_dashboard_current_v1
          ON news_ai_jobs_v1(task_type,prompt_version,state,last_error,available_at);
        CREATE INDEX IF NOT EXISTS news_ai_job_attempts_recent_v1
          ON news_ai_job_attempts_v1(attempted_at,job_id);
        CREATE INDEX IF NOT EXISTS news_ai_jobs_recent_state_v1
          ON news_ai_jobs_v1(state,updated_at,task_type);
        CREATE TRIGGER IF NOT EXISTS dashboard_news_current_insert_v1
        AFTER INSERT ON dashboard_news_current_state_v1 BEGIN
          UPDATE dashboard_news_current_counts_v1 SET
            waiting_content=waiting_content+(NEW.content_state='WAITING'),
            unavailable_content=unavailable_content+(NEW.content_state='UNAVAILABLE'),
            invalid_display=invalid_display+NEW.invalid_display,
            ready_annotations=ready_annotations+(NEW.annotation_state='READY'),
            pending_annotations=pending_annotations+(NEW.annotation_state='PENDING')
          WHERE id=1;
        END;
        CREATE TRIGGER IF NOT EXISTS dashboard_news_current_update_v1
        AFTER UPDATE OF content_state,invalid_display,annotation_state
          ON dashboard_news_current_state_v1 BEGIN
          UPDATE dashboard_news_current_counts_v1 SET
            waiting_content=waiting_content-(OLD.content_state='WAITING')
              +(NEW.content_state='WAITING'),
            unavailable_content=unavailable_content-(OLD.content_state='UNAVAILABLE')
              +(NEW.content_state='UNAVAILABLE'),
            invalid_display=invalid_display-OLD.invalid_display+NEW.invalid_display,
            ready_annotations=ready_annotations-(OLD.annotation_state='READY')
              +(NEW.annotation_state='READY'),
            pending_annotations=pending_annotations-(OLD.annotation_state='PENDING')
              +(NEW.annotation_state='PENDING')
          WHERE id=1;
        END;
        CREATE TRIGGER IF NOT EXISTS dashboard_news_current_delete_v1
        AFTER DELETE ON dashboard_news_current_state_v1 BEGIN
          UPDATE dashboard_news_current_counts_v1 SET
            waiting_content=waiting_content-(OLD.content_state='WAITING'),
            unavailable_content=unavailable_content-(OLD.content_state='UNAVAILABLE'),
            invalid_display=invalid_display-OLD.invalid_display,
            ready_annotations=ready_annotations-(OLD.annotation_state='READY'),
            pending_annotations=pending_annotations-(OLD.annotation_state='PENDING')
          WHERE id=1;
        END;
        """
    )
    metadata_columns = {
        str(row[1]) for row in connection.execute(
            "PRAGMA table_info(dashboard_critical_state_metadata_v1)"
        ).fetchall()
    }
    if "contract_fingerprint" not in metadata_columns:
        connection.execute(
            "ALTER TABLE dashboard_critical_state_metadata_v1 "
            "ADD COLUMN contract_fingerprint TEXT"
        )
    if "contract_json" not in metadata_columns:
        connection.execute(
            "ALTER TABLE dashboard_critical_state_metadata_v1 "
            "ADD COLUMN contract_json TEXT"
        )
    contract = annotation_materialization_contract()
    installed = connection.execute(
        """SELECT 1 FROM dashboard_critical_state_metadata_v1
           WHERE version=? AND contract_fingerprint=?""",
        (INSTALL_VERSION, contract.fingerprint),
    ).fetchone()
    if installed:
        return
    with connection:
        connection.execute("DELETE FROM dashboard_news_current_state_v1")
        for row in connection.execute(
            "SELECT DISTINCT cluster_id FROM news_revisions"
        ).fetchall():
            refresh_news_cluster_state(connection, str(row[0]))
        _backfill_current_annotation_completions(connection)
        from .news_scheduler import reconcile_completed_jobs
        reconcile_completed_jobs(connection, manage_transaction=False)
        connection.execute(
            """INSERT INTO dashboard_critical_state_metadata_v1
                 (version,contract_fingerprint,contract_json)
               VALUES (?,?,?)
               ON CONFLICT(version) DO UPDATE SET
                 contract_fingerprint=excluded.contract_fingerprint,
                 contract_json=excluded.contract_json,
                 installed_at=CURRENT_TIMESTAMP""",
            (INSTALL_VERSION, contract.fingerprint, contract.components_json),
        )


def annotation_queue_snapshot(
    connection: sqlite3.Connection,
    *,
    prompt_version: str,
    observed_at: str,
) -> dict[str, int]:
    """Read exact annotation state without touching accumulated news history."""
    fixed = {
        (str(row[0]), int(row[1])): int(row[2])
        for row in connection.execute(
            """SELECT state,retired,job_count
               FROM dashboard_annotation_job_counts_v1
               WHERE task_type='ACTIVE_ANNOTATION' AND prompt_version=?""",
            (prompt_version,),
        ).fetchall()
    }
    active = connection.execute(
        f"""SELECT count(*)
            FROM news_ai_jobs_v1
            WHERE task_type='ACTIVE_ANNOTATION' AND prompt_version=?
              AND state='BACKING_OFF' AND available_at>?
              AND COALESCE(last_error,'')<>'{RETIRED_ERROR}'""",
        (prompt_version, observed_at),
    ).fetchone()
    current = connection.execute(
        """SELECT waiting_content,unavailable_content,invalid_display,
                  ready_annotations,pending_annotations
           FROM dashboard_news_current_counts_v1 WHERE id=1"""
    ).fetchone()
    backing_off = int(active[0] or 0)
    dead_letter = fixed.get(("DEAD_LETTER", 0), 0)
    pending = int(current[4])
    return {
        "ready": int(current[3]),
        "queued": max(0, pending - backing_off - dead_letter),
        "backing_off": backing_off,
        "dead_letter": dead_letter,
        "waiting_content": int(current[0]),
        "unavailable_content": int(current[1]),
        "invalid_display": int(current[2]),
    }


def scheduler_state_counts(connection: sqlite3.Connection) -> list[sqlite3.Row]:
    return connection.execute(
        """SELECT task_type,state,sum(job_count) AS total
           FROM dashboard_annotation_job_counts_v1
           WHERE retired=0
             AND (task_type<>'ACTIVE_ANNOTATION' OR work_lane='LIVE')
           GROUP BY task_type,state"""
    ).fetchall()


def news_current_counts(connection: sqlite3.Connection) -> dict[str, int]:
    try:
        row = connection.execute(
            """SELECT waiting_content,unavailable_content,invalid_display
               FROM dashboard_news_current_counts_v1 WHERE id=1"""
        ).fetchone()
    except sqlite3.OperationalError as error:
        if "no such table" not in str(error):
            raise
        row = None
    if row is None:
        return {"waiting_content": 0, "unavailable_content": 0,
                "invalid_display": 0}
    return {
        "waiting_content": int(row[0]),
        "unavailable_content": int(row[1]),
        "invalid_display": int(row[2]),
    }
