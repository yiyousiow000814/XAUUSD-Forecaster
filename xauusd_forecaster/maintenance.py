"""Lossless local retention for completed quote days and the Forward ledger."""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import re
import sqlite3
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .forward_ledger import ForwardLedger
from .training_owner import _process_identity_alive, _process_start_token


UTC = timezone.utc
BACKUP_RECEIPT_SCHEMA = "xauusd.forward.daily-backup-receipt.v2"
BACKUP_TEMP_PREFIX = ".daily-backup-v2-"
BACKUP_RETENTION_SCHEMA = "xauusd.forward.backup-retention.v1"
BACKUP_RETENTION_PLAN_SCHEMA = "xauusd.forward.backup-retention-plan.v1"
BACKUP_RETENTION_STATE = "daily-backup-retention-state.json"
BACKUP_RETENTION_PLAN = ".daily-backup-retention-plan.json"
BACKUP_RETENTION_OWNER = ".daily-backup-retention-owner"
DAILY_BACKUP_NAME = re.compile(r"^forward-evidence-(\d{8})\.sqlite3$")
LEGACY_BACKUP_TEMP_NAME = re.compile(
    r"^\.(forward-evidence-\d{8}\.sqlite3)\.(\d+)\.([0-9a-f]{32})\.tmp$"
)
BACKUP_RECLAIM_PLAN = ".proven-stale-backup-reclaim-plan.json"
BACKUP_RECLAIM_STATE = "proven-stale-backup-reclaim-state.json"
BACKUP_RECLAIM_PLAN_SCHEMA = "xauusd.forward.proven-stale-reclaim-plan.v1"
BACKUP_RECLAIM_STATE_SCHEMA = "xauusd.forward.proven-stale-reclaim.v1"
BACKUP_RECLAIM_GRACE = timedelta(hours=48)
BACKUP_RECLAIM_REFERENCE_FILE_LIMIT = 512
BACKUP_RECLAIM_REFERENCE_BYTE_LIMIT = 64 * 1024**2
BACKUP_RECLAIM_HISTORY_LIMIT = 64


@dataclass(frozen=True)
class BackupRetentionPolicy:
    daily: int = 7
    weekly: int = 4
    monthly: int = 3
    maximum_snapshots: int = 14
    maximum_total_bytes: int = 128 * 1024**3
    maximum_age_days: int = 100

    def __post_init__(self) -> None:
        if (
            self.daily < 1
            or self.weekly < 0
            or self.monthly < 0
            or self.maximum_snapshots < 1
            or self.maximum_total_bytes < 1
            or self.maximum_age_days < 1
        ):
            raise ValueError("backup retention policy values must be positive")


DEFAULT_BACKUP_RETENTION_POLICY = BackupRetentionPolicy()


@dataclass(frozen=True)
class DailyBackupResult:
    status: str
    path: Path
    receipt_path: Path
    heavy_operation: bool
    recovered_interrupted_owner: bool = False


@dataclass(frozen=True)
class BackupRetentionResult:
    status: str
    managed_count: int
    retained_count: int
    deleted_count: int
    unknown_count: int
    managed_bytes: int
    unknown_bytes: int
    disk_gib_days: float
    state_path: Path


