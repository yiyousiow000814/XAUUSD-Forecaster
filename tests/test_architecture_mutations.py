from __future__ import annotations

import sys
from pathlib import Path

import pytest

from architecture_tools.mutations import (
    Mutation,
    MutationAuditError,
    _remove_shared_web_dependencies,
    _run,
    _share_web_dependencies,
    _validated_source,
    build_report,
    load_mutations,
)


ROOT = Path(__file__).resolve().parents[1]


def fixture_mutation(**overrides) -> Mutation:
    values = {
        "mutation_id": "MUT-FIXTURE", "contract_id": "CONTRACT",
        "platform": "PYTHON", "path": "fixture.py",
        "selector_kind": "python_symbol", "symbol": "protected",
        "operator": "replace_exact", "before": "return True", "after": "return False",
        "expected_break": "fixture", "command": (sys.executable, "-c", "pass"),
        "timeout_seconds": 2, "smoke": True, "failure_pattern": "",
        "isolation": "TEMPORARY_GIT_WORKTREE",
    }
    values.update(overrides)
    return Mutation(**values)


def test_registry_is_bounded_cross_platform_and_contains_required_pilot() -> None:
    mutations = load_mutations(ROOT)
    assert len(mutations) >= 12
    assert sum(item.smoke for item in mutations) >= 6
    assert {item.platform for item in mutations} == {"PYTHON", "WEB", "WINDOWS"}
    assert any(item.smoke and item.platform == "WEB" for item in mutations)
    assert any("NEWS" in item.mutation_id or "BROADCAST" in item.mutation_id for item in mutations)


def test_selector_requires_one_exact_context_inside_named_symbol() -> None:
    source = "def protected():\n    return True\n\ndef sibling():\n    return 1\n"
    assert "return False" in _validated_source(source, fixture_mutation())
    with pytest.raises(MutationAuditError, match="exact context"):
        _validated_source(source, fixture_mutation(before="return missing"))


def test_process_classification_distinguishes_survived_killed_and_timeout(tmp_path) -> None:
    survived = _run((sys.executable, "-c", "pass"), tmp_path, 2)
    killed = _run((sys.executable, "-c", "raise AssertionError('contract')"), tmp_path, 2)
    timeout = _run((sys.executable, "-c", "import time; time.sleep(2)"), tmp_path, 1)
    assert survived[0] == "SURVIVED"
    assert killed[0] == "KILLED"
    assert timeout[0] == "TIMEOUT"


def test_web_mutation_worktree_uses_the_locked_dependency_tree(tmp_path) -> None:
    root = tmp_path / "source"
    worktree = tmp_path / "isolated"
    (root / "web/node_modules/typescript").mkdir(parents=True)
    (worktree / "web").mkdir(parents=True)

    _share_web_dependencies(root, worktree)

    linked = worktree / "web/node_modules"
    assert (linked / "typescript").is_dir()
    assert linked.samefile(root / "web/node_modules")

    _remove_shared_web_dependencies(worktree)

    assert not linked.exists()
    assert (root / "web/node_modules/typescript").is_dir()


def test_report_never_hides_surviving_or_invalid_mutants(tmp_path) -> None:
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests/test_sample.py").write_text(
        "def test_a():\n    assert 1\n\ndef test_b():\n    assert 1\n", encoding="utf-8"
    )
    report = build_report(tmp_path, [
        {"id": "k", "contract_id": "A", "platform": "PYTHON", "outcome": "KILLED"},
        {"id": "s", "contract_id": "B", "platform": "WEB", "outcome": "SURVIVED"},
        {"id": "i", "contract_id": "C", "platform": "WINDOWS", "outcome": "INVALID"},
    ], "digest")
    assert report["counts"]["KILLED"] == 1
    assert report["counts"]["SURVIVED"] == 1
    assert {row["contract_id"]: row["status"] for row in report["contracts"]} == {
        "A": "MUTATION_KILLED", "B": "SURVIVING_MUTATION", "C": "NO_VALID_KILL",
    }
    assert report["duplicate_test_ast_fingerprints"]
