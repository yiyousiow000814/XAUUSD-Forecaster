from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from scripts.architecture_compiler import (
    ArchitectureCompileError,
    build_artifacts,
    calculate_digest,
    dependency_evidence,
    extract_csharp,
    extract_powershell,
    extract_python,
    extract_typescript,
    generated_differences,
    require_cardinality,
    source_files,
    validate_writer_authority,
    write_artifacts,
)


ROOT = Path(__file__).resolve().parents[1]


def _write(root: Path, relative: str, body: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def test_source_digest_is_independent_of_checkout_line_endings(tmp_path: Path) -> None:
    path = tmp_path / "contract.py"
    path.write_bytes(b"first\nsecond\n")
    lf = calculate_digest(tmp_path, [path])
    path.write_bytes(b"first\r\nsecond\r\n")
    crlf = calculate_digest(tmp_path, [path])
    assert lf == crlf


def test_source_inventory_excludes_local_hidden_workspaces(tmp_path: Path) -> None:
    _write(tmp_path, "scripts/owner.py", "OWNER = 'repository'\n")
    _write(tmp_path, ".local/operator_probe.py", "TOKEN = 'local-only'\n")
    _write(tmp_path, ".codex/scratch.py", "VALUE = 'scratch'\n")
    assert [path.relative_to(tmp_path).as_posix() for path in source_files(tmp_path)] == ["scripts/owner.py"]


def test_python_module_inventory_follows_add_and_delete(tmp_path: Path) -> None:
    path = tmp_path / "xauusd_forecaster/decision/new_rule.py"
    _write(tmp_path, "xauusd_forecaster/decision/new_rule.py", "def decide():\n    return 'WAIT'\n")
    assert any(fact["type"] == "python_module" and fact["module"].endswith("new_rule") for fact in extract_python(tmp_path))
    path.unlink()
    assert not any(fact.get("module", "").endswith("new_rule") for fact in extract_python(tmp_path))


def test_observed_imports_are_distinct_from_allowed_policy(tmp_path: Path) -> None:
    _write(tmp_path, "xauusd_forecaster/decision/rule.py", "from xauusd_forecaster.evidence import ledger\n")
    report = dependency_evidence(extract_python(tmp_path))
    assert {tuple((item["from"], item["to"])) for item in report["observed"]} == {("decision", "evidence")}
    assert all(item != {"from": "decision", "to": "evidence"} for item in report["allowed_unused"])

    (tmp_path / "xauusd_forecaster/decision/rule.py").write_text("VALUE = 1\n", encoding="utf-8")
    changed = dependency_evidence(extract_python(tmp_path))
    assert changed["observed"] == []
    assert {"from": "decision", "to": "evidence"} in changed["allowed_unused"]


def test_prohibited_import_fails_closed_world_verification(tmp_path: Path) -> None:
    _write(tmp_path, "xauusd_forecaster/evidence/store.py", "from xauusd_forecaster.news import semantics\n")
    report = dependency_evidence(extract_python(tmp_path), closed_world=True)
    assert report["violations"] == [{"from": "evidence", "to": "news"}]


def test_typescript_route_inventory_changes_with_filesystem(tmp_path: Path) -> None:
    route = tmp_path / "web/app/admin/probe/route.ts"
    _write(tmp_path, "web/app/admin/probe/route.ts", "import { x } from './owner';\nexport function GET() { return x; }\n")
    _write(tmp_path, "web/app/admin/probe/owner.ts", "export const x = 1;\n")
    facts = extract_typescript(tmp_path)
    assert any(fact["type"] == "web_api_route" and fact["route"] == "/admin/probe" for fact in facts)
    assert any(fact["type"] == "web_import" and fact["target"] == "./owner" for fact in facts)
    route.unlink()
    assert not any(fact["type"] == "web_api_route" for fact in extract_typescript(tmp_path))


def test_execution_and_sql_operations_have_exact_static_evidence(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "scripts/worker.py",
        "import sqlite3\nfrom threading import Thread\n"
        "db = sqlite3.connect('state.db')\nThread(target=lambda: None)\n"
        "db.execute('INSERT INTO evidence(id) VALUES (1)')\n",
    )
    facts = extract_python(tmp_path)
    assert any(fact["type"] == "python_execution" and fact["name"] == "Thread" for fact in facts)
    assert any(fact["type"] == "sql_operation" and fact["operation"] == "WRITE" and fact["table"] == "evidence" for fact in facts)


def test_second_undeclared_writer_is_rejected(tmp_path: Path) -> None:
    _write(tmp_path, "scripts/a.py", "db.execute('INSERT INTO evidence(id) VALUES (1)')\n")
    _write(tmp_path, "scripts/b.py", "db.execute('UPDATE evidence SET id = 2')\n")
    facts = extract_python(tmp_path)
    with pytest.raises(ArchitectureCompileError, match="undeclared writer"):
        validate_writer_authority(facts, {"evidence": {"scripts/a.py"}})


def test_test_fixture_sql_is_evidence_not_writer_authority(tmp_path: Path) -> None:
    _write(tmp_path, "scripts/a.py", "db.execute('INSERT INTO evidence(id) VALUES (1)')\n")
    facts = extract_python(tmp_path)
    facts.append({
        "type": "d1_sql",
        "operation": "WRITE",
        "table": "evidence",
        "path": "web/tests/reverse-compatibility.test.mjs",
    })
    validate_writer_authority(facts, {"evidence": {"scripts/a.py"}})


def test_binding_cardinality_rejects_zero_and_excessive_matches() -> None:
    facts = [
        {"type": "python_module", "module": "pkg.one", "path": "pkg/one.py"},
        {"type": "python_module", "module": "pkg.two", "path": "pkg/two.py"},
    ]
    with pytest.raises(ArchitectureCompileError, match="matched 0"):
        require_cardinality(facts, {"module": "pkg.missing"}, 1, 1)
    with pytest.raises(ArchitectureCompileError, match="matched 2"):
        require_cardinality(facts, {"type": "python_module"}, 1, 1)


def test_neutral_powershell_and_bounded_csharp_are_honest(tmp_path: Path) -> None:
    _write(tmp_path, "scripts/control.ps1", "function Invoke-Probe {}\nStart-Process probe.exe\n")
    _write(tmp_path, "ctrader/Robot.cs", "namespace Aurum { public class Robot {} }\n")
    powershell = extract_powershell(tmp_path); csharp = extract_csharp(tmp_path)
    assert any(fact["type"] == "powershell_function" and fact["certainty"] == "FALLBACK" for fact in powershell)
    assert any(fact["type"] == "csharp_class" and fact["certainty"] == "BOUNDED" for fact in csharp)


def test_generated_artifacts_are_byte_deterministic_and_source_bound() -> None:
    first = build_artifacts(ROOT); second = build_artifacts(ROOT)
    assert first == second
    digest = json.loads(first["source-digest.json"])["source_digest"]
    assert digest == json.loads(first["code-index.json"])["source_digest"]
    assert digest == json.loads(first["evidence-index.json"])["source_digest"]
    windows = json.loads(first["windows-evidence.json"])
    assert windows["status"] in {"CURRENT", "STALE", "UNAVAILABLE"}
    if os.name == "nt":
        assert windows["facts"]
        assert all(fact["certainty"] == "EXACT" for fact in windows["facts"])


def test_repository_label_is_declared_not_checkout_directory_name(tmp_path: Path) -> None:
    code_index = json.loads(build_artifacts(ROOT)["code-index.json"])
    assert code_index["hierarchy"]["label"] == "XAUUSD-Forecaster"
    assert code_index["hierarchy"]["label"] != tmp_path.name


def test_generated_check_detects_manual_edit(tmp_path: Path) -> None:
    artifacts = {"one.json": b'{"generated":true}\n'}
    write_artifacts(tmp_path, artifacts)
    assert generated_differences(tmp_path, artifacts) == []
    (tmp_path / "architecture/generated/one.json").write_text("manual\n", encoding="utf-8")
    assert generated_differences(tmp_path, artifacts) == ["one.json"]


def test_generated_artifacts_do_not_expose_absolute_workspace_paths() -> None:
    for name, data in build_artifacts(ROOT).items():
        text = data.decode("utf-8")
        assert str(ROOT).replace("\\", "/") not in text, name
        assert "C:\\Users\\" not in text, name

