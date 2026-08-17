#!/usr/bin/env python
"""Replay the frozen news retrieval benchmark without calling an LLM."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path


MODULE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MODULE_ROOT))

from xauusd_forecaster.news_retrieval_benchmark import (  # noqa: E402
    evaluate_candidate_retrieval,
    load_benchmark_manifest,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    database = args.database.resolve()
    manifest = load_benchmark_manifest(args.manifest.resolve())
    connection = sqlite3.connect(
        f"file:{database}?mode=ro", uri=True, timeout=30,
    )
    connection.row_factory = sqlite3.Row
    try:
        result = evaluate_candidate_retrieval(connection, manifest)
    finally:
        connection.close()
    rendered = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
