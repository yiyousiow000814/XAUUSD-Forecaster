"""Durable, disposable read models for optional dashboard resources."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from .sqlite_wal import open_forward_writer_connection


READ_MODEL_CONTRACTS = {
    "audit": "dashboard-audit-summary-v1",
    "learning": "dashboard-learning-summary-v1",
    "market_chart": "dashboard-market-chart-summary-v1",
}

# Leave five minutes of the existing 15-minute freshness window for bounded
# publication and downstream Sync. A slower build is a real freshness failure.
DEFAULT_MAX_SNAPSHOT_SECONDS = 600.0
DEFAULT_MAX_SNAPSHOT_AGE_SECONDS = 900.0
REFRESHED = 1
REFRESHED_DIRTY = 2

_RESOURCE_SOURCE_TABLES = {
    "audit": (
        "daily_news_briefs", "decision_events", "news_annotations",
        "news_revisions", "news_title_translations", "outcomes",
    ),
    "learning": (
        "derived_outcomes", "execution_model_updates_v2",
        "execution_position_scores_v2", "execution_predictions_v2",
        "execution_training_examples_v2", "model_updates_v2",
        "prediction_scores_v2", "predictions_v2",
    ),
    "market_chart": (
        "decision_events", "derived_market_snapshots", "model_updates_v2",
        "prediction_scores_v2", "predictions_v2",
    ),
}


class DashboardReadModelUnavailable(RuntimeError):
    """Raised when no valid durable optional read model is available."""


@dataclass(frozen=True)
class DashboardReadModelSnapshot:
    """One pinned SQLite source snapshot used by exactly one model build."""

    database: Path
    connection: sqlite3.Connection
    source_revision: int
    started_at: datetime


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _payload_bytes(payload: Mapping[str, object]) -> bytes:
    return json.dumps(
        payload, ensure_ascii=False, allow_nan=False, separators=(",", ":"),
    ).encode("utf-8")


def _payload_hash(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def install_dashboard_read_model_schema(connection: sqlite3.Connection) -> None:
    """Install local derived-state ownership and per-resource dirty tracking."""
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS dashboard_optional_read_models_v1 (
            resource TEXT PRIMARY KEY,
            contract_version TEXT NOT NULL,
            source_revision INTEGER NOT NULL CHECK(source_revision >= 0),
            generated_at TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            payload_hash TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS dashboard_optional_read_model_state_v1 (
            resource TEXT PRIMARY KEY,
            source_revision INTEGER NOT NULL DEFAULT 0,
            last_success_at TEXT,
            last_error TEXT,
            updated_at TEXT NOT NULL
        );
        """
    )
    existing_columns = {
        str(row[1]) for row in connection.execute(
            "PRAGMA table_info(dashboard_optional_read_models_v1)"
        )
    }
    for column, declaration in (
        ("snapshot_started_at", "TEXT"),
        ("snapshot_completed_at", "TEXT"),
        ("live_source_revision", "INTEGER"),
    ):
        if column not in existing_columns:
            connection.execute(
                f"ALTER TABLE dashboard_optional_read_models_v1 "
                f"ADD COLUMN {column} {declaration}"
            )
    with connection:
        for resource in READ_MODEL_CONTRACTS:
            connection.execute(
                """INSERT OR IGNORE INTO dashboard_optional_read_model_state_v1
                     (resource,source_revision,updated_at) VALUES (?,0,?)""",
                (resource, _utc_now().isoformat()),
            )
        for resource, tables in _RESOURCE_SOURCE_TABLES.items():
            for table in tables:
                for operation in ("INSERT", "UPDATE", "DELETE"):
                    trigger = f"dashboard_optional_{resource}_{table}_{operation.lower()}_v1"
                    connection.execute(
                        f"""CREATE TRIGGER IF NOT EXISTS {trigger}
                             AFTER {operation} ON {table} BEGIN
                               UPDATE dashboard_optional_read_model_state_v1
                                  SET source_revision=source_revision+1,
                                      updated_at=CURRENT_TIMESTAMP
                                WHERE resource='{resource}';
                             END"""
                    )


