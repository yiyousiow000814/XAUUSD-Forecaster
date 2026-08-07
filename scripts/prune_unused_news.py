"""Remove intake-only news rows after creating a verified local backup."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

MODULE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MODULE_ROOT))

from xauusd_forecaster.news_pruning import prune_unused_news


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("database", type=Path)
    parser.add_argument("--backup-directory", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    receipt = prune_unused_news(
        args.database,
        backup_directory=args.backup_directory,
        dry_run=not args.apply,
    )
    print(json.dumps(receipt, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
