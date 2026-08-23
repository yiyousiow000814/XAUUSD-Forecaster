#!/usr/bin/env python
"""Validate local links and explicit repository paths in architecture docs."""

from __future__ import annotations

import argparse
import re
from pathlib import Path
from urllib.parse import unquote, urlsplit


ARCHITECTURE_DOCS = (
    Path("README.md"),
    Path("docs/README.md"),
    Path("docs/contracts/ARCHITECTURE_RULES.md"),
    Path("docs/design/SYSTEM_ARCHITECTURE.md"),
    Path("docs/design/DECISION_AND_EVIDENCE.md"),
    Path("docs/design/NEWS_AND_AI.md"),
    Path("docs/design/TRAINING_AND_MODELS.md"),
    Path("docs/design/DASHBOARD_AND_SYNC.md"),
    Path("docs/design/WEB_AND_CLOUDFLARE.md"),
    Path("docs/design/RUNTIME_AND_RELEASE.md"),
    Path("docs/reference/CODEBASE_MAP.md"),
    Path("docs/plans/REPOSITORY_MODULARIZATION.md"),
)
CODEBASE_MAP = Path("docs/reference/CODEBASE_MAP.md")
LINK_PATTERN = re.compile(r"(?<!!)\[[^\]]+\]\(([^)]+)\)")
CODE_PATTERN = re.compile(r"`([^`\r\n]+)`")
REPOSITORY_PATH_PREFIXES = (
    "scripts/", "xauusd_forecaster/", "web/", "ctrader/", "tests/",
    "docs/", ".github/",
)
REPOSITORY_PATH_FILES = frozenset({"AGENTS.md", "README.md"})


def _local_link_target(raw: str) -> str | None:
    target = raw.strip().split(maxsplit=1)[0].strip("<>")
    parsed = urlsplit(target)
    if parsed.scheme or parsed.netloc or not parsed.path:
        return None
    return unquote(parsed.path)


def validate_markdown_links(root: Path, documents=ARCHITECTURE_DOCS) -> list[str]:
    errors: list[str] = []
    for relative in documents:
        document = root / relative
        if not document.is_file():
            errors.append(f"missing architecture document: {relative.as_posix()}")
            continue
        text = document.read_text(encoding="utf-8")
        for raw in LINK_PATTERN.findall(text):
            target = _local_link_target(raw)
            if target is None:
                continue
            resolved = (document.parent / target).resolve()
            try:
                resolved.relative_to(root.resolve())
            except ValueError:
                errors.append(f"link leaves repository: {relative.as_posix()} -> {raw}")
                continue
            if not resolved.exists():
                errors.append(f"broken link: {relative.as_posix()} -> {raw}")
    return errors


def validate_codebase_paths(root: Path, document=CODEBASE_MAP) -> list[str]:
    path = root / document
    if not path.is_file():
        return [f"missing codebase map: {document.as_posix()}"]
    errors: list[str] = []
    for value in sorted(set(CODE_PATTERN.findall(path.read_text(encoding="utf-8")))):
        normalized = value.replace("\\", "/")
        if not (
            normalized in REPOSITORY_PATH_FILES
            or normalized.startswith(REPOSITORY_PATH_PREFIXES)
        ):
            continue
        if any(character in normalized for character in "*?[]"):
            errors.append(f"repository path must be explicit: {normalized}")
            continue
        if not (root / normalized).exists():
            errors.append(f"missing repository path: {normalized}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path.cwd())
    args = parser.parse_args()
    root = args.root.resolve()
    errors = [
        *validate_markdown_links(root),
        *validate_codebase_paths(root),
    ]
    if errors:
        for error in errors:
            print(error)
        return 1
    print("Architecture documentation links and repository paths are valid.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
