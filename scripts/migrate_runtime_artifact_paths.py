#!/usr/bin/env python
"""Plan, apply, verify, or roll back the bounded artifact locator migration."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path


MODULE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MODULE_ROOT))

from xauusd_forecaster.artifact_path_migration import (  # noqa: E402
    apply_artifact_path_migration,
    build_artifact_path_migration_plan,
    ensure_old_stable_compatibility_alias,
    read_migration_receipt,
    rollback_artifact_path_migration,
    verify_artifact_path_migration,
    write_migration_receipt,
)
from xauusd_forecaster.training_v2 import (  # noqa: E402
    require_current_contract_generation,
)


UTC = timezone.utc


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--runtime-root", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument(
        "--action", choices=("plan", "apply", "verify", "rollback"), required=True,
    )
    args = parser.parse_args()
    database = args.database.resolve()
    runtime_forward = (args.runtime_root.resolve() / ".local" / "forward")
    connection = sqlite3.connect(database, timeout=5)
    connection.row_factory = sqlite3.Row
    try:
        if args.action == "plan":
            receipt = build_artifact_path_migration_plan(
                connection, database=database,
                runtime_forward_root=runtime_forward,
            )
            write_migration_receipt(
                args.receipt, receipt, runtime_forward_root=runtime_forward,
            )
            result = "PLANNED"
        else:
            receipt = read_migration_receipt(
                args.receipt, runtime_forward_root=runtime_forward,
            )
            if args.action == "apply":
                result = apply_artifact_path_migration(connection, receipt)
                try:
                    require_current_contract_generation(connection)
                    alias_result = ensure_old_stable_compatibility_alias(receipt)
                except Exception:
                    rollback_artifact_path_migration(connection, receipt)
                    raise
                receipt["old_stable_compatibility_alias"]["result"] = alias_result
            elif args.action == "rollback":
                result = rollback_artifact_path_migration(connection, receipt)
                # Database rollback does not retire the prior Stable revision.
                # Keep its exact owned locator bridge until that rollback target
                # is formally retired by Release Control.
                receipt["old_stable_compatibility_alias"]["result"] = (
                    "PRESERVED_FOR_OLD_STABLE"
                )
            else:
                result = verify_artifact_path_migration(connection, receipt)
                require_current_contract_generation(connection)
            receipt["transaction_result"] = result
            receipt["completed_at"] = datetime.now(UTC).isoformat(
                timespec="microseconds"
            )
            write_migration_receipt(
                args.receipt, receipt, runtime_forward_root=runtime_forward,
            )
        print(json.dumps({
            "status": result,
            "receipt": str(args.receipt.resolve()),
            "active_generation_id": receipt["active_generation_id"],
            "dispositions": receipt["model_updates_v2_dispositions"],
        }, sort_keys=True))
        return 0
    finally:
        connection.close()


if __name__ == "__main__":
    raise SystemExit(main())
