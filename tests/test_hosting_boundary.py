from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
AUTOMATION_ROOTS = (
    ROOT / ".github" / "workflows",
    ROOT / "ctrader",
    ROOT / "scripts",
    ROOT / "web",
    ROOT / "xauusd_forecaster",
)
TEXT_SUFFIXES = {".js", ".json", ".mjs", ".ps1", ".py", ".sh", ".ts", ".tsx", ".yaml", ".yml"}


def _automation_files() -> list[Path]:
    return sorted(
        path
        for root in AUTOMATION_ROOTS
        if root.exists()
        for path in root.rglob("*")
        if path.is_file()
        and path.suffix.lower() in TEXT_SUFFIXES
        and not {".next", ".open-next", "node_modules"}.intersection(path.parts)
    )


def test_repository_automation_cannot_create_github_deployments() -> None:
    forbidden = {
        "GitHub Actions environment": re.compile(r"(?m)^\s*environment\s*:"),
        "GitHub deployments write permission": re.compile(
            r"(?m)^\s*deployments\s*:\s*write\s*(?:#.*)?$",
            re.IGNORECASE,
        ),
        "GitHub Deployments API": re.compile(
            r"(?:repos/[^\s'\"`]+/deployments\b|/deployments\b)",
            re.IGNORECASE,
        ),
    }
    violations: list[str] = []

    for path in _automation_files():
        text = path.read_text(encoding="utf-8", errors="replace")
        for boundary, pattern in forbidden.items():
            if pattern.search(text):
                violations.append(f"{path.relative_to(ROOT)}: {boundary}")

    assert not violations, (
        "GitHub Deployments and Environments are outside the Cloudflare-only "
        "hosting boundary:\n" + "\n".join(violations)
    )
