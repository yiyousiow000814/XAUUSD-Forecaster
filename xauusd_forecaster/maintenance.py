"""Lossless local retention for completed quote days and the Forward ledger."""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .forward_ledger import ForwardLedger


UTC = timezone.utc


def archive_completed_quote_days(quote_root: Path, now: datetime) -> list[Path]:
    """Gzip completed UTC quote files atomically; never touch today's live file."""
    archived: list[Path] = []
    if not quote_root.exists():
        return archived
    today = now.astimezone(UTC).strftime("%Y%m%d")
    for source in sorted(quote_root.glob("xauusd-quotes-*.jsonl")):
        day = source.stem.removeprefix("xauusd-quotes-")
        if len(day) != 8 or not day.isdigit() or day >= today:
            continue
        modified = datetime.fromtimestamp(source.stat().st_mtime, UTC)
        if now.astimezone(UTC) - modified < timedelta(minutes=5):
            continue
        target = source.with_suffix(source.suffix + ".gz")
        if target.exists():
            continue
        temporary = target.with_suffix(target.suffix + ".tmp")
        digest = hashlib.sha256()
        with source.open("rb") as input_handle, gzip.open(
            temporary, "wb", compresslevel=6
        ) as output_handle:
            while chunk := input_handle.read(1024 * 1024):
                digest.update(chunk)
                output_handle.write(chunk)
        os.replace(temporary, target)
        source.unlink()
        receipt = target.with_suffix(target.suffix + ".receipt.json")
        receipt.write_text(
            json.dumps(
                {
                    "schema": "xauusd.forward.quote-archive.v1",
                    "source_name": source.name,
                    "archive_name": target.name,
                    "uncompressed_sha256": digest.hexdigest(),
                    "archived_at": now.astimezone(UTC).isoformat(),
                },
                indent=2,
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        archived.append(target)
    return archived


def backup_forward_ledger(
    ledger: ForwardLedger, backup_root: Path, now: datetime
) -> Path:
    """Create one verified SQLite online-backup snapshot per UTC day."""
    backup_root.mkdir(parents=True, exist_ok=True)
    day = now.astimezone(UTC).strftime("%Y%m%d")
    target = backup_root / f"forward-evidence-{day}.sqlite3"
    temporary = backup_root / (
        f".{target.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
    )
    destination = sqlite3.connect(temporary)
    try:
        ledger.connection.backup(destination)
        result = destination.execute("PRAGMA integrity_check").fetchone()[0]
        if result != "ok":
            raise RuntimeError(f"backup integrity check failed: {result}")
    finally:
        destination.close()
    try:
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    return target
