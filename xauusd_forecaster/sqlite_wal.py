"""Single-owner WAL checkpoint policy for the production Forward database."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path


FORWARD_WAL_AUTOCHECKPOINT_PAGES = 0
FORWARD_WAL_SIZE_LIMIT_BYTES = 64 * 1024**2
FORWARD_WAL_CHECKPOINT_INTERVAL_SECONDS = 60.0
FORWARD_WAL_CHECKPOINT_BUSY_TIMEOUT_MS = 250
FORWARD_WAL_CHECKPOINT_SCHEMA = "xauusd.forward.wal-checkpoint.v1"
FORWARD_WAL_CHECKPOINT_STATE = "wal-checkpoint-state.json"


def is_forward_sqlite_contention(error: BaseException) -> bool:
    """Identify only SQLite BUSY/LOCKED failures at the writer boundary."""
    if not isinstance(error, sqlite3.Error):
        return False
    code = getattr(error, "sqlite_errorcode", None)
    if isinstance(code, int):
        return (code & 0xFF) in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED}
    message = str(error).strip().lower()
    return message in {
        "database is locked",
        "database table is locked",
        "database schema is locked",
    }


def _digest(payload: dict[str, object]) -> str:
    return hashlib.sha256(json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")).hexdigest()


def _atomic_json(path: Path, payload: dict[str, object]) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def configure_forward_writer_connection(
    connection: sqlite3.Connection,
    *,
    size_limit_bytes: int = FORWARD_WAL_SIZE_LIMIT_BYTES,
) -> None:
    """Keep checkpoint work off every production writer's commit path."""
    if size_limit_bytes < 4096:
        raise ValueError("Forward WAL size limit must be at least one page")
    auto = int(connection.execute(
        f"PRAGMA wal_autocheckpoint={FORWARD_WAL_AUTOCHECKPOINT_PAGES}"
    ).fetchone()[0])
    limit = int(connection.execute(
        f"PRAGMA journal_size_limit={size_limit_bytes}"
    ).fetchone()[0])
    if auto != FORWARD_WAL_AUTOCHECKPOINT_PAGES or limit != size_limit_bytes:
        raise RuntimeError("FORWARD_WAL_WRITER_POLICY_REJECTED")


def open_forward_writer_connection(
    database: str | Path,
    *,
    timeout: float = 60.0,
    row_factory: object | None = None,
) -> sqlite3.Connection:
    """Open one Forward writer through the shared WAL ownership boundary."""
    connection = sqlite3.connect(database, timeout=timeout)
    try:
        if row_factory is not None:
            connection.row_factory = row_factory
        connection.execute(f"PRAGMA busy_timeout={int(timeout * 1000)}")
        journal_mode = str(
            connection.execute("PRAGMA journal_mode").fetchone()[0]
        ).lower()
        if journal_mode != "wal":
            journal_mode = str(
                connection.execute("PRAGMA journal_mode=WAL").fetchone()[0]
            ).lower()
        if journal_mode != "wal":
            raise RuntimeError("FORWARD_LEDGER_WAL_MODE_REQUIRED")
        configure_forward_writer_connection(connection)
        return connection
    except BaseException:
        connection.close()
        raise


@dataclass(frozen=True)
class ForwardWalCheckpointResult:
    status: str
    log_frames: int
    checkpointed_frames: int
    pending_frames: int
    wal_bytes_before: int
    wal_bytes_after: int
    truncate_attempted: bool
    state_path: Path


