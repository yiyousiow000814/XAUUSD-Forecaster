"""Bounded, isolated targeted mutation execution for architecture contracts."""

from __future__ import annotations

import ast
import hashlib
import json
import os
import re
import subprocess
import tempfile
import time
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any


OUTCOMES = {"KILLED", "SURVIVED", "INVALID", "TIMEOUT", "ERROR"}


@dataclass(frozen=True)
class Mutation:
    mutation_id: str
    contract_id: str
    platform: str
    path: str
    selector_kind: str
    symbol: str
    operator: str
    before: str
    after: str
    expected_break: str
    command: tuple[str, ...]
    timeout_seconds: int
    smoke: bool
    failure_pattern: str
    isolation: str


class MutationAuditError(RuntimeError):
    pass


def load_mutations(root: Path) -> list[Mutation]:
    document = tomllib.loads(
        (root / "architecture/contracts/mutations.toml").read_text(encoding="utf-8")
    )
    rows = document.get("mutation", [])
    mutations = [Mutation(
        mutation_id=row["id"], contract_id=row["contract_id"],
        platform=row["platform"], path=row["path"],
        selector_kind=row["selector_kind"], symbol=row["symbol"],
        operator=row["operator"], before=row["before"], after=row["after"],
        expected_break=row["expected_break"], command=tuple(row["command"]),
        timeout_seconds=int(row["timeout_seconds"]), smoke=bool(row.get("smoke", False)),
        failure_pattern=row.get("failure_pattern", ""),
        isolation=row["required_isolation"],
    ) for row in rows]
    ids = [item.mutation_id for item in mutations]
    if len(ids) != len(set(ids)):
        raise MutationAuditError("duplicate mutation ID")
    if any(item.operator != "replace_exact" or not item.before or item.before == item.after
           for item in mutations):
        raise MutationAuditError("mutation registry contains an unsupported operator")
    if any(item.isolation != "TEMPORARY_GIT_WORKTREE" for item in mutations):
        raise MutationAuditError("every mutation must require a temporary Git worktree")
    return mutations


def _line_bounds(text: str, first_line: int, last_line: int) -> tuple[int, int]:
    lines = text.splitlines(keepends=True)
    return sum(map(len, lines[:first_line - 1])), sum(map(len, lines[:last_line]))


def _python_symbol_bounds(text: str, symbol: str) -> tuple[int, int]:
    tree = ast.parse(text)
    matches = [node for node in ast.walk(tree)
               if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
               and node.name == symbol]
    if len(matches) != 1:
        raise MutationAuditError(f"Python selector {symbol!r} matched {len(matches)} symbols")
    node = matches[0]
    return _line_bounds(text, node.lineno, node.end_lineno)


def _brace_bounds(text: str, declaration: re.Pattern[str], symbol: str) -> tuple[int, int]:
    matches = list(declaration.finditer(text))
    if len(matches) != 1:
        raise MutationAuditError(f"selector {symbol!r} matched {len(matches)} symbols")
    opening = text.find("{", matches[0].start())
    depth = 0
    for index in range(opening, len(text)):
        if text[index] == "{":
            depth += 1
        elif text[index] == "}":
            depth -= 1
            if depth == 0:
                return matches[0].start(), index + 1
    raise MutationAuditError(f"selector {symbol!r} has no complete extent")


def _validated_source(text: str, mutation: Mutation) -> str:
    if mutation.selector_kind == "python_symbol":
        start, end = _python_symbol_bounds(text, mutation.symbol)
    elif mutation.selector_kind == "python_module_constant":
        tree = ast.parse(text)
        matches = [node for node in tree.body if isinstance(node, (ast.Assign, ast.AnnAssign))
                   and any(isinstance(target, ast.Name) and target.id == mutation.symbol
                           for target in (node.targets if isinstance(node, ast.Assign) else [node.target]))]
        if len(matches) != 1:
            raise MutationAuditError(f"Python constant selector {mutation.symbol!r} matched {len(matches)}")
        node = matches[0]
        start, end = _line_bounds(text, node.lineno, node.end_lineno)
    elif mutation.selector_kind == "typescript_function":
        declaration = re.compile(rf"(?:export\s+)?function\s+{re.escape(mutation.symbol)}\s*\(")
        start, end = _brace_bounds(text, declaration, mutation.symbol)
    elif mutation.selector_kind == "powershell_function":
        declaration = re.compile(rf"(?im)^function\s+{re.escape(mutation.symbol)}\s*\{{")
        start, end = _brace_bounds(text, declaration, mutation.symbol)
    else:
        raise MutationAuditError(f"unsupported selector kind {mutation.selector_kind}")
    extent = text[start:end]
    if extent.count(mutation.before) != 1:
        raise MutationAuditError(
            f"{mutation.mutation_id} exact context matched {extent.count(mutation.before)} times"
        )
    return text[:start] + extent.replace(mutation.before, mutation.after, 1) + text[end:]


