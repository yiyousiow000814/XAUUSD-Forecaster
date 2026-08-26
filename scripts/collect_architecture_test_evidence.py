#!/usr/bin/env python
"""Execute the bounded critical contract set and record normalized outcomes."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import time
import tomllib
from pathlib import Path

from architecture_compiler import build_artifacts


def _run(command: list[str], cwd: Path) -> tuple[str, int]:
    started = time.perf_counter()
    result = subprocess.run(command, cwd=cwd, check=False, capture_output=True, text=True, timeout=300)
    return ("PASSED" if result.returncode == 0 else "FAILED", round((time.perf_counter() - started) * 1000))


def main() -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--root", type=Path, default=Path.cwd()); parser.add_argument("--run", action="store_true"); args = parser.parse_args()
    root = args.root.resolve(); artifacts = build_artifacts(root); digest = json.loads(artifacts["source-digest.json"])["source_digest"]
    bindings = tomllib.loads((root / "architecture/contracts/test_bindings.toml").read_text(encoding="utf-8"))["binding"]
    if not args.run:
        print(f"Contract evidence source digest: {digest}"); return 0
    results = []
    prior_path = root / "architecture/evidence/execution-results.json"
    prior = json.loads(prior_path.read_text(encoding="utf-8")) if prior_path.is_file() else {"source_digest": "", "results": []}
    for platform in ("PYTHON", "WINDOWS"):
        test_ids = sorted({item["test_id"] for item in bindings if item["platform"] == platform})
        if not test_ids: continue
        if platform == "WINDOWS" and os.name != "nt":
            if prior.get("source_digest") == digest:
                results.extend(item for item in prior.get("results", []) if item["test_id"] in test_ids)
            continue
        status, duration = _run(["python", "-m", "pytest", "-q", *test_ids], root)
        results.extend({"test_id": test_id, "status": status, "duration_ms": duration} for test_id in test_ids)
    web_ids = [item["test_id"].split("::", 1)[1] for item in bindings if item["platform"] == "WEB"]
    if web_ids:
        pattern = "|".join(web_ids)
        status, duration = _run(["node", "--import", "./tests/register-cloudflare-worker-loader.mjs", "--test", "--test-name-pattern", pattern, "tests/architecture-mobile-interaction.test.mjs"], root / "web")
        results.extend({"test_id": item["test_id"], "status": status, "duration_ms": duration} for item in bindings if item["platform"] == "WEB")
    document = {"schema": "architecture-test-execution-v1", "source_digest": digest, "results": sorted(results, key=lambda item: item["test_id"])}
    output = prior_path; output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
    failed = [item for item in results if item["status"] != "PASSED"]
    print(f"Recorded {len(results)} critical contract test executions at {digest}.")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
