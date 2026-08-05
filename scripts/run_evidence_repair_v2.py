#!/usr/bin/env python
"""Run one verified append-only Phase 2F evidence repair batch."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


MODULE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MODULE_ROOT))

from xauusd_forecaster.forward_ledger import ForwardLedger  # noqa: E402
from xauusd_forecaster.repair_v2 import run_repair  # noqa: E402


UTC = timezone.utc


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--local-root", type=Path, default=MODULE_ROOT / ".local" / "forward")
    parser.add_argument("--source-cutoff")
    parser.add_argument("--evaluation-epoch")
    args = parser.parse_args()
    local_root = args.local_root.resolve()
    database = local_root / "forward-evidence.sqlite3"
    now = datetime.now(UTC)
    source_cutoff = datetime.fromisoformat(args.source_cutoff) if args.source_cutoff else now
    evaluation_epoch = datetime.fromisoformat(args.evaluation_epoch) if args.evaluation_epoch else source_cutoff
    commit = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=MODULE_ROOT, text=True
    ).strip()

    backup = local_root / "backups" / f"pre-repair-v2-{source_cutoff.strftime('%Y%m%dT%H%M%SZ')}.sqlite3"
    backup.parent.mkdir(parents=True, exist_ok=True)
    source = sqlite3.connect(database)
    destination = sqlite3.connect(backup)
    try:
        source.backup(destination)
        integrity = destination.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise RuntimeError(f"backup integrity check failed: {integrity}")
    finally:
        destination.close()
        source.close()
    database_hash_before = file_hash(database)
    backup_hash = file_hash(backup)

    ledger = ForwardLedger(database, now=now)
    try:
        report = run_repair(
            ledger, local_root=local_root, source_cutoff=source_cutoff,
            evaluation_epoch_v2=evaluation_epoch, code_commit=commit,
        )
        integrity_after = ledger.connection.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity_after != "ok":
            raise RuntimeError(f"post-repair integrity check failed: {integrity_after}")
    finally:
        ledger.close()
    receipt = {
        "schema": "xauusd.phase2f.migration-receipt.v2",
        "created_at": datetime.now(UTC).isoformat(),
        "source_cutoff": source_cutoff.isoformat(),
        "evaluation_epoch_v2": evaluation_epoch.isoformat(),
        "git_commit": commit,
        "database": str(database),
        "database_sha256_before": database_hash_before,
        "backup": str(backup),
        "backup_sha256": backup_hash,
        "backup_integrity_check": "ok",
        "post_repair_integrity_check": integrity_after,
        "repair": report,
    }
    target = local_root / f"migration-receipt-v2-{source_cutoff.strftime('%Y%m%dT%H%M%SZ')}.json"
    target.write_text(json.dumps(receipt, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(receipt, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
