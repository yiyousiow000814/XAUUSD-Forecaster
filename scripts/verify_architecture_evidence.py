#!/usr/bin/env python
"""Fail closed when generated architecture evidence is stale or contradictory."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from architecture_compiler import build_artifacts, generated_differences


def main() -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--root", type=Path, default=Path.cwd()); args = parser.parse_args()
    root = args.root.resolve(); artifacts = build_artifacts(root); differences = generated_differences(root, artifacts)
    if differences:
        print("Architecture evidence is stale: " + ", ".join(differences)); return 1
    evidence = json.loads(artifacts["evidence-index.json"])
    bad = [claim["claim_id"] for claim in evidence["claims"] if "CONTRADICTED" in claim["categories"]]
    if bad:
        print("Contradicted architecture claims: " + ", ".join(bad)); return 1
    print(f"Architecture evidence passed ({len(evidence['claims'])} claims).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
