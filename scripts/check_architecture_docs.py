#!/usr/bin/env python
"""Validate current architecture-document links and explicit source paths."""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import unquote, urlsplit


ARCHITECTURE_DOCS = (
    Path("docs/README.md"),
    Path("docs/contracts/ARCHITECTURE_RULES.md"),
    Path("docs/design/SYSTEM_ARCHITECTURE.md"),
    Path("docs/audits/CURRENT_MAIN_ARCHITECTURE_2026_09_01.md"),
)
LINK_PATTERN = re.compile(r"(?<!!)\[[^\]]+\]\(([^)]+)\)")
CODE_PATTERN = re.compile(r"`([^`\r\n]+)`")
REPOSITORY_PREFIXES = (
    "scripts/", "xauusd_forecaster/", "web/", "broadcast/", "ctrader/",
    "tests/", "docs/", ".github/",
)


def _local_link(raw: str) -> str | None:
    target = raw.strip().split(maxsplit=1)[0].strip("<>")
    parsed = urlsplit(target)
    if parsed.scheme or parsed.netloc or not parsed.path:
        return None
    return unquote(parsed.path)


def validate_architecture_docs(root: Path) -> list[str]:
    root = root.resolve()
    errors: list[str] = []
    for relative in ARCHITECTURE_DOCS:
        document = root / relative
        if not document.is_file():
            errors.append(f"missing architecture document: {relative.as_posix()}")
            continue
        text = document.read_text(encoding="utf-8")
        for raw in LINK_PATTERN.findall(text):
            target = _local_link(raw)
            if target is None:
                continue
            resolved = (document.parent / target).resolve()
            try:
                resolved.relative_to(root)
            except ValueError:
                errors.append(f"link leaves repository: {relative.as_posix()} -> {raw}")
                continue
            if not resolved.exists():
                errors.append(f"broken link: {relative.as_posix()} -> {raw}")
        for value in sorted(set(CODE_PATTERN.findall(text))):
            normalized = value.replace("\\", "/")
            if not normalized.startswith(REPOSITORY_PREFIXES):
                continue
            if any(character in normalized for character in "*?[]"):
                errors.append(f"repository path must be explicit: {normalized}")
            elif not (root / normalized).exists():
                errors.append(f"missing repository path: {normalized}")
    return errors


def main() -> int:
    errors = validate_architecture_docs(Path.cwd())
    if errors:
        print("\n".join(errors))
        return 1
    print("Architecture document links and source paths are valid.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
