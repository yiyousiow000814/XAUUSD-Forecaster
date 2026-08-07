"""Explicit maintenance for intake rows that never became usable evidence.

This module is intentionally not part of normal collection.  It exists to
repair the historical design mistake where headline-only search candidates
were appended to the immutable evidence ledger before publisher text was
available.  Every destructive run requires a verified SQLite backup.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
import sqlite3

from .news_relevance import google_news_item_is_relevant, is_google_news_source
from .news_time import assess_news_time


NEWS_TABLES = (
    "news_revisions",
    "news_annotations",
    "news_title_translations",
    "news_llm_failures",
    "news_content_failures",
)
CHILD_TABLES = NEWS_TABLES[1:]


@dataclass(frozen=True)
class NewsPrunePlan:
    keep_items: int
    delete_items: int
    delete_revisions: int
    delete_unused_revisions: int
    reasons: dict[str, int]
    keys: tuple[tuple[str, str], ...]
    revision_keys: tuple[tuple[str, str, int], ...]


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
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _install_delete_triggers(connection: sqlite3.Connection) -> None:
    for table in NEWS_TABLES:
        connection.execute(
            f"CREATE TRIGGER IF NOT EXISTS {table}_no_delete "
            f"BEFORE DELETE ON {table} BEGIN SELECT RAISE(ABORT, "
            f"'{table} is append-only'); END;"
        )


def prune_unused_news(
    database_path: Path,
    *,
    backup_directory: Path,
    dry_run: bool = False,
) -> dict[str, object]:
    """Back up, remove unusable intake rows, verify, and return a receipt."""
    database_path = database_path.resolve()
    backup_directory.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(database_path, timeout=60)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout=60000")
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
        "keep_items": plan.keep_items,
        "delete_items": plan.delete_items,
        "delete_revisions": plan.delete_revisions,
        "delete_unused_revisions": plan.delete_unused_revisions,
        "reasons": plan.reasons,
    }
    if dry_run or (not plan.keys and not plan.revision_keys):
        connection.close()
        return receipt

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    backup_path = backup_directory / f"pre-news-prune-{stamp}.sqlite3"
    backup = sqlite3.connect(backup_path)
    connection.backup(backup)
    backup.close()
    backup_check = sqlite3.connect(backup_path).execute("PRAGMA integrity_check").fetchone()[0]
    if backup_check != "ok":
        connection.close()
        raise RuntimeError(f"backup integrity check failed: {backup_check}")

    try:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            "CREATE TEMP TABLE prune_news_keys(source TEXT, source_item_id TEXT, "
            "PRIMARY KEY(source, source_item_id))"
        )
        connection.executemany("INSERT INTO prune_news_keys VALUES (?, ?)", plan.keys)
        connection.execute(
            "CREATE TEMP TABLE prune_news_revisions(source TEXT, source_item_id TEXT, "
            "revision_number INTEGER, PRIMARY KEY(source, source_item_id, revision_number))"
        )
        connection.executemany(
            "INSERT INTO prune_news_revisions VALUES (?, ?, ?)", plan.revision_keys
        )
        for table in NEWS_TABLES:
            connection.execute(f"DROP TRIGGER IF EXISTS {table}_no_delete")
        for table in CHILD_TABLES:
            connection.execute(
                f"DELETE FROM {table} WHERE EXISTS (SELECT 1 FROM prune_news_keys p "
                f"WHERE p.source={table}.source AND "
                f"p.source_item_id={table}.source_item_id)"
            )
        connection.execute(
            "DELETE FROM news_revisions WHERE EXISTS (SELECT 1 FROM prune_news_keys p "
            "WHERE p.source=news_revisions.source AND "
            "p.source_item_id=news_revisions.source_item_id)"
        )
        for table in CHILD_TABLES:
            connection.execute(
                f"DELETE FROM {table} WHERE EXISTS (SELECT 1 FROM prune_news_revisions p "
                f"WHERE p.source={table}.source AND "
                f"p.source_item_id={table}.source_item_id AND "
                f"p.revision_number={table}.revision_number)"
            )
        connection.execute(
            "DELETE FROM news_revisions WHERE EXISTS "
            "(SELECT 1 FROM prune_news_revisions p "
            "WHERE p.source=news_revisions.source AND "
            "p.source_item_id=news_revisions.source_item_id AND "
            "p.revision_number=news_revisions.revision_number)"
        )
        _install_delete_triggers(connection)
        connection.commit()
        connection.execute("VACUUM")
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        foreign_keys = connection.execute("PRAGMA foreign_key_check").fetchall()
        if integrity != "ok" or foreign_keys:
            raise RuntimeError(
                f"post-prune verification failed: integrity={integrity}, "
                f"foreign_key_rows={len(foreign_keys)}"
            )
    except Exception:
        connection.rollback()
        _install_delete_triggers(connection)
        connection.commit()
        connection.close()
        raise

    receipt.update(
        {
            "backup": str(backup_path),
            "backup_sha256": _sha256(backup_path),
            "integrity_check": "ok",
            "remaining_items": connection.execute(
                "SELECT count(*) FROM (SELECT DISTINCT source, source_item_id FROM news_revisions)"
            ).fetchone()[0],
            "remaining_revisions": connection.execute(
                "SELECT count(*) FROM news_revisions"
            ).fetchone()[0],
        }
    )
    connection.close()
    receipt_path = backup_directory / f"news-prune-receipt-{stamp}.json"
    receipt_path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8")
    receipt["receipt"] = str(receipt_path)
    return receipt
