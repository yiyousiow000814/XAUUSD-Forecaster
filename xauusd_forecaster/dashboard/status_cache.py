"""Bounded process-local status snapshot caching for the Dashboard API."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path


STATUS_SNAPSHOT_TTL_SECONDS = 15.0
STATUS_SNAPSHOT_WAIT_SECONDS = 5.0
STATUS_SNAPSHOT_MAX_STALE_SECONDS = 90.0


class StatusSnapshotUnavailable(RuntimeError):
    """Raised when no bounded-age dashboard snapshot can be served."""


class StatusSnapshotCache:
    """Serialize one dashboard snapshot at a time and fail closed while stale."""

    def __init__(
        self,
        *,
        ttl_seconds: float = STATUS_SNAPSHOT_TTL_SECONDS,
        wait_seconds: float = STATUS_SNAPSHOT_WAIT_SECONDS,
        max_stale_seconds: float = STATUS_SNAPSHOT_MAX_STALE_SECONDS,
        clock=time.monotonic,
    ) -> None:
        self.ttl_seconds = ttl_seconds
        self.wait_seconds = wait_seconds
        self.max_stale_seconds = max_stale_seconds
        self.clock = clock
        self._condition = threading.Condition()
        self._database: Path | None = None
        self._body: bytes | None = None
        self._built_at = 0.0
        self._refreshing = False
        self._last_error: str | None = None

    def _age(self) -> float | None:
        if self._body is None:
            return None
        return max(0.0, self.clock() - self._built_at)

    def health(self) -> tuple[int, dict]:
        with self._condition:
            age = self._age()
            if age is None:
                return 503, {
                    "status": "STARTING" if self._refreshing else "UNAVAILABLE",
                    "snapshot_age_seconds": None,
                    "last_error": self._last_error,
                }
            if self._last_error:
                return 503, {
                    "status": "ERROR",
                    "snapshot_age_seconds": age,
                    "last_error": self._last_error,
                }
            if age > self.max_stale_seconds:
                return 503, {
                    "status": "STALE",
                    "snapshot_age_seconds": age,
                    "last_error": self._last_error,
                }
            return 200, {
                "status": "OK",
                "snapshot_age_seconds": age,
                "refreshing": self._refreshing,
            }

    def _refresh(self, database: Path, builder) -> tuple[bytes, str, float]:
        try:
            payload = builder(database)
            body = json.dumps(
                payload, allow_nan=False, separators=(",", ":"),
            ).encode()
        except Exception as error:
            with self._condition:
                self._refreshing = False
                self._last_error = f"{type(error).__name__}: {str(error)[:400]}"
                self._condition.notify_all()
            raise
        with self._condition:
            self._body = body
            self._built_at = self.clock()
            self._refreshing = False
            self._last_error = None
            self._condition.notify_all()
        return body, "fresh", 0.0

    def _refresh_in_background(self, database: Path, builder) -> None:
        try:
            self._refresh(database, builder)
        except Exception:
            return

    def get(self, database: Path, builder) -> tuple[bytes, str, float]:
        database = database.resolve()
        stale_result: tuple[bytes, str, float] | None = None
        start_background_refresh = False
        with self._condition:
            if self._database != database:
                self._database = database
                self._body = None
                self._built_at = 0.0
                self._last_error = None
            age = self._age()
            if self._body is not None and age is not None and age <= self.ttl_seconds:
                return self._body, "fresh", age
            if (
                self._body is not None
                and age is not None
                and age <= self.max_stale_seconds
            ):
                stale_result = (self._body, "stale", age)
                if not self._refreshing:
                    self._refreshing = True
                    start_background_refresh = True
                build_here = False
            else:
                build_here = not self._refreshing
                if build_here:
                    self._refreshing = True

        if stale_result is not None:
            if start_background_refresh:
                threading.Thread(
                    target=self._refresh_in_background,
                    args=(database, builder),
                    daemon=True,
                    name="dashboard-status-refresh",
                ).start()
            return stale_result

        if build_here:
            return self._refresh(database, builder)

        with self._condition:
            refresh_finished = self._condition.wait_for(
                lambda: not self._refreshing, timeout=self.wait_seconds,
            )
            if not refresh_finished:
                raise StatusSnapshotUnavailable(
                    "dashboard snapshot refresh is still running"
                )
            age = self._age()
            if self._last_error:
                raise StatusSnapshotUnavailable(self._last_error)
            if self._body is not None and age is not None:
                return self._body, "fresh", age
            raise StatusSnapshotUnavailable(
                "dashboard snapshot refresh completed without a result"
            )