def _syntax_valid(root: Path, mutation: Mutation) -> tuple[bool, str]:
    path = root / mutation.path
    if mutation.platform == "PYTHON":
        try:
            ast.parse(path.read_text(encoding="utf-8-sig"), filename=mutation.path)
        except SyntaxError as error:
            return False, f"SyntaxError:{error.msg}"
    elif mutation.platform == "WINDOWS":
        escaped_path = str(path).replace("'", "''")
        script = (
            "$e=$null;$t=$null;[System.Management.Automation.Language.Parser]::ParseFile("
            f"'{escaped_path}',[ref]$t,[ref]$e)|Out-Null;"
            "if($e.Count){$e|ForEach-Object Message;exit 1}"
        )
        result = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
                                capture_output=True, text=True, timeout=30)
        if result.returncode:
            return False, (result.stdout + result.stderr).strip()[:500]
    return True, ""


def _run(command: tuple[str, ...], cwd: Path, timeout: int) -> tuple[str, int | None, str, int]:
    started = time.perf_counter()
    try:
        result = subprocess.run(command, cwd=cwd, capture_output=True, text=True,
                                encoding="utf-8", errors="replace", timeout=timeout)
    except subprocess.TimeoutExpired as error:
        output = ((error.stdout or "") + (error.stderr or ""))[-2_000:]
        return "TIMEOUT", None, output, round((time.perf_counter() - started) * 1000)
    output = (result.stdout + result.stderr)[-4_000:]
    return (
        "SURVIVED" if result.returncode == 0 else "KILLED",
        result.returncode,
        output,
        round((time.perf_counter() - started) * 1000),
    )


