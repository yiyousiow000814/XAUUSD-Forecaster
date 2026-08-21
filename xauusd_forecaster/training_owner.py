"""Durable single-owner background training outside the decision clock path."""

from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .execution_learning import train_due_execution
from .forward_ledger import ForwardLedger
from .news_contract_migration import append_missing_current_news_snapshots
from .training_v2 import train_due_v2


UTC = timezone.utc
LEASE_SECONDS = 15 * 60


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
            lease_expires_at TEXT,
            last_started_at TEXT,
            last_completed_at TEXT,
            last_error TEXT
        )"""
    )
    connection.execute(
        "INSERT OR IGNORE INTO background_training_owner_v1(id,state) VALUES(1,'IDLE')"
    )


def request_background_training(
    connection, cutoff: datetime, *, reconcile: bool = False,
) -> None:
    """Persist coalesced work; requests arriving during a lease trigger one rerun."""
    now = datetime.now(UTC).isoformat()
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
    ) -> None:
        self.ledger_path = Path(ledger_path)
        self.model_root = Path(model_root)
        self.execution_root = Path(execution_root)
        self.quote_root = Path(quote_root) if quote_root else None
        self.owner_id = f"training-{uuid.uuid4()}"
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None:
            return
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
            self._thread = None

    def _claim(self, connection) -> dict | None:
        now = datetime.now(UTC)
        expires = now + timedelta(seconds=LEASE_SECONDS)
        # Schema installers and prior read-model work may leave an implicit
        # transaction open on this owner-only connection.
        connection.commit()
        connection.execute("BEGIN IMMEDIATE")
        try:
            row = connection.execute(
                "SELECT * FROM background_training_owner_v1 WHERE id=1"
            ).fetchone()
            claimable = row and (
                row["state"] == "PENDING"
                or (row["state"] == "RUNNING" and row["lease_expires_at"]
                    and datetime.fromisoformat(row["lease_expires_at"]) <= now)
            ) and (not row["available_at"]
                   or datetime.fromisoformat(row["available_at"]) <= now)
            if not claimable:
                connection.commit()
                return None
            connection.execute(
                """UPDATE background_training_owner_v1 SET state='RUNNING',
                   rerun_requested=0,lease_owner=?,lease_expires_at=?,
                   last_started_at=?,last_error=NULL WHERE id=1""",
                (self.owner_id, expires.isoformat(), now.isoformat()),
            )
            connection.commit()
            return {"cutoff_at": row["cutoff_at"], "reconcile": bool(row["reconcile"])}
        except BaseException:
            connection.rollback()
            raise

    def _complete(self, connection, error: str | None) -> None:
        with connection:
            row = connection.execute(
                "SELECT rerun_requested FROM background_training_owner_v1 WHERE id=1"
            ).fetchone()
            rerun = bool(row and row["rerun_requested"])
            pending = rerun or error is not None
            available_at = (
                (datetime.now(UTC) + timedelta(seconds=30)).isoformat()
                if error is not None else datetime.now(UTC).isoformat()
            )
            connection.execute(
                """UPDATE background_training_owner_v1 SET state=?,
                   reconcile=CASE WHEN ? THEN reconcile ELSE 0 END,
                   available_at=?,
                   lease_owner=NULL,lease_expires_at=NULL,last_completed_at=?,last_error=?
                   WHERE id=1 AND lease_owner=?""",
                ("PENDING" if pending else "IDLE", int(pending),
                 available_at, datetime.now(UTC).isoformat(), error, self.owner_id),
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
                self._complete(ledger.connection, error)
        finally:
            ledger.close()
