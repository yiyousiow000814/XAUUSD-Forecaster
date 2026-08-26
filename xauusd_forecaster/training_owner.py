"""Durable single-owner background training outside the decision clock path."""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .execution_learning import train_due_execution
from xauusd_forecaster.evidence.ledger import ForwardLedger
from .news_contract_migration import append_missing_current_news_snapshots
from .training_v2 import train_due_v2


UTC = timezone.utc
LEASE_SECONDS = 15 * 60
LEASE_HEARTBEAT_SECONDS = 30.0


def _windows_process_start_token(process_id: int) -> tuple[str | None, bool | None]:
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.GetProcessTimes.argtypes = (
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.FILETIME), ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME), ctypes.POINTER(wintypes.FILETIME),
    )
    kernel32.GetProcessTimes.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel32.CloseHandle.restype = wintypes.BOOL

    process = kernel32.OpenProcess(0x1000, False, process_id)
    if not process:
        error = ctypes.get_last_error()
        return None, False if error == 87 else None
    try:
        creation = wintypes.FILETIME()
        exit_time = wintypes.FILETIME()
        kernel = wintypes.FILETIME()
        user = wintypes.FILETIME()
        if not kernel32.GetProcessTimes(
            process, ctypes.byref(creation), ctypes.byref(exit_time),
            ctypes.byref(kernel), ctypes.byref(user),
        ):
            return None, None
        value = (int(creation.dwHighDateTime) << 32) | int(creation.dwLowDateTime)
        return f"windows-filetime:{value}", True
    finally:
        kernel32.CloseHandle(process)


def _process_start_token(process_id: int) -> tuple[str | None, bool | None]:
    """Return an OS process-start identity and whether that PID is alive."""
    if os.name == "nt":
        return _windows_process_start_token(process_id)
    stat = Path(f"/proc/{process_id}/stat")
    try:
        value = stat.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None, False
    except OSError:
        return None, None
    try:
        fields = value[value.rfind(")") + 2:].split()
        return f"proc-start:{fields[19]}", True
    except (IndexError, ValueError):
        return None, None


def _process_identity_alive(process_id: int, start_token: str) -> bool | None:
    current_token, alive = _process_start_token(process_id)
    if alive is not True:
        return alive
    if not current_token or not start_token:
        return None
    return current_token == start_token


def install_training_owner_schema(connection) -> None:
    connection.execute(
        """CREATE TABLE IF NOT EXISTS background_training_owner_v1 (
            id INTEGER PRIMARY KEY CHECK(id=1),
            state TEXT NOT NULL CHECK(state IN ('IDLE','PENDING','RUNNING')),
            requested_at TEXT,
            cutoff_at TEXT,
            available_at TEXT,
            reconcile INTEGER NOT NULL DEFAULT 0 CHECK(reconcile IN (0,1)),
            rerun_requested INTEGER NOT NULL DEFAULT 0 CHECK(rerun_requested IN (0,1)),
            lease_owner TEXT,
            process_id INTEGER,
            process_start_token TEXT,
            lease_heartbeat_at TEXT,
            lease_expires_at TEXT,
            last_started_at TEXT,
            last_completed_at TEXT,
            last_error TEXT
        )"""
    )
    columns = {
        str(row[1]) for row in connection.execute(
            "PRAGMA table_info(background_training_owner_v1)"
        ).fetchall()
    }
    for name, declaration in (
        ("process_id", "INTEGER"),
        ("process_start_token", "TEXT"),
        ("lease_heartbeat_at", "TEXT"),
    ):
        if name not in columns:
            connection.execute(
                f"ALTER TABLE background_training_owner_v1 ADD COLUMN {name} {declaration}"
            )
    connection.execute(
        "INSERT OR IGNORE INTO background_training_owner_v1(id,state) VALUES(1,'IDLE')"
    )


