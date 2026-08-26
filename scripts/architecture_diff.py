#!/usr/bin/env python
"""Compare generated architecture facts with a Git base as Markdown or JSON."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path


def _base_document(root: Path, ref: str, path: str) -> dict:
    result = subprocess.run(["git", "show", f"{ref}:{path}"], cwd=root, capture_output=True, text=True, encoding="utf-8")
    return json.loads(result.stdout) if result.returncode == 0 else {"facts": [], "dependencies": {"violations": []}}


def main() -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--root", type=Path, default=Path.cwd()); parser.add_argument("--base", required=True); parser.add_argument("--json", action="store_true"); args = parser.parse_args()
    root = args.root.resolve(); path = root / "architecture/generated/code-index.json"
    current = json.loads(path.read_text(encoding="utf-8")); base = _base_document(root, args.base, "architecture/generated/code-index.json")
    old = {fact["id"]: fact for fact in base.get("facts", [])}; new = {fact["id"]: fact for fact in current.get("facts", [])}
    report = {"schema": "architecture-diff-v1", "base": args.base,
              "facts_added": [new[key] for key in sorted(new.keys() - old.keys())],
              "facts_removed": [old[key] for key in sorted(old.keys() - new.keys())],
              "import_policy_violations": current.get("dependencies", {}).get("violations", [])}
    if args.json:
        print(json.dumps(report, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    else:
        print("## Architecture diff")
        print(f"\n- Facts added: {len(report['facts_added'])}")
        print(f"- Facts removed: {len(report['facts_removed'])}")
        print(f"- Import policy violations: {len(report['import_policy_violations'])}")
        for label, items in (("Added", report["facts_added"]), ("Removed", report["facts_removed"])):
            if items:
                print(f"\n### {label}\n")
                for item in items[:50]: print(f"- `{item['id']}`")
    return 1 if report["import_policy_violations"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