def checkpoint_forward_wal(
    database: Path,
    state_root: Path,
    now: datetime,
    *,
    size_limit_bytes: int = FORWARD_WAL_SIZE_LIMIT_BYTES,
    busy_timeout_ms: int = FORWARD_WAL_CHECKPOINT_BUSY_TIMEOUT_MS,
) -> ForwardWalCheckpointResult:
    """Run one bounded background checkpoint and persist its exact outcome."""
    database = database.resolve()
    state_root = state_root.resolve()
    if database.parent != state_root:
        raise ValueError("Forward WAL database must be one file under runtime root")
    if not database.is_file():
        raise FileNotFoundError("Forward WAL database is unavailable")
    if busy_timeout_ms < 0 or busy_timeout_ms > 1_000:
        raise ValueError("Forward WAL busy timeout is outside the bounded contract")
    if size_limit_bytes < 4096:
        raise ValueError("Forward WAL size limit must be at least one page")

    wal_path = Path(f"{database}-wal")
    state_path = state_root / FORWARD_WAL_CHECKPOINT_STATE
    wal_bytes_before = wal_path.stat().st_size if wal_path.is_file() else 0
    log_frames = 0
    checkpointed_frames = 0
    pending_frames = 0
    truncate_attempted = False
    status = "CHECKPOINTED"
    error: str | None = None
    connection = sqlite3.connect(database, timeout=busy_timeout_ms / 1000)
    try:
        connection.execute(f"PRAGMA busy_timeout={busy_timeout_ms}")
        if str(connection.execute("PRAGMA journal_mode").fetchone()[0]).lower() != "wal":
            raise RuntimeError("FORWARD_LEDGER_WAL_MODE_REQUIRED")
        configure_forward_writer_connection(
            connection, size_limit_bytes=size_limit_bytes,
        )
        busy, log_frames, checkpointed_frames = map(
            int, connection.execute("PRAGMA wal_checkpoint(PASSIVE)").fetchone(),
        )
        pending_frames = max(0, log_frames - checkpointed_frames)
        if busy:
            status = "CHECKPOINT_BUSY"
        elif pending_frames:
            status = "READER_PINNED"
        elif wal_bytes_before > size_limit_bytes:
            truncate_attempted = True
            truncate_busy, truncate_log, truncate_checkpointed = map(
                int,
                connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone(),
            )
            if truncate_busy:
                status = "TRUNCATE_BUSY"
            elif truncate_log != 0 or truncate_checkpointed != 0:
                status = "TRUNCATE_INCOMPLETE"
            else:
                status = "TRUNCATED"
    except sqlite3.Error as exc:
        status = "CHECKPOINT_ERROR"
        error = f"{type(exc).__name__}: {str(exc)[:400]}"
    finally:
        connection.close()

    wal_bytes_after = wal_path.stat().st_size if wal_path.is_file() else 0
    payload: dict[str, object] = {
        "schema": FORWARD_WAL_CHECKPOINT_SCHEMA,
        "recorded_at": now.astimezone(UTC).isoformat(timespec="microseconds"),
        "database": str(database),
        "status": status,
        "log_frames": log_frames,
        "checkpointed_frames": checkpointed_frames,
        "pending_frames": pending_frames,
        "wal_bytes_before": wal_bytes_before,
        "wal_bytes_after": wal_bytes_after,
        "journal_size_limit_bytes": size_limit_bytes,
        "busy_timeout_ms": busy_timeout_ms,
        "truncate_attempted": truncate_attempted,
        "error": error,
    }
    payload["receipt_digest"] = _digest(payload)
    _atomic_json(state_path, payload)
    return ForwardWalCheckpointResult(
        status=status,
        log_frames=log_frames,
        checkpointed_frames=checkpointed_frames,
        pending_frames=pending_frames,
        wal_bytes_before=wal_bytes_before,
        wal_bytes_after=wal_bytes_after,
        truncate_attempted=truncate_attempted,
        state_path=state_path,
    )


class ForwardWalCheckpointOwner:
    """Own checkpoint I/O outside collector and annotator commit paths."""

    def __init__(
        self,
        database: Path,
        state_root: Path,
        *,
        clock=lambda: datetime.now(UTC),
        poll_seconds: float = FORWARD_WAL_CHECKPOINT_INTERVAL_SECONDS,
    ) -> None:
        self.database = database.resolve()
        self.state_root = state_root.resolve()
        self.clock = clock
        self.poll_seconds = poll_seconds
        self._stop = threading.Event()
        self._state_lock = threading.Lock()
        self._thread = threading.Thread(
            target=self._run, name="forward-wal-checkpoint", daemon=True,
        )
        self.last_result: ForwardWalCheckpointResult | None = None
        self.last_error: str | None = None

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                result = checkpoint_forward_wal(
                    self.database, self.state_root, self.clock(),
                )
                with self._state_lock:
                    self.last_result = result
                    self.last_error = None
            except Exception as exc:
                with self._state_lock:
                    self.last_result = None
                    self.last_error = f"{type(exc).__name__}: {str(exc)[:400]}"
            self._stop.wait(self.poll_seconds)

    def start(self) -> None:
        self._thread.start()

    def snapshot(self) -> dict[str, object]:
        with self._state_lock:
            result = self.last_result
            error = self.last_error
        if error is not None:
            return {"status": "CHECKPOINT_ERROR", "error": error}
        if result is None:
            return {"status": "PENDING"}
        return {
            "status": result.status,
            "log_frames": result.log_frames,
            "checkpointed_frames": result.checkpointed_frames,
            "pending_frames": result.pending_frames,
            "wal_bytes_before": result.wal_bytes_before,
            "wal_bytes_after": result.wal_bytes_after,
            "truncate_attempted": result.truncate_attempted,
        }

    def close(self) -> None:
        self._stop.set()
        if self._thread.is_alive():
            self._thread.join(timeout=5.0)
