import ast
from collections import Counter
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

import pytest


ROOT = Path(__file__).resolve().parents[1]


WINDOWS_MANIFEST = json.loads(
    (ROOT / ".github" / "windows-runtime-shards.json").read_text(encoding="utf-8")
)
PYTHON_MANIFEST = json.loads(
    (ROOT / ".github" / "python-test-shards.json").read_text(encoding="utf-8")
)


def _top_level_tests(relative: str) -> list[str]:
    tree = ast.parse((ROOT / relative).read_text(encoding="utf-8"))
    return [
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name.startswith("test_")
    ]


def _owned_tests(spec: dict[str, str]) -> list[str]:
    names = _top_level_tests(spec["path"])
    if "from" not in spec and "through" not in spec:
        return [f'{spec["path"]}::{name}' for name in names]
    assert set(spec) >= {"path", "from", "through"}
    start = names.index(spec["from"])
    end = names.index(spec["through"])
    assert start <= end
    return [f'{spec["path"]}::{name}' for name in names[start : end + 1]]


def test_windows_runtime_gate_is_parallel_bounded_and_keeps_required_name() -> None:
    workflow = (
        ROOT / ".github" / "workflows" / "windows-runtime-gates.yml"
    ).read_text(encoding="utf-8")
    timeouts = [int(value) for value in re.findall(r"timeout-minutes:\s*(\d+)", workflow)]
    assert timeouts and max(timeouts) <= 5
    assert "matrix: ${{ fromJSON(needs.plan.outputs.matrix) }}" in workflow
    assert "fail-fast: false" in workflow
    assert "runs-on: ${{ matrix.runner }}" in workflow
    assert "cancel-in-progress: true" in workflow
    assert "windows-runtime-pr-${{ github.event.pull_request.number || github.ref }}" in workflow
    assert workflow.count("name: Windows runtime contracts") == 1
    assert "needs: [plan, shards]" in workflow
    assert "needs.shards.result" in workflow
    assert "tests/test_runtime_launchers.py" not in workflow
    assert "pytest==9.1.1 pytest-timeout==2.4.0" in workflow


def test_windows_runtime_manifest_assigns_every_required_test_exactly_once() -> None:
    expected: set[str] = set()
    for relative in (
        "tests/test_runtime_health.py",
        "tests/test_news_scheduler.py",
        "tests/test_runtime_root_ownership.py",
        "tests/test_cross_version_runtime_recovery.py",
        "tests/test_runtime_launchers.py",
            "tests/test_control_plane_install.py",
            "tests/test_release_evidence_nodes.py",
            "tests/test_release_runtime_read_model.py",
            "tests/test_recovery_hotfix.py",
            "tests/test_watchdog_singleton.py",
        ):
        expected.update(f"{relative}::{name}" for name in _top_level_tests(relative))
    expected.add(
        "tests/test_artifact_path_migration.py::"
        "test_real_repair_entrypoint_preserves_source_authority_and_fails_closed"
    )

    assignments = Counter(
        nodeid
        for shard in WINDOWS_MANIFEST["shards"]
        for spec in shard["tests"]
        for nodeid in _owned_tests(spec)
    )
    assert set(assignments) == expected
    assert {nodeid: count for nodeid, count in assignments.items() if count != 1} == {}
    assert {shard["family"] for shard in WINDOWS_MANIFEST["shards"]} == {
        "windows-runtime-core",
        "windows-runtime-paths",
        "windows-runtime-release",
        "windows-runtime-artifact-repair",
        "windows-runtime-cross-version",
    }
    shard_by_id = {shard["id"]: shard for shard in WINDOWS_MANIFEST["shards"]}
    assert "compatibility" not in shard_by_id
    assert {
        "compatibility-release",
        "compatibility-boundaries",
    }.issubset(shard_by_id)
    compatibility_assignments = {
        nodeid
        for shard_id in ("compatibility-release", "compatibility-boundaries")
        for spec in shard_by_id[shard_id]["tests"]
        for nodeid in _owned_tests(spec)
    }
    assert len(compatibility_assignments) == 79