def request_background_training(
    connection, cutoff: datetime, *, reconcile: bool = False, clock=None,
) -> None:
    """Persist coalesced work; requests arriving during a lease trigger one rerun."""
    now = (clock or (lambda: datetime.now(UTC)))().isoformat()
    with connection:
        install_training_owner_schema(connection)
        connection.execute(
            """UPDATE background_training_owner_v1 SET
                 requested_at=?, cutoff_at=?,
                 available_at=CASE WHEN state='RUNNING' THEN available_at ELSE ? END,
                 reconcile=max(reconcile, ?),
                 rerun_requested=CASE WHEN state='RUNNING' THEN 1 ELSE rerun_requested END,
                 state=CASE WHEN state='RUNNING' THEN state ELSE 'PENDING' END
               WHERE id=1""",
            (now, cutoff.isoformat(), now, int(reconcile)),
        )


class BackgroundTrainingOwner:
    """One crash-recoverable worker; it never shares a SQLite connection."""

    def __init__(
        self, ledger_path: str | Path, model_root: str | Path,
        execution_root: str | Path, quote_root: str | Path | None = None,
        *, lease_seconds: float = LEASE_SECONDS,
        heartbeat_seconds: float = LEASE_HEARTBEAT_SECONDS,
        clock=None, process_probe=None,
    ) -> None:
        self.ledger_path = Path(ledger_path)
        self.model_root = Path(model_root)
        self.execution_root = Path(execution_root)
        self.quote_root = Path(quote_root) if quote_root else None
        self.lease_seconds = lease_seconds
        self.heartbeat_seconds = heartbeat_seconds
        self.clock = clock or (lambda: datetime.now(UTC))
        self.process_id = os.getpid()
        self.process_start_token, alive = _process_start_token(self.process_id)
        if alive is not True or not self.process_start_token:
            raise RuntimeError("TRAINING_OWNER_PROCESS_IDENTITY_UNAVAILABLE")
        self.process_probe = process_probe or _process_identity_alive
        self.owner_id = f"training-{self.process_id}-{uuid.uuid4()}"
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lease_thread: threading.Thread | None = None
        self._lease_stop: threading.Event | None = None

    def start(self) -> None:
        if self._thread is not None:
            if self._thread.is_alive():
                return
            self._thread = None
        self._thread = threading.Thread(
            target=self._run, name="xauusd-background-training", daemon=True,
        )
        self._thread.start()

    def wake(self) -> None:
        self._wake.set()

    def close(self, timeout: float = 10.0) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
            if not self._thread.is_alive():
                self._thread = None

    def _claim(self, connection) -> dict | None:
        now = self.clock()
        expires = now + timedelta(seconds=self.lease_seconds)
        # Schema installers and prior read-model work may leave an implicit
        # transaction open on this owner-only connection.
        connection.commit()
        connection.execute("BEGIN IMMEDIATE")
        try:
            row = connection.execute(
                "SELECT * FROM background_training_owner_v1 WHERE id=1"
            ).fetchone()
            claimable = bool(row and row["state"] == "PENDING")
            if row and row["state"] == "RUNNING":
                expired = bool(
                    row["lease_expires_at"]
                    and datetime.fromisoformat(row["lease_expires_at"]) <= now
                )
                identity_complete = bool(
                    row["process_id"] and row["process_start_token"]
                    and row["lease_owner"] and row["lease_heartbeat_at"]
                )
                if not identity_complete:
                    connection.execute(
                        """UPDATE background_training_owner_v1
                              SET last_error='TRAINING_OWNER_IDENTITY_UNRESOLVED'
                            WHERE id=1"""
                    )
                    claimable = False
                else:
                    alive = self.process_probe(
                        int(row["process_id"]), str(row["process_start_token"]),
                    )
                    if alive is None:
                        connection.execute(
                            """UPDATE background_training_owner_v1
                                  SET last_error='TRAINING_OWNER_IDENTITY_UNRESOLVED'
                                WHERE id=1"""
                        )
                    claimable = bool(expired and alive is False)
            claimable = claimable and bool(
                not row["available_at"]
                or datetime.fromisoformat(row["available_at"]) <= now
            )
            if not claimable:
                connection.commit()
                return None
            connection.execute(
                """UPDATE background_training_owner_v1 SET state='RUNNING',
                   rerun_requested=0,lease_owner=?,process_id=?,
                   process_start_token=?,lease_heartbeat_at=?,lease_expires_at=?,
                   last_started_at=?,last_error=NULL WHERE id=1""",
                (self.owner_id, self.process_id, self.process_start_token,
                 now.isoformat(), expires.isoformat(), now.isoformat()),
            )
            connection.commit()
            return {"cutoff_at": row["cutoff_at"], "reconcile": bool(row["reconcile"])}
        except BaseException:
            connection.rollback()
            raise

    def _renew_lease(self) -> bool:
        now = self.clock()
        connection = sqlite3.connect(self.ledger_path, timeout=60)
        try:
            connection.execute("PRAGMA busy_timeout=60000")
            with connection:
                cursor = connection.execute(
                    """UPDATE background_training_owner_v1
                          SET lease_heartbeat_at=?,lease_expires_at=?
                        WHERE id=1 AND state='RUNNING' AND lease_owner=?
                          AND process_id=? AND process_start_token=?""",
                    (now.isoformat(),
                     (now + timedelta(seconds=self.lease_seconds)).isoformat(),
                     self.owner_id, self.process_id, self.process_start_token),
                )
            return cursor.rowcount == 1
        finally:
            connection.close()

    def _start_lease_keeper(self) -> None:
        stop = threading.Event()
        self._lease_stop = stop

        def keep() -> None:
            while not stop.wait(self.heartbeat_seconds):
                try:
                    if not self._renew_lease():
                        return
                except sqlite3.Error:
                    continue

        self._lease_thread = threading.Thread(
            target=keep, name="xauusd-training-lease-keeper", daemon=True,
        )
        self._lease_thread.start()

    def _stop_lease_keeper(self) -> None:
        if self._lease_stop is not None:
            self._lease_stop.set()
        if self._lease_thread is not None:
            self._lease_thread.join(timeout=max(1.0, self.heartbeat_seconds * 2))
        self._lease_stop = None
        self._lease_thread = None

    def _complete(self, connection, error: str | None) -> None:
        with connection:
            row = connection.execute(
                """SELECT rerun_requested,lease_owner
                   FROM background_training_owner_v1 WHERE id=1"""
            ).fetchone()
            if row is None or row["lease_owner"] != self.owner_id:
                return
            rerun = bool(row and row["rerun_requested"])
            pending = rerun or error is not None
            available_at = (
                (self.clock() + timedelta(seconds=30)).isoformat()
                if error is not None else self.clock().isoformat()
            )
            connection.execute(
                """UPDATE background_training_owner_v1 SET state=?,
                   reconcile=CASE WHEN ? THEN reconcile ELSE 0 END,
                   available_at=?,
                   lease_owner=NULL,process_id=NULL,process_start_token=NULL,
                   lease_heartbeat_at=NULL,lease_expires_at=NULL,
                   last_completed_at=?,last_error=?
                   WHERE id=1 AND lease_owner=?""",
                ("PENDING" if pending else "IDLE", int(pending),
                 available_at, self.clock().isoformat(), error, self.owner_id),
            )
        if pending:
            self._wake.set()

    def _run(self) -> None:
        ledger = ForwardLedger(self.ledger_path)
        install_training_owner_schema(ledger.connection)
        try:
            while not self._stop.is_set():
                job = self._claim(ledger.connection)
                if job is None:
                    self._wake.wait(2.0)
                    self._wake.clear()
                    continue
                error = None
                self._start_lease_keeper()
                try:
                    cutoff = datetime.fromisoformat(str(job["cutoff_at"]))
                    migration = (
                        append_missing_current_news_snapshots(ledger, cutoff)
                        if job["reconcile"] else None
                    )
                    training = train_due_v2(ledger, cutoff, self.model_root)
                    execution = train_due_execution(
                        ledger, cutoff, self.execution_root, self.quote_root,
                    )
                    print(json.dumps({
                        "event": "BACKGROUND_TRAINING_COMPLETED",
                        "migration": migration, "results": training,
                        "execution_results": execution,
                    }, sort_keys=True), flush=True)
                except Exception as exc:  # evidence is persisted; the loop survives
                    error = f"{type(exc).__name__}: {exc}"[:2000]
                    print(json.dumps({
                        "event": "BACKGROUND_TRAINING_FAILED", "error": error,
                    }, sort_keys=True), flush=True)
                finally:
                    self._stop_lease_keeper()
                self._complete(ledger.connection, error)
        finally:
            self._stop_lease_keeper()
            ledger.close()
