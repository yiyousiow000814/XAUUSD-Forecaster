from __future__ import annotations

import argparse
import fnmatch
import json
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / ".github" / "windows-runtime-shards.json"


def _changed_paths(base: str | None) -> list[str]:
    if not base or set(base) == {"0"}:
        return [".github/windows-runtime-shards.json"]
    result = subprocess.run(
        ["git", "diff", "--name-only", f"{base}...HEAD"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return [line.replace("\\", "/") for line in result.stdout.splitlines() if line]


def _matches(path: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatchcase(path, pattern) for pattern in patterns)


def select(base: str | None) -> list[dict[str, str]]:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    changed = _changed_paths(base)
    if any(_matches(path, manifest["shared_paths"]) for path in changed):
        selected = manifest["shards"]
    else:
        selected = [
            shard
            for shard in manifest["shards"]
            if any(_matches(path, shard["paths"]) for path in changed)
        ]
    return (
        [{"id": shard["id"], "runner": "windows-latest"} for shard in selected]
        or [{"id": "no-windows-impact", "runner": "ubuntu-latest"}]
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base")
    parser.add_argument("--github-output", type=Path)
    args = parser.parse_args()
    matrix = json.dumps({"include": select(args.base)}, separators=(",", ":"))
    if args.github_output:
        with args.github_output.open("a", encoding="utf-8") as stream:
            stream.write(f"matrix={matrix}\n")
    else:
        print(matrix)


if __name__ == "__main__":
    main()
