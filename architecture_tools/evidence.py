"""Contract registry, test binding, execution, and runtime evidence compiler."""

from __future__ import annotations

import ast
import hashlib
import json
import re
import tomllib
from pathlib import Path
from typing import Any


RISKS = {"CRITICAL", "HIGH", "MEDIUM", "REFERENCE"}
REQUIRED_CONTRACT_FIELDS = {"id", "statement", "owner", "risk", "fact_ids", "document", "required_evidence"}
SECRET_RE = re.compile(r"(?:-----BEGIN [A-Z ]+PRIVATE KEY-----|(?:api|secret|token|password)[_-]?key[\"']?\s*[:=])", re.I)


class ContractEvidenceError(RuntimeError):
    pass


def load_registry(root: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    contracts_doc = tomllib.loads((root / "architecture/contracts/invariants.toml").read_text(encoding="utf-8"))
    bindings_doc = tomllib.loads((root / "architecture/contracts/test_bindings.toml").read_text(encoding="utf-8"))
    contracts = contracts_doc.get("contract", []); bindings = bindings_doc.get("binding", [])
    ids = [item.get("id") for item in contracts]
    if len(ids) != len(set(ids)):
        raise ContractEvidenceError("duplicate contract ID")
    for contract in contracts:
        missing = REQUIRED_CONTRACT_FIELDS - set(contract)
        if missing or contract["risk"] not in RISKS or not (root / contract["document"]).is_file():
            raise ContractEvidenceError(f"invalid contract registry entry {contract.get('id')}: missing={sorted(missing)}")
    known = set(ids); test_ids: set[str] = set()
    for binding in bindings:
        if binding.get("contract_id") not in known:
            raise ContractEvidenceError(f"binding references unknown contract {binding.get('contract_id')}")
        if binding.get("test_id") in test_ids:
            # One test may protect several contracts, but each binding row remains unique by pair.
            if any(item is not binding and item.get("test_id") == binding["test_id"] and item.get("contract_id") == binding["contract_id"] for item in bindings):
                raise ContractEvidenceError("duplicate contract/test binding")
        test_ids.add(binding["test_id"])
    return contracts, bindings


def collect_python_tests(root: Path) -> list[dict[str, Any]]:
    tests: list[dict[str, Any]] = []
    for path in sorted((root / "tests").rglob("test_*.py")):
        rel = path.relative_to(root).as_posix(); tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=rel)
        imports: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import): imports.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module: imports.add(node.module)
        owners = sorted({".".join(name.split(".")[:2]) for name in imports if name.startswith("xauusd_forecaster.")})
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test_"):
                marker_contracts: list[str] = []
                for decorator in node.decorator_list:
                    if (isinstance(decorator, ast.Call) and isinstance(decorator.func, ast.Attribute)
                            and decorator.func.attr == "arch_contract"):
                        marker_contracts.extend(item.value for item in decorator.args
                                                if isinstance(item, ast.Constant) and isinstance(item.value, str))
                tests.append({"test_id": f"{rel}::{node.name}", "file": rel, "line": node.lineno,
                              "platform": "WINDOWS" if rel.startswith("tests/runtime/test_control") else "PYTHON",
                              "owners_touched": owners, "marker_contract_ids": sorted(set(marker_contracts))})
    return tests


def collect_web_tests(code_facts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{"test_id": fact["test_id"], "file": fact["path"], "line": fact["line"], "platform": "WEB", "owners_touched": []}
            for fact in code_facts if fact.get("type") == "web_test"]


def _execution(root: Path, source_digest: str) -> dict[str, Any]:
    path = root / "architecture/evidence/execution-results.json"
    if not path.is_file():
        return {"source_digest": "", "results": []}
    document = json.loads(path.read_text(encoding="utf-8"))
    if SECRET_RE.search(json.dumps(document)):
        raise ContractEvidenceError("execution evidence contains sensitive material")
    return document


