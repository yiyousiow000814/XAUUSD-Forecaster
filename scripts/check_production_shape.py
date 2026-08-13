#!/usr/bin/env python
"""Fail closed when a staged runtime breaks a cross-component contract."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import urllib.request
from pathlib import Path


MODULE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MODULE_ROOT))

from xauusd_forecaster.production_shape import production_shape_violations  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--status-url", default="http://127.0.0.1:8765/api/status")
    parser.add_argument("--sync-status-file", type=Path)
    args = parser.parse_args()

    with urllib.request.urlopen(args.status_url, timeout=20) as response:
        status = json.loads(response.read())
    sync_status = None
    if args.sync_status_file and args.sync_status_file.exists():
        sync_status = json.loads(args.sync_status_file.read_text(encoding="utf-8-sig"))
    connection = sqlite3.connect(
        f"file:{args.database.resolve()}?mode=ro", uri=True, timeout=5,
    )
    try:
        violations = production_shape_violations(
            connection, status, sync_status=sync_status,
        )
    finally:
        connection.close()
    print(json.dumps({
        "status": "PASS" if not violations else "FAIL",
        "violations": violations,
    }, ensure_ascii=False, sort_keys=True))
    return 0 if not violations else 1


if __name__ == "__main__":
    raise SystemExit(main())
