#!/usr/bin/env python
"""Run local structured news annotation separately from the decision clock."""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path


MODULE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MODULE_ROOT))

from xauusd_forecaster.annotation import (  # noqa: E402
    DEFAULT_GEMINI_MODEL,
    FALLBACK_GEMINI_MODEL,
    TARGET_IMPACT_PROMPT_VERSION,
    TARGET_PROMPT_VERSION,
    annotate_pending_news,
    assess_pending_news_impacts,
    gemini_routine_remaining,
    translate_pending_headlines,
)
from xauusd_forecaster.forward_ledger import ForwardLedger  # noqa: E402


def write_heartbeat(path: Path, *, work_items: int) -> None:
    now = datetime.now(UTC).isoformat()
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(
            {"last_success": now, "last_error": None, "work_items": work_items}
        ),
        encoding="utf-8",
    )
    temporary.replace(path)


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
    parser.add_argument(
        "--status-file",
        type=Path,
        default=MODULE_ROOT / ".local" / "forward" / "news-annotator-status.json",
    )
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
            target_statuses = (
                annotate_pending_news(
                    ledger,
                    model=DEFAULT_GEMINI_MODEL,
                    limit=limit,
                    prompt_version=TARGET_PROMPT_VERSION,
                )
                if not statuses
                else [{
                    "status": "STANDBY",
                    "reason": "ACTIVE_CONTRACT_QUEUE_HAS_PRIORITY",
                }]
            )
            print(
                json.dumps(
                    {
                        "event": "TARGET_ANNOTATION_BATCH",
                        "model": DEFAULT_GEMINI_MODEL,
                        "prompt_version": TARGET_PROMPT_VERSION,
                        "statuses": target_statuses,
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
            impacts = assess_pending_news_impacts(ledger, limit=limit)
            print(
                json.dumps(
                    {"event": "NEWS_IMPACT_BATCH", "statuses": impacts}
                ),
                flush=True,
            )
            target_impacts = (
                assess_pending_news_impacts(
                    ledger,
                    limit=limit,
                    annotation_prompt_version=TARGET_PROMPT_VERSION,
                    impact_prompt_version=TARGET_IMPACT_PROMPT_VERSION,
                )
                if not impacts
                else [{
                    "status": "STANDBY",
                    "reason": "ACTIVE_CONTRACT_QUEUE_HAS_PRIORITY",
                }]
            )
            print(
                json.dumps(
                    {
                        "event": "TARGET_NEWS_IMPACT_BATCH",
                        "prompt_version": TARGET_IMPACT_PROMPT_VERSION,
                        "statuses": target_impacts,
                    }
                ),
                flush=True,
            )
            work_items = sum(
                len(batch)
                for batch in (
                    statuses, fallback_statuses, target_statuses,
                    translations, impacts, target_impacts,
                )
                if isinstance(batch, list)
            )
            write_heartbeat(args.status_file, work_items=work_items)
            if args.once:
                break
            time.sleep(max(5.0, args.interval_seconds))
    finally:
        ledger.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
