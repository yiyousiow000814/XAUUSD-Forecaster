#!/usr/bin/env python3
"""Run the bounded targeted architecture mutation audit."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from architecture_tools.mutations import build_report, execute_mutation, load_mutations


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--profile", choices=("smoke", "full"), default="smoke")
    parser.add_argument("--mutation", action="append", default=[])
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    root = args.root.resolve()
    digest = json.loads((root / "architecture/generated/source-digest.json").read_text(
        encoding="utf-8"
    ))["source_digest"]
    mutations = load_mutations(root)
    if args.mutation:
        selected = [item for item in mutations if item.mutation_id in set(args.mutation)]
    elif args.profile == "smoke":
        selected = [item for item in mutations if item.smoke]
    else:
        selected = mutations
    missing = sorted(set(args.mutation) - {item.mutation_id for item in selected})
    if missing:
        parser.error(f"unknown mutation IDs: {missing}")
    results = []
    for mutation in selected:
        result = execute_mutation(root, mutation)
        results.append(result)
        print(f"{mutation.mutation_id}: {result['outcome']} ({result['reason']})", flush=True)
    report = build_report(root, results, digest)
    output = args.output or root / "architecture/generated/mutation-report.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, sort_keys=True,
                                 separators=(",", ":")) + "\n", encoding="utf-8")
    return 1 if any(row["outcome"] == "SURVIVED" for row in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