def read_dashboard_read_model(
    database: Path, resource: str, *, now: datetime | None = None,
) -> tuple[bytes, dict[str, object]]:
    """Read and verify one bounded model with exactly one SQLite query."""
    expected_contract = READ_MODEL_CONTRACTS.get(resource)
    if expected_contract is None:
        raise DashboardReadModelUnavailable(f"unknown read model: {resource}")
    connection = sqlite3.connect(
        f"file:{database.resolve()}?mode=ro", uri=True, timeout=5,
    )
    try:
        row = connection.execute(
            """SELECT m.contract_version,m.source_revision,m.generated_at,
                      m.payload_json,m.payload_hash,m.updated_at,
                      m.snapshot_started_at,m.snapshot_completed_at,
                      m.live_source_revision,s.source_revision
                 FROM dashboard_optional_read_models_v1 m
                 JOIN dashboard_optional_read_model_state_v1 s USING(resource)
                WHERE m.resource=?""",
            (resource,),
        ).fetchone()
    except sqlite3.Error as error:
        raise DashboardReadModelUnavailable(str(error)) from error
    finally:
        connection.close()
    if row is None:
        raise DashboardReadModelUnavailable(f"{resource} read model is not built")
    if str(row[0]) != expected_contract:
        raise DashboardReadModelUnavailable(f"{resource} read model contract mismatch")
    body = str(row[3]).encode("utf-8")
    if not body or _payload_hash(body) != str(row[4]):
        raise DashboardReadModelUnavailable(f"{resource} read model is corrupt")
    try:
        json.loads(body)
        generated_at = datetime.fromisoformat(str(row[2]).replace("Z", "+00:00"))
    except (json.JSONDecodeError, ValueError) as error:
        raise DashboardReadModelUnavailable(
            f"{resource} read model metadata is corrupt"
        ) from error
    observed = now or _utc_now()
    if generated_at.tzinfo is None:
        generated_at = generated_at.replace(tzinfo=UTC)
    age_seconds = max(0.0, (observed - generated_at).total_seconds())
    return body, {
        "contract_version": str(row[0]),
        "source_revision": int(row[1]),
        "generated_at": str(row[2]),
        "updated_at": str(row[5]),
        "snapshot_started_at": str(row[6]) if row[6] is not None else None,
        "snapshot_completed_at": str(row[7]) if row[7] is not None else None,
        "live_source_revision_at_publish": (
            int(row[8]) if row[8] is not None else None
        ),
        "current_source_revision": int(row[9]),
        "dirty": int(row[9]) > int(row[1]),
        "age_seconds": age_seconds,
        "state": "READY" if age_seconds <= 900 else "STALE",
    }


