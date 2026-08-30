"""Lossless local retention for completed quote days and the Forward ledger."""

from __future__ import annotations

import gzip
import hashlib
import json
import os
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


@dataclass(frozen=True)
class DailyBackupResult:
    status: str
    path: Path
    receipt_path: Path
    heavy_operation: bool
    recovered_interrupted_owner: bool = False


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
    connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
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
                    with self._state_lock:
                        self.last_result = result
                        self.last_error = None
                except Exception as exc:
                    with self._state_lock:
                        self.last_result = None
                        self.last_error = f"{type(exc).__name__}:{exc}"
                finally:
                    self._last_day = day
            self._stop.wait(self.poll_seconds)

    def start(self) -> None:
        self._thread.start()

    def snapshot(self) -> dict:
        with self._state_lock:
            result = self.last_result
            error = self.last_error
        return {
            "status": result.status if result else "PENDING",
            "path": str(result.path) if result else None,
            "heavy_operation": result.heavy_operation if result else False,
            "error": error,
        }

    def close(self) -> None:
        self._stop.set()
        if self._thread.is_alive():
            self._thread.join(timeout=5.0)