def test_windows_runtime_selector_uses_authoritative_impact_map(monkeypatch) -> None:
    spec = importlib.util.spec_from_file_location(
        "select_windows_runtime_shards",
        ROOT / "scripts" / "select_windows_runtime_shards.py",
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    monkeypatch.setattr(
        module, "_changed_paths", lambda _base: [".github/windows-runtime-shards.json"]
    )
    assert {item["id"] for item in module.select("base")} == {
        shard["id"] for shard in WINDOWS_MANIFEST["shards"]
    }
    monkeypatch.setattr(module, "_changed_paths", lambda _base: ["web/app/page.tsx"])
    assert module.select("base") == [
        {"id": "no-windows-impact", "runner": "ubuntu-latest"}
    ]
    monkeypatch.setattr(
        module, "_changed_paths", lambda _base: ["scripts/worker_cpu_evidence.ps1"]
    )
    assert {item["id"] for item in module.select("base")} == {
        "evidence",
        "provider-adapters",
        "transaction",
    }
    owner_shards = {
        "scripts/control_center_persistence_gateway.ps1": "persistence",
        "scripts/control_center_provider_adapters.ps1": "provider-adapters",
        "scripts/control_center_runtime_supervision.ps1": "runtime-supervision",
        "scripts/control_center_evidence_authority.ps1": "evidence",
        "scripts/control_center_transaction_engine.ps1": "transaction",
        "scripts/control_center_recovery_engine.ps1": "recovery",
        "scripts/control_center_install.ps1": "control-install",
        "scripts/control_center_presentation.ps1": "presentation",
    }
    for owner_path, owner_shard in owner_shards.items():
        monkeypatch.setattr(module, "_changed_paths", lambda _base, p=owner_path: [p])
        selected = {item["id"] for item in module.select("base")}
        assert {"facade-composition", owner_shard}.issubset(selected)


def test_windows_runtime_runner_emits_bounded_machine_evidence() -> None:
    runner = (ROOT / "scripts" / "run_windows_runtime_shard.py").read_text(
        encoding="utf-8"
    )
    for contract in (
        '"--timeout=30"',
        '"--durations=30"',
        '"schema_version": "windows-runtime-shard-result-v1"',
        '"elapsed_seconds"',
        '"test_selectors"',
    ):
        assert contract in runner


def test_python_gate_is_parallel_bounded_and_keeps_required_name() -> None:
    workflow = (
        ROOT / ".github" / "workflows" / "quality-gates.yml"
    ).read_text(encoding="utf-8")
    timeouts = [int(value) for value in re.findall(r"timeout-minutes:\s*(\d+)", workflow)]
    assert timeouts and max(timeouts) <= 5
    assert "matrix: ${{ fromJSON(needs.plan-python.outputs.matrix) }}" in workflow
    assert "fail-fast: false" in workflow
    assert "cancel-in-progress: true" in workflow
    assert workflow.count("name: Python regression suite") == 1
    assert "needs: [plan-python, python-shards]" in workflow
    assert "needs.python-shards.result" in workflow
    assert "python -m pytest -q" not in workflow
    assert "pytest==9.1.1 pytest-timeout==2.4.0" in workflow


def test_web_gate_keeps_complete_suite_and_uses_bounded_lockfile_install() -> None:
    workflow = (
        ROOT / ".github" / "workflows" / "quality-gates.yml"
    ).read_text(encoding="utf-8")
    timeouts = [int(value) for value in re.findall(r"timeout-minutes:\s*(\d+)", workflow)]
    assert timeouts and max(timeouts) <= 5
    assert workflow.count("name: Web build and tests") == 1
    assert "npm install --global npm@11.6.2" in workflow
    assert "python ../scripts/run_bounded_web_install.py" in workflow
    assert "--timeout-seconds 150" in workflow
    assert "--kill-grace-seconds 10" in workflow
    assert "python -m pip install -e .." in workflow
    assert workflow.count("run: npm test") == 1

    runner = (ROOT / "scripts" / "run_bounded_web_install.py").read_text(
        encoding="utf-8"
    )
    for contract in (
        '"ci"',
        '"--prefer-offline"',
        '"--no-audit"',
        '"--no-fund"',
        '"--fetch-retries=1"',
        '"--fetch-retry-mintimeout=1000"',
        '"--fetch-retry-maxtimeout=5000"',
        '"--fetch-timeout=30000"',
        '"WEB_DEPENDENCY_INSTALL_TIMEOUT"',
        "package_lock_sha256=",
        "npm_cache_path=",
        "web_dependency_install_elapsed_seconds=",
    ):
        assert contract in runner
    assert "--ignore-scripts" not in runner
    assert "npm install" not in runner
    assert "package-lock-only" not in runner


def test_bounded_web_install_timeout_terminates_process_tree(tmp_path: Path) -> None:
    spec = importlib.util.spec_from_file_location(
        "run_bounded_web_install", ROOT / "scripts" / "run_bounded_web_install.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    child_pid_path = tmp_path / "child.pid"
    helper = tmp_path / "tree.py"
    helper.write_text(
        "import pathlib, subprocess, sys, time\n"
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        "pathlib.Path(sys.argv[1]).write_text(str(child.pid), encoding='ascii')\n"
        "print('tree-ready', flush=True)\n"
        "time.sleep(60)\n",
        encoding="utf-8",
    )
    return_code, tail, elapsed, timed_out = module.run_bounded_command(
        [sys.executable, str(helper), str(child_pid_path)],
        cwd=tmp_path,
        timeout_seconds=1,
        kill_grace_seconds=2,
    )
    assert return_code == 124
    assert timed_out is True
    assert elapsed < 6
    assert "tree-ready" in tail
    child_pid = int(child_pid_path.read_text(encoding="ascii"))
    time.sleep(0.2)
    if os.name == "nt":
        probe = subprocess.run(
            ["tasklist", "/FI", f"PID eq {child_pid}", "/FO", "CSV", "/NH"],
            check=False,
            capture_output=True,
            text=True,
        )
        assert str(child_pid) not in probe.stdout
    else:
        with pytest.raises(ProcessLookupError):
            os.kill(child_pid, 0)


def test_bounded_web_install_propagates_success_and_failure(tmp_path: Path) -> None:
    spec = importlib.util.spec_from_file_location(
        "run_bounded_web_install", ROOT / "scripts" / "run_bounded_web_install.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    success = module.run_bounded_command(
        [sys.executable, "-c", "print('complete-suite-ready')"],
        cwd=tmp_path,
        timeout_seconds=5,
        kill_grace_seconds=1,
    )
    failure = module.run_bounded_command(
        [sys.executable, "-c", "raise SystemExit(23)"],
        cwd=tmp_path,
        timeout_seconds=5,
        kill_grace_seconds=1,
    )
    assert success[0] == 0 and success[3] is False
    assert success[1] == ("complete-suite-ready",)
    assert failure[0] == 23 and failure[3] is False


def test_python_shard_manifest_assigns_every_platform_test_file_exactly_once() -> None:
    excluded = {
        "tests/test_runtime_launchers.py",
        "tests/test_control_plane_install.py",
        "tests/test_release_runtime_read_model.py",
        "tests/test_recovery_hotfix.py",
        "tests/test_watchdog_singleton.py",
    }
    expected = {
        path.relative_to(ROOT).as_posix()
        for path in (ROOT / "tests").glob("test_*.py")
    } - excluded
    assignments = Counter(
        path
        for shard in PYTHON_MANIFEST["shards"]
        for path in shard["tests"]
    )

    assert PYTHON_MANIFEST["schema_version"] == "python-test-shards-v1"
    assert set(assignments) == expected
    assert {path: count for path, count in assignments.items() if count != 1} == {}
    assert len({shard["id"] for shard in PYTHON_MANIFEST["shards"]}) == len(
        PYTHON_MANIFEST["shards"]
    )


def test_python_shard_selector_and_runner_use_authoritative_manifest() -> None:
    selector_spec = importlib.util.spec_from_file_location(
        "select_python_test_shards",
        ROOT / "scripts" / "select_python_test_shards.py",
    )
    assert selector_spec and selector_spec.loader
    selector = importlib.util.module_from_spec(selector_spec)
    selector_spec.loader.exec_module(selector)
    assert selector.matrix() == [
        {"id": shard["id"]} for shard in PYTHON_MANIFEST["shards"]
    ]

    runner_spec = importlib.util.spec_from_file_location(
        "run_python_test_shard", ROOT / "scripts" / "run_python_test_shard.py",
    )
    assert runner_spec and runner_spec.loader
    runner = importlib.util.module_from_spec(runner_spec)
    runner_spec.loader.exec_module(runner)
    first = PYTHON_MANIFEST["shards"][0]
    assert runner.shard_paths(first["id"]) == first["tests"]
    with pytest.raises(ValueError, match="unknown or duplicate"):
        runner.shard_paths("not-a-shard")

    source = (ROOT / "scripts" / "run_python_test_shard.py").read_text(
        encoding="utf-8"
    )
    for contract in (
        '"--timeout=30"',
        '"--durations=30"',
        '"schema_version": "python-test-shard-result-v1"',
        '"elapsed_seconds"',
        '"test_paths"',
    ):
        assert contract in source


_PUBLICATION_CLOCK_NAMES = frozenset({
    "published", "published_at", "published_time", "source_published_time",
})
_RECEIPT_CLOCK_NAMES = frozenset({
    "received", "received_at", "first_seen", "first_seen_time",
    "collector_first_seen_time",
})
_OBSERVATION_CLOCK_NAMES = frozenset({
    "fetched", "fetched_at", "observed", "observed_at",
})


def _name_clock_origins(name: str) -> set[str]:
    if name in _PUBLICATION_CLOCK_NAMES:
        return {"publication"}
    if name in _RECEIPT_CLOCK_NAMES:
        return {"receipt"}
    if name in _OBSERVATION_CLOCK_NAMES:
        return {"observation"}
    return set()


def _clock_origins(node: ast.AST, aliases: dict[str, set[str]]) -> set[str]:
    if node is None:
        return set()
    if isinstance(node, ast.Name):
        if node.id in aliases:
            return aliases[node.id]
        return _name_clock_origins(node.id)
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        if node.value == "source_published_time":
            return {"publication"}
        if node.value == "collector_first_seen_time":
            return {"receipt"}
        return set()
    if isinstance(node, (ast.Dict, ast.List, ast.Set)):
        return set()
    origins: set[str] = set()
    for child in ast.iter_child_nodes(node):
        origins.update(_clock_origins(child, aliases))
    return origins


def _scope_nodes(scope: ast.AST) -> list[ast.AST]:
    nodes: list[ast.AST] = []

    def visit(node: ast.AST) -> None:
        nodes.append(node)
        for child in ast.iter_child_nodes(node):
            if child is not scope and isinstance(
                child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef),
            ):
                continue
            visit(child)

    visit(scope)
    return nodes


def _assigned_names(target: ast.AST) -> set[str]:
    if isinstance(target, ast.Name):
        return {target.id}
    if isinstance(target, (ast.Tuple, ast.List)):
        return {
            name
            for element in target.elts
            for name in _assigned_names(element)
        }
    return set()


def _has_explicit_skew_threshold(node: ast.AST) -> bool:
    for child in ast.walk(node):
        if isinstance(child, ast.Call) and isinstance(
            child.func, (ast.Name, ast.Attribute),
        ):
            name = (
                child.func.id if isinstance(child.func, ast.Name)
                else child.func.attr
            )
            if name == "timedelta":
                return True
        if isinstance(child, ast.Name) and any(
            marker in child.id.lower()
            for marker in ("clock_skew", "future_tolerance")
        ):
            return True
    return False


def _publication_receipt_comparisons(tree: ast.AST) -> list[int]:
    offenders: list[int] = []
    scopes = [tree, *(
        node for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    )]
    for scope in scopes:
        nodes = _scope_nodes(scope)
        aliases: dict[str, set[str]] = {}
        if isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for argument in (*scope.args.posonlyargs, *scope.args.args,
                             *scope.args.kwonlyargs):
                aliases[argument.arg] = _name_clock_origins(argument.arg)
        changed = True
        while changed:
            changed = False
            for node in nodes:
                if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                    continue
                value_origins = _clock_origins(node.value, aliases)
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for target in targets:
                    for name in _assigned_names(target):
                        prior = aliases.setdefault(name, set())
                        if not value_origins.issubset(prior):
                            prior.update(value_origins)
                            changed = True
        for node in nodes:
            if not isinstance(node, ast.Compare):
                continue
            origins = _clock_origins(node, aliases)
            if (
                {"publication", "receipt"}.issubset(origins)
                or (
                    {"publication", "observation"}.issubset(origins)
                    and _has_explicit_skew_threshold(node)
                )
            ):
                offenders.append(node.lineno)
    return sorted(set(offenders))


def test_publication_receipt_comparison_is_owned_by_news_time() -> None:
    """Reject aliased publication/receipt policy comparisons outside the owner."""
    offenders: list[str] = []
    for root in (ROOT / "xauusd_forecaster", ROOT / "scripts"):
        for path in root.rglob("*.py"):
            if path == ROOT / "xauusd_forecaster" / "news_time.py":
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            offenders.extend(
                f"{path.relative_to(ROOT)}:{line}"
                for line in _publication_receipt_comparisons(tree)
            )

    assert offenders == [], (
        "publication-vs-receipt admission belongs in news_time.py: "
        + ", ".join(offenders)
    )


def test_publication_receipt_guard_follows_local_aliases() -> None:
    tree = ast.parse(
        """
def bypass(row):
    published = row["source_published_time"]
    received = row["collector_first_seen_time"]
    if published > received:
        return False
    return True
"""
    )

    assert _publication_receipt_comparisons(tree) == [5]


def test_publication_receipt_guard_catches_fetched_at_tolerance() -> None:
    tree = ast.parse(
        """
def bypass(record, fetched_at):
    published = record["source_published_time"]
    if published > fetched_at + timedelta(minutes=5):
        return False
    return True
"""
    )

    assert _publication_receipt_comparisons(tree) == [4]
