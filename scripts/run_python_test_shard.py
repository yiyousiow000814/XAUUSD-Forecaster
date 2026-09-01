from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys
import time


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / ".github" / "python-test-shards.json"


def shard_paths(shard_id: str) -> list[str]:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    matches = [item for item in manifest["shards"] if item["id"] == shard_id]
    if len(matches) != 1:
        raise ValueError(f"unknown or duplicate Python test shard: {shard_id}")
    paths = [str(path).replace("\\", "/") for path in matches[0]["tests"]]
    if not paths or len(set(paths)) != len(paths):
        raise ValueError(f"empty or duplicate test path in {shard_id}")
    return paths


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--shard", required=True)
    parser.add_argument(
        "--output", type=Path, default=ROOT / "output" / "python-tests",
    )
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    paths = shard_paths(args.shard)
    junit = output / f"{args.shard}.xml"
    command = [
        sys.executable,
        "-m",
        "pytest",
        "-q",
        "--timeout=30",
        "--timeout-method=thread",
        "--durations=30",
        "--durations-min=0",
        f"--junitxml={junit}",
        *paths,
    ]
    started_at = datetime.now(timezone.utc)
    started = time.perf_counter()
    completed = subprocess.run(command, cwd=ROOT, check=False)
    elapsed = time.perf_counter() - started
    report = {
        "schema_version": "python-test-shard-result-v1",
        "shard": args.shard,
        "started_at": started_at.isoformat(timespec="microseconds"),
        "elapsed_seconds": round(elapsed, 3),
        "result": "PASS" if completed.returncode == 0 else "FAIL",
        "exit_code": completed.returncode,
        "test_paths": paths,
    }
    (output / f"{args.shard}.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8",
    )
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