def _share_web_dependencies(root: Path, worktree: Path) -> None:
    """Expose the lockfile-installed Web dependencies inside an isolated worktree."""
    source = root / "web" / "node_modules"
    target = worktree / "web" / "node_modules"
    if not source.is_dir():
        raise MutationAuditError("Web mutation dependencies are not installed")
    try:
        if os.name == "nt":
            result = subprocess.run(
                ["cmd.exe", "/d", "/c", "mklink", "/J", str(target), str(source)],
                capture_output=True,
                text=True,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            if result.returncode:
                raise OSError((result.stdout + result.stderr).strip())
        else:
            target.symlink_to(source, target_is_directory=True)
    except OSError as error:
        raise MutationAuditError(
            f"could not expose Web mutation dependencies: {error}"
        ) from error


def _remove_shared_web_dependencies(worktree: Path) -> None:
    """Remove only the task-owned link without traversing installed dependencies."""
    target = worktree / "web" / "node_modules"
    if target.is_symlink():
        target.unlink()
    elif target.exists():
        # Windows directory junctions are reparse points but Path.is_symlink()
        # is false. rmdir removes the junction itself and never its target.
        os.rmdir(target)


def execute_mutation(root: Path, mutation: Mutation) -> dict[str, Any]:
    baseline, baseline_code, baseline_output, baseline_ms = _run(
        mutation.command, root / ("web" if mutation.platform == "WEB" else ""),
        mutation.timeout_seconds,
    )
    if baseline != "SURVIVED":
        return {"id": mutation.mutation_id, "contract_id": mutation.contract_id,
                "platform": mutation.platform, "outcome": "ERROR",
                "reason": "BASELINE_FAILED", "baseline_duration_ms": baseline_ms,
                "failure_signature": baseline_output[-500:]}
    original_status = subprocess.run(["git", "status", "--porcelain"], cwd=root,
                                     capture_output=True, text=True, check=True).stdout
    with tempfile.TemporaryDirectory(prefix="architecture-mutation-") as temporary:
        worktree = Path(temporary) / "repo"
        add = subprocess.run(["git", "worktree", "add", "--detach", str(worktree), "HEAD"],
                             cwd=root, capture_output=True, text=True)
        if add.returncode:
            return {"id": mutation.mutation_id, "contract_id": mutation.contract_id,
                    "platform": mutation.platform, "outcome": "ERROR",
                    "reason": "WORKTREE_ADD_FAILED", "failure_signature": add.stderr[-500:]}
        try:
            if mutation.platform == "WEB":
                try:
                    _share_web_dependencies(root, worktree)
                except MutationAuditError as error:
                    return {"id": mutation.mutation_id, "contract_id": mutation.contract_id,
                            "platform": mutation.platform, "outcome": "ERROR",
                            "reason": "DEPENDENCY_ISOLATION_FAILED",
                            "failure_signature": str(error)[:500]}
            target = worktree / mutation.path
            text = target.read_text(encoding="utf-8-sig")
            try:
                mutated = _validated_source(text, mutation)
            except (MutationAuditError, SyntaxError) as error:
                return {"id": mutation.mutation_id, "contract_id": mutation.contract_id,
                        "platform": mutation.platform, "outcome": "INVALID",
                        "reason": "SELECTOR_INVALID", "failure_signature": str(error)[:500]}
            target.write_text(mutated, encoding="utf-8", newline="")
            if target.read_text(encoding="utf-8") == text:
                return {"id": mutation.mutation_id, "contract_id": mutation.contract_id,
                        "platform": mutation.platform, "outcome": "INVALID",
                        "reason": "NO_SOURCE_CHANGE", "failure_signature": ""}
            valid, signature = _syntax_valid(worktree, mutation)
            if not valid:
                return {"id": mutation.mutation_id, "contract_id": mutation.contract_id,
                        "platform": mutation.platform, "outcome": "INVALID",
                        "reason": "SYNTAX_INVALID", "failure_signature": signature}
            outcome, code, output, duration = _run(
                mutation.command, worktree / ("web" if mutation.platform == "WEB" else ""),
                mutation.timeout_seconds,
            )
            if outcome == "KILLED" and mutation.failure_pattern and not re.search(
                mutation.failure_pattern, output, re.I | re.S
            ):
                outcome = "ERROR"; reason = "UNEXPECTED_FAILURE_SIGNATURE"
            else:
                reason = "FOCUSED_TEST_FAILED" if outcome == "KILLED" else (
                    "FOCUSED_TEST_PASSED" if outcome == "SURVIVED" else "TIME_LIMIT_EXCEEDED"
                )
            signature = re.sub(r"[A-Za-z]:[/\\][^\r\n]+", "<path>", output)[-700:]
            return {"id": mutation.mutation_id, "contract_id": mutation.contract_id,
                    "platform": mutation.platform, "outcome": outcome, "reason": reason,
                    "exit_code": code, "baseline_duration_ms": baseline_ms,
                    "mutation_duration_ms": duration, "failure_signature": signature}
        finally:
            if mutation.platform == "WEB":
                _remove_shared_web_dependencies(worktree)
            subprocess.run(["git", "worktree", "remove", "--force", str(worktree)],
                           cwd=root, capture_output=True, text=True)
            final_status = subprocess.run(["git", "status", "--porcelain"], cwd=root,
                                          capture_output=True, text=True, check=True).stdout
            if final_status != original_status:
                raise MutationAuditError("original worktree changed during mutation execution")


def normalized_test_fingerprints(root: Path) -> list[dict[str, Any]]:
    groups: dict[str, list[str]] = {}
    for path in sorted((root / "tests").rglob("test_*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test_"):
                normalized = ast.dump(ast.Module(body=node.body, type_ignores=[]), include_attributes=False)
                digest = hashlib.sha256(normalized.encode()).hexdigest()
                groups.setdefault(digest, []).append(f"{path.relative_to(root).as_posix()}::{node.name}")
    return [{"fingerprint": digest, "test_ids": sorted(ids)} for digest, ids in sorted(groups.items())
            if len(ids) > 1]


def build_report(root: Path, results: list[dict[str, Any]], source_digest: str) -> dict[str, Any]:
    counts = {outcome: sum(row["outcome"] == outcome for row in results) for outcome in sorted(OUTCOMES)}
    by_contract: dict[str, list[str]] = {}
    for row in results:
        by_contract.setdefault(row["contract_id"], []).append(row["outcome"])
    test_path = root / "architecture/generated/test-evidence.json"
    test_evidence = json.loads(test_path.read_text(encoding="utf-8")) if test_path.is_file() else {}
    test_rows = test_evidence.get("tests", [])
    contract_tests = {row["id"]: len(row.get("bound_test_ids", []))
                      for row in test_evidence.get("contracts", [])}
    hotspots = sorted(
        ({"test_id": row["test_id"], "duration_ms": row["duration_ms"]}
         for row in test_rows if "duration_ms" in row),
        key=lambda row: (-row["duration_ms"], row["test_id"]),
    )[:20]
    contract_rows = [{"contract_id": contract, "outcomes": sorted(outcomes),
                      "tests": contract_tests.get(contract, 0),
                      "status": "MUTATION_KILLED" if "KILLED" in outcomes and "SURVIVED" not in outcomes
                      else "SURVIVING_MUTATION" if "SURVIVED" in outcomes else "NO_VALID_KILL"}
                     for contract, outcomes in sorted(by_contract.items())]
    return {"schema": "architecture-mutation-report-v1", "generated_header": "Generated; do not edit.",
            "source_digest": source_digest, "status": "CURRENT", "counts": counts,
            "mutations": sorted(results, key=lambda item: item["id"]),
            "test_inventory": {**test_evidence.get("counts", {}),
                               "by_platform": {platform: sum(row.get("platform") == platform for row in test_rows)
                                               for platform in ("PYTHON", "WEB", "WINDOWS")}},
            "contracts": contract_rows,
            "duration_hotspots": hotspots,
            "many_tests_with_survivors": [row["contract_id"] for row in contract_rows
                                          if row["tests"] >= 3 and row["status"] == "SURVIVING_MUTATION"],
            "few_tests_all_killed": [row["contract_id"] for row in contract_rows
                                     if row["tests"] <= 1 and row["status"] == "MUTATION_KILLED"],
            "duplicate_test_ast_fingerprints": normalized_test_fingerprints(root)}