def compile_contract_evidence(
    root: Path,
    source_digest: str,
    code_facts: list[dict[str, Any]],
    mutation_document: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    contracts, bindings = load_registry(root)
    discovered = collect_python_tests(root) + collect_web_tests(code_facts)
    known_contracts = {item["id"] for item in contracts}
    inline = []
    for test in discovered:
        for contract_id in test.get("marker_contract_ids", []):
            if contract_id not in known_contracts:
                raise ContractEvidenceError(f"test marker references unknown contract {contract_id}")
            if not any(item["contract_id"] == contract_id and item["test_id"] == test["test_id"] for item in bindings):
                inline.append({"contract_id": contract_id, "test_id": test["test_id"], "platform": test["platform"], "classification": "CONTRACT"})
    bindings = bindings + inline
    by_test = {item["test_id"]: item for item in discovered}
    missing = sorted({item["test_id"] for item in bindings} - set(by_test))
    if missing:
        raise ContractEvidenceError(f"bound tests were not collected: {missing}")
    execution = _execution(root, source_digest); current = execution.get("source_digest") == source_digest
    results = {item["test_id"]: item for item in execution.get("results", [])}
    binding_map: dict[str, list[dict[str, Any]]] = {}
    for binding in bindings: binding_map.setdefault(binding["test_id"], []).append(binding)
    test_rows = []
    for test in sorted(discovered, key=lambda item: item["test_id"]):
        bound = binding_map.get(test["test_id"], []); result = results.get(test["test_id"])
        protects = sorted(item["contract_id"] for item in bound)
        test_rows.append({**test, "contract_ids": protects, "relationship": "PROTECTS" if protects else ("TOUCHES" if test["owners_touched"] else "UNCLASSIFIED"),
                          "classification": bound[0].get("classification", "UNCLASSIFIED") if bound else "UNCLASSIFIED",
                          "execution": "EXECUTED" if current and result and result.get("status") == "PASSED" else ("STALE" if result and not current else "NOT_EXECUTED"),
                          **({"duration_ms": result["duration_ms"]} if result and "duration_ms" in result else {})})
    mutation_document = mutation_document or {}
    mutation_current = mutation_document.get("source_digest") == source_digest
    mutation_rows = mutation_document.get("mutations", []) if mutation_current else []
    mutation_by_contract: dict[str, list[dict[str, Any]]] = {}
    for row in mutation_rows:
        if row.get("outcome") in {"KILLED", "SURVIVED", "INVALID", "TIMEOUT", "ERROR"}:
            mutation_by_contract.setdefault(str(row.get("contract_id")), []).append(row)
    contract_rows = []
    runtime_rows = []
    for contract in contracts:
        owned = [item for item in bindings if item["contract_id"] == contract["id"]]
        executed = [item for item in owned if current and results.get(item["test_id"], {}).get("status") == "PASSED"]
        categories = ["DECLARED"] + (["TEST_BOUND"] if owned else []) + (["TEST_EXECUTED"] if executed else [])
        for binding in executed:
            events = binding.get("runtime_events", [])
            if events:
                event_hash = hashlib.sha256("\0".join(events).encode()).hexdigest()
                runtime_rows.append({"trace_id": f"runtime:{contract['id']}:{event_hash[:16]}", "contract_id": contract["id"],
                                     "test_id": binding["test_id"], "source_digest": source_digest,
                                     "source": {"path": by_test[binding["test_id"]]["file"], "line": by_test[binding["test_id"]]["line"]},
                                     "probe_kind": "ASSERTED_FIXTURE_SEQUENCE",
                                     "events": [{"sequence": index + 1, "event_type": event} for index, event in enumerate(events)],
                                     "normalized_hash": event_hash})
        if any(item["contract_id"] == contract["id"] for item in runtime_rows): categories.append("RUNTIME_OBSERVED")
        contract_mutations = mutation_by_contract.get(contract["id"], [])
        valid_kills = [item for item in contract_mutations if item["outcome"] == "KILLED"]
        survivors = [item for item in contract_mutations if item["outcome"] == "SURVIVED"]
        if valid_kills and not survivors:
            categories.append("MUTATION_KILLED")
        required = contract["required_evidence"]
        status = "VERIFIED" if set(required) <= set(categories) else "PARTIAL" if len(categories) > 1 else "DECLARED_ONLY"
        contract_rows.append({**contract, "categories": categories, "status": status,
                              "bound_test_ids": sorted(item["test_id"] for item in owned),
                              "mutation_ids": sorted(item["id"] for item in contract_mutations),
                              "mutation_outcomes": sorted({item["outcome"] for item in contract_mutations}),
                              "missing_evidence": sorted(set(required) - set(categories))})
    test_document = {"schema": "architecture-test-evidence-v1", "generated_header": "Generated; do not edit.",
                     "source_digest": source_digest, "execution_digest_state": "CURRENT" if current else ("STALE" if execution.get("source_digest") else "UNAVAILABLE"),
                     "contracts": contract_rows, "tests": test_rows,
                     "counts": {"collected": len(test_rows), "contract": sum(row["classification"] == "CONTRACT" for row in test_rows),
                                "touches_only": sum(row["relationship"] == "TOUCHES" for row in test_rows),
                                "unclassified": sum(row["classification"] == "UNCLASSIFIED" for row in test_rows)}}
    runtime_document = {"schema": "architecture-runtime-evidence-v1", "generated_header": "Generated; do not edit.",
                        "source_digest": source_digest, "status": "CURRENT" if current else "STALE", "traces": sorted(runtime_rows, key=lambda item: item["trace_id"])}
    serialized = json.dumps({"tests": test_document, "runtime": runtime_document})
    if SECRET_RE.search(serialized) or str(root.resolve()).replace("\\", "/") in serialized.replace("\\", "/"):
        raise ContractEvidenceError("generated contract evidence contains sensitive or absolute data")
    return test_document, runtime_document
