from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "scripts" / "release_evidence_nodes.ps1"
AUTHORITY = ROOT / "scripts" / "release_evidence_authority.ps1"
COMMON = ROOT / "scripts" / "control_center_common.ps1"
PERSISTENCE = ROOT / "scripts" / "control_center_persistence_gateway.ps1"
CONTRACT = ROOT / "scripts" / "release-evidence-contract.json"
OWNERSHIP = ROOT / "scripts" / "release-evidence-change-ownership.json"
FREE_PLAN = ROOT / "scripts" / "release-free-plan-contract.json"
CONTROL = ROOT / "scripts" / "xauusd_control_center.ps1"
CONTROL_OWNERS = tuple(sorted((ROOT / "scripts").glob("control_center_*.ps1")))


def _control_source() -> str:
    return "\n".join(
        path.read_text(encoding="utf-8") for path in (CONTROL, *CONTROL_OWNERS)
    )


def _control_function(name: str) -> str:
    marker = f"function {name}"
    matches = []
    for path in CONTROL_OWNERS:
        source = path.read_text(encoding="utf-8")
        if marker in source:
            matches.append(marker + source.split(marker, 1)[1].split("\nfunction ", 1)[0])
    assert len(matches) == 1
    return matches[0]


def _ps_literal(value: Path | str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _run_module(tmp_path: Path, shell: str, body: str) -> str:
    if shutil.which(shell) is None:
        pytest.skip(f"{shell} is required for this Windows release evidence contract")
    probe = tmp_path / f"probe-{Path(shell).stem}.ps1"
    probe.write_text(
        "$ErrorActionPreference='Stop'\n"
        f". {_ps_literal(COMMON)}\n"
        f". {_ps_literal(PERSISTENCE)}\n"
        f". {_ps_literal(MODULE)}\n"
        f". {_ps_literal(AUTHORITY)}\n"
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
    assert all(node["producer_adapter"] for node in nodes.values())
    assert len({node["producer_adapter"] for node in nodes.values()}) == 15
    assert all(node["consumers"] for node in nodes.values())
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
def test_all_fifteen_producer_adapters_are_real_unique_commands(
    tmp_path: Path, shell: str,
) -> None:
    output = _run_module(
        tmp_path,
        shell,
        f"""
$registry=Get-ReleaseEvidenceProducerRegistry -ContractPath {_ps_literal(CONTRACT)}
$missing=@($registry.Values|Where-Object{{-not (Get-Command ([string]$_) -ErrorAction SilentlyContinue)}})
[ordered]@{{nodes=$registry.Count;unique=@($registry.Values|Select-Object -Unique).Count;
 missing=$missing.Count}}|ConvertTo-Json -Compress
""",
    )
    assert json.loads(output) == {"nodes": 15, "unique": 15, "missing": 0}


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
    assert "release-evidence-change-ownership.json" in manifest["files"]
    assert "release-free-plan-contract.json" in manifest["files"]
    assert "release_evidence_authority.ps1" in manifest["files"]
    assert "release_evidence_nodes.ps1" in manifest["files"]
    facade = CONTROL.read_text(encoding="utf-8")
    control = _control_source()
    assert '. (Join-Path $PSScriptRoot "release_evidence_nodes.ps1")' in facade
    assert '. (Join-Path $PSScriptRoot "release_evidence_authority.ps1")' in facade
    assert control.count("Write-CandidateArtifactEvidence -Candidate $discovered") == 2
    assert control.count("-Root $releaseEvidenceRoot -ContractPath $releaseEvidenceContractPath") >= 2
    assert 'Join-Path $runtimeForwardRoot "release-evidence"' in control
    assert control.count("Get-ReleaseControlStatusSnapshot") >= 3
    authority = AUTHORITY.read_text(encoding="utf-8")
    assert "function Publish-CandidateQualificationEvidence" in authority
    assert "function Publish-PromotionFreshnessEvidence" in authority
    assert "function Publish-CandidateQualificationEvidence" not in control
    assert "function Publish-PromotionFreshnessEvidence" not in control


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
$receipt=Write-CandidateArtifactEvidence -Candidate $candidate `
 -Root (Join-Path {_ps_literal(runtime_root)} '.local\\forward\\release-evidence') `
 -ContractPath {_ps_literal(CONTRACT)}
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


@pytest.mark.parametrize("shell", ("powershell.exe", "pwsh.exe"))
def test_behavior_key_planner_and_bounded_lookup_are_exact(
    tmp_path: Path, shell: str,
) -> None:
    evidence_root = tmp_path.parent / f"authority-{Path(shell).stem}"
    output = _run_module(
        tmp_path,
        shell,
        f"""
$source=[pscustomobject]@{{validation_key='old';qualification_state='PASSED'}}
$inputs=[pscustomobject][ordered]@{{protected_origin='https://example.invalid';
 provider_application_policy=[pscustomobject]@{{digest='policy'}};
 access_artifacts=[pscustomobject]@{{digest='artifacts'}};
 acceptance_contract='access-v1'}}
$args=@{{Root={_ps_literal(evidence_root)};ContractPath={_ps_literal(CONTRACT)};
 ValidationKey='old';BehaviorInputs=$inputs;SourceIdentity=$source;
 StartedAt='2026-09-01T00:00:00Z';CompletedAt='2026-09-01T00:00:01Z';
 WhyRan='HUMAN_ROOT_FIXTURE'}}
$first=Publish-HumanAccessRootEvidence -Arguments $args
$key=Get-ReleaseEvidenceBehaviorKey -ContractPath {_ps_literal(CONTRACT)} `
 -Node 'human_access_root' -Inputs $inputs
$found=Find-ReleaseEvidenceBehaviorReceipt -Root {_ps_literal(evidence_root)} `
 -Node 'human_access_root' -BehaviorKey $key
$reuse=Publish-ReleaseEvidenceReuse -Root {_ps_literal(evidence_root)} `
 -ContractPath {_ps_literal(CONTRACT)} -ValidationKey 'new' `
 -Node 'human_access_root' -Adapter 'Publish-HumanAccessRootEvidence' `
 -BehaviorInputs $inputs -SourceIdentity ([pscustomobject]@{{qualification_state='PASSED'}}) `
 -StartedAt '2026-09-01T00:01:00Z' -CompletedAt '2026-09-01T00:01:01Z' `
 -ReuseReason 'EXACT_ACCESS_BEHAVIOR_UNCHANGED'
$changed=[pscustomobject][ordered]@{{protected_origin='https://other.invalid';
 provider_application_policy=[pscustomobject]@{{digest='policy'}};
 access_artifacts=[pscustomobject]@{{digest='artifacts'}};
 acceptance_contract='access-v1'}}
$changedKey=Get-ReleaseEvidenceBehaviorKey -ContractPath {_ps_literal(CONTRACT)} `
 -Node 'human_access_root' -Inputs $changed
[ordered]@{{found=($found.receipt_digest -ceq $first.receipt_digest);
 reused=($reuse.prior_receipt -ceq $first.receipt_digest);
 mode=$reuse.execution_mode;changed=($changedKey -cne $key)}}|ConvertTo-Json -Compress
""",
    )
    assert json.loads(output) == {
        "found": True,
        "reused": True,
        "mode": "REUSED",
        "changed": True,
    }


def test_change_family_plan_is_deterministic_and_unknown_fails_closed(
    tmp_path: Path,
) -> None:
    output = _run_module(
        tmp_path,
        "powershell.exe",
        f"""
$docs=Get-ReleaseEvidenceChangePlan -OwnershipPath {_ps_literal(OWNERSHIP)} `
 -ChangedFiles @('docs/contracts/RELEASE_CONTROL.md')
$worker=Get-ReleaseEvidenceChangePlan -OwnershipPath {_ps_literal(OWNERSHIP)} `
 -ChangedFiles @('web/app/api/status/route.ts')
$mixed=Get-ReleaseEvidenceChangePlan -OwnershipPath {_ps_literal(OWNERSHIP)} `
 -ChangedFiles @('web/app/api/status/route.ts','scripts/run_dashboard_sync.py')
$unknown=Get-ReleaseEvidenceChangePlan -OwnershipPath {_ps_literal(OWNERSHIP)} `
 -ChangedFiles @('unowned/new-boundary.bin')
[ordered]@{{docs=$docs.family;worker=$worker.family;mixed=$mixed.family;
 mixed_closed=$mixed.fail_closed;unknown=$unknown.family;
 unknown_nodes=@($unknown.affected_nodes).Count}}|ConvertTo-Json -Compress
""",
    )
    result = json.loads(output)
    assert result == {
        "docs": "docs-only",
        "worker": "worker-route-serialization",
        "mixed": "unknown-cross-family",
        "mixed_closed": True,
        "unknown": "unknown-cross-family",
        "unknown_nodes": 12,
    }


@pytest.mark.parametrize("shell", ("powershell.exe", "pwsh.exe"))
def test_free_plan_producer_recomputes_bounded_workload_and_fails_closed(
    tmp_path: Path, shell: str,
) -> None:
    output = _run_module(
        tmp_path,
        shell,
        f"""
$producers=@(
 [pscustomobject]@{{id='heartbeat';executions_per_day=2880;
  worker_requests_per_execution=1;d1_rows_read_per_execution=4;
  d1_rows_written_per_execution=2;d1_queries_per_invocation=6;
  subrequests_per_invocation=6}},
 [pscustomobject]@{{id='history';executions_per_day=720;
  worker_requests_per_execution=1;d1_rows_read_per_execution=20;
  d1_rows_written_per_execution=25;d1_queries_per_invocation=4;
  subrequests_per_invocation=4}}
)
$proof=[pscustomobject][ordered]@{{
 worker_bundle_config=[pscustomobject]@{{schema_version='worker-bundle-config-v1';
  compressed_bytes=2500000;environment_variables=12;static_assets=300}};
 sql_behavior=[pscustomobject]@{{schema_version='sql-behavior-v1';max_d1_queries_per_invocation=6}};
 workload_manifest=[pscustomobject]@{{schema_version='release-workload-manifest-v1';producers=$producers}};
 data_shape_contract=[pscustomobject]@{{schema_version='d1-data-shape-v1';database_bytes=100000000;account_storage_bytes=100000000}};
 cadence=[pscustomobject]@{{schema_version='bounded-cadence-v1';bounded=$true;producers=@(
  [pscustomobject]@{{id='heartbeat';interval_seconds=30;executions_per_day=2880}},
  [pscustomobject]@{{id='history';interval_seconds=120;executions_per_day=720}})}};
 provider_limits_version='cloudflare-workers-free-2026-08'}}
$passed=Test-ReleaseFreePlanBoundedProof -LimitsPath {_ps_literal(FREE_PLAN)} -Proof $proof
$proof.workload_manifest.producers[1].d1_rows_written_per_execution=200
$blocked=Test-ReleaseFreePlanBoundedProof -LimitsPath {_ps_literal(FREE_PLAN)} -Proof $proof
$proof.workload_manifest.producers[1].d1_rows_written_per_execution=25
$proof.cadence.producers[1].executions_per_day=719
$mismatch=Test-ReleaseFreePlanBoundedProof -LimitsPath {_ps_literal(FREE_PLAN)} -Proof $proof
[ordered]@{{state=$passed.state;requests=$passed.measurements.worker_requests_per_day;
 rows_read=$passed.measurements.d1_rows_read_per_day;
 rows_written=$passed.measurements.d1_rows_written_per_day;
 blocked=$blocked.state;mismatch=$mismatch.reason}}|ConvertTo-Json -Compress
""",
    )
    result = json.loads(output)
    assert result == {
        "state": "PASSED",
        "requests": 3600,
        "rows_read": 25920,
        "rows_written": 23760,
        "blocked": "BLOCKED",
        "mismatch": "FREE_PLAN_CADENCE_MISMATCH",
    }


@pytest.mark.parametrize("shell", ("powershell.exe", "pwsh.exe"))
def test_complete_fifteen_node_waterfall_is_authoritative_and_terminal_is_append_only(
    tmp_path: Path, shell: str,
) -> None:
    evidence_root = tmp_path.parent / f"full-dag-{Path(shell).stem}"
    output = _run_module(
        tmp_path,
        shell,
        f"""
$contract=Get-ReleaseEvidenceContract {_ps_literal(CONTRACT)}
$key='worker:git'
$started=[DateTimeOffset]::Parse('2026-09-01T00:00:00Z')
foreach($definition in @($contract.nodes|Where-Object{{[string]$_.id -notin @('promote_attempt','observe_attempt')}})){{
 $inputs=[ordered]@{{}}
 foreach($name in @($definition.behavior_inputs)){{$inputs[[string]$name]="$([string]$definition.id):$name"}}
 $source=[pscustomobject]@{{qualification_state='PASSED'}}
 if([string]$definition.qualification_kind -eq 'LEASE'){{
  $source|Add-Member -NotePropertyName expires_at -NotePropertyValue '2026-09-01T02:00:00Z'
 }}
 $args=@{{Root={_ps_literal(evidence_root)};ContractPath={_ps_literal(CONTRACT)};
  ValidationKey=$key;BehaviorInputs=[pscustomobject]$inputs;SourceIdentity=$source;
  StartedAt=$started.ToString('o');CompletedAt=$started.AddSeconds(1).ToString('o');
  WhyRan='PRODUCTION_SHAPED_DAG_FIXTURE'}}
 Publish-ReleaseEvidenceAuthorityNode @args -Node ([string]$definition.id) `
  -Adapter ([string]$definition.producer_adapter)|Out-Null
 $started=$started.AddSeconds(1)
}}
$qualification=Assert-ReleaseEvidenceQualification -Root {_ps_literal(evidence_root)} `
 -ContractPath {_ps_literal(CONTRACT)} -ValidationKey $key `
 -Now ([DateTimeOffset]::Parse('2026-09-01T01:00:00Z'))
$promoteDefinition=$contract.nodes|Where-Object id -eq 'promote_attempt'
$promoteInputs=[pscustomobject][ordered]@{{transaction_id='tx-1';target_identity='worker:git';
 dependency_receipts=$qualification.receipt_digests}}
$promoteArgs=@{{Root={_ps_literal(evidence_root)};ContractPath={_ps_literal(CONTRACT)};
 ValidationKey=$key;BehaviorInputs=$promoteInputs;SourceIdentity=([pscustomobject]@{{qualification_state='PASSED';transaction_id='tx-1'}});
 StartedAt=$started.ToString('o');CompletedAt=$started.AddSeconds(1).ToString('o');WhyRan='PROMOTE_FROZEN'}}
$promote=Publish-PromoteAttemptEvidence -Arguments $promoteArgs
$observeInputs=[pscustomobject][ordered]@{{transaction_id='tx-1';target_identity='worker:git';
 observe_contract=[pscustomobject]@{{terminal_state='PASSED'}}}}
$observeArgs=@{{Root={_ps_literal(evidence_root)};ContractPath={_ps_literal(CONTRACT)};
 ValidationKey=$key;BehaviorInputs=$observeInputs;SourceIdentity=([pscustomobject]@{{qualification_state='PASSED';transaction_id='tx-1'}});
 StartedAt=$started.AddSeconds(1).ToString('o');CompletedAt=$started.AddSeconds(2).ToString('o');WhyRan='OBSERVE_PASSED'}}
$first=Publish-ObserveAttemptEvidence -Arguments $observeArgs
$observeArgs.BehaviorInputs=[pscustomobject][ordered]@{{transaction_id='tx-2';target_identity='worker:git';
 observe_contract=[pscustomobject]@{{terminal_state='FAILED'}}}}
$observeArgs.SourceIdentity=[pscustomobject]@{{qualification_state='FAILED';transaction_id='tx-2'}}
$observeArgs.State='FAILED';$observeArgs.WhyRan='OBSERVE_FAILED'
$second=Publish-ObserveAttemptEvidence -Arguments $observeArgs
$current=Get-ReleaseEvidenceCurrentReceipt -Root {_ps_literal(evidence_root)} `
 -ValidationKey $key -Node 'observe_attempt'
$waterfall=Get-ReleaseEvidenceWaterfall -Root {_ps_literal(evidence_root)} -ValidationKey $key
[ordered]@{{prerequisites=@($qualification.receipts.PSObject.Properties).Count;
 nodes=$waterfall.node_count;promote=$promote.state;first=$first.state;second=$second.state;
 first_preserved=($current.receipt_digest -ceq $first.receipt_digest);
 distinct_terminal=($first.receipt_digest -cne $second.receipt_digest)}}|ConvertTo-Json -Compress
""",
    )
    assert json.loads(output) == {
        "prerequisites": 13,
        "nodes": 15,
        "promote": "PASSED",
        "first": "PASSED",
        "second": "FAILED",
        "first_preserved": True,
        "distinct_terminal": True,
    }


def test_authority_cutover_rejects_missing_moved_and_stale_receipts(tmp_path: Path) -> None:
    evidence_root = tmp_path.parent / "authority-negative"
    output = _run_module(
        tmp_path,
        "powershell.exe",
        f"""
$contract=Get-ReleaseEvidenceContract {_ps_literal(CONTRACT)};$key='worker:git'
$missing='';try{{Assert-ReleaseEvidenceQualification -Root {_ps_literal(evidence_root)} `
 -ContractPath {_ps_literal(CONTRACT)} -ValidationKey $key|Out-Null}}catch{{$missing=$_.Exception.Message}}
$inputs=[pscustomobject][ordered]@{{worker_version_id='w';git_sha='g';windows_revision='g';artifact_kind='PRODUCTION_CANDIDATE'}}
$args=@{{Root={_ps_literal(evidence_root)};ContractPath={_ps_literal(CONTRACT)};ValidationKey=$key;
 Node='artifact_provenance';Adapter='Write-CandidateArtifactEvidence';BehaviorInputs=$inputs;
 SourceIdentity=([pscustomobject]@{{qualification_state='PASSED'}});StartedAt='2026-09-01T00:00:00Z';
 CompletedAt='2026-09-01T00:00:01Z';WhyRan='ARTIFACT'}}
Publish-ReleaseEvidenceAuthorityNode @args|Out-Null
$placementInputs=[pscustomobject][ordered]@{{candidate_worker='w';stable_worker='s';traffic_assignment='0/100'}}
$args.Node='candidate_placement';$args.Adapter='Publish-CandidatePlacementEvidence';
$args.BehaviorInputs=$placementInputs;$args.SourceIdentity=[pscustomobject]@{{qualification_state='PASSED';expires_at='2026-09-01T00:10:00Z'}}
Publish-ReleaseEvidenceAuthorityNode @args|Out-Null
$stale='';try{{Assert-ReleaseEvidenceQualification -Root {_ps_literal(evidence_root)} `
 -ContractPath {_ps_literal(CONTRACT)} -ValidationKey $key -RequiredNodes @('candidate_placement') `
 -Now ([DateTimeOffset]::Parse('2026-09-01T00:11:00Z'))|Out-Null}}catch{{$stale=$_.Exception.Message}}
$artifact2=[pscustomobject][ordered]@{{worker_version_id='w2';git_sha='g';windows_revision='g';artifact_kind='PRODUCTION_CANDIDATE'}}
$args.Node='artifact_provenance';$args.Adapter='Write-CandidateArtifactEvidence';$args.BehaviorInputs=$artifact2;
$args.SourceIdentity=[pscustomobject]@{{qualification_state='PASSED'}};$args.CompletedAt='2026-09-01T00:00:02Z'
Publish-ReleaseEvidenceAuthorityNode @args|Out-Null
$moved='';try{{Assert-ReleaseEvidenceQualification -Root {_ps_literal(evidence_root)} `
 -ContractPath {_ps_literal(CONTRACT)} -ValidationKey $key -RequiredNodes @('candidate_placement') `
 -Now ([DateTimeOffset]::Parse('2026-09-01T00:05:00Z'))|Out-Null}}catch{{$moved=$_.Exception.Message}}
[ordered]@{{missing=$missing;stale=$stale;moved=$moved}}|ConvertTo-Json -Compress
""",
    )
    result = json.loads(output)
    assert result["missing"] == "RELEASE_EVIDENCE_PRODUCER_MISSING:artifact_provenance"
    assert result["stale"] == "RELEASE_EVIDENCE_LEASE_STALE:candidate_placement"
    assert result["moved"] == "RELEASE_EVIDENCE_DEPENDENCY_DIGEST_MOVED:candidate_placement"


def test_promote_and_runtime_switch_no_longer_authorize_from_legacy_nested_booleans() -> None:
    promotion = _control_function("Start-ReleasePromotion")
    runtime_switch = _control_function("Update-RuntimeCheckout")
    assert "Assert-ReleaseEvidenceQualification" in promotion
    assert "Publish-PromoteAttemptEvidence" in promotion
    assert "dependency_receipts" in promotion
    assert "candidate.validation_state" not in promotion
    assert "candidate.validation." not in promotion
    assert "promote_attempt" in runtime_switch
    assert "validation_state" not in runtime_switch


@pytest.mark.parametrize("shell", ("powershell.exe", "pwsh.exe"))
def test_controller_registers_exact_candidate_free_plan_proof(
    tmp_path: Path, shell: str,
) -> None:
    runtime_root = tmp_path / "runtime"
    proof_path = tmp_path / "free-plan-proof.json"
    candidate = {
        "validation_key": "worker:git",
        "worker_version_id": "worker",
        "git_sha": "git",
        "windows_revision": "git",
    }
    producer = {
        "id": "bounded-sync",
        "executions_per_day": 1440,
        "worker_requests_per_execution": 1,
        "d1_rows_read_per_execution": 10,
        "d1_rows_written_per_execution": 10,
        "d1_queries_per_invocation": 5,
        "subrequests_per_invocation": 5,
    }
    proof_path.write_text(json.dumps({
        "validation_key": candidate["validation_key"],
        "candidate": candidate,
        "worker_bundle_config": {
            "schema_version": "worker-bundle-config-v1",
            "compressed_bytes": 2_000_000,
            "environment_variables": 10,
            "static_assets": 100,
        },
        "sql_behavior": {
            "schema_version": "sql-behavior-v1",
            "max_d1_queries_per_invocation": 5,
        },
        "workload_manifest": {
            "schema_version": "release-workload-manifest-v1",
            "producers": [producer],
        },
        "data_shape_contract": {
            "schema_version": "d1-data-shape-v1",
            "database_bytes": 100_000_000,
            "account_storage_bytes": 100_000_000,
        },
        "cadence": {
            "schema_version": "bounded-cadence-v1",
            "bounded": True,
            "producers": [{
                "id": producer["id"],
                "interval_seconds": 60,
                "executions_per_day": producer["executions_per_day"],
            }],
        },
        "provider_limits_version": "cloudflare-workers-free-2026-08",
    }), encoding="utf-8")
    control = ROOT / "scripts" / "xauusd_control_center.ps1"
    output = _run_module(
        tmp_path,
        shell,
        f"""
$null=. {_ps_literal(control)} -Action CodeRevision -RuntimeRoot {_ps_literal(runtime_root)} `
 -RepositoryRoot {_ps_literal(ROOT)}
$candidate=[pscustomobject]@{{validation_key='worker:git';worker_version_id='worker';
 git_sha='git';windows_revision='git';artifact_kind='PRODUCTION_CANDIDATE'}}
$receipt=Register-CandidateFreePlanEvidence -Candidate $candidate -ProofPath {_ps_literal(proof_path)}
$mismatch='';$candidate.git_sha='other'
try{{Register-CandidateFreePlanEvidence -Candidate $candidate -ProofPath {_ps_literal(proof_path)}|Out-Null}}
catch{{$mismatch=$_.Exception.Message}}
[ordered]@{{state=$receipt.state;node=$receipt.node;
 requests=$receipt.source_identity.subject.qualification.measurements.worker_requests_per_day;
 mismatch=$mismatch}}|ConvertTo-Json -Compress
""",
    )
    assert json.loads(output) == {
        "state": "PASSED",
        "node": "free_plan",
        "requests": 1440,
        "mismatch": "FREE_PLAN_PROOF_CANDIDATE_MISMATCH",
    }


def test_behavior_index_tamper_and_immutable_collision_fail_closed(tmp_path: Path) -> None:
    evidence_root = tmp_path / "evidence"
    output = _run_module(
        tmp_path,
        "powershell.exe",
        f"""
$inputs=[pscustomobject][ordered]@{{worker_version_id='worker';git_sha='git';
 windows_revision='git';artifact_kind='PRODUCTION_CANDIDATE'}}
$source=[pscustomobject]@{{qualification_state='PASSED'}}
$args=@{{Root={_ps_literal(evidence_root)};ContractPath={_ps_literal(CONTRACT)};
 ValidationKey='worker:git';Node='artifact_provenance';Adapter='Write-CandidateArtifactEvidence';
 BehaviorInputs=$inputs;SourceIdentity=$source;StartedAt='2026-09-01T00:00:00Z';
 CompletedAt='2026-09-01T00:00:01Z';WhyRan='ARTIFACT'}}
$receipt=Publish-ReleaseEvidenceAuthorityNode @args
$behaviorDigest=Get-ReleaseEvidenceSha256 $receipt.behavior_key
$indexPath=Join-Path {_ps_literal(evidence_root)} "_behavior/artifact_provenance/$behaviorDigest.json"
$index=Get-Content -LiteralPath $indexPath -Raw -Encoding UTF8|ConvertFrom-ReleaseEvidenceJson
$index.receipt_digest=('f'*64)
Write-ReleaseEvidenceUtf8Atomic -Path $indexPath -Content (ConvertTo-ReleaseEvidenceJson $index)
$tamper='';try{{Find-ReleaseEvidenceBehaviorReceipt -Root {_ps_literal(evidence_root)} `
 -Node 'artifact_provenance' -BehaviorKey $receipt.behavior_key|Out-Null}}
catch{{$tamper=$_.Exception.Message}}
$keyDigest=Get-ReleaseEvidenceSha256 'worker:git'
$path=Join-Path {_ps_literal(evidence_root)} "$keyDigest/artifact_provenance/$($receipt.receipt_digest).json"
[IO.File]::WriteAllText($path,'{{"different":true}}',[Text.UTF8Encoding]::new($false))
$collision='';try{{Publish-ReleaseEvidenceAuthorityNode @args|Out-Null}}
catch{{$collision=$_.Exception.Message}}
[ordered]@{{tamper=$tamper;collision=$collision}}|ConvertTo-Json -Compress
""",
    )
    assert json.loads(output) == {
        "tamper": "RELEASE_EVIDENCE_BEHAVIOR_RECEIPT_MISSING",
        "collision": "RELEASE_EVIDENCE_IMMUTABLE_COLLISION",
    }


@pytest.mark.parametrize("shell", ("powershell.exe", "pwsh.exe"))
def test_access_root_authority_maps_exact_existing_receipts(
    tmp_path: Path, shell: str,
) -> None:
    output = _run_module(
        tmp_path,
        shell,
        """
$root=('a'*64);$provider=('b'*64)
function Get-LatestAccessProviderInspectionReceipt {
 return [pscustomobject]@{provider_fingerprint=$provider}
}
$humanCandidate=[pscustomobject]@{access_acceptance=[pscustomobject]@{receipt_digest=$root}}
$human=Resolve-ReleaseAccessEvidenceAuthority -Candidate $humanCandidate `
 -AuthInspection ([pscustomobject]@{state='HUMAN_ACCESS_BOUNDARY_ACCEPTED'})
$reusedCandidate=[pscustomobject]@{access_qualification=[pscustomobject]@{
 prior_access_receipt_digest=$root;provider_fingerprint=$provider}}
$reused=Resolve-ReleaseAccessEvidenceAuthority -Candidate $reusedCandidate `
 -AuthInspection ([pscustomobject]@{state='ACCESS_QUALIFICATION_REUSED'})
$renewedCandidate=[pscustomobject]@{access_qualification=[pscustomobject]@{
 root_human_receipt_digest=$root;provider_fingerprint=$provider}}
$renewed=Resolve-ReleaseAccessEvidenceAuthority -Candidate $renewedCandidate `
 -AuthInspection ([pscustomobject]@{state='ACCESS_QUALIFICATION_RENEWED'})
$invalid='';try{Resolve-ReleaseAccessEvidenceAuthority -Candidate ([pscustomobject]@{
 access_qualification=[pscustomobject]@{prior_access_receipt_digest='wrong';
 provider_fingerprint=$provider}}) -AuthInspection ([pscustomobject]@{
 state='ACCESS_QUALIFICATION_REUSED'})|Out-Null}catch{$invalid=$_.Exception.Message}
[ordered]@{human=$human.root_receipt_digest;reused=$reused.root_receipt_digest;
 renewed=$renewed.root_receipt_digest;provider=$renewed.provider_fingerprint;
 invalid=$invalid}|ConvertTo-Json -Compress
""",
    )
    assert json.loads(output) == {
        "human": "a" * 64,
        "reused": "a" * 64,
        "renewed": "a" * 64,
        "provider": "b" * 64,
        "invalid": "ACCESS_EVIDENCE_AUTHORITY_INVALID",
    }


def test_shadow_parity_corpus_is_never_more_permissive_than_legacy(
    tmp_path: Path,
) -> None:
    corpus_path = ROOT / "tests" / "fixtures" / "release_evidence_shadow_parity.json"
    corpus = json.loads(corpus_path.read_text(encoding="utf-8"))
    assert corpus["schema_version"] == "release-evidence-shadow-parity-v1"
    assert {case["id"] for case in corpus["cases"]} == {
        "sanitized-current-passed", "historical-failed",
        "historical-review-required", "historical-access-pending",
        "historical-migration-required", "historical-cpu-pending",
        "provider-unavailable", "superseded", "malformed-tampered",
        "restart-missing-producer",
    }
    # The complete case is exercised through the real fifteen-node PowerShell
    # producer/consumer waterfall above.  Every incomplete historical case is
    # exercised by the real missing/tampered/changed-key negative contracts.
    for case in corpus["cases"]:
        assert case["legacy_promotable"] == (case["legacy_state"] == "PASSED")
        assert not (case["dag_promotable"] and not case["legacy_promotable"]), case["id"]
        if case["dag_fixture"] == "COMPLETE":
            assert case["dag_promotable"] is True
        else:
            assert case["dag_promotable"] is False
