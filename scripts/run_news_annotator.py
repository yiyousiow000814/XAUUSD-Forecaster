#!/usr/bin/env python
"""Run local structured news annotation separately from the decision clock."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path


MODULE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MODULE_ROOT))

from xauusd_forecaster.annotation import (  # noqa: E402
    DEFAULT_GEMINI_MODEL,
    FALLBACK_GEMINI_MODEL,
    annotate_pending_news,
    gemini_routine_remaining,
    translate_pending_headlines,
)
from xauusd_forecaster.forward_ledger import ForwardLedger  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--database",
        type=Path,
        default=MODULE_ROOT / ".local" / "forward" / "forward-evidence.sqlite3",
    )
    parser.add_argument("--interval-seconds", type=float, default=60.0)
    parser.add_argument(
        "--batch-size", type=int, default=0,
        help="0 uses the safe per-key Gemini capacity automatically",
    )
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    ledger = ForwardLedger(args.database)
    try:
        while True:
            limit = None if args.batch_size <= 0 else args.batch_size
            statuses = annotate_pending_news(
                ledger, model=DEFAULT_GEMINI_MODEL, limit=limit
            )
            print(
                json.dumps(
                    {
                        "event": "ANNOTATION_BATCH",
                        "model": DEFAULT_GEMINI_MODEL,
                        "statuses": statuses,
                    }
                ),
                flush=True,
            )
            fallback_statuses = (
                annotate_pending_news(
                    ledger, model=FALLBACK_GEMINI_MODEL, limit=limit
                )
                if gemini_routine_remaining(ledger, DEFAULT_GEMINI_MODEL) == 0
                else [{"status": "STANDBY", "reason": "PRIMARY_ROUTINE_QUOTA_AVAILABLE"}]
            )
            print(
                json.dumps(
                    {
                        "event": "ANNOTATION_FALLBACK_BATCH",
                        "model": FALLBACK_GEMINI_MODEL,
                        "statuses": fallback_statuses,
                    }
                ),
                flush=True,
            )
            translations = translate_pending_headlines(ledger)
            print(
                json.dumps(
                    {"event": "HEADLINE_TRANSLATION_BATCH", "statuses": translations}
                ),
                flush=True,
            )
            if args.once:
                break
            time.sleep(max(5.0, args.interval_seconds))
    finally:
        ledger.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
