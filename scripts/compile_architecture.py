#!/usr/bin/env python
"""Compile deterministic architecture artifacts from source and declarations."""

from __future__ import annotations

import argparse
from pathlib import Path

from architecture_compiler import ArchitectureCompileError, build_artifacts, generated_differences, write_artifacts


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(); root = args.root.resolve()
    try:
        artifacts = build_artifacts(root)
        differences = generated_differences(root, artifacts)
        if args.check:
            if differences:
                print("Architecture artifacts are stale: " + ", ".join(differences))
                return 1
            print(f"Architecture artifacts are current ({len(artifacts)} files).")
            return 0
        write_artifacts(root, artifacts)
        print(f"Generated {len(artifacts)} architecture artifacts.")
        return 0
    except ArchitectureCompileError as exc:
        print(f"Architecture compilation failed: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
