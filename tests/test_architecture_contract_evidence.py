from __future__ import annotations

import json
from pathlib import Path

import pytest

from architecture_tools.evidence import ContractEvidenceError, compile_contract_evidence


ROOT = Path(__file__).resolve().parents[1]


def _write(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True); path.write_text(body, encoding="utf-8")


def _fixture(root: Path, *, binding: bool = True, marker: bool = False, result: str | None = "PASSED", digest: str = "current", receipt_digest: str | None = None, runtime: bool = False) -> None:
    _write(root / "docs/contract.md", "# Contract\n")
    decorator = '@pytest.mark.arch_contract("RULE")\n' if marker else ""
    _write(root / "tests/test_owner.py", "import pytest\nfrom xauusd_forecaster.decision import selection\n\n" + decorator + "def test_rule():\n    assert selection\n")
    _write(root / "architecture/contracts/invariants.toml", """schema = "architecture-contracts-v1"
[[contract]]
id = "RULE"
statement = "A durable rule."
owner = "xauusd_forecaster.decision"
risk = "CRITICAL"
fact_ids = ["node:decision"]
document = "docs/contract.md"
required_evidence = ["TEST_BOUND", "TEST_EXECUTED"]
""")
    rows = "" if not binding else """
[[binding]]
contract_id = "RULE"
test_id = "tests/test_owner.py::test_rule"
platform = "PYTHON"
classification = "CONTRACT"
""" + ('runtime_events = ["started", "finished"]\n' if runtime else "")
    _write(root / "architecture/contracts/test_bindings.toml", 'schema = "architecture-test-bindings-v1"\n' + rows)
    if result is not None:
        receipt = {"schema": "architecture-test-execution-v1", "source_digest": receipt_digest or digest,
                   "results": [{"test_id": "tests/test_owner.py::test_rule", "status": result, "duration_ms": 1}]}
        _write(root / "architecture/evidence/execution-results.json", json.dumps(receipt))


def test_import_alone_is_touches_not_protects(tmp_path: Path) -> None:
    _fixture(tmp_path, binding=False, result=None)
    tests, _ = compile_contract_evidence(tmp_path, "current", [])
    row = next(item for item in tests["tests"] if item["test_id"].endswith("test_rule"))
    assert row["relationship"] == "TOUCHES"; assert row["contract_ids"] == []
    assert tests["contracts"][0]["status"] == "DECLARED_ONLY"


def test_explicit_binding_protects_and_current_pass_executes(tmp_path: Path) -> None:
    _fixture(tmp_path)
    tests, _ = compile_contract_evidence(tmp_path, "current", [])
    row = next(item for item in tests["tests"] if item["test_id"].endswith("test_rule"))
    assert row["relationship"] == "PROTECTS"; assert row["execution"] == "EXECUTED"
    assert tests["contracts"][0]["status"] == "VERIFIED"


def test_explicit_marker_produces_protects_without_sidecar(tmp_path: Path) -> None:
    _fixture(tmp_path, binding=False, marker=True)
    tests, _ = compile_contract_evidence(tmp_path, "current", [])
    row = next(item for item in tests["tests"] if item["test_id"].endswith("test_rule"))
    assert row["relationship"] == "PROTECTS"
    assert row["contract_ids"] == ["RULE"]


def test_old_digest_is_stale_and_failed_test_never_executes(tmp_path: Path) -> None:
    _fixture(tmp_path, receipt_digest="old")
    stale, _ = compile_contract_evidence(tmp_path, "current", [])
    assert stale["execution_digest_state"] == "STALE"
    assert stale["contracts"][0]["status"] == "PARTIAL"
    _fixture(tmp_path, result="FAILED")
    failed, _ = compile_contract_evidence(tmp_path, "current", [])
    assert failed["tests"][0]["execution"] == "NOT_EXECUTED"
    assert "TEST_EXECUTED" in failed["contracts"][0]["missing_evidence"]


def test_runtime_probe_is_normalized_source_linked_and_deterministic(tmp_path: Path) -> None:
    _fixture(tmp_path, runtime=True)
    first = compile_contract_evidence(tmp_path, "current", []); second = compile_contract_evidence(tmp_path, "current", [])
    assert first == second
    trace = first[1]["traces"][0]
    assert trace["probe_kind"] == "ASSERTED_FIXTURE_SEQUENCE"
    assert trace["source"] == {"path": "tests/test_owner.py", "line": 4}
    assert [item["event_type"] for item in trace["events"]] == ["started", "finished"]


def test_sensitive_execution_evidence_is_rejected(tmp_path: Path) -> None:
    _fixture(tmp_path)
    path = tmp_path / "architecture/evidence/execution-results.json"
    document = json.loads(path.read_text(encoding="utf-8")); document["api_key"] = "forbidden"
    path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(ContractEvidenceError, match="sensitive"):
        compile_contract_evidence(tmp_path, "current", [])


def test_current_critical_pilot_is_visible_and_complete() -> None:
    digest = json.loads((ROOT / "architecture/generated/source-digest.json").read_text(encoding="utf-8"))["source_digest"]
    code = json.loads((ROOT / "architecture/generated/code-index.json").read_text(encoding="utf-8"))["facts"]
    tests, runtime = compile_contract_evidence(ROOT, digest, code)
    assert len(tests["contracts"]) == 16
    assert all(contract["status"] == "VERIFIED" for contract in tests["contracts"])
    assert len(runtime["traces"]) == 10