class DashboardReadModelOwner:
    """Refresh optional resources outside HTTP request ownership."""

    def __init__(
        self,
        database: Path,
        builders: Mapping[
            str, Callable[[DashboardReadModelSnapshot], Mapping[str, object]]
        ],
        *,
        poll_seconds: float = 30.0,
        max_snapshot_seconds: float = DEFAULT_MAX_SNAPSHOT_SECONDS,
        max_snapshot_age_seconds: float = DEFAULT_MAX_SNAPSHOT_AGE_SECONDS,
    ) -> None:
        self.database = database.resolve()
        self.builders = dict(builders)
        self.poll_seconds = poll_seconds
        self.max_snapshot_seconds = max_snapshot_seconds
        self.max_snapshot_age_seconds = max_snapshot_age_seconds
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._refresh_lock = threading.Lock()

    def _connect(self) -> sqlite3.Connection:
        return open_forward_writer_connection(self.database, timeout=60)

    def _snapshot_connection(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            f"file:{self.database}?mode=ro", uri=True, timeout=60,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        return connection

    def _record_failure(self, resource: str, error: Exception) -> None:
        connection = self._connect()
        try:
            with connection:
                connection.execute(
                    """UPDATE dashboard_optional_read_model_state_v1
                          SET last_error=?,updated_at=? WHERE resource=?""",
                    (f"{type(error).__name__}: {str(error)[:400]}",
                     _utc_now().isoformat(), resource),
                )
        finally:
            connection.close()

    def refresh_resource(self, resource: str) -> int:
        with self._refresh_lock:
            return self._refresh_resource(resource)

    def _refresh_resource(self, resource: str) -> int:
        contract = READ_MODEL_CONTRACTS[resource]
        connection = self._connect()
        try:
            install_dashboard_read_model_schema(connection)
            state = connection.execute(
                """SELECT source_revision FROM dashboard_optional_read_model_state_v1
                   WHERE resource=?""",
                (resource,),
            ).fetchone()
            source_revision = int(state[0])
            current = connection.execute(
                """SELECT contract_version,source_revision,payload_json,payload_hash
                   FROM dashboard_optional_read_models_v1 WHERE resource=?""",
                (resource,),
            ).fetchone()
            if (
                current is not None
                and str(current[0]) == contract
                and int(current[1]) == source_revision
                and _payload_hash(str(current[2]).encode("utf-8")) == str(current[3])
            ):
                return 0
        finally:
            connection.close()

        snapshot_started = _utc_now()
        started_clock = time.monotonic()
        read_connection = self._snapshot_connection()
        snapshot_expired = False
        try:
            def interrupt_expired_snapshot() -> int:
                nonlocal snapshot_expired
                snapshot_expired = (
                    time.monotonic() - started_clock > self.max_snapshot_seconds
                )
                return int(snapshot_expired)

            read_connection.set_progress_handler(
                interrupt_expired_snapshot, 1_000,
            )
            read_connection.execute("BEGIN DEFERRED")
            state = read_connection.execute(
                """SELECT source_revision
                     FROM dashboard_optional_read_model_state_v1
                    WHERE resource=?""",
                (resource,),
            ).fetchone()
            if state is None:
                raise RuntimeError(f"{resource} read model state is unavailable")
            source_revision = int(state[0])
            snapshot = DashboardReadModelSnapshot(
                database=self.database,
                connection=read_connection,
                source_revision=source_revision,
                started_at=snapshot_started,
            )
            payload = dict(self.builders[resource](snapshot))
            snapshot_completed = _utc_now()
            if time.monotonic() - started_clock > self.max_snapshot_seconds:
                raise TimeoutError(
                    f"{resource} read model snapshot exceeded "
                    f"{self.max_snapshot_seconds:.0f} seconds"
                )
        except sqlite3.OperationalError as error:
            if snapshot_expired:
                raise TimeoutError(
                    f"{resource} read model snapshot exceeded "
                    f"{self.max_snapshot_seconds:.0f} seconds"
                ) from error
            raise
        finally:
            read_connection.set_progress_handler(None, 0)
            if read_connection.in_transaction:
                read_connection.rollback()
            read_connection.close()

        generated_value = payload.get("generated_at")
        if not isinstance(generated_value, str) or not generated_value:
            raise ValueError(f"{resource} read model generated_at is required")
        generated_at = generated_value
        try:
            generated = datetime.fromisoformat(generated_at.replace("Z", "+00:00"))
        except ValueError as error:
            raise ValueError(
                f"{resource} read model generated_at is invalid"
            ) from error
        if generated.tzinfo is None:
            raise ValueError(f"{resource} read model generated_at must be timezone-aware")
        snapshot_clock_offset = (
            generated.astimezone(UTC) - snapshot_started
        ).total_seconds()
        if abs(snapshot_clock_offset) > 5:
            raise ValueError(
                f"{resource} read model generated_at is not bound to its snapshot"
            )
        snapshot_age = (snapshot_completed - generated.astimezone(UTC)).total_seconds()
        if snapshot_age < -5 or snapshot_age > self.max_snapshot_age_seconds:
            raise TimeoutError(
                f"{resource} read model snapshot age is {snapshot_age:.3f} seconds"
            )
        body = _payload_bytes(payload)
        digest = _payload_hash(body)
        connection = self._connect()
        try:
            # Publication is the only write-side critical section. Pin L and
            # publish R atomically; the long builder transaction is read-only.
            connection.execute("BEGIN IMMEDIATE")
            with connection:
                latest = connection.execute(
                    """SELECT source_revision
                       FROM dashboard_optional_read_model_state_v1
                       WHERE resource=?""",
                    (resource,),
                ).fetchone()
                if latest is None:
                    raise RuntimeError(f"{resource} read model state is unavailable")
                live_source_revision = int(latest[0])
                current = connection.execute(
                    """SELECT source_revision,payload_json,payload_hash
                         FROM dashboard_optional_read_models_v1
                        WHERE resource=?""",
                    (resource,),
                ).fetchone()
                if current is not None and int(current[0]) > source_revision:
                    return 0
                if (
                    current is not None
                    and int(current[0]) == source_revision
                    and _payload_hash(str(current[1]).encode("utf-8")) == str(current[2])
                ):
                    return 0
                now = _utc_now().isoformat()
                connection.execute(
                    """INSERT INTO dashboard_optional_read_models_v1
                         (resource,contract_version,source_revision,generated_at,
                          payload_json,payload_hash,updated_at,snapshot_started_at,
                          snapshot_completed_at,live_source_revision)
                       VALUES (?,?,?,?,?,?,?,?,?,?)
                       ON CONFLICT(resource) DO UPDATE SET
                         contract_version=excluded.contract_version,
                         source_revision=excluded.source_revision,
                         generated_at=excluded.generated_at,
                         payload_json=excluded.payload_json,
                         payload_hash=excluded.payload_hash,
                         updated_at=excluded.updated_at,
                         snapshot_started_at=excluded.snapshot_started_at,
                         snapshot_completed_at=excluded.snapshot_completed_at,
                         live_source_revision=excluded.live_source_revision
                       WHERE excluded.source_revision >=
                             dashboard_optional_read_models_v1.source_revision""",
                    (resource, contract, source_revision, generated_at,
                     body.decode("utf-8"), digest, now,
                     snapshot_started.isoformat(), snapshot_completed.isoformat(),
                     live_source_revision),
                )
                connection.execute(
                    """UPDATE dashboard_optional_read_model_state_v1
                          SET last_success_at=?,last_error=NULL,updated_at=?
                        WHERE resource=?""",
                    (now, now, resource),
                )
            return (
                REFRESHED_DIRTY
                if live_source_revision > source_revision else REFRESHED
            )
        finally:
            connection.close()

    def refresh_once(self) -> dict[str, int]:
        refreshed: dict[str, int] = {}
        for resource in READ_MODEL_CONTRACTS:
            try:
                refreshed[resource] = self.refresh_resource(resource)
            except Exception as error:  # independent optional failure domains
                refreshed[resource] = -1
                try:
                    self._record_failure(resource, error)
                except Exception:
                    pass
        return refreshed

    def _run(self) -> None:
        catch_up_used = False
        while not self._stop.is_set():
            refreshed = self.refresh_once()
            dirty = any(value == REFRESHED_DIRTY for value in refreshed.values())
            if dirty and not catch_up_used:
                catch_up_used = True
                continue
            catch_up_used = False
            self._stop.wait(self.poll_seconds)

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="dashboard-read-model-owner", daemon=True,
        )
        self._thread.start()

    def stop(self, timeout: float = 10.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)

