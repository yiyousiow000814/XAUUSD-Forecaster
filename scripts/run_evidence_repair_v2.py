#!/usr/bin/env python
"""Run one verified append-only Phase 2F evidence repair batch."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import subprocess
import sys
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path


MODULE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MODULE_ROOT))

from xauusd_forecaster.forward_ledger import ForwardLedger  # noqa: E402
from xauusd_forecaster.repair_v2 import run_repair  # noqa: E402
from xauusd_forecaster.clock_recovery import (  # noqa: E402
    exclude_snapshot_only_clock, snapshot_only_evidence, is_excluded_snapshot,
)


UTC = timezone.utc


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--local-root", type=Path)
    parser.add_argument("--source-cutoff")
    parser.add_argument("--evaluation-epoch")
    parser.add_argument("--snapshot-only-clock")
    parser.add_argument("--expected-snapshot-hash")
    parser.add_argument("--inspect-snapshot-only", action="store_true")
    args = parser.parse_args()
    if args.snapshot_only_clock:
        if (not args.expected_snapshot_hash or args.source_cutoff or args.evaluation_epoch
                or args.local_root is None):
            parser.error("snapshot-only recovery requires explicit local root/hash and no epoch migration")
        database = args.local_root.resolve() / "forward-evidence.sqlite3"
        clock = datetime.fromisoformat(args.snapshot_only_clock)
        # This path reuses a previously verified backup. It never creates a new
        # database, installs schema, scans history or hashes the database file.
        mode = "ro" if args.inspect_snapshot_only else "rw"
        with closing(sqlite3.connect(
            database.as_uri() + f"?mode={mode}", uri=True, timeout=3,
        )) as connection:
            if args.inspect_snapshot_only:
                result = snapshot_only_evidence(
                    connection, decision_time=clock,
                    expected_snapshot_hash=args.expected_snapshot_hash,
                )
                result["exclusion_recorded"] = is_excluded_snapshot(
                    connection, decision_time=clock,
                    snapshot_hash=args.expected_snapshot_hash,
                )
            else:
                commit = subprocess.check_output(
                    ["git", "rev-parse", "HEAD"], cwd=MODULE_ROOT,
                    text=True, encoding="utf-8", timeout=10,
                ).strip()
                result = exclude_snapshot_only_clock(
                    connection, decision_time=clock,
                    expected_snapshot_hash=args.expected_snapshot_hash,
                    code_commit=commit, recovered_at=datetime.now(UTC),
                )
        print(json.dumps(result, sort_keys=True))
        return 0
    if args.expected_snapshot_hash or args.inspect_snapshot_only:
        parser.error("snapshot-only options require --snapshot-only-clock")
    local_root = (args.local_root or MODULE_ROOT / ".local" / "forward").resolve()
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