def _json_digest(payload: dict) -> str:
    return hashlib.sha256(json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")).hexdigest()


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


def _atomic_json(path: Path, payload: dict) -> None:
    temporary = path.with_suffix(path.suffix + f".{uuid.uuid4().hex}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _forward_epoch(connection: sqlite3.Connection) -> str:
    row = connection.execute(
        "SELECT value FROM runtime_metadata WHERE key='FORWARD_EPOCH'"
    ).fetchone()
    if row is None or not row[0]:
        raise RuntimeError("BACKUP_SOURCE_FORWARD_EPOCH_MISSING")
    return str(row[0])


def _source_identity(connection: sqlite3.Connection, database: Path) -> dict:
    stat = database.stat()
    return {
        "path": str(database.resolve()),
        "device": int(stat.st_dev),
        "file_id": int(stat.st_ino),
        "forward_epoch": _forward_epoch(connection),
        "size_at_completion": int(stat.st_size),
    }


def _snapshot_identity(path: Path) -> dict:
    stat = path.stat()
    # A finalized snapshot is immutable. Without immutable=1, merely reading a
    # WAL-mode backup can create new -wal/-shm sidecars in the backup estate.
    connection = sqlite3.connect(
        f"file:{path.as_posix()}?mode=ro&immutable=1", uri=True,
    )
    try:
        page_size = int(connection.execute("PRAGMA page_size").fetchone()[0])
        page_count = int(connection.execute("PRAGMA page_count").fetchone()[0])
        identity = {
            "path": str(path.resolve()),
            "device": int(stat.st_dev),
            "file_id": int(stat.st_ino),
            "size": int(stat.st_size),
            "modified_ns": int(stat.st_mtime_ns),
            "page_size": page_size,
            "page_count": page_count,
            "user_version": int(
                connection.execute("PRAGMA user_version").fetchone()[0]
            ),
            "application_id": int(
                connection.execute("PRAGMA application_id").fetchone()[0]
            ),
            "forward_epoch": _forward_epoch(connection),
        }
    finally:
        connection.close()
    if identity["size"] != identity["page_size"] * identity["page_count"]:
        raise RuntimeError("BACKUP_SNAPSHOT_SIZE_MISMATCH")
    return identity


def _receipt_path(target: Path) -> Path:
    return target.with_suffix(target.suffix + ".receipt.json")


def _failure_path(target: Path) -> Path:
    return target.with_suffix(target.suffix + ".failure.json")


def _write_failure_receipt(
    path: Path, *, day: str, source: dict, error: Exception,
) -> None:
    payload = {
        "schema": "xauusd.forward.daily-backup-failure.v1",
        "utc_day": day,
        "failed_at": datetime.now(UTC).isoformat(timespec="microseconds"),
        "source_database": source,
        "error_type": type(error).__name__,
        "error": str(error)[:1000],
    }
    payload["receipt_digest"] = _json_digest(payload)
    _atomic_json(path, payload)


def _validate_failure_receipt(
    path: Path, *, day: str, source: dict,
) -> None:
    payload = json.loads(path.read_text(encoding="utf-8"))
    digest = str(payload.pop("receipt_digest", ""))
    if not digest or digest != _json_digest(payload):
        raise RuntimeError("BACKUP_FAILURE_RECEIPT_TAMPERED")
    if payload.get("schema") != "xauusd.forward.daily-backup-failure.v1":
        raise RuntimeError("BACKUP_FAILURE_RECEIPT_SCHEMA_INVALID")
    if payload.get("utc_day") != day:
        raise RuntimeError("BACKUP_FAILURE_RECEIPT_DAY_MISMATCH")
    expected_source = payload.get("source_database") or {}
    for field in ("path", "device", "file_id", "forward_epoch"):
        if expected_source.get(field) != source[field]:
            raise RuntimeError(f"BACKUP_FAILURE_STALE_SOURCE:{field}")


def _completion_receipt(
    *, target: Path, day: str, source: dict, completion_mode: str,
) -> dict:
    payload = {
        "schema": BACKUP_RECEIPT_SCHEMA,
        "utc_day": day,
        "completed_at": datetime.now(UTC).isoformat(timespec="microseconds"),
        "completion_mode": completion_mode,
        "integrity_contract": (
            "SQLITE_ONLINE_BACKUP_THEN_FULL_INTEGRITY_CHECK_BEFORE_ATOMIC_FINAL"
            if completion_mode == "CREATED"
            else "LEGACY_FINAL_NAME_ONLY_AFTER_FULL_INTEGRITY_CHECK"
        ),
        "source_database": source,
        "snapshot": _snapshot_identity(target),
    }
    payload["receipt_digest"] = _json_digest(payload)
    return payload


def _validate_completion_receipt(
    receipt_path: Path, *, target: Path, day: str, source: dict,
) -> None:
    payload = json.loads(receipt_path.read_text(encoding="utf-8"))
    digest = str(payload.pop("receipt_digest", ""))
    if not digest or digest != _json_digest(payload):
        raise RuntimeError("BACKUP_RECEIPT_TAMPERED")
    if payload.get("schema") != BACKUP_RECEIPT_SCHEMA:
        raise RuntimeError("BACKUP_RECEIPT_SCHEMA_INVALID")
    if payload.get("utc_day") != day:
        raise RuntimeError("BACKUP_RECEIPT_DAY_MISMATCH")
    expected_source = payload.get("source_database") or {}
    for field in ("path", "device", "file_id", "forward_epoch"):
        if expected_source.get(field) != source[field]:
            raise RuntimeError(f"BACKUP_STALE_SOURCE:{field}")
    if payload.get("snapshot") != _snapshot_identity(target):
        raise RuntimeError("BACKUP_SNAPSHOT_IDENTITY_CHANGED")


def _retention_policy_payload(policy: BackupRetentionPolicy) -> dict:
    return {
        "daily": policy.daily,
        "weekly": policy.weekly,
        "monthly": policy.monthly,
        "maximum_snapshots": policy.maximum_snapshots,
        "maximum_total_bytes": policy.maximum_total_bytes,
        "maximum_age_days": policy.maximum_age_days,
    }


def _stable_source_identity(source: dict) -> dict:
    return {
        field: source[field]
        for field in ("path", "device", "file_id", "forward_epoch")
    }


def _managed_backup_entries(
    backup_root: Path, *, source: dict,
) -> tuple[list[dict], list[dict]]:
    managed: list[dict] = []
    owned_names = {
        BACKUP_RETENTION_STATE,
        BACKUP_RETENTION_PLAN,
        BACKUP_RECLAIM_STATE,
        BACKUP_RECLAIM_PLAN,
    }
    for target in sorted(backup_root.glob("forward-evidence-*.sqlite3")):
        match = DAILY_BACKUP_NAME.fullmatch(target.name)
        if match is None:
            continue
        receipt_path = _receipt_path(target)
        if not receipt_path.is_file():
            continue
        try:
            _validate_completion_receipt(
                receipt_path,
                target=target,
                day=match.group(1),
                source=source,
            )
        except (OSError, ValueError, RuntimeError, json.JSONDecodeError):
            # A changed or malformed pair is not deletion authority. Leave both
            # objects outside owned_names so the inventory reports them as
            # UNKNOWN and retention cannot unlink either one.
            continue
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        managed.append({
            "day": match.group(1),
            "target": target,
            "receipt": receipt_path,
            "bytes": target.stat().st_size,
            "modified_at": datetime.fromtimestamp(
                target.stat().st_mtime, UTC,
            ).isoformat(),
            "snapshot": receipt["snapshot"],
            "receipt_digest": receipt["receipt_digest"],
        })
        owned_names.update((target.name, receipt_path.name))

    unknown = []
    for path in sorted(backup_root.iterdir()):
        if path.is_file() and path.name not in owned_names:
            unknown.append({
                "name": path.name,
                "bytes": path.stat().st_size,
                "modified_at": datetime.fromtimestamp(
                    path.stat().st_mtime, UTC,
                ).isoformat(),
            })
    return managed, unknown


def _retained_backup_names(
    entries: list[dict], policy: BackupRetentionPolicy, now: datetime,
) -> set[str]:
    ordered = sorted(entries, key=lambda item: item["day"], reverse=True)
    keep: list[dict] = []

    def add_unique(candidates: list[dict], count: int, key) -> None:
        seen = set()
        for item in candidates:
            value = key(datetime.strptime(item["day"], "%Y%m%d").date())
            if value in seen:
                continue
            seen.add(value)
            keep.append(item)
            if len(seen) >= count:
                break

    keep.extend(ordered[:policy.daily])
    remaining = [item for item in ordered if item not in keep]
    add_unique(
        remaining,
        policy.weekly,
        lambda day: day.isocalendar()[:2],
    )
    remaining = [item for item in ordered if item not in keep]
    add_unique(remaining, policy.monthly, lambda day: (day.year, day.month))

    horizon = now.astimezone(UTC).date() - timedelta(days=policy.maximum_age_days)
    keep = [
        item for item in keep
        if datetime.strptime(item["day"], "%Y%m%d").date() >= horizon
    ][:policy.maximum_snapshots]
    while sum(item["bytes"] for item in keep) > policy.maximum_total_bytes:
        if len(keep) <= 1:
            raise RuntimeError("BACKUP_RETENTION_NEWEST_EXCEEDS_BYTE_BUDGET")
        keep.pop()
    return {item["target"].name for item in keep}


def _retention_owner_paths(backup_root: Path) -> tuple[Path, Path]:
    root = backup_root / BACKUP_RETENTION_OWNER
    return root, root / "owner.json"


def _acquire_retention_owner(backup_root: Path) -> tuple[Path, bool] | None:
    owner_root, owner_path = _retention_owner_paths(backup_root)
    recovered = False
    for _ in range(2):
        try:
            owner_root.mkdir()
        except FileExistsError:
            try:
                owner = json.loads(owner_path.read_text(encoding="utf-8"))
                process_id = int(owner["process_id"])
                token = str(owner["process_start_token"])
                if owner.get("schema") != "xauusd.forward.backup-retention-owner.v1":
                    raise ValueError("owner schema")
            except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
                raise RuntimeError("BACKUP_RETENTION_OWNER_EVIDENCE_INVALID") from exc
            alive = _process_identity_alive(process_id, token)
            if alive is True:
                return None
            if alive is not False:
                raise RuntimeError("BACKUP_RETENTION_OWNER_LIVENESS_UNKNOWN")
            owner_path.unlink()
            owner_root.rmdir()
            recovered = True
            continue
        process_id = os.getpid()
        token, alive = _process_start_token(process_id)
        if alive is not True or not token:
            owner_root.rmdir()
            raise RuntimeError("BACKUP_RETENTION_OWNER_IDENTITY_UNAVAILABLE")
        _atomic_json(owner_path, {
            "schema": "xauusd.forward.backup-retention-owner.v1",
            "process_id": process_id,
            "process_start_token": token,
            "acquired_at": datetime.now(UTC).isoformat(timespec="microseconds"),
        })
        return owner_root, recovered
    raise RuntimeError("BACKUP_RETENTION_OWNER_RECOVERY_FAILED")


def _validated_retention_plan(
    path: Path, *, source: dict, policy: BackupRetentionPolicy,
) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    digest = str(payload.pop("plan_digest", ""))
    if not digest or digest != _json_digest(payload):
        raise RuntimeError("BACKUP_RETENTION_PLAN_TAMPERED")
    if payload.get("schema") != BACKUP_RETENTION_PLAN_SCHEMA:
        raise RuntimeError("BACKUP_RETENTION_PLAN_SCHEMA_INVALID")
    if payload.get("source_database") != _stable_source_identity(source):
        raise RuntimeError("BACKUP_RETENTION_PLAN_SOURCE_CHANGED")
    if payload.get("policy") != _retention_policy_payload(policy):
        raise RuntimeError("BACKUP_RETENTION_PLAN_POLICY_CHANGED")
    payload["plan_digest"] = digest
    return payload


def _has_delete_capability(path: Path) -> bool | None:
    """Return whether the OS grants DELETE access without disturbing the file."""
    if os.name != "nt":
        try:
            descriptor = os.open(path, os.O_RDONLY)
        except OSError:
            return False
        os.close(descriptor)
        return True
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateFileW.argtypes = (
            wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
            wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
        )
        kernel32.CreateFileW.restype = wintypes.HANDLE
        kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
        kernel32.CloseHandle.restype = wintypes.BOOL
        handle = kernel32.CreateFileW(
            str(path),
            0x00010000,  # DELETE
            0x00000001 | 0x00000002 | 0x00000004,
            None,
            3,  # OPEN_EXISTING
            0,
            None,
        )
        invalid = wintypes.HANDLE(-1).value
        if handle == invalid:
            error = ctypes.get_last_error()
            if error in (5, 32, 33):
                return False
            return None
        kernel32.CloseHandle(handle)
        return True
    except (AttributeError, OSError, ValueError):
        return None


def _runtime_json_reference_names(
    runtime_root: Path, names: set[str], *, excluded: set[Path],
) -> set[str]:
    referenced: set[str] = set()
    inspected_files = 0
    inspected_bytes = 0
    authority_files = list(runtime_root.glob("*.json"))
    for directory in runtime_root.iterdir():
        if directory.is_dir() and (
            directory.name.endswith("receipts")
            or directory.name.endswith("inspections")
        ):
            authority_files.extend(directory.glob("*.json"))
    for path in sorted(set(authority_files)):
        resolved = path.resolve()
        if resolved in excluded:
            continue
        inspected_files += 1
        if inspected_files > BACKUP_RECLAIM_REFERENCE_FILE_LIMIT:
            raise RuntimeError("BACKUP_RECLAIM_REFERENCE_FILE_BOUND_EXCEEDED")
        try:
            size = path.stat().st_size
        except OSError as exc:
            raise RuntimeError("BACKUP_RECLAIM_REFERENCE_STAT_FAILED") from exc
        inspected_bytes += size
        if inspected_bytes > BACKUP_RECLAIM_REFERENCE_BYTE_LIMIT:
            raise RuntimeError("BACKUP_RECLAIM_REFERENCE_BYTE_BOUND_EXCEEDED")
        try:
            content = path.read_bytes()
        except OSError as exc:
            raise RuntimeError("BACKUP_RECLAIM_REFERENCE_READ_FAILED") from exc
        for name in names - referenced:
            if name.encode("ascii") in content:
                referenced.add(name)
    return referenced


def _legacy_temp_families(
    backup_root: Path, now: datetime,
) -> tuple[list[dict], list[dict]]:
    candidates: list[dict] = []
    unknown: list[dict] = []
    roots = []
    for path in sorted(backup_root.iterdir()):
        match = LEGACY_BACKUP_TEMP_NAME.fullmatch(path.name)
        if path.is_file() and match:
            roots.append((path, match))
    if not roots:
        return [], []
    names = {path.name for path, _ in roots}
    referenced = _runtime_json_reference_names(
        backup_root.parent,
        names,
        excluded={
            (backup_root / BACKUP_RECLAIM_PLAN).resolve(),
            (backup_root / BACKUP_RECLAIM_STATE).resolve(),
        },
    )
    now_utc = now.astimezone(UTC)
    for path, match in roots:
        reason = None
        stat = path.stat()
        modified = datetime.fromtimestamp(stat.st_mtime, UTC)
        process_id = int(match.group(2))
        _, alive = _process_start_token(process_id)
        final = backup_root / match.group(1)
        family = [
            item for item in (
                path.with_name(path.name + "-journal"),
                path.with_name(path.name + "-wal"),
                path.with_name(path.name + "-shm"),
                path,
            )
            if item.is_file()
        ]
        if now_utc - modified < BACKUP_RECLAIM_GRACE:
            reason = "GRACE_ACTIVE"
        elif alive is True:
            reason = "OWNER_PID_ACTIVE"
        elif alive is None:
            reason = "OWNER_PID_UNKNOWN"
        elif not final.is_file():
            reason = "FINAL_TARGET_MISSING"
        elif path.name in referenced:
            reason = "RUNTIME_REFERENCE_ACTIVE"
        elif any(_has_delete_capability(item) is not True for item in family):
            reason = "BLOCKING_HANDLE_OR_DELETE_AUTHORITY_UNKNOWN"
        record = {
            "root": path.name,
            "owner_process_id": process_id,
            "final_target": final.name,
            "files": [
                {
                    "name": item.name,
                    "bytes": item.stat().st_size,
                    "modified_ns": item.stat().st_mtime_ns,
                }
                for item in family
            ],
        }
        if reason:
            record["reason"] = reason
            unknown.append(record)
        else:
            record["proof"] = {
                "naming_contract": "legacy-online-backup-temp-v1",
                "grace_hours": int(BACKUP_RECLAIM_GRACE.total_seconds() / 3600),
                "owner_pid_absent": True,
                "final_target_exists": True,
                "runtime_reference_absent": True,
                "delete_capability": True,
            }
            candidates.append(record)
    return candidates, unknown


def _validated_reclaim_plan(path: Path, backup_root: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    digest = str(payload.pop("plan_digest", ""))
    if not digest or digest != _json_digest(payload):
        raise RuntimeError("BACKUP_RECLAIM_PLAN_TAMPERED")
    if payload.get("schema") != BACKUP_RECLAIM_PLAN_SCHEMA:
        raise RuntimeError("BACKUP_RECLAIM_PLAN_SCHEMA_INVALID")
    if Path(str(payload.get("backup_root"))).resolve() != backup_root.resolve():
        raise RuntimeError("BACKUP_RECLAIM_PLAN_ROOT_CHANGED")
    payload["plan_digest"] = digest
    return payload


def _validated_reclaim_state(path: Path, backup_root: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    digest = str(payload.pop("receipt_digest", ""))
    if not digest or digest != _json_digest(payload):
        raise RuntimeError("BACKUP_RECLAIM_STATE_TAMPERED")
    if payload.get("schema") != BACKUP_RECLAIM_STATE_SCHEMA:
        raise RuntimeError("BACKUP_RECLAIM_STATE_SCHEMA_INVALID")
    if Path(str(payload.get("backup_root"))).resolve() != backup_root.resolve():
        raise RuntimeError("BACKUP_RECLAIM_STATE_ROOT_CHANGED")
    payload["receipt_digest"] = digest
    return payload


def reclaim_proven_stale_backup_temps(
    backup_root: Path, now: datetime,
) -> dict:
    """Remove only abandoned legacy backup temp families with complete proof."""
    backup_root = backup_root.resolve()
    plan_path = backup_root / BACKUP_RECLAIM_PLAN
    state_path = backup_root / BACKUP_RECLAIM_STATE
    prior = (
        _validated_reclaim_state(state_path, backup_root)
        if state_path.is_file() else None
    )
    if plan_path.is_file():
        plan = _validated_reclaim_plan(plan_path, backup_root)
        if prior and prior.get("plan_digest") == plan["plan_digest"]:
            plan_path.unlink()
            return prior
        if plan.get("previous_receipt_digest") != (
            prior.get("receipt_digest") if prior else None
        ):
            raise RuntimeError("BACKUP_RECLAIM_PREVIOUS_RECEIPT_CHANGED")
    else:
        candidates, initial_unknown = _legacy_temp_families(backup_root, now)
        if not candidates and not initial_unknown and prior:
            return prior
        plan = {
            "schema": BACKUP_RECLAIM_PLAN_SCHEMA,
            "created_at": now.astimezone(UTC).isoformat(timespec="microseconds"),
            "backup_root": str(backup_root),
            "previous_receipt_digest": (
                prior.get("receipt_digest") if prior else None
            ),
            "candidates": candidates,
        }
        plan["plan_digest"] = _json_digest(plan)
        if candidates:
            _atomic_json(plan_path, plan)

    planned_names = {item["root"] for item in plan["candidates"]}
    references = _runtime_json_reference_names(
        backup_root.parent,
        planned_names,
        excluded={plan_path.resolve(), state_path.resolve()},
    ) if planned_names else set()
    reclaimed = []
    for candidate in plan["candidates"]:
        _, alive = _process_start_token(int(candidate["owner_process_id"]))
        if alive is not False:
            raise RuntimeError("BACKUP_RECLAIM_OWNER_NO_LONGER_ABSENT")
        if not (backup_root / candidate["final_target"]).is_file():
            raise RuntimeError("BACKUP_RECLAIM_FINAL_TARGET_CHANGED")
        if candidate["root"] in references:
            raise RuntimeError("BACKUP_RECLAIM_REFERENCE_APPEARED")
        for item in candidate["files"]:
            path = backup_root / item["name"]
            if path.parent.resolve() != backup_root:
                raise RuntimeError("BACKUP_RECLAIM_PATH_OUTSIDE_ROOT")
            if path.exists():
                stat = path.stat()
                if (
                    stat.st_size != item["bytes"]
                    or stat.st_mtime_ns != item["modified_ns"]
                    or _has_delete_capability(path) is not True
                ):
                    raise RuntimeError("BACKUP_RECLAIM_IDENTITY_CHANGED")
                path.unlink()
        reclaimed.append(candidate)

    remaining_candidates, remaining_unknown = _legacy_temp_families(
        backup_root, now,
    )
    if remaining_candidates:
        raise RuntimeError("BACKUP_RECLAIM_CANDIDATE_REMAINED")
    reclaimed_history = [
        *(prior.get("reclaimed", []) if prior else []),
        *reclaimed,
    ]
    if len(reclaimed_history) > BACKUP_RECLAIM_HISTORY_LIMIT:
        raise RuntimeError("BACKUP_RECLAIM_HISTORY_BOUND_EXCEEDED")
    state = {
        "schema": BACKUP_RECLAIM_STATE_SCHEMA,
        "completed_at": now.astimezone(UTC).isoformat(timespec="microseconds"),
        "backup_root": str(backup_root),
        "plan_digest": plan["plan_digest"],
        "previous_receipt_digest": (
            prior.get("receipt_digest") if prior else None
        ),
        "last_reclaimed_count": len(reclaimed),
        "last_reclaimed_bytes": sum(
            item["bytes"] for candidate in reclaimed for item in candidate["files"]
        ),
        "reclaimed_count": len(reclaimed_history),
        "reclaimed_bytes": sum(
            item["bytes"]
            for candidate in reclaimed_history for item in candidate["files"]
        ),
        "reclaimed": reclaimed_history,
        "unknown": remaining_unknown,
    }
    state["receipt_digest"] = _json_digest(state)
    _atomic_json(state_path, state)
    plan_path.unlink(missing_ok=True)
    return state


def apply_backup_retention(
    database: Path,
    backup_root: Path,
    now: datetime,
    *,
    source_connection: sqlite3.Connection | None = None,
    policy: BackupRetentionPolicy = DEFAULT_BACKUP_RETENTION_POLICY,
) -> BackupRetentionResult:
    """Prune only receipt-owned daily snapshots under one crash-safe plan."""
    database = database.resolve()
    backup_root = backup_root.resolve()
    backup_root.mkdir(parents=True, exist_ok=True)
    acquired = _acquire_retention_owner(backup_root)
    state_path = backup_root / BACKUP_RETENTION_STATE
    if acquired is None:
        return BackupRetentionResult(
            "IN_PROGRESS", 0, 0, 0, 0, 0, 0, 0.0, state_path,
        )
    owner_root, recovered_owner = acquired
    owned_connection = source_connection is None
    connection = source_connection or sqlite3.connect(
        f"file:{database.as_posix()}?mode=ro", uri=True,
    )
    plan_path = backup_root / BACKUP_RETENTION_PLAN
    deleted: list[dict] = []
    try:
        source = _source_identity(connection, database)
        if plan_path.exists():
            plan = _validated_retention_plan(
                plan_path, source=source, policy=policy,
            )
        else:
            managed, _ = _managed_backup_entries(backup_root, source=source)
            retained = _retained_backup_names(managed, policy, now)
            candidates = [
                {
                    "target": item["target"].name,
                    "receipt": item["receipt"].name,
                    "day": item["day"],
                    "bytes": item["bytes"],
                    "snapshot": item["snapshot"],
                    "receipt_digest": item["receipt_digest"],
                }
                for item in managed if item["target"].name not in retained
            ]
            plan = {
                "schema": BACKUP_RETENTION_PLAN_SCHEMA,
                "created_at": now.astimezone(UTC).isoformat(timespec="microseconds"),
                "source_database": _stable_source_identity(source),
                "policy": _retention_policy_payload(policy),
                "delete": candidates,
            }
            plan["plan_digest"] = _json_digest(plan)
            if candidates:
                _atomic_json(plan_path, plan)

        for item in plan["delete"]:
            target = backup_root / item["target"]
            receipt_path = backup_root / item["receipt"]
            if target.exists():
                if not receipt_path.is_file():
                    raise RuntimeError("BACKUP_RETENTION_RECEIPT_MISSING")
                _validate_completion_receipt(
                    receipt_path, target=target, day=item["day"], source=source,
                )
                receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
                if (
                    receipt["receipt_digest"] != item["receipt_digest"]
                    or receipt["snapshot"] != item["snapshot"]
                ):
                    raise RuntimeError("BACKUP_RETENTION_IDENTITY_CHANGED")
                target.unlink()
            if receipt_path.exists():
                receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
                if receipt.get("receipt_digest") != item["receipt_digest"]:
                    raise RuntimeError("BACKUP_RETENTION_RECEIPT_CHANGED")
                receipt_path.unlink()
            deleted.append(item)

        reclaim_state = reclaim_proven_stale_backup_temps(backup_root, now)
        managed, unknown = _managed_backup_entries(backup_root, source=source)
        now_utc = now.astimezone(UTC)
        managed_gib_days = sum(
            (item["bytes"] / 1024**3)
            * max(0.0, (now_utc.date() - datetime.strptime(
                item["day"], "%Y%m%d"
            ).date()).days)
            for item in managed
        )
        unknown_gib_days = sum(
            (item["bytes"] / 1024**3)
            * max(0.0, (
                now_utc - datetime.fromisoformat(item["modified_at"])
            ).total_seconds() / 86400)
            for item in unknown
        )
        state = {
            "schema": BACKUP_RETENTION_SCHEMA,
            "completed_at": now_utc.isoformat(timespec="microseconds"),
            "source_database": source,
            "policy": _retention_policy_payload(policy),
            "plan_digest": plan["plan_digest"],
            "recovered_interrupted_owner": recovered_owner,
            "managed_count": len(managed),
            "retained_count": len(managed),
            "deleted_count": len(deleted),
            "managed_bytes": sum(item["bytes"] for item in managed),
            "unknown_count": len(unknown),
            "unknown_bytes": sum(item["bytes"] for item in unknown),
            "proven_stale_reclaimed_count": reclaim_state["reclaimed_count"],
            "proven_stale_reclaimed_bytes": reclaim_state["reclaimed_bytes"],
            "managed_gib_days": round(managed_gib_days, 6),
            "unknown_gib_days": round(unknown_gib_days, 6),
            "disk_gib_days": round(
                managed_gib_days + unknown_gib_days, 6,
            ),
            "retained": [item["target"].name for item in managed],
            "deleted": deleted,
        }
        state["receipt_digest"] = _json_digest(state)
        _atomic_json(state_path, state)
        plan_path.unlink(missing_ok=True)
        return BackupRetentionResult(
            "DELETED" if deleted else "NO_CHANGE",
            len(managed),
            len(managed),
            len(deleted),
            len(unknown),
            state["managed_bytes"],
            state["unknown_bytes"],
            state["disk_gib_days"],
            state_path,
        )
    finally:
        if owned_connection:
            connection.close()
        (owner_root / "owner.json").unlink(missing_ok=True)
        owner_root.rmdir()


def _owner_paths(backup_root: Path, target: Path) -> tuple[Path, Path]:
    root = backup_root / f".{target.name}.backup-owner"
    return root, root / "owner.json"


def _validated_owned_temporary(value: object, backup_root: Path, day: str) -> Path:
    temporary = Path(str(value)).resolve()
    expected_root = backup_root.resolve()
    try:
        temporary.relative_to(expected_root)
    except ValueError as exc:
        raise RuntimeError("BACKUP_OWNER_TEMP_OUTSIDE_ROOT") from exc
    if not temporary.name.startswith(f"{BACKUP_TEMP_PREFIX}{day}-"):
        raise RuntimeError("BACKUP_OWNER_TEMP_NAME_INVALID")
    return temporary


def _acquire_backup_owner(
    backup_root: Path, target: Path, day: str,
) -> tuple[Path, Path, bool] | None:
    owner_root, owner_path = _owner_paths(backup_root, target)
    recovered = False
    for _ in range(2):
        try:
            owner_root.mkdir()
        except FileExistsError:
            try:
                owner = json.loads(owner_path.read_text(encoding="utf-8"))
                process_id = int(owner["process_id"])
                token = str(owner["process_start_token"])
                if owner.get("schema") != "xauusd.forward.daily-backup-owner.v1":
                    raise ValueError("owner schema")
                if Path(str(owner["target_path"])).resolve() != target.resolve():
                    raise ValueError("owner target")
                temporary = _validated_owned_temporary(
                    owner["temporary_path"], backup_root, day,
                )
            except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
                raise RuntimeError("BACKUP_OWNER_EVIDENCE_INVALID") from exc
            alive = _process_identity_alive(process_id, token)
            if alive is True:
                return None
            if alive is not False:
                raise RuntimeError("BACKUP_OWNER_LIVENESS_UNKNOWN")
            temporary.unlink(missing_ok=True)
            owner_path.unlink(missing_ok=True)
            owner_root.rmdir()
            recovered = True
            continue
        process_id = os.getpid()
        token, alive = _process_start_token(process_id)
        if alive is not True or not token:
            owner_root.rmdir()
            raise RuntimeError("BACKUP_OWNER_IDENTITY_UNAVAILABLE")
        temporary = backup_root / (
            f"{BACKUP_TEMP_PREFIX}{day}-{process_id}-{uuid.uuid4().hex}.tmp"
        )
        _atomic_json(owner_path, {
            "schema": "xauusd.forward.daily-backup-owner.v1",
            "process_id": process_id,
            "process_start_token": token,
            "temporary_path": str(temporary.resolve()),
            "target_path": str(target.resolve()),
            "acquired_at": datetime.now(UTC).isoformat(timespec="microseconds"),
        })
        return owner_root, temporary, recovered
    raise RuntimeError("BACKUP_OWNER_RECOVERY_FAILED")


def _perform_verified_backup(
    source: sqlite3.Connection, temporary: Path, target: Path,
) -> None:
    destination = sqlite3.connect(temporary)
    try:
        source.backup(destination)
        result = destination.execute("PRAGMA integrity_check").fetchone()[0]
        if result != "ok":
            raise RuntimeError(f"backup integrity check failed: {result}")
    finally:
        destination.close()
    os.replace(temporary, target)


def ensure_daily_forward_backup(
    database: Path,
    backup_root: Path,
    now: datetime,
    *,
    source_connection: sqlite3.Connection | None = None,
) -> DailyBackupResult:
    """Own one verified backup per UTC day without restart amplification."""
    database = database.resolve()
    backup_root.mkdir(parents=True, exist_ok=True)
    day = now.astimezone(UTC).strftime("%Y%m%d")
    target = backup_root / f"forward-evidence-{day}.sqlite3"
    receipt_path = _receipt_path(target)
    failure_path = _failure_path(target)
    owned_connection = source_connection is None
    source = source_connection or sqlite3.connect(
        f"file:{database.as_posix()}?mode=ro", uri=True,
    )
    try:
        source_identity = _source_identity(source, database)
        if receipt_path.exists():
            if not target.is_file():
                raise RuntimeError("BACKUP_RECEIPT_TARGET_MISSING")
            _validate_completion_receipt(
                receipt_path, target=target, day=day, source=source_identity,
            )
            return DailyBackupResult(
                "NO_CHANGE", target, receipt_path, heavy_operation=False,
            )
        if failure_path.exists():
            _validate_failure_receipt(
                failure_path, day=day, source=source_identity,
            )
            raise RuntimeError("BACKUP_PREVIOUS_ATTEMPT_FAILED")
        owner = _acquire_backup_owner(backup_root, target, day)
        if owner is None:
            return DailyBackupResult(
                "IN_PROGRESS", target, receipt_path, heavy_operation=False,
            )
        owner_root, temporary, recovered = owner
        try:
            if receipt_path.exists():
                if not target.is_file():
                    raise RuntimeError("BACKUP_RECEIPT_TARGET_MISSING")
                _validate_completion_receipt(
                    receipt_path, target=target, day=day,
                    source=source_identity,
                )
                return DailyBackupResult(
                    "NO_CHANGE", target, receipt_path, heavy_operation=False,
                    recovered_interrupted_owner=recovered,
                )
            if target.exists():
                snapshot = _snapshot_identity(target)
                if snapshot["forward_epoch"] != source_identity["forward_epoch"]:
                    raise RuntimeError("BACKUP_STALE_SOURCE:forward_epoch")
                _atomic_json(receipt_path, _completion_receipt(
                    target=target, day=day, source=source_identity,
                    completion_mode="LEGACY_ADOPTED",
                ))
                return DailyBackupResult(
                    "LEGACY_ADOPTED", target, receipt_path,
                    heavy_operation=False,
                    recovered_interrupted_owner=recovered,
                )
            try:
                _perform_verified_backup(source, temporary, target)
                _atomic_json(receipt_path, _completion_receipt(
                    target=target, day=day, source=source_identity,
                    completion_mode="CREATED",
                ))
            except Exception as exc:
                _write_failure_receipt(
                    failure_path, day=day, source=source_identity, error=exc,
                )
                raise
            return DailyBackupResult(
                "CREATED_AFTER_INTERRUPTED_OWNER" if recovered else "CREATED",
                target, receipt_path, heavy_operation=True,
                recovered_interrupted_owner=recovered,
            )
        finally:
            temporary.unlink(missing_ok=True)
            (owner_root / "owner.json").unlink(missing_ok=True)
            owner_root.rmdir()
    finally:
        if owned_connection:
            source.close()


def backup_forward_ledger(
    ledger: ForwardLedger, backup_root: Path, now: datetime
) -> Path:
    """Compatibility wrapper for one receipt-owned daily backup."""
    result = ensure_daily_forward_backup(
        ledger.path, backup_root, now, source_connection=ledger.connection,
    )
    if result.status == "IN_PROGRESS" and not result.path.is_file():
        raise RuntimeError("BACKUP_ALREADY_IN_PROGRESS")
    return result.path


class DailyBackupOwner:
    """Run daily backup maintenance after Collector viability is established."""

    def __init__(
        self, database: Path, backup_root: Path, *,
        clock=lambda: datetime.now(UTC), poll_seconds: float = 60.0,
    ) -> None:
        self.database = database.resolve()
        self.backup_root = backup_root.resolve()
        self.clock = clock
        self.poll_seconds = poll_seconds
        self._stop = threading.Event()
        self._state_lock = threading.Lock()
        self._thread = threading.Thread(
            target=self._run, name="daily-forward-backup", daemon=True,
        )
        self.last_result: DailyBackupResult | None = None
        self.last_retention: BackupRetentionResult | None = None
        self.last_error: str | None = None
        self._last_day: str | None = None

    def _run(self) -> None:
        while not self._stop.is_set():
            now = self.clock()
            day = now.astimezone(UTC).strftime("%Y%m%d")
            if day != self._last_day:
                try:
                    result = ensure_daily_forward_backup(
                        self.database, self.backup_root, now,
                    )
                    retention = apply_backup_retention(
                        self.database, self.backup_root, now,
                    )
                    with self._state_lock:
                        self.last_result = result
                        self.last_retention = retention
                        self.last_error = None
                    self._last_day = day
                except Exception as exc:
                    with self._state_lock:
                        self.last_result = None
                        self.last_error = f"{type(exc).__name__}:{exc}"
            self._stop.wait(self.poll_seconds)

    def start(self) -> None:
        self._thread.start()

    def snapshot(self) -> dict:
        with self._state_lock:
            result = self.last_result
            retention = self.last_retention
            error = self.last_error
        return {
            "status": result.status if result else "PENDING",
            "path": str(result.path) if result else None,
            "heavy_operation": result.heavy_operation if result else False,
            "retention_status": retention.status if retention else "PENDING",
            "retention_managed_count": (
                retention.managed_count if retention else None
            ),
            "retention_unknown_bytes": (
                retention.unknown_bytes if retention else None
            ),
            "error": error,
        }

    def close(self) -> None:
        self._stop.set()
        if self._thread.is_alive():
            self._thread.join(timeout=5.0)
