#!/usr/bin/env python
"""Mirror the read-only dashboard snapshot to the private Sites dashboard."""

from __future__ import annotations

import argparse
import copy
import http.client
import json
import os
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path


MODULE_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = MODULE_ROOT / ".local" / "forward" / "dashboard-sync.json"
DEFAULT_STATUS = MODULE_ROOT / ".local" / "forward" / "dashboard-sync-status.json"
REMOTE_PAYLOAD_LIMIT_BYTES = 450_000
REMOTE_NEWS_LIMIT = 80
REMOTE_DECISION_LIMIT = 20
REMOTE_EVIDENCE_LIMIT = 60


def remote_snapshot(payload: dict) -> bytes:
    """Build a bounded Sites mirror without truncating retained news content."""
    snapshot = copy.deepcopy(payload)
    training = snapshot.get("training")
    if isinstance(training, dict):
        training.pop("models", None)  # Duplicated by learning_curves.models.

    learning = snapshot.get("learning_curves")
    if isinstance(learning, dict):
        # These analytical series are retained in local SQLite/API and are not
        # rendered by the Sites UI.
        learning.pop("identity_curves", None)
        learning.pop("full_minus_market", None)
        learning.pop("broad_full_minus_official_full", None)
        models = learning.get("models")
        if isinstance(models, list):
            learning["archived_model_count"] = sum(
                row.get("lifecycle_status") not in {"LATEST", "PREVIOUS"}
                for row in models
            )
            learning["models"] = [
                row
                for row in models
                if row.get("lifecycle_status") in {"LATEST", "PREVIOUS"}
            ]

    for name, limit in (
        ("recent_news", REMOTE_NEWS_LIMIT),
        ("recent_decisions", REMOTE_DECISION_LIMIT),
        ("news_evidence", REMOTE_EVIDENCE_LIMIT),
    ):
        rows = snapshot.get(name)
        if isinstance(rows, list):
            snapshot[name] = rows[:limit]

    snapshot["mirror_window"] = {
        "bounded": True,
        "recent_news": len(snapshot.get("recent_news", [])),
        "recent_decisions": len(snapshot.get("recent_decisions", [])),
        "news_evidence": len(snapshot.get("news_evidence", [])),
    }
    encoded = json.dumps(
        snapshot, ensure_ascii=False, allow_nan=False, separators=(",", ":")
    ).encode("utf-8")
    while len(encoded) > REMOTE_PAYLOAD_LIMIT_BYTES and snapshot.get("recent_news"):
        snapshot["recent_news"].pop()
        snapshot["mirror_window"]["recent_news"] = len(snapshot["recent_news"])
        encoded = json.dumps(
            snapshot, ensure_ascii=False, allow_nan=False, separators=(",", ":")
        ).encode("utf-8")
    if len(encoded) > REMOTE_PAYLOAD_LIMIT_BYTES:
        raise ValueError(
            f"bounded dashboard payload is still {len(encoded)} bytes "
            f"(limit {REMOTE_PAYLOAD_LIMIT_BYTES})"
        )
    return encoded


def write_sync_status(
    path: Path,
    *,
    success: bool,
    attempts_used: int | None = None,
    error: Exception | None = None,
) -> None:
    """Atomically publish the synchronizer's actual operational heartbeat."""
    existing: dict = {}
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            existing = {}
    now = datetime.now(UTC).isoformat()
    if success:
        existing.update(
            {
                "last_success": now,
                "last_attempt": now,
                "last_error": None,
                "last_error_type": None,
                "attempts_used": attempts_used,
            }
        )
    else:
        existing.update(
            {
                "last_attempt": now,
                "last_error": str(error)[:500] if error else "Unknown sync error",
                "last_error_type": type(error).__name__ if error else "UnknownError",
            }
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(existing, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)


def sync_once(config: dict) -> None:
    with urllib.request.urlopen(config["local_status_url"], timeout=5) as response:
        payload = remote_snapshot(json.loads(response.read()))
    headers = {
        "Authorization": f"Bearer {config['token']}",
        "Content-Type": "application/json",
    }
    sites_bypass_token = os.environ.get("SITES_BYPASS_TOKEN", "").strip()
    if sites_bypass_token:
        headers["OAI-Sites-Authorization"] = f"Bearer {sites_bypass_token}"
    request = urllib.request.Request(
        config["remote_ingest_url"],
        data=payload,
        headers=headers,
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        if response.status != 200:
            raise RuntimeError(f"dashboard sync returned HTTP {response.status}")


def sync_with_retry(config: dict, *, attempts: int = 3) -> int:
    """Retry transient transport failures without waiting for the next sync cycle."""
    for attempt in range(1, attempts + 1):
        try:
            sync_once(config)
            return attempt
        except Exception as error:
            transient = isinstance(
                error,
                (ConnectionError, TimeoutError, http.client.RemoteDisconnected),
            ) or (
                isinstance(error, urllib.error.HTTPError)
                and (error.code == 429 or error.code >= 500)
            )
            if not transient or attempt >= attempts:
                raise
            print(
                json.dumps(
                    {
                        "event": "DASHBOARD_SYNC_RETRY",
                        "attempt": attempt,
                        "error_type": type(error).__name__,
                        "error": str(error)[:500],
                    }
                ),
                flush=True,
            )
            time.sleep(float(attempt * 2))
    raise RuntimeError("dashboard sync retry loop exhausted")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--status-file", type=Path, default=DEFAULT_STATUS)
    parser.add_argument("--interval-seconds", type=float, default=30.0)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    config = json.loads(args.config.read_text(encoding="utf-8"))
    while True:
        try:
            attempts_used = sync_with_retry(config)
            write_sync_status(
                args.status_file, success=True, attempts_used=attempts_used
            )
            print(
                json.dumps(
                    {"event": "DASHBOARD_SYNC_OK", "attempts_used": attempts_used}
                ),
                flush=True,
            )
        except Exception as error:
            write_sync_status(args.status_file, success=False, error=error)
            print(
                json.dumps(
                    {
                        "event": "DASHBOARD_SYNC_ERROR",
                        "error_type": type(error).__name__,
                        "error": str(error)[:500],
                    }
                ),
                flush=True,
            )
        if args.once:
            break
        time.sleep(max(5.0, args.interval_seconds))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
