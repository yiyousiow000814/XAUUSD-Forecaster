"""Real Sync owner with an explicit loopback-only test configuration."""
from __future__ import annotations

import argparse
from datetime import UTC, datetime, timedelta
import importlib.util
import json
from pathlib import Path
import sys
import threading
from urllib.parse import urlsplit


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixture-root", type=Path, required=True)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--provider", required=True)
    args = parser.parse_args()
    root = args.fixture_root.resolve(strict=True)
    if not (root / "fixture-owned.json").is_file():
        raise RuntimeError("STAGED_OWNERSHIP_REQUIRED")
    denied = [Path.home() / name for name in (
        "XAUUSD-Forecaster", "XAUUSD-Forecaster-runtime", "XAUUSD-Forecaster.local",
    )]
    if any(root == path or path in root.parents for path in denied):
        raise RuntimeError("STAGED_PRODUCTION_TARGET_DENIED")
    provider = urlsplit(args.provider)
    if provider.scheme != "https" or provider.hostname != "127.0.0.1" or not provider.port:
        raise RuntimeError("STAGED_NON_LOOPBACK_PROVIDER_DENIED")
    sys.path.insert(0, str(args.source_root.resolve(strict=True)))
    spec = importlib.util.spec_from_file_location("staged_sync", args.source_root / "scripts/run_dashboard_sync.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    state = root / "runtime/.local/forward"
    state.mkdir(parents=True, exist_ok=True)
    config = module.configure_runtime_state({
        "local_status_url": args.provider + "/api/status",
        "targets": [{"name": "fixture", "remote_ingest_url": args.provider + "/api/ingest", "token": "isolated-test-token"}],
    }, state)
    target = module.configured_targets(config)[0]
    future = (datetime.now(UTC) + timedelta(days=1)).isoformat()
    Path(target["resource_schedule_state_file"]).write_text(json.dumps({
        "schema_version": 1, "resources": {
            policy[0]: {"next_run_at": future} for policy in module.RESOURCE_POLICIES
            if policy[0] != "news_evidence"
        },
    }), encoding="utf-8")
    stop = threading.Event()

    def watch_stop() -> None:
        while not stop.wait(0.1):
            if (root / "stop-sync").exists():
                stop.set()

    watcher = threading.Thread(target=watch_stop, daemon=True)
    watcher.start()
    try:
        module.run_continuous_sync(config, status_file=state / "dashboard-sync-status.json",
                                   interval_seconds=5, stop_event=stop, max_heartbeats=36)
    finally:
        stop.set()
        watcher.join(timeout=2)


if __name__ == "__main__":
    main()
