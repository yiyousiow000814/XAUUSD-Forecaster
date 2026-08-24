#!/usr/bin/env python
"""Stage and verify the first atomic News CURRENT through an exact Version host."""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.parse
from pathlib import Path

MODULE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MODULE_ROOT))

from scripts.run_dashboard_sync import (  # noqa: E402
    NEWS_MIRROR_CONTRACT_VERSION,
    _get_json,
    _read_news_sync_state,
    _sync_news,
)

VERSION_HOST = re.compile(
    r"^[a-z0-9-]+-aurum-signal-room\.[a-z0-9-]+\.workers\.dev$"
)


def _version_origin(value: str) -> str:
    parsed = urllib.parse.urlsplit(value.strip())
    host = (parsed.hostname or "").lower()
    if (
        parsed.scheme != "https"
        or parsed.path not in ("", "/")
        or parsed.query
        or parsed.fragment
        or not VERSION_HOST.fullmatch(host)
    ):
        raise ValueError("version host must be an exact aurum-signal-room workers.dev origin")
    return urllib.parse.urlunsplit(("https", host, "", "", ""))


def bootstrap(
    *, base_config: dict, origin: str, token: str, state_file: Path,
    max_cycles: int, retry_seconds: float,
) -> dict:
    if not token.strip():
        raise ValueError("ingest token is missing")
    if not str(base_config.get("local_status_url") or "").startswith("http://127.0.0.1:"):
        raise ValueError("bootstrap requires the local Dashboard API authority")
    config = {
        **base_config,
        "name": "candidate-news-bootstrap",
        "legacy": False,
        "token": token.strip(),
        "remote_ingest_url": origin + "/api/ingest",
        "remote_news_index_url": origin + "/api/news-index",
        "remote_news_ingest_url": origin + "/api/news-content",
        "news_state_file": str(state_file),
    }
    config.pop("targets", None)
    for cycle in range(1, max_cycles + 1):
        try:
            _sync_news({}, config)
        except Exception:
            if cycle >= max_cycles:
                raise
            time.sleep(retry_seconds)
            continue
        state = _read_news_sync_state(state_file)
        if (
            state.get("contract_version") == NEWS_MIRROR_CONTRACT_VERSION
            and state.get("projection_state") == "CURRENT"
        ):
            health = _get_json(origin + "/api/news-index?health_check=1", config)
            required = {
                "status": "OK", "projection_state": "CURRENT",
                "verified_complete": True, "missing_detail_count": 0,
                "invariant_violation_count": 0,
            }
            mismatches = {
                key: {"expected": expected, "actual": health.get(key)}
                for key, expected in required.items() if health.get(key) != expected
            }
            if mismatches:
                raise RuntimeError(f"first CURRENT verification failed: {mismatches}")
            return {
                "status": "PASSED", "version_host": origin,
                "cycles": cycle,
                "generation_id": health.get("active_generation_id"),
                "snapshot_id": health.get("snapshot_id"),
                "index_count": health.get("index_count"),
                "detail_count": health.get("detail_count"),
                "source_digest": health.get("source_digest"),
                "receipt_digest": health.get("receipt_digest"),
                "missing_detail_count": health.get("missing_detail_count"),
                "invariant_violation_count": health.get("invariant_violation_count"),
            }
    raise RuntimeError("first CURRENT did not complete within the cycle bound")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--version-host", required=True)
    parser.add_argument("--state-file", type=Path, required=True)
    parser.add_argument("--token-env", default="CLOUDFLARE_INGEST_TOKEN")
    parser.add_argument("--max-cycles", type=int, default=1_000)
    parser.add_argument("--retry-seconds", type=float, default=2.0)
    args = parser.parse_args()
    if args.max_cycles < 1 or args.retry_seconds < 0:
        parser.error("cycle and retry bounds are invalid")
    config = json.loads(args.config.read_text(encoding="utf-8"))
    result = bootstrap(
        base_config=config,
        origin=_version_origin(args.version_host),
        token=os.environ.get(args.token_env, ""),
        state_file=args.state_file,
        max_cycles=args.max_cycles,
        retry_seconds=args.retry_seconds,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
