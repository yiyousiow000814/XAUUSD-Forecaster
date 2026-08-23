"""Durable, disposable read models for optional dashboard resources."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path


READ_MODEL_CONTRACTS = {
    "audit": "dashboard-audit-summary-v1",
    "learning": "dashboard-learning-summary-v1",
    "market_chart": "dashboard-market-chart-summary-v1",
}

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
            """SELECT contract_version,source_revision,generated_at,
                      payload_json,payload_hash,updated_at
                 FROM dashboard_optional_read_models_v1 WHERE resource=?""",
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
        "age_seconds": age_seconds,
        "state": "READY" if age_seconds <= 900 else "STALE",
    }


class DashboardReadModelOwner:
    """Refresh optional resources outside HTTP request ownership."""

    def __init__(
        self,
        database: Path,
        builders: Mapping[str, Callable[[Path], Mapping[str, object]]],
        *,
        poll_seconds: float = 30.0,
    ) -> None:
        self.database = database.resolve()
        self.builders = dict(builders)
        self.poll_seconds = poll_seconds
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database, timeout=60)
        connection.execute("PRAGMA busy_timeout=60000")
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

        payload = dict(self.builders[resource](self.database))
        generated_at = str(payload.get("generated_at") or _utc_now().isoformat())
        body = _payload_bytes(payload)
        digest = _payload_hash(body)
        connection = self._connect()
        try:
            with connection:
                latest = connection.execute(
                    """SELECT source_revision
                       FROM dashboard_optional_read_model_state_v1
                       WHERE resource=?""",
                    (resource,),
                ).fetchone()
                if latest is None or int(latest[0]) != source_revision:
                    return 0
                now = _utc_now().isoformat()
                connection.execute(
                    """INSERT INTO dashboard_optional_read_models_v1
                         (resource,contract_version,source_revision,generated_at,
                          payload_json,payload_hash,updated_at)
                       VALUES (?,?,?,?,?,?,?)
                       ON CONFLICT(resource) DO UPDATE SET
                         contract_version=excluded.contract_version,
                         source_revision=excluded.source_revision,
                         generated_at=excluded.generated_at,
                         payload_json=excluded.payload_json,
                         payload_hash=excluded.payload_hash,
                         updated_at=excluded.updated_at""",
                    (resource, contract, source_revision, generated_at,
                     body.decode("utf-8"), digest, now),
                )
                connection.execute(
                    """UPDATE dashboard_optional_read_model_state_v1
                          SET last_success_at=?,last_error=NULL,updated_at=?
                        WHERE resource=?""",
                    (now, now, resource),
                )
            return 1
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
        while not self._stop.is_set():
            self.refresh_once()
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
