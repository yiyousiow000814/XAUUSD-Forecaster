#!/usr/bin/env python
"""Initialize U5 from recent XAU Tick files without creating evidence rows."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


MODULE_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = MODULE_ROOT.parents[1]
sys.path.insert(0, str(MODULE_ROOT))

from xauusd_forecaster.m1 import aggregate_xautk002_batch  # noqa: E402
from xauusd_forecaster.u5_state import U5State  # noqa: E402


DEFAULT_MANIFEST = (
    REPOSITORY_ROOT / "data" / "research" / "acquisitions"
    / "xauusd_full_ticks_2dp_bidask" / "xauusd_full_tick_manifest.json"
)
DEFAULT_OUTPUT = MODULE_ROOT / ".local" / "forward" / "u5-state.json"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(f"refusing to overwrite U5 state: {args.output}")
    manifest = json.loads(args.source_manifest.read_text(encoding="utf-8"))
    source_root = args.source_manifest.parent
    selected = []
    m1_rows = 0
    for item in reversed(manifest["days"]):
        selected.append(item)
        # A normal trading day has roughly 1,400 completed minutes. Select a
        # bounded tail first; exact readiness is checked after aggregation.
        if len(selected) >= 12:
            break
    state = U5State()
    for item in reversed(selected):
        result = aggregate_xautk002_batch(source_root / item["relative_path"])
        for row in result.frame.itertuples(index=False):
            state.update(row.minute.to_pydatetime(), row.bid_close, row.ask_close)
        m1_rows += len(result.frame)
    if state.status != "READY":
        raise RuntimeError(f"selected warm-up tail did not mature U5: {m1_rows} M1 rows")
    state.save(args.output)
    receipt = args.output.with_name("u5-warmup-receipt.json")
    receipt.write_text(
        json.dumps(
            {
                "schema": "xauusd.forward.u5-warmup-receipt.v1",
                "data_role": "WARMUP_ONLY",
                "source_manifest": str(args.source_manifest.resolve()),
                "selected_files": [item["relative_path"] for item in reversed(selected)],
                "m1_rows": m1_rows,
                "state_status": state.status,
                "training_allowed": False,
                "performance_evaluation_allowed": False,
                "decisions_created": 0,
                "outcomes_created": 0,
            },
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    print(receipt.read_text(encoding="utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
