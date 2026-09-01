"""Receipt-bound storage lifecycle status for the local Dashboard."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from xauusd_forecaster.maintenance import (
    BACKUP_RECEIPT_SCHEMA,
    BACKUP_RETENTION_SCHEMA,
    BACKUP_RETENTION_STATE,
)
from xauusd_forecaster.sqlite_wal import (
    FORWARD_WAL_CHECKPOINT_SCHEMA,
    FORWARD_WAL_CHECKPOINT_STATE,
)


UTC = timezone.utc


def canonical_payload_digest(payload: dict) -> str:
    return hashlib.sha256(json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")).hexdigest()


def backup_lifecycle_status(
    backup_root: Path,
    *,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> dict:
    state_path = backup_root / BACKUP_RETENTION_STATE
    result = {
        "status": "UNKNOWN",
        "last_success": None,
        "age_seconds": None,
        "last_verified_backup": None,
        "managed_count": 0,
        "managed_bytes": 0,
        "unknown_count": 0,
        "unknown_bytes": 0,
        "managed_gib_days": 0.0,
        "unknown_gib_days": 0.0,
        "disk_gib_days": 0.0,
        "proven_stale_reclaimed_count": 0,
        "proven_stale_reclaimed_bytes": 0,
        "policy": None,
        "last_error": "Backup retention state is not available",
    }
    if state_path.is_file():
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
            digest = str(state.pop("receipt_digest", ""))
            if not digest or digest != canonical_payload_digest(state):
                raise ValueError("retention receipt digest")
            if state.get("schema") != BACKUP_RETENTION_SCHEMA:
                raise ValueError("retention receipt schema")
            retained = state.get("retained") or []
            verified = []
            for name in retained:
                target = backup_root / str(name)
                if (
                    target.parent.resolve() != backup_root.resolve()
                    or not re.fullmatch(
                        r"forward-evidence-\d{8}\.sqlite3", target.name,
                    )
                    or not target.is_file()
                ):
                    raise ValueError("retained backup identity")
                verified.append(target)
            if len(verified) != int(state.get("managed_count") or 0):
                raise ValueError("retained backup count")
            result.update({
                "status": "OK",
                "last_success": state.get("completed_at"),
                "age_seconds": max(0.0, (
                    clock() - datetime.fromisoformat(str(state["completed_at"]))
                ).total_seconds()),
                "last_verified_backup": (
                    datetime.fromtimestamp(
                        max(path.stat().st_mtime for path in verified), UTC,
                    ).isoformat()
                    if verified else None
                ),
                "managed_count": int(state.get("managed_count") or 0),
                "managed_bytes": int(state.get("managed_bytes") or 0),
                "unknown_count": int(state.get("unknown_count") or 0),
                "unknown_bytes": int(state.get("unknown_bytes") or 0),
                "managed_gib_days": float(
                    state.get("managed_gib_days") or 0.0
                ),
                "unknown_gib_days": float(
                    state.get("unknown_gib_days") or 0.0
                ),
                "disk_gib_days": float(state.get("disk_gib_days") or 0.0),
                "proven_stale_reclaimed_count": int(
                    state.get("proven_stale_reclaimed_count") or 0
                ),
                "proven_stale_reclaimed_bytes": int(
                    state.get("proven_stale_reclaimed_bytes") or 0
                ),
                "policy": state.get("policy"),
                "last_error": None,
            })
            return result
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            result["last_error"] = f"Invalid backup retention state: {exc}"
            return result

    verified = []
    for receipt_path in backup_root.glob("forward-evidence-*.sqlite3.receipt.json"):
        try:
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            digest = str(receipt.pop("receipt_digest", ""))
            if (
                receipt.get("schema") != BACKUP_RECEIPT_SCHEMA
                or not digest
                or digest != canonical_payload_digest(receipt)
            ):
                continue
            target = Path(str((receipt.get("snapshot") or {}).get("path") or ""))
            if target.parent.resolve() == backup_root.resolve() and target.is_file():
                verified.append(target)
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            continue
    if verified:
        result.update({
            "status": "PENDING_RETENTION",
            "last_verified_backup": datetime.fromtimestamp(
                max(path.stat().st_mtime for path in verified), UTC,
            ).isoformat(),
            "managed_count": len(verified),
            "managed_bytes": sum(path.stat().st_size for path in verified),
            "last_error": "Backup retention inventory has not completed",
        })
    return result


def wal_checkpoint_status(
    state_root: Path,
    *,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> dict:
    state_path = state_root / FORWARD_WAL_CHECKPOINT_STATE
    result = {
        "status": "UNKNOWN",
        "last_success": None,
        "age_seconds": None,
        "pending_frames": None,
        "wal_bytes": None,
        "journal_size_limit_bytes": None,
        "last_error": "WAL checkpoint state is not available",
    }
    if not state_path.is_file():
        return result
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
        digest = str(state.pop("receipt_digest", ""))
        if not digest or digest != canonical_payload_digest(state):
            raise ValueError("WAL checkpoint receipt digest")
        if state.get("schema") != FORWARD_WAL_CHECKPOINT_SCHEMA:
            raise ValueError("WAL checkpoint receipt schema")
        recorded_at = datetime.fromisoformat(str(state["recorded_at"]))
        age_seconds = max(0.0, (clock() - recorded_at).total_seconds())
        checkpoint_status = str(state.get("status") or "UNKNOWN")
        if checkpoint_status in {"CHECKPOINTED", "TRUNCATED"}:
            component_status = "OK"
        elif checkpoint_status in {
            "CHECKPOINT_BUSY", "READER_PINNED", "TRUNCATE_BUSY",
            "TRUNCATE_INCOMPLETE",
        }:
            component_status = "WARN"
        else:
            component_status = "ERROR"
        if age_seconds > 300:
            component_status = "ERROR"
        result.update({
            "status": component_status,
            "checkpoint_status": checkpoint_status,
            "last_success": state.get("recorded_at"),
            "age_seconds": age_seconds,
            "pending_frames": int(state.get("pending_frames") or 0),
            "wal_bytes": int(state.get("wal_bytes_after") or 0),
            "journal_size_limit_bytes": int(
                state.get("journal_size_limit_bytes") or 0
            ),
            "last_error": state.get("error"),
        })
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        result["status"] = "ERROR"
        result["last_error"] = f"Invalid WAL checkpoint state: {exc}"
    return result
