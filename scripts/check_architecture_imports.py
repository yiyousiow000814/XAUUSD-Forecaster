#!/usr/bin/env python
"""Enforce canonical Python import and compatibility boundaries."""

from __future__ import annotations

import argparse
import ast
import re
from dataclasses import dataclass
from pathlib import Path


PACKAGE_ROOT = Path("xauusd_forecaster")
SCRIPTS_ROOT = Path("scripts")
MIGRATION_MAP = Path("docs/reference/MODULE_MIGRATION_MAP.md")
TRANSITIONAL_SCRIPT_IMPORTS = frozenset()
CANONICAL_AREAS = frozenset({
    "ai", "assistant", "dashboard", "decision", "evidence", "news",
    "runtime", "training",
})
NON_DASHBOARD_AREAS = CANONICAL_AREAS - {"dashboard"}


@dataclass(frozen=True)
class ImportViolation:
    path: Path
    line: int
    message: str

    def render(self) -> str:
        return f"{self.path.as_posix()}:{self.line}: {self.message}"


def _module_name(path: Path, root: Path) -> str:
    relative = path.relative_to(root).with_suffix("")
    parts = list(relative.parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _expanded_imports(node: ast.Import | ast.ImportFrom) -> list[str]:
    if isinstance(node, ast.Import):
        return [alias.name for alias in node.names]
    module = node.module or ""
    if module == "scripts":
        return [f"scripts.{alias.name}" for alias in node.names]
    return [module] if module else []


def _canonical_area(module: str) -> str | None:
    parts = module.split(".")
    if len(parts) >= 2 and parts[0] == "xauusd_forecaster" and parts[1] in CANONICAL_AREAS:
        return parts[1]
    return None


def _thin_shim_modules(root: Path) -> set[str]:
    path = root / MIGRATION_MAP
    if not path.is_file():
        return set()
    shims: set[str] = set()
    pattern = re.compile(r"`(xauusd_forecaster/[^`]+\.py)`[^\n]*\|\s*THIN_SHIM\s*\|", re.I)
    for value in pattern.findall(path.read_text(encoding="utf-8")):
        shims.add(value[:-3].replace("/", "."))
    return shims


def _init_is_side_effect_free(tree: ast.Module) -> bool:
    for node in tree.body:
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            continue
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            continue
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if all(isinstance(target, ast.Name) and target.id == "__all__" for target in targets):
                continue
        return False
    return True


def _shim_is_thin(tree: ast.Module) -> bool:
    return _init_is_side_effect_free(tree)


def check_architecture_imports(root: Path) -> list[ImportViolation]:
    root = root.resolve()
    violations: list[ImportViolation] = []
    shim_modules = _thin_shim_modules(root)
    migration_path = root / MIGRATION_MAP
    if migration_path.is_file():
        documented_paths = re.findall(
            r"`((?:xauusd_forecaster|scripts|tests|docs)/[^` )]+)`",
            migration_path.read_text(encoding="utf-8"),
        )
        for value in sorted(set(documented_paths)):
            if not (root / value).exists():
                violations.append(ImportViolation(
                    MIGRATION_MAP, 1, f"migration map path does not exist: {value}",
                ))
    source_paths = sorted((root / PACKAGE_ROOT).rglob("*.py")) + sorted((root / SCRIPTS_ROOT).glob("*.py"))

    for path in source_paths:
        relative = path.relative_to(root)
        module = _module_name(path, root)
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(relative))
        area = _canonical_area(module)

        if path.name == "__init__.py" and not _init_is_side_effect_free(tree):
            violations.append(ImportViolation(relative, 1, "package __init__.py has executable side effects"))

        if module in shim_modules and not _shim_is_thin(tree):
            violations.append(ImportViolation(relative, 1, "documented THIN_SHIM contains executable logic"))

        for node in ast.walk(tree):
            if not isinstance(node, (ast.Import, ast.ImportFrom)):
                continue
            for imported in _expanded_imports(node):
                if module.startswith("xauusd_forecaster") and imported.startswith("scripts"):
                    violations.append(ImportViolation(relative, node.lineno, "package code imports a runtime entry-point script"))
                if module.startswith("scripts.") and imported.startswith("scripts.") and (module, imported) not in TRANSITIONAL_SCRIPT_IMPORTS:
                    violations.append(ImportViolation(relative, node.lineno, f"prohibited script shared-library import: {imported}"))
                target_area = _canonical_area(imported)
                if area in NON_DASHBOARD_AREAS and target_area == "dashboard":
                    violations.append(ImportViolation(relative, node.lineno, f"{area} may not depend on Dashboard"))
                if area == "decision" and target_area == "assistant":
                    violations.append(ImportViolation(relative, node.lineno, "Decision may not depend on Assistant"))
                if area is not None and imported in shim_modules:
                    violations.append(ImportViolation(relative, node.lineno, f"canonical code imports legacy shim: {imported}"))
    return violations


def main() -> int:
    parser = argparse.ArgumentParser(description="Check canonical Python dependency boundaries")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    args = parser.parse_args()
    violations = check_architecture_imports(args.root)
    if violations:
        for violation in violations:
            print(violation.render())
        return 1
    print("Architecture import boundaries passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
