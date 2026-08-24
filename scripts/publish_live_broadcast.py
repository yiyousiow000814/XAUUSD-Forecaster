#!/usr/bin/env python
"""Explicit, inactive-by-default PUBLIC_LIVE_V1 publisher rehearsal."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

MODULE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MODULE_ROOT))

from xauusd_forecaster.live_broadcast import public_live_state, publish_live_state


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--sequence", required=True, type=int)
    parser.add_argument("--source-revision", required=True)
    parser.add_argument("--send", action="store_true", help="future coordinated cutover only")
    parser.add_argument("--activate-production-publisher", action="store_true")
    args = parser.parse_args()
    status = json.loads(args.source.read_text(encoding="utf-8"))
    state = public_live_state(
        status, sequence=args.sequence, source_revision=args.source_revision,
    )
    result = publish_live_state(
        os.environ.get("LIVE_BROADCAST_PUBLISH_TOKEN", ""), state,
        dry_run=not args.send,
        allow_production_publish=args.activate_production_publisher,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
