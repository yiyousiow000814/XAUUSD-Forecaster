from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
OWNER = ROOT / "scripts" / "recovery_hotfix.ps1"
EVIDENCE_OWNER = ROOT / "scripts" / "release_evidence_authority.ps1"
EVIDENCE_NODES = ROOT / "scripts" / "release_evidence_nodes.ps1"
CHANGE_OWNERSHIP = ROOT / "scripts" / "release-evidence-change-ownership.json"
CONTROL = ROOT / "scripts" / "xauusd_control_center.ps1"
MANIFEST = ROOT / "scripts" / "runtime-control-files.json"


def _ps(value: Path | str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _run_owner(tmp_path: Path, shell: str, body: str) -> str:
    if shutil.which(shell) is None:
        pytest.skip(f"{shell} unavailable")
    probe = tmp_path / f"recovery-{Path(shell).stem}.ps1"
    probe.write_text(
        "$ErrorActionPreference='Stop'\n"
        f". {_ps(OWNER)}\n"
        + body,
        encoding="utf-8-sig",
    )
    command = [shell, "-NoProfile"]
    if shell == "powershell.exe":
        command += ["-ExecutionPolicy", "Bypass"]
    command += ["-File", str(probe)]
    result = subprocess.run(
        command, cwd=ROOT, capture_output=True, text=True, encoding="utf-8"
    )
    if result.returncode:
        raise AssertionError(f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}")
    return result.stdout.strip()


@pytest.mark.parametrize("shell", ["powershell.exe", "pwsh.exe"])
def test_missing_transaction_mode_is_normal_and_recovery_is_explicit(
    tmp_path: Path, shell: str,
) -> None:
    result = _run_owner(
        tmp_path,
        shell,
        "$old=[pscustomobject]@{phase='OBSERVING'};"
        "$new=[pscustomobject]@{mode='RECOVERY_HOTFIX'};"
        "$bad=[pscustomobject]@{mode='MAGIC'};"
        'Write-Output "$(Get-ReleaseTransactionMode $old),'
        '$(Get-ReleaseTransactionMode $new),$(Get-ReleaseTransactionMode $bad)"',
    )
    assert result == "NORMAL,RECOVERY_HOTFIX,UNKNOWN"


@pytest.mark.parametrize("shell", ["powershell.exe", "pwsh.exe"])
@pytest.mark.parametrize(
    ("family", "eligible"),
    [
        ("worker-route-serialization", True),
        ("dashboard-api", True),
        ("dashboard-sync", True),
        ("windows-process-ownership", True),
        ("control-plane-only", True),
        ("d1-schema-migration", False),
        ("access", False),
        ("storage-lifecycle", False),
        ("runtime-root-authority", False),
        ("annotator-news", False),
        ("collector-decision", False),
        ("unknown-cross-family", False),
    ],
)
def test_recovery_hotfix_family_eligibility_is_fail_closed(
    tmp_path: Path, shell: str, family: str, eligible: bool,
) -> None:
    result = _run_owner(
        tmp_path,
        shell,
        f"$plan=[pscustomobject]@{{family='{family}';fail_closed=$false}};"
        "$r=Get-RecoveryHotfixEligibility $plan;"
        'Write-Output "$($r.eligible),$($r.eligibility_class),'
        '$($r.observe_contract.required_consecutive_health_cycles)"',
    )
    parts = result.split(",")
    assert parts[0] == str(eligible)
    assert parts[1] == ("BOUNDED_RECOVERY_HOTFIX" if eligible else "NORMAL_RELEASE_REQUIRED")
    assert parts[2] == ("2" if eligible else "")


@pytest.mark.parametrize("shell", ["powershell.exe", "pwsh.exe"])
def test_real_change_ownership_boundary_allows_only_one_eligible_family(
    tmp_path: Path, shell: str,
) -> None:
    result = _run_owner(
        tmp_path,
        shell,
        f". {_ps(EVIDENCE_NODES)};. {_ps(EVIDENCE_OWNER)};"
        f"$ownership={_ps(CHANGE_OWNERSHIP)};"
        "$worker=Get-RecoveryHotfixEligibility (Get-ReleaseEvidenceChangePlan "
        "-OwnershipPath $ownership -ChangedFiles @('web/app/api/status/route.ts'));"
        "$control=Get-RecoveryHotfixEligibility (Get-ReleaseEvidenceChangePlan "
        "-OwnershipPath $ownership -ChangedFiles @('scripts/recovery_hotfix.ps1'));"
        "$dashboard=Get-RecoveryHotfixEligibility (Get-ReleaseEvidenceChangePlan "
        "-OwnershipPath $ownership -ChangedFiles @('scripts/run_dashboard_sync.py'));"
        "$runtimeRoot=Get-RecoveryHotfixEligibility (Get-ReleaseEvidenceChangePlan "
        "-OwnershipPath $ownership -ChangedFiles @('xauusd_forecaster/runtime_paths.py'));"
        "$d1=Get-RecoveryHotfixEligibility (Get-ReleaseEvidenceChangePlan "
        "-OwnershipPath $ownership -ChangedFiles @('web/drizzle/0001.sql'));"
        "$mixed=Get-RecoveryHotfixEligibility (Get-ReleaseEvidenceChangePlan "
        "-OwnershipPath $ownership -ChangedFiles @('web/app/api/status/route.ts',"
        "'scripts/run_dashboard_sync.py'));"
        "$unknown=Get-RecoveryHotfixEligibility (Get-ReleaseEvidenceChangePlan "
        "-OwnershipPath $ownership -ChangedFiles @('unowned/new-boundary.bin'));"
        'Write-Output "$($worker.eligible),$($control.eligible),$($dashboard.eligible),'
        '$($runtimeRoot.eligible),$($d1.eligible),$($mixed.eligible),'
        '$($mixed.reason),$($unknown.eligible)"',
    )
    assert result == (
        "True,True,True,False,False,False,RECOVERY_HOTFIX_CHANGE_FAMILY_UNKNOWN,False"
    )


@pytest.mark.parametrize("shell", ["powershell.exe", "pwsh.exe"])
def test_degraded_known_active_can_recover_but_unknown_cannot(
    tmp_path: Path, shell: str,
) -> None:
    lkg = (
        "[pscustomobject]@{git_sha=('a'*40);worker_version_id="
        "'11111111-1111-4111-8111-111111111111';windows_revision=('a'*40);"
        "artifact_kind='PRODUCTION_CANDIDATE'}"
    )
    result = _run_owner(
        tmp_path,
        shell,
        f"$lkg={lkg};$active=[pscustomobject]@{{observation_status='AVAILABLE';"
        "identity_status='COMPLETE';ownership_status='SINGLE_OWNER';health='DEGRADED'};"
        "$model=[pscustomobject]@{transaction_active=$false;active=$active;"
        "last_known_good=$lkg;drift_status='DRIFT'};"
        "$restore=Assert-RecoveryRuntimeAuthority $model RESTORE_LKG;"
        "$hotfix=try{Assert-RecoveryRuntimeAuthority $model APPLY_RECOVERY_HOTFIX|Out-Null;'BAD'}"
        "catch{$_.Exception.Message};$active.observation_status='UNKNOWN';"
        "$unknown=try{Assert-RecoveryRuntimeAuthority $model RESTORE_LKG|Out-Null;'BAD'}"
        "catch{$_.Exception.Message};"
        'Write-Output "$($restore.active_degraded),$hotfix,$unknown"',
    )
    assert result == (
        "True,RECOVERY_HOTFIX_REQUIRES_ACTIVE_COMMITTED_MATCH,"
        "RECOVERY_ACTIVE_AUTHORITY_UNKNOWN"
    )


@pytest.mark.parametrize("shell", ["powershell.exe", "pwsh.exe"])
def test_action_time_dag_renewal_allows_degraded_only_in_explicit_recovery_mode(
    tmp_path: Path, shell: str,
) -> None:
    result = _run_owner(
        tmp_path,
        shell,
        f". {_ps(EVIDENCE_NODES)};. {_ps(EVIDENCE_OWNER)};"
        "$script:promotionFreshnessMinimumLifetime=[TimeSpan]::FromMinutes(5);"
        "$script:accessProviderAuditMaximumLookback=[TimeSpan]::FromMinutes(5);"
        "$script:releaseEvidenceRoot='evidence';$script:releaseEvidenceContractPath='contract';"
        "$candidate=[pscustomobject]@{validation_key='candidate:key';worker_version_id='candidate';"
        "migration_qualification=[pscustomobject]@{live_owner='NOT_REQUIRED'}};"
        "$stable=[pscustomobject]@{worker_version_id='stable';windows_revision=('a'*40)};"
        "$state=[pscustomobject]@{candidate=$candidate;stable=$stable};"
        "function Get-ReleaseEvidenceCurrentReceipt{param($Root,$ValidationKey,$Node);"
        "if($Node -eq 'migration_acceptance'){return [pscustomobject]@{source_identity="
        "[pscustomobject]@{qualification_state='NOT_REQUIRED';behavior_inputs="
        "[pscustomobject]@{current_generation='NOT_REQUIRED'}}}};"
        "if($Node -eq 'human_access_root'){return [pscustomobject]@{source_identity="
        "[pscustomobject]@{qualification_state='NOT_REQUIRED'}}};throw $Node};"
        "function New-ReleaseEvidenceAdapterArguments{param($Candidate,$BehaviorInputs,"
        "$SourceIdentity,$StartedAt,$CompletedAt,$WhyRan);return @{}};"
        "function Publish-MigrationLiveLeaseEvidence{param($Arguments)};"
        "function Publish-CandidatePlacementEvidence{param($Arguments)};"
        "function Publish-AccessProviderLeaseEvidence{param($Arguments)};"
        "function Publish-RollbackPrecheckEvidence{param($Arguments)};"
        "function Get-CurrentReleaseRuntimeReadModel{return [pscustomobject]@{"
        "transaction_active=$false;active_matches_committed=$true;committed_stable=$stable;"
        "active=[pscustomobject]@{worker_version_id='stable';observation_status='AVAILABLE';"
        "identity_status='COMPLETE';health='DEGRADED';ownership_status='SINGLE_OWNER'};"
        "observed_at='2026-09-03T00:00:00Z'}};"
        "function Assert-ReleaseEvidenceQualification{return [pscustomobject]@{state='PASSED'}};"
        "$model=Get-CurrentReleaseRuntimeReadModel;"
        "$ok=(Publish-PromotionFreshnessEvidence -State $state -AllowDegradedActive "
        "-RuntimeReadModel $model).state;$model.active_matches_committed=$false;"
        "$restore=(Publish-PromotionFreshnessEvidence -State $state -AllowDegradedActive "
        "-RecoveryAction RESTORE_LKG -RuntimeReadModel $model).state;"
        "$normal=try{Publish-PromotionFreshnessEvidence -State $state|Out-Null;'BAD'}"
        "catch{$_.Exception.Message};Write-Output \"$ok,$restore,$normal\"",
    )
    assert result == "PASSED,PASSED,ROLLBACK_RUNTIME_READ_MODEL_INVALID"


@pytest.mark.parametrize("shell", ["powershell.exe", "pwsh.exe"])
def test_recovery_receipt_refs_require_complete_exact_digests(
    tmp_path: Path, shell: str,
) -> None:
    result = _run_owner(
        tmp_path,
        shell,
        "$digests=[ordered]@{};1..13|%{$digests[('n'+$_)]=('a'*64)};"
        "$q=[pscustomobject]@{receipt_digests=[pscustomobject]$digests};"
        "$ok=Get-RecoveryEvidenceReceiptReferences $q;"
        "$q.receipt_digests.n7='bad';$bad=try{Get-RecoveryEvidenceReceiptReferences $q|"
        "Out-Null;'BAD'}catch{$_.Exception.Message};"
        'Write-Output "$(@($ok.PSObject.Properties).Count),$bad"',
    )
    assert result == "13,RECOVERY_EVIDENCE_RECEIPT_INVALID:n7"


@pytest.mark.parametrize("shell", ["powershell.exe", "pwsh.exe"])
def test_recovery_metadata_binds_inside_existing_promote_attempt_contract(
    tmp_path: Path, shell: str,
) -> None:
    contract = ROOT / "scripts" / "release-evidence-contract.json"
    result = _run_owner(
        tmp_path,
        shell,
        f". {_ps(EVIDENCE_NODES)};. {_ps(EVIDENCE_OWNER)};"
        "$target=[pscustomobject][ordered]@{validation_key='candidate:key';"
        "worker_version_id='worker';git_sha=('a'*40);windows_revision=('a'*40);"
        "artifact_kind='PRODUCTION_CANDIDATE';release_mode='RECOVERY_HOTFIX';"
        "recovery_action='APPLY_RECOVERY_HOTFIX'};"
        "$inputs=[pscustomobject][ordered]@{transaction_id='tx';target_identity=$target;"
        "dependency_receipts=[pscustomobject]@{exact_head_ci=('b'*64)}};"
        f"$key=Get-ReleaseEvidenceBehaviorKey -ContractPath {_ps(contract)} "
        "-Node 'promote_attempt' -Inputs $inputs;Write-Output $key",
    )
    assert result.startswith("release-behavior-key-v1:")
    assert len(result) == len("release-behavior-key-v1:") + 64


def test_recovery_uses_same_evidence_dag_and_existing_phases() -> None:
    owner = OWNER.read_text(encoding="utf-8")
    control = CONTROL.read_text(encoding="utf-8")
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assert "recovery_hotfix.ps1" in manifest["files"]
    assert "PromoteRecoveryHotfix" in control
    assert "RestoreLastKnownGood" in control
    assert "Publish-PromoteAttemptEvidence" in control
    assert "Publish-ObserveAttemptEvidence" in control
    assert "hotfix_receipt" not in owner + control
    assert "hotfix_exact_ci" not in owner + control
    assert "RECOVERY_HOTFIX" in owner
    assert "RESTORE_LKG" in owner
    assert "APPLY_RECOVERY_HOTFIX" in owner
    assert "PREPARE" not in owner and "VERIFY" not in owner


@pytest.mark.parametrize("shell", ["powershell.exe", "pwsh.exe"])
def test_control_center_and_recovery_owner_parse_under_both_powershells(shell: str) -> None:
    if shutil.which(shell) is None:
        pytest.skip(f"{shell} unavailable")
    body = (
        f"$files=@({_ps(OWNER)},{_ps(CONTROL)});"
        "$bad=@();foreach($f in $files){$t=$null;$e=$null;"
        "[void][Management.Automation.Language.Parser]::ParseFile($f,[ref]$t,[ref]$e);"
        "if($e.Count){$bad+=$e}};if($bad.Count){$bad|% Message;exit 1};'PARSE_OK'"
    )
    command = [shell, "-NoProfile"]
    if shell == "powershell.exe":
        command += ["-ExecutionPolicy", "Bypass"]
    command += ["-Command", body]
    result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8")
    assert result.returncode == 0, result.stderr + result.stdout
    assert result.stdout.strip() == "PARSE_OK"
