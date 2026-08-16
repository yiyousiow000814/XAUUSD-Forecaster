#!/usr/bin/env python
"""Run the low-latency, local-only private Assistant worker."""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
from datetime import UTC, datetime
from pathlib import Path


MODULE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MODULE_ROOT))

from scripts.run_dashboard_sync import (  # noqa: E402
    DEFAULT_CONFIG,
    _sync_assistant_chat,
    configured_targets,
)
from xauusd_forecaster.assistant_local_runtime import (  # noqa: E402
    MINISTRAL_ASSISTANT_MODEL,
    QWEN_ASSISTANT_MODEL,
)


DEFAULT_STATUS = MODULE_ROOT / ".local" / "forward" / "assistant-worker-status.json"
OLLAMA_TAGS_URL = "http://127.0.0.1:11434/api/tags"
OLLAMA_GENERATE_URL = "http://127.0.0.1:11434/api/generate"
PRIMARY_KEEPALIVE_REFRESH_SECONDS = 60.0
STATUS_HEARTBEAT_SECONDS = 15.0


def _iso_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _write_status(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def require_local_models() -> list[str]:
    with urllib.request.urlopen(OLLAMA_TAGS_URL, timeout=5.0) as response:
        payload = json.loads(response.read())
    names = {
        str(item.get("name") or "")
        for item in payload.get("models", [])
        if isinstance(item, dict)
    }
    required = [QWEN_ASSISTANT_MODEL, MINISTRAL_ASSISTANT_MODEL]
    missing = [model for model in required if model not in names]
    if missing:
        raise RuntimeError("Missing local Assistant models: " + ", ".join(missing))
    return required


def keep_primary_resident() -> None:
    request = urllib.request.Request(
        OLLAMA_GENERATE_URL,
        data=json.dumps({
            "model": QWEN_ASSISTANT_MODEL,
            "keep_alive": -1,
        }, separators=(",", ":")).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=180.0) as response:
        payload = json.loads(response.read())
    if not isinstance(payload, dict) or payload.get("done") is not True:
        raise RuntimeError("Local Assistant primary model did not become resident")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--status-file", type=Path, default=DEFAULT_STATUS)
    parser.add_argument("--interval-seconds", type=float, default=1.0)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    config = json.loads(args.config.read_text(encoding="utf-8"))
    last_keepalive = 0.0
    last_status_write = 0.0
    while True:
        checked_at = _iso_now()
        try:
            models = require_local_models()
            if time.monotonic() - last_keepalive >= PRIMARY_KEEPALIVE_REFRESH_SECONDS:
                keep_primary_resident()
                last_keepalive = time.monotonic()
            totals = {"claimed": 0, "answered": 0, "deferred": 0, "failed_attempts": 0}
            for target in configured_targets(config):
                result = _sync_assistant_chat({}, target)
                for key in totals:
                    totals[key] += int(getattr(result, key))
            now_monotonic = time.monotonic()
            if (
                any(totals.values())
                or now_monotonic - last_status_write >= STATUS_HEARTBEAT_SECONDS
            ):
                _write_status(args.status_file, {
                    "service": "assistant",
                    "state": "RUNNING",
                    "provider": "OLLAMA_LOCAL",
                    "models": models,
                    "resident_primary": QWEN_ASSISTANT_MODEL,
                    "last_check": checked_at,
                    "last_success": _iso_now(),
                    **totals,
                })
                last_status_write = now_monotonic
        except Exception as error:
            _write_status(args.status_file, {
                "service": "assistant",
                "state": "DEGRADED",
                "provider": "OLLAMA_LOCAL",
                "last_check": checked_at,
                "error_type": type(error).__name__,
                "error": str(error)[:500],
            })
            last_status_write = time.monotonic()
            print(json.dumps({
                "event": "ASSISTANT_WORKER_ERROR",
                "error_type": type(error).__name__,
                "error": str(error)[:500],
            }), flush=True)
        if args.once:
            break
        time.sleep(max(0.5, args.interval_seconds))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
