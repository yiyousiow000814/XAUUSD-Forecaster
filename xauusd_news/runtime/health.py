"""Atomic runtime heartbeats for independently supervised services."""

from __future__ import annotations

import json
import tempfile
import threading
import time
from datetime import UTC, datetime
from pathlib import Path


class RuntimeHeartbeatPulse:
    """Keep a supervised service fresh while one bounded operation blocks."""

    def __init__(
        self,
        path: Path,
        *,
        service: str,
        state: str = "RUNNING",
        work_items: int = 0,
        last_error: str | None = None,
        interval_seconds: float = 30.0,
    ) -> None:
        if interval_seconds <= 0:
            raise ValueError("heartbeat pulse interval must be positive")
        self.path = path
        self.service = service
        self.interval_seconds = float(interval_seconds)
        self._state = state
        self._work_items = int(work_items)
        self._last_error = last_error
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def _write(self) -> None:
        with self._lock:
            state = self._state
            work_items = self._work_items
            last_error = self._last_error
        write_runtime_heartbeat(
            self.path,
            service=self.service,
            state=state,
            work_items=work_items,
            last_error=last_error,
        )

    def _run(self) -> None:
        while not self._stop.wait(self.interval_seconds):
            self._write()

    def update(
        self,
        *,
        work_items: int | None = None,
        state: str | None = None,
        last_error: str | None = None,
        clear_error: bool = False,
    ) -> None:
        with self._lock:
            if work_items is not None:
                self._work_items = int(work_items)
            if state is not None:
                self._state = state
            if clear_error:
                self._last_error = None
            elif last_error is not None:
                self._last_error = str(last_error)
        self._write()

    def __enter__(self) -> RuntimeHeartbeatPulse:
        self.start()
        return self

    def start(self) -> None:
        """Start pulsing for a caller with an existing cleanup boundary."""
        if self._thread is not None:
            raise RuntimeError("heartbeat pulse is already running")
        self._write()
        self._thread = threading.Thread(
            target=self._run,
            name=f"{self.service}-heartbeat",
            daemon=True,
        )
        self._thread.start()

    def close(self) -> None:
        """Stop the pulse and join its bounded background thread."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=self.interval_seconds + 1.0)
            self._thread = None

    def __exit__(self, *_exc: object) -> None:
        self.close()


def write_runtime_heartbeat(
    path: Path,
    *,
    service: str,
    state: str = "RUNNING",
    work_items: int = 0,
    last_error: str | None = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps({
        "service": service,
        "state": state,
        "last_success": datetime.now(UTC).isoformat(),
        "last_error": last_error,
        "work_items": work_items,
    })
    temporary: Path | None = None
    try:
        # Candidate and rollback processes can overlap briefly. A unique file
        # keeps their atomic replaces independent instead of making both
        # writers race on one shared `.tmp` path.
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as stream:
            stream.write(payload)
            temporary = Path(stream.name)
        for attempt in range(50):
            try:
                temporary.replace(path)
                break
            except PermissionError:
                if attempt == 49:
                    raise
                # Windows can briefly deny a replace while another process is
                # replacing or reading the same heartbeat. Keep this bounded;
                # persistent filesystem failures must still reach watchdog.
                time.sleep(0.02)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
