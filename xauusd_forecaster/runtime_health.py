"""Atomic runtime heartbeats for independently supervised services."""

from __future__ import annotations

import json
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
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps({
            "service": service,
            "state": state,
            "last_success": datetime.now(UTC).isoformat(),
            "last_error": None,
            "work_items": work_items,
        }),
        encoding="utf-8",
    )
    temporary.replace(path)
