#!/usr/bin/env python
"""Independent, inactive-by-default Windows PUBLIC_LIVE_V1 publisher owner."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.request
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

MODULE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MODULE_ROOT))

from xauusd_forecaster.live_broadcast import (  # noqa: E402
    ContinuousLivePublisher,
    LiveSequenceStore,
)
from xauusd_forecaster.runtime_paths import authoritative_runtime_root  # noqa: E402

LOCAL_STATUS_URL = "http://127.0.0.1:8765/api/status"
DEFAULT_INTERVAL_SECONDS = 30


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True), encoding="utf-8",
    )
    temporary.replace(path)


def read_local_status(*, timeout_seconds: int = 10) -> dict[str, Any]:
    request = urllib.request.Request(
        LOCAL_STATUS_URL, method="GET", headers={"accept": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        return json.loads(response.read().decode("utf-8"))


def current_revision(root: Path = MODULE_ROOT) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        capture_output=True, check=True, text=True,
    )
    revision = result.stdout.strip()
    if len(revision) != 40 or any(character not in "0123456789abcdef" for character in revision):
        raise RuntimeError("exact source revision is unavailable")
    return revision


def run_publisher_loop(
    publisher: ContinuousLivePublisher,
    *,
    source_revision: str,
    status_path: Path,
    interval_seconds: int = DEFAULT_INTERVAL_SECONDS,
    status_reader: Callable[[], Mapping[str, Any]] = read_local_status,
    sleep: Callable[[float], None] = time.sleep,
    max_cycles: int | None = None,
) -> None:
    cycles = 0
    last_success: str | None = None
    while max_cycles is None or cycles < max_cycles:
        attempted_at = utc_now()
        try:
            result = publisher.publish(
                status_reader(), source_revision=source_revision,
                dry_run=False, allow_production_publish=True,
            )
            last_success = utc_now()
            atomic_json(status_path, {
                "service": "broadcast", "state": "RUNNING",
                "last_attempt": attempted_at, "last_success": last_success,
                "last_sequence": result.get("sequence"), "last_error": None,
            })
        except Exception as error:  # failure is isolated to this delivery owner
            atomic_json(status_path, {
                "service": "broadcast", "state": "DEGRADED",
                "last_attempt": attempted_at, "last_success": last_success,
                "last_error": f"{type(error).__name__}: {error}"[:1024],
            })
        cycles += 1
        if max_cycles is None or cycles < max_cycles:
            sleep(interval_seconds)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--interval-seconds", type=int, default=DEFAULT_INTERVAL_SECONDS)
    parser.add_argument("--activate-production-publisher", action="store_true")
    parser.add_argument(
        "--state-root", type=Path, required=True,
    )
    args = parser.parse_args()
    state_root = authoritative_runtime_root(args.state_root)
    if not args.activate_production_publisher:
        parser.error("production publisher activation flag is required")
    if args.interval_seconds < 5:
        parser.error("interval must be at least 5 seconds")
    if os.environ.get("AURUM_LIVE_BROADCAST_PUBLISHER_ENABLED") != "1":
        parser.error("publisher is disabled")
    token = os.environ.get("LIVE_BROADCAST_PUBLISH_TOKEN", "")
    if not token:
        parser.error("LIVE_BROADCAST_PUBLISH_TOKEN is required")
    publisher = ContinuousLivePublisher(
        token,
        LiveSequenceStore(state_root / "live-broadcast-sequence.json"),
    )
    run_publisher_loop(
        publisher,
        source_revision=current_revision(),
        status_path=state_root / "live-broadcast-publisher-status.json",
        interval_seconds=args.interval_seconds,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
