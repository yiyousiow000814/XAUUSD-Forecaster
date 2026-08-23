from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def _checker_module():
    path = Path(__file__).resolve().parents[1] / "scripts" / "check_architecture_imports.py"
    spec = importlib.util.spec_from_file_location("check_architecture_imports_test", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(spec.name, None)
    return module


def _write(root: Path, relative: str, body: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def test_package_code_cannot_import_scripts(tmp_path) -> None:
    module = _checker_module()
    _write(tmp_path, "xauusd_forecaster/decision/runtime.py", "from scripts import run_forward_collector\n")
    _write(tmp_path, "docs/reference/MODULE_MIGRATION_MAP.md", "# Map\n")

    violations = module.check_architecture_imports(tmp_path)

    assert any("package code imports" in item.message for item in violations)


def test_non_dashboard_owner_cannot_import_dashboard(tmp_path) -> None:
    module = _checker_module()
    _write(tmp_path, "xauusd_forecaster/news/runtime.py", "from xauusd_forecaster.dashboard import status_cache\n")
    _write(tmp_path, "docs/reference/MODULE_MIGRATION_MAP.md", "# Map\n")

    violations = module.check_architecture_imports(tmp_path)

    assert any("may not depend on Dashboard" in item.message for item in violations)


def test_nested_init_rejects_executable_work(tmp_path) -> None:
    module = _checker_module()
    _write(tmp_path, "xauusd_forecaster/news/__init__.py", "CLIENT = object()\n")
    _write(tmp_path, "docs/reference/MODULE_MIGRATION_MAP.md", "# Map\n")

    violations = module.check_architecture_imports(tmp_path)

    assert any("executable side effects" in item.message for item in violations)


def test_documented_thin_shim_cannot_contain_logic(tmp_path) -> None:
    module = _checker_module()
    _write(tmp_path, "xauusd_forecaster/legacy.py", "def execute():\n    return 1\n")
    _write(
        tmp_path,
        "docs/reference/MODULE_MIGRATION_MAP.md",
        "| `xauusd_forecaster/legacy.py` | `xauusd_forecaster/news/runtime.py` | News | THIN_SHIM | remove |\n",
    )

    violations = module.check_architecture_imports(tmp_path)

    assert any("THIN_SHIM contains executable logic" in item.message for item in violations)


def test_migration_map_paths_must_be_explicit_and_exist(tmp_path) -> None:
    module = _checker_module()
    _write(tmp_path, "xauusd_forecaster/__init__.py", '"""Package."""\n')
    _write(
        tmp_path,
        "docs/reference/MODULE_MIGRATION_MAP.md",
        "| `xauusd_forecaster/missing.py` | `xauusd_forecaster/news/runtime.py` | News | NONE | remove |\n",
    )

    violations = module.check_architecture_imports(tmp_path)

    assert any("migration map path does not exist" in item.message for item in violations)
