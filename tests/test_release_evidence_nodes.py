from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "scripts" / "release_evidence_nodes.ps1"
CONTRACT = ROOT / "scripts" / "release-evidence-contract.json"


def _ps_literal(value: Path | str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _run_module(tmp_path: Path, shell: str, body: str) -> str:
    if shutil.which(shell) is None:
        pytest.skip(f"{shell} is required for this Windows release evidence contract")
    probe = tmp_path / f"probe-{Path(shell).stem}.ps1"
    probe.write_text(
        "$ErrorActionPreference='Stop'\n"
        f". {_ps_literal(MODULE)}\n"
        + body,
        encoding="utf-8-sig",
    )
    command = [shell, "-NoProfile"]
    if shell == "powershell.exe":
        command += ["-ExecutionPolicy", "Bypass"]
    command += ["-File", str(probe)]
    completed = subprocess.run(
        command,
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return completed.stdout.strip()


def test_release_evidence_contract_has_complete_acyclic_node_ownership() -> None:
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    nodes = {node["id"]: node for node in contract["nodes"]}
    assert set(nodes) == {
        "artifact_provenance",
        "exact_head_ci",
        "windows_runtime",
        "migration_acceptance",
        "migration_live_lease",
        "directed_worker",
        "worker_cpu",
        "semantic_contract",
        "free_plan",
        "human_access_root",
        "access_provider_lease",
        "candidate_placement",
        "rollback_precheck",
        "promote_attempt",
        "observe_attempt",
    }
    assert all(node["owner"] for node in nodes.values())
    assert all(node["behavior_inputs"] for node in nodes.values())
    assert nodes["human_access_root"]["dependencies"] == []
    assert nodes["free_plan"]["dependencies"] == []
    assert "windows_runtime" not in nodes["worker_cpu"]["dependencies"]

    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node_id: str) -> None:
        assert node_id not in visiting
        if node_id in visited:
            return
        visiting.add(node_id)
        for dependency in nodes[node_id]["dependencies"]:
            assert dependency in nodes
            visit(dependency)
        visiting.remove(node_id)
        visited.add(node_id)

    for node_id in nodes:
        visit(node_id)


@pytest.mark.parametrize("shell", ("powershell.exe", "pwsh.exe"))
def test_node_receipt_is_immutable_deterministic_and_waterfall_ready(
    tmp_path: Path, shell: str
) -> None:
    # Keep below Win32 MAX_PATH so the Windows PowerShell 5.1 contract is
    # exercised without relying on a host long-path policy.
    evidence_root = tmp_path.parent / f"re-{Path(shell).stem}"
    output = _run_module(
        tmp_path,
        shell,
        f"""
$source=[ordered]@{{validation_key='worker:git';worker_version_id='worker';git_sha='git'}}
$args=@{{Root={_ps_literal(evidence_root)};ContractPath={_ps_literal(CONTRACT)};
ValidationKey='worker:git';Node='artifact_provenance';BehaviorKey='artifact-key';
State='PASSED';SourceIdentity=$source;StartedAt='2026-09-01T00:00:00.0000000+00:00';
CompletedAt='2026-09-01T00:00:01.2500000+00:00';ExecutionMode='FRESH';
WhyRan='EXACT_ARTIFACT_DISCOVERED'}}
$first=Write-ReleaseEvidenceNodeReceipt @args
$second=Write-ReleaseEvidenceNodeReceipt @args
$placement=Write-ReleaseEvidenceNodeReceipt -Root {_ps_literal(evidence_root)} `
 -ContractPath {_ps_literal(CONTRACT)} -ValidationKey 'worker:git' `
 -Node 'candidate_placement' -BehaviorKey 'placement-key' -State 'PASSED' `
 -SourceIdentity $source -StartedAt '2026-09-01T00:00:01.2500000Z' `
 -CompletedAt '2026-09-01T00:00:02.2500000Z' -ExecutionMode 'FRESH' `
 -WhyRan 'CANDIDATE_ZERO_PERCENT' `
 -Dependencies @([pscustomobject]@{{node='artifact_provenance';receipt_digest=$first.receipt_digest}})
$waterfall=Get-ReleaseEvidenceWaterfall -Root {_ps_literal(evidence_root)} -ValidationKey 'worker:git'
[ordered]@{{digest=$first.receipt_digest;same=($first.receipt_digest -ceq $second.receipt_digest);
valid=(Test-ReleaseEvidenceNodeReceipt -Receipt $first);elapsed=$first.elapsed_ms;
nodes=$waterfall.node_count;waterfall_elapsed=$waterfall.elapsed_ms}}|ConvertTo-Json -Compress
""",
    )
    result = json.loads(output)
    assert result["same"] is True
    assert result["valid"] is True
    assert len(result["digest"]) == 64
    assert result["elapsed"] == 1250
    assert result["nodes"] == 2
    assert result["waterfall_elapsed"] == 2250

    receipt_files = list(evidence_root.rglob("artifact_provenance/*.json"))
    assert len(receipt_files) == 1
    assert receipt_files[0].read_bytes()[:3] != b"\xef\xbb\xbf"


@pytest.mark.parametrize("shell", ("powershell.exe", "pwsh.exe"))
def test_evidence_store_supports_long_authoritative_runtime_root(
    tmp_path: Path, shell: str,
) -> None:
    evidence_root = (
        tmp_path
        / "physically-distinct-production-runtime-state-authority"
        / ".local"
        / "forward"
        / "release-evidence"
    )
    output = _run_module(
        tmp_path,
        shell,
        f"""
$source=[ordered]@{{validation_key='worker:git';worker_version_id='worker';git_sha='git'}}
$receipt=Write-ReleaseEvidenceNodeReceipt -Root {_ps_literal(evidence_root)} `
 -ContractPath {_ps_literal(CONTRACT)} -ValidationKey 'worker:git' `
 -Node 'artifact_provenance' -BehaviorKey 'artifact-key' -State 'PASSED' `
 -SourceIdentity $source -StartedAt '2026-09-01T00:00:00Z' `
 -CompletedAt '2026-09-01T00:00:01Z' -ExecutionMode 'FRESH' -WhyRan 'DISCOVERED'
$waterfall=Get-ReleaseEvidenceWaterfall -Root {_ps_literal(evidence_root)} -ValidationKey 'worker:git'
[ordered]@{{valid=(Test-ReleaseEvidenceNodeReceipt $receipt);nodes=$waterfall.node_count}}|
 ConvertTo-Json -Compress
""",
    )
    assert json.loads(output) == {"valid": True, "nodes": 1}


def test_receipt_digest_and_dependency_validation_fail_closed(tmp_path: Path) -> None:
    evidence_root = tmp_path.parent / "re-tamper"
    output = _run_module(
        tmp_path,
        "powershell.exe",
        f"""
$source=[ordered]@{{validation_key='worker:git';worker_version_id='worker';git_sha='git'}}
$receipt=Write-ReleaseEvidenceNodeReceipt -Root {_ps_literal(evidence_root)} `
 -ContractPath {_ps_literal(CONTRACT)} -ValidationKey 'worker:git' `
 -Node 'artifact_provenance' -BehaviorKey 'artifact-key' -State 'PASSED' `
 -SourceIdentity $source -StartedAt '2026-09-01T00:00:00Z' `
 -CompletedAt '2026-09-01T00:00:01Z' -ExecutionMode 'FRESH' -WhyRan 'DISCOVERED'
$receipt.behavior_key='tampered'
$tamperAccepted=Test-ReleaseEvidenceNodeReceipt -Receipt $receipt
$dependencyRejected=$false
$missingDependencyRejected=$false
try {{
 Write-ReleaseEvidenceNodeReceipt -Root {_ps_literal(evidence_root)} `
  -ContractPath {_ps_literal(CONTRACT)} -ValidationKey 'worker:git' `
  -Node 'worker_cpu' -BehaviorKey 'cpu-key' -State 'PASSED' -SourceIdentity $source `
  -StartedAt '2026-09-01T00:00:00Z' -CompletedAt '2026-09-01T00:00:01Z' `
  -ExecutionMode 'FRESH' -WhyRan 'CPU' `
  -Dependencies @([pscustomobject]@{{node='human_access_root';receipt_digest=('a'*64)}})|Out-Null
}} catch {{ $dependencyRejected=$_.Exception.Message -eq 'RELEASE_EVIDENCE_DEPENDENCY_INVALID' }}
try {{
 Write-ReleaseEvidenceNodeReceipt -Root {_ps_literal(evidence_root)} `
  -ContractPath {_ps_literal(CONTRACT)} -ValidationKey 'worker:git' `
  -Node 'candidate_placement' -BehaviorKey 'placement-key' -State 'PASSED' -SourceIdentity $source `
  -StartedAt '2026-09-01T00:00:00Z' -CompletedAt '2026-09-01T00:00:01Z' `
  -ExecutionMode 'FRESH' -WhyRan 'PLACEMENT' `
  -Dependencies @([pscustomobject]@{{node='artifact_provenance';receipt_digest=('a'*64)}})|Out-Null
}} catch {{ $missingDependencyRejected=$_.Exception.Message -eq 'RELEASE_EVIDENCE_DEPENDENCY_RECEIPT_MISSING' }}
[ordered]@{{tamper_accepted=$tamperAccepted;dependency_rejected=$dependencyRejected;
missing_dependency_rejected=$missingDependencyRejected}}|ConvertTo-Json -Compress
""",
    )
    result = json.loads(output)
    assert result == {
        "tamper_accepted": False,
        "dependency_rejected": True,
        "missing_dependency_rejected": True,
    }


def test_receipt_bytes_match_across_powershell_runtimes(tmp_path: Path) -> None:
    byte_sets: list[bytes] = []
    for shell in ("powershell.exe", "pwsh.exe"):
        evidence_root = tmp_path.parent / f"parity-{Path(shell).stem}"
        _run_module(
            tmp_path,
            shell,
            f"""
$source=[ordered]@{{validation_key='worker:git';label='中文';instant='2026-09-01T00:00:00.0000000+00:00'}}
Write-ReleaseEvidenceNodeReceipt -Root {_ps_literal(evidence_root)} `
 -ContractPath {_ps_literal(CONTRACT)} -ValidationKey 'worker:git' `
 -Node 'artifact_provenance' -BehaviorKey 'artifact-key' -State 'PASSED' `
 -SourceIdentity $source -StartedAt '2026-09-01T00:00:00Z' `
 -CompletedAt '2026-09-01T00:00:01Z' -ExecutionMode 'FRESH' -WhyRan 'DISCOVERED'|Out-Null
""",
        )
        receipt = next(evidence_root.rglob("artifact_provenance/*.json"))
        byte_sets.append(receipt.read_bytes())
    assert byte_sets[0] == byte_sets[1]


def test_mutable_index_cannot_redirect_current_evidence(tmp_path: Path) -> None:
    evidence_root = tmp_path.parent / "re-index"
    output = _run_module(
        tmp_path,
        "powershell.exe",
        f"""
$source=[ordered]@{{validation_key='worker:git'}}
Write-ReleaseEvidenceNodeReceipt -Root {_ps_literal(evidence_root)} `
 -ContractPath {_ps_literal(CONTRACT)} -ValidationKey 'worker:git' `
 -Node 'artifact_provenance' -BehaviorKey 'artifact-key' -State 'PASSED' `
 -SourceIdentity $source -StartedAt '2026-09-01T00:00:00Z' `
 -CompletedAt '2026-09-01T00:00:01Z' -ExecutionMode 'FRESH' -WhyRan 'DISCOVERED'|Out-Null
$key=Get-ReleaseEvidenceSha256 'worker:git'
$indexPath=Join-Path {_ps_literal(evidence_root)} "$key\\current\\artifact_provenance.json"
$index=Get-Content -LiteralPath $indexPath -Raw -Encoding UTF8|ConvertFrom-ReleaseEvidenceJson
$index.receipt_path='..\\..\\unrelated.json'
Write-ReleaseEvidenceUtf8Atomic -Path $indexPath -Content (ConvertTo-ReleaseEvidenceJson $index)
$rejected=$false
try {{ Get-ReleaseEvidenceWaterfall -Root {_ps_literal(evidence_root)} -ValidationKey 'worker:git'|Out-Null }}
catch {{ $rejected=$_.Exception.Message -eq 'RELEASE_EVIDENCE_INDEX_INVALID' }}
$rejected
""",
    )
    assert output == "True"


def test_control_bundle_and_candidate_discovery_use_evidence_owner() -> None:
    manifest = json.loads(
        (ROOT / "scripts" / "runtime-control-files.json").read_text(encoding="utf-8")
    )
    assert "release-evidence-contract.json" in manifest["files"]
    assert "release_evidence_nodes.ps1" in manifest["files"]
    control = (ROOT / "scripts" / "xauusd_control_center.ps1").read_text(
        encoding="utf-8"
    )
    assert '. (Join-Path $PSScriptRoot "release_evidence_nodes.ps1")' in control
    assert control.count("Write-CandidateArtifactEvidence -Candidate $discovered") == 2
    assert 'Join-Path $runtimeForwardRoot "release-evidence"' in control
    assert control.count("Get-ReleaseControlStatusSnapshot") >= 3


def test_real_control_center_writes_candidate_artifact_receipt(tmp_path: Path) -> None:
    runtime_root = tmp_path.parents[2] / f"xre-{tmp_path.parent.name}"
    shutil.rmtree(runtime_root, ignore_errors=True)
    control = ROOT / "scripts" / "xauusd_control_center.ps1"
    output = _run_module(
        tmp_path,
        "powershell.exe",
        f"""
$null=. {_ps_literal(control)} -Action CodeRevision -RuntimeRoot {_ps_literal(runtime_root)} `
 -RepositoryRoot {_ps_literal(ROOT)}
$candidate=[pscustomobject]@{{validation_key='worker:git';worker_version_id='worker';
git_sha='git';windows_revision='git';artifact_kind='PRODUCTION_CANDIDATE';
version_created_at='2026-09-01T00:00:00Z';discovered_at='2026-09-01T00:00:02Z'}}
$receipt=Write-CandidateArtifactEvidence -Candidate $candidate
$snapshot=[pscustomobject]@{{candidate=$candidate}}
$waterfall=Get-ReleaseEvidenceWaterfall -Root (Join-Path {_ps_literal(runtime_root)} '.local\\forward\\release-evidence') `
 -ValidationKey 'worker:git'
[ordered]@{{valid=(Test-ReleaseEvidenceNodeReceipt $receipt);node=$receipt.node;
elapsed=$receipt.elapsed_ms;waterfall_nodes=$waterfall.node_count}}|ConvertTo-Json -Compress
""",
    )
    assert json.loads(output) == {
        "valid": True,
        "node": "artifact_provenance",
        "elapsed": 2000,
        "waterfall_nodes": 1,
    }
    shutil.rmtree(runtime_root)
