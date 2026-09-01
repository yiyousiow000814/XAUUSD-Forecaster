"""Classify intake rows without mutating the immutable raw-news ledger."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
import sqlite3
import uuid

from .evidence_v2 import install_v2_schema
from .forward_ledger import canonical_hash
from .news_evidence import EVIDENCE_POLICY_VERSION
from .news_relevance import google_news_item_is_relevant, is_google_news_source
from .news_time import assess_news_time
from .sqlite_wal import open_forward_writer_connection


@dataclass(frozen=True)
class NewsPrunePlan:
    keep_items: int
    delete_items: int
    delete_revisions: int
    delete_unused_revisions: int
    reasons: dict[str, int]
    keys: tuple[tuple[str, str], ...]
    revision_keys: tuple[tuple[str, str, int], ...]
    item_reasons: tuple[tuple[str, str, str], ...]


def _time(value: object) -> datetime | None:
    if value in (None, ""):
        return None
    parsed = datetime.fromisoformat(str(value))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def build_news_prune_plan(
    connection: sqlite3.Connection,
    *,
    forward_epoch: datetime,
) -> NewsPrunePlan:
    """Delete only items that could not have been valid model evidence."""
    connection.row_factory = sqlite3.Row
    rows = connection.execute(
        """SELECT source, source_item_id, revision_number,
                  source_published_time, collector_first_seen_time,
                  item_first_seen_time, headline, body
           FROM news_revisions
           ORDER BY source, source_item_id, revision_number"""
    ).fetchall()
    grouped: dict[tuple[str, str], list[sqlite3.Row]] = {}
    for row in rows:
        grouped.setdefault((str(row["source"]), str(row["source_item_id"])), []).append(row)

    delete_keys: list[tuple[str, str]] = []
    delete_revision_keys: list[tuple[str, str, int]] = []
    item_reasons: list[tuple[str, str, str]] = []
    reasons: dict[str, int] = {}
    delete_revisions = 0
    annotated_revisions = {
        (str(row["source"]), str(row["source_item_id"]), int(row["revision_number"]))
        for row in connection.execute(
            "SELECT source, source_item_id, revision_number FROM news_annotations"
        ).fetchall()
    }
    for key, revisions in grouped.items():
        usable = False
        item_reason = "NO_FULL_TEXT"
        for row in revisions:
            body = str(row["body"] or "")
            if not body.startswith("[FULL_TEXT") or len(body) < 240:
                continue
            item_first_seen = _time(
                row["item_first_seen_time"] or row["collector_first_seen_time"]
            )
            revision_visible_at = _time(row["collector_first_seen_time"])
            if item_first_seen is None or revision_visible_at is None:
                item_reason = "FIRST_SEEN_MISSING"
                continue
            timing = assess_news_time(
                row,
                decision_time=revision_visible_at,
                forward_epoch=forward_epoch,
                max_discovery_delay=None,
                allow_pre_forward_publication=True,
            )
            if not timing.eligible:
                item_reason = timing.reason_code
                continue
            if is_google_news_source(key[0]):
                relevant, reason = google_news_item_is_relevant(
                    key[0], str(row["headline"] or ""),
                    _time(row["source_published_time"]), item_first_seen,
                )
                if not relevant:
                    item_reason = reason
                    continue
            usable = True
            break
        if not usable:
            delete_keys.append(key)
            item_reasons.append((key[0], key[1], item_reason))
            delete_revisions += len(revisions)
            reasons[item_reason] = reasons.get(item_reason, 0) + 1
            continue

        # A full-text revision makes the item usable, but older headline-only
        # intake placeholders can still remain.  Remove only placeholders that
        # were never annotated, which proves they never became model-visible
        # news evidence.  Annotated history stays intact for auditability.
        for row in revisions:
            revision_key = (key[0], key[1], int(row["revision_number"]))
            body = str(row["body"] or "")
            if not body.startswith("[FULL_TEXT") and revision_key not in annotated_revisions:
                delete_revision_keys.append(revision_key)

    return NewsPrunePlan(
        keep_items=len(grouped) - len(delete_keys),
        delete_items=len(delete_keys),
        delete_revisions=delete_revisions,
        delete_unused_revisions=len(delete_revision_keys),
        reasons=dict(sorted(reasons.items())),
        keys=tuple(delete_keys),
        revision_keys=tuple(delete_revision_keys),
        item_reasons=tuple(item_reasons),
    )


def prune_unused_news(
    database_path: Path,
    *,
    backup_directory: Path,
    dry_run: bool = False,
) -> dict[str, object]:
    """Append visibility classifications; raw revisions always remain intact."""
    database_path = database_path.resolve()
    del backup_directory
    connection = open_forward_writer_connection(
        database_path, timeout=60, row_factory=sqlite3.Row,
    )
    install_v2_schema(connection)
    epoch_row = connection.execute(
        "SELECT value FROM runtime_metadata WHERE key='FORWARD_EPOCH'"
    ).fetchone()
    if epoch_row is None:
        raise RuntimeError("FORWARD_EPOCH is missing")
    forward_epoch = _time(epoch_row["value"])
    if forward_epoch is None:
        raise RuntimeError("FORWARD_EPOCH is invalid")
    plan = build_news_prune_plan(connection, forward_epoch=forward_epoch)
    receipt: dict[str, object] = {
        "database": str(database_path),
        "dry_run": dry_run,
        "destructive": False,
        "keep_items": plan.keep_items,
        "classified_items": plan.delete_items,
        "classified_revisions": plan.delete_revisions + plan.delete_unused_revisions,
        # Compatibility fields describe the classification plan, not deletion.
        "delete_items": plan.delete_items,
        "delete_revisions": 0,
        "delete_unused_revisions": 0,
        "reasons": plan.reasons,
    }
    if dry_run or (not plan.keys and not plan.revision_keys):
        connection.close()
        return receipt
    reason_by_item = {(source, item): reason for source, item, reason in plan.item_reasons}
    item_keys = set(plan.keys)
    placeholder_keys = set(plan.revision_keys)
    rows = connection.execute(
        """SELECT source,source_item_id,revision_number,content_hash
        FROM news_revisions ORDER BY source,source_item_id,revision_number"""
    ).fetchall()
    classified_at = datetime.now(UTC).isoformat()
    inserted = 0
    with connection:
        for row in rows:
            item_key = (str(row["source"]), str(row["source_item_id"]))
            revision_key = (*item_key, int(row["revision_number"]))
            if item_key in item_keys:
                status = "CONTENT_UNAVAILABLE"
                reason = reason_by_item[item_key]
            elif revision_key in placeholder_keys:
                status = "DUPLICATE_DOCUMENT"
                reason = "UNUSED_INTAKE_PLACEHOLDER"
            else:
                continue
            classification_id = str(uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"news-classification:{EVIDENCE_POLICY_VERSION}:{revision_key}",
            ))
            cursor = connection.execute(
                """INSERT OR IGNORE INTO news_item_classifications_v1 VALUES
                (?,?,?,?,?,?,?,?,?)""",
                (classification_id, item_key[0], item_key[1], revision_key[2],
                 classified_at, EVIDENCE_POLICY_VERSION, status, reason,
                 canonical_hash((row["content_hash"], status, reason))),
            )
            inserted += cursor.rowcount
    integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
    receipt.update({
        "classification_rows_inserted": inserted,
        "integrity_check": integrity,
        "remaining_items": connection.execute(
            "SELECT count(*) FROM (SELECT DISTINCT source, source_item_id FROM news_revisions)"
        ).fetchone()[0],
        "remaining_revisions": connection.execute(
            "SELECT count(*) FROM news_revisions"
        ).fetchone()[0],
    })
    connection.close()
    return receipt
