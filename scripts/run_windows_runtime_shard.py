from __future__ import annotations

import argparse
import ast
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys
import time


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / ".github" / "windows-runtime-shards.json"


def _test_functions(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return [
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name.startswith("test_")
    ]


def expand_test_spec(spec: dict[str, str]) -> list[str]:
    relative = spec["path"].replace("\\", "/")
    if "from" not in spec and "through" not in spec:
        return [relative]
    if "from" not in spec or "through" not in spec:
        raise ValueError(f"partial test range for {relative}")
    names = _test_functions(ROOT / relative)
    try:
        start = names.index(spec["from"])
        end = names.index(spec["through"])
    except ValueError as error:
        raise ValueError(f"unknown test range sentinel for {relative}: {error}") from error
    if end < start:
        raise ValueError(f"reversed test range for {relative}")
    return [f"{relative}::{name}" for name in names[start : end + 1]]


def shard_nodeids(shard_id: str) -> list[str]:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    matches = [shard for shard in manifest["shards"] if shard["id"] == shard_id]
    if len(matches) != 1:
        raise ValueError(f"unknown or duplicate Windows shard: {shard_id}")
    return [
        nodeid
        for spec in matches[0]["tests"]
        for nodeid in expand_test_spec(spec)
    ]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--shard", required=True)
    parser.add_argument(
        "--output", type=Path, default=ROOT / "output" / "windows-runtime"
    )
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    nodeids = shard_nodeids(args.shard)
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
        *nodeids,
    ]
    started_at = datetime.now(timezone.utc)
    started = time.perf_counter()
    completed = subprocess.run(command, cwd=ROOT, check=False)
    elapsed = time.perf_counter() - started
    report = {
        "schema_version": "windows-runtime-shard-result-v1",
        "shard": args.shard,
        "started_at": started_at.isoformat(timespec="microseconds"),
        "elapsed_seconds": round(elapsed, 3),
        "result": "PASS" if completed.returncode == 0 else "FAIL",
        "exit_code": completed.returncode,
        "test_selectors": nodeids,
    }
    (output / f"{args.shard}.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
