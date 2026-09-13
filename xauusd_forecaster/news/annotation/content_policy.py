"""Durable operator policy for explicitly prohibited source content."""

from datetime import datetime, timezone
from sqlite3 import Connection


PROHIBITED_CONTENT = "PROVIDER_PROHIBITED_CONTENT"
_POLICY_VERSION = "provider-prohibited-content-v1"
_IDENTITY_PREFIX = "provider-prohibited:"


def permitted_content_sql(alias: str = "n", hash_column: str = "content_hash") -> str:
    """Indexed content-identity check shared by the three pending readers."""
    if not alias.isidentifier() or not hash_column.isidentifier():
        raise ValueError("Invalid SQL alias")
    return f"""NOT EXISTS (SELECT 1 FROM news_item_classifications_v1 policy
        WHERE policy.classification_id='{_IDENTITY_PREFIX}' || {alias}.{hash_column})"""


def content_is_skipped(connection: Connection, row: dict) -> bool:
    return connection.execute(
        "SELECT 1 FROM news_item_classifications_v1 WHERE classification_id=?",
        (_IDENTITY_PREFIX + str(row["content_hash"]),),
    ).fetchone() is not None


def skipped_content_status() -> dict[str, object]:
    return {"status": "SKIPPED", "failure_code": PROHIBITED_CONTENT,
            "retry_state": "SKIPPED", "is_terminal": True, "next_retry_at": None}


def skip_prohibited_content(connection: Connection, row: dict) -> dict[str, object]:
    """Record rejection before another stage can dispatch; preserve audit rows.

    Leased work remains its worker's responsibility. A concurrent request that
    was dispatched before the marker cannot be recalled.
    """
    now = datetime.now(timezone.utc).isoformat()
    with connection:
        connection.execute(
            """INSERT OR IGNORE INTO news_item_classifications_v1
            (classification_id,source,source_item_id,revision_number,classified_at,
             policy_version,visibility_status,reason_code,source_hash)
            VALUES (?,?,?,?,?,?,'CONTENT_UNAVAILABLE',?,?)""",
            (_IDENTITY_PREFIX + str(row["content_hash"]), row["source"],
             row["source_item_id"], row["revision_number"], now,
             _POLICY_VERSION, PROHIBITED_CONTENT, row["content_hash"]),
        )
        connection.execute(
            """UPDATE news_ai_jobs_v1 AS j
            SET state='DEAD_LETTER',last_error=?,updated_at=?,completed_at=?,
                lease_owner=NULL,lease_expires_at=NULL
            WHERE state IN ('QUEUED','BACKING_OFF') AND EXISTS (
                SELECT 1 FROM news_revisions n WHERE n.source=j.source
                AND n.source_item_id=j.source_item_id
                AND n.revision_number=j.revision_number AND n.content_hash=?)""",
            (PROHIBITED_CONTENT, now, now, row["content_hash"]),
        )
        connection.execute(
            """UPDATE news_ai_retry_schedule_overrides_v1 SET active=0
            WHERE active=1 AND job_id IN (
                SELECT job_id FROM news_ai_jobs_v1
                WHERE state='DEAD_LETTER' AND last_error=?)""",
            (PROHIBITED_CONTENT,),
        )
    return skipped_content_status()
