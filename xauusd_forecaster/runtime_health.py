"""Atomic runtime heartbeats for independently supervised services."""

from __future__ import annotations

import json
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path


def write_runtime_heartbeat(
    path: Path,
    *,
    service: str,
    state: str = "RUNNING",
    work_items: int = 0,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps({
        "service": service,
        "state": state,
        "last_success": datetime.now(UTC).isoformat(),
        "last_error": None,
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
