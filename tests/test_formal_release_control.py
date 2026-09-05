from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import re
import hashlib
import shutil
import pytest


ROOT = Path(__file__).resolve().parents[1]
FORMAL = ROOT / "formal" / "release-control"
MANIFEST = json.loads((FORMAL / "shards.json").read_text(encoding="utf-8"))

LEGACY_PROPERTIES = {
    "TypeOK", "AtMostOneProductionWriter", "PrepareVerifyKeepsStableSync",
    "CandidatePreparationPreservesStable", "VerificationWatermarkDoesNotRegress",
    "StaleSupervisorIsFenced", "PassedIdentityIsExact", "AcceptedEvidenceIsRequired",
    "AccessEvidenceIsRequired", "InvalidAccessReceiptCannotPass",
    "AccessApprovalIsIdempotent", "PassedGatesAreSafe", "HardFailuresBlock",
    "UnrelatedDebtIsNotFailure", "SwitchRequiresAcceptance",
    "StableUnchangedDuringSwitchAndObserve", "SingleTransaction",
    "ActiveLegacyEqualsCurrent", "LegacyStableWritesRemainFenced",
    "CurrentIdentitySetMatchesGeneration", "FreshStagingIdentitySetMatchesGeneration",
    "CurrentGenerationCannotBeCleaned", "FreshStagingCannotBeCleaned",
    "InvalidStagedLegacyCannotActivate", "RecoveredActivationRequiresIndependentChecks",
    "ActiveRecoveredSupervisorIsSafe", "CpuQualificationRequiredForPass",
    "ProviderPendingIsNotCandidateFailure", "CpuRetryBudgetIsBounded",
    "CpuHardFailureCannotQualify", "ReusedCpuEvidenceMatchesArtifact",
    "CpuRecoveryPreservesIndependentStages", "StableChangesOnlyAfterObservation",
    "CpuEvidenceOnlyGrows", "ObservedFailureEventuallyRestoresPrevious",
    "SwitchFailureEventuallyTerminates", "TransactionEventuallyTerminates",
    "AbandonedInstallEventuallySafe",
}


def test_every_legacy_property_has_an_authoritative_shard() -> None:
    assigned = {prop for shard in MANIFEST["shards"] for prop in shard["properties"]}
    assert LEGACY_PROPERTIES <= assigned
    for shard in MANIFEST["shards"]:
        module = FORMAL / shard["module"]
        config = FORMAL / shard["config"]
        assert module.is_file()
        assert config.is_file()
        contract = module.read_text(encoding="utf-8") + config.read_text(encoding="utf-8")
        for prop in shard["properties"]:
            assert prop in contract, (shard["id"], prop)


def test_required_models_do_not_reintroduce_the_monolithic_cartesian_product() -> None:
    cpu = (FORMAL / "CpuEvidence.tla").read_text(encoding="utf-8")
    integration = (FORMAL / "ReleaseIntegration.tla").read_text(encoding="utf-8")
    core = (FORMAL / "CoreRelease.tla").read_text(encoding="utf-8")
    other = "\n".join(
        (FORMAL / name).read_text(encoding="utf-8")
        for name in ("InstallRecovery.tla", "NewsMigration.tla", "AccessEvidence.tla")
    )
    assert "evidence" in cpu and "topUps" in cpu and "receiptKey" in cpu
    assert 'CpuStates == {"NOT_REQUIRED", "PENDING", "QUALIFIED", "HARD_FAILURE"}' in integration
    assert "CpuSamples" not in integration + core + other
    for detailed_cpu_dimension in (
        "receiptValid", "receiptQuotasSatisfied", "reserveUses", "topUps"
    ):
        assert detailed_cpu_dimension not in integration + core + other
    assert "install" not in cpu and "stagingGeneration" not in cpu and "accessReceipt" not in cpu
    assert "evidence" not in core and "topUps" not in core


def test_cpu_formal_shard_models_one_globally_bounded_multi_family_repair() -> None:
    cpu = (FORMAL / "CpuEvidence.tla").read_text(encoding="utf-8")
    safety = (FORMAL / "CpuEvidenceSafety.cfg").read_text(encoding="utf-8")
    liveness = (FORMAL / "CpuEvidenceLiveness.cfg").read_text(encoding="utf-8")
    for contract in (
        "MaxRepairFamilies == 4",
        "RequestsPerFamily == 4",
        "MaxRepairRequests == 16",
        "repairSet' = Families \\ evidence",
        "acceptedBeforeRepair' = evidence",
        "DeficitRepairRequestBudgetIsBounded",
        "DeficitRepairSetIsFrozen",
        "QualifiedFamiliesAreNeverReplayed",
        "DeficitRepairCannotFabricateEvidence",
        "NoSecondDeficitRepairRound",
    ):
        assert contract in cpu
    for prop in (
        "DeficitRepairRequestBudgetIsBounded",
        "DeficitRepairSetIsFrozen",
        "QualifiedFamiliesAreNeverReplayed",
        "DeficitRepairCannotFabricateEvidence",
        "NoSecondDeficitRepairRound",
    ):
        assert prop in safety
        assert prop in liveness


def test_cpu_formal_shard_models_single_use_outlier_confirmation() -> None:
    cpu = (FORMAL / "CpuEvidence.tla").read_text(encoding="utf-8")
    safety = (FORMAL / "CpuEvidenceSafety.cfg").read_text(encoding="utf-8")
    liveness = (FORMAL / "CpuEvidenceLiveness.cfg").read_text(encoding="utf-8")
    for contract in (
        'state = "OUTLIER_REVIEW"',
        'state = "CONFIRMING"',
        'qualification = "ISOLATED_OUTLIER"',
        "confirmationUses' = 1",
        "originalOutlierRetained' = TRUE",
    ):
        assert contract in cpu
    for prop in (
        "OutlierRequiresBoundedConfirmation",
        "NoSecondOutlierConfirmation",
        "OutlierConfirmationMatchesRequestShape",
        "RepeatedCpuPressureCannotQualify",
        "OriginalOutlierCannotBeErased",
    ):
        assert prop in safety
        assert prop in liveness


def test_access_formal_shard_models_machine_renewal_without_new_human_root() -> None:
    access = (FORMAL / "AccessEvidence.tla").read_text(encoding="utf-8")
    config = (FORMAL / "AccessEvidenceSafety.cfg").read_text(encoding="utf-8")
    for contract in (
        'machineReceiptState = "STALE"',
        'auditState = "CLEAN"',
        "priorHumanValid",
        "chainValid",
        "RenewQualification",
        "ApplicableRenewedEvidence",
    ):
        assert contract in access
    for prop in (
        "RenewalRequiresContinuousAudit",
        "StaleMachineEvidenceCannotPass",
        "BrokenChainCannotRenew",
        "RenewalKeepsHumanRoot",
        "RenewalIsBounded",
    ):
        assert prop in access
        assert prop in config


def test_formal_workflow_is_parallel_bounded_and_cancels_stale_heads() -> None:
    workflow = (ROOT / ".github" / "workflows" / "formal-verification.yml").read_text(encoding="utf-8")
    timeouts = [int(value) for value in re.findall(r"timeout-minutes:\s*(\d+)", workflow)]
    assert timeouts and max(timeouts) <= 5
    assert "matrix: ${{ fromJSON(needs.plan.outputs.matrix) }}" in workflow
    assert "fail-fast: false" in workflow
    assert "cancel-in-progress: true" in workflow
    assert "formal-verification-pr-${{ github.event.pull_request.number || github.ref }}" in workflow
    assert workflow.count("name: Release Control TLC") == 1
    assert "needs: [plan, shards]" in workflow
    assert "needs.shards.result" in workflow
    assert "ReleaseControlSafety.cfg" not in workflow
    assert "timeout-minutes: 90" not in workflow


def test_selector_uses_authoritative_ownership_and_has_a_bounded_noop(monkeypatch) -> None:
    spec = importlib.util.spec_from_file_location("select_tla_shards", ROOT / "scripts" / "select_tla_shards.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "_changed_paths", lambda _base: ["formal/release-control/shards.json"])
    assert {item["id"] for item in module.select("base")} == {item["id"] for item in MANIFEST["shards"]}
    monkeypatch.setattr(module, "_changed_paths", lambda _base: ["web/app/page.tsx"])
    assert module.select("base") == [{"id": "no-modeled-impact"}]
    monkeypatch.setattr(module, "_changed_paths", lambda _base: ["scripts/worker_cpu_evidence.ps1"])
    assert {item["id"] for item in module.select("base")} == {
        "cpu-evidence-safety", "cpu-evidence-liveness"
    }


def test_runner_pins_tool_and_emits_machine_readable_measurements() -> None:
    runner = (ROOT / "scripts" / "run_tla_model.py").read_text(encoding="utf-8")
    lock = json.loads((ROOT / "formal/tools/tlc/tool-lock.json").read_text(encoding="utf-8"))
    assert len(lock["sha256"]) == 64
    assert hashlib.sha256((ROOT / lock["artifact"]).read_bytes()).hexdigest() == lock["sha256"]
    assert "urlopen" not in runner
    assert '"-workers", "auto"' in runner
    assert '"-coverage"' not in runner
    assert 'choices=("local", "ci")' in runner
    assert 'add_argument("--report"' not in runner
    for field in ("elapsed_seconds", "generated_states", "distinct_states", "maximum_queue_depth", "properties"):
        assert field in runner


@pytest.fixture
def tool_runner():
    spec = importlib.util.spec_from_file_location("run_tla_model", ROOT / "scripts/run_tla_model.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_cold_and_warm_cache_use_the_reviewed_repository_bytes(tmp_path, tool_runner):
    lock = tool_runner._load_tool_lock()
    cold = tool_runner._ensure_tools(tmp_path)
    assert cold.parent.name == lock["sha256"]
    assert cold.read_bytes() == (ROOT / lock["artifact"]).read_bytes()
    before = cold.stat().st_mtime_ns
    assert tool_runner._ensure_tools(tmp_path) == cold
    assert cold.stat().st_mtime_ns == before
    assert tool_runner._sha256(cold) == lock["sha256"]


@pytest.mark.parametrize("failure", ["missing", "tamper", "html", "truncated", "cache-tamper", "classpath"])
def test_invalid_or_unavailable_tool_never_starts_java(tmp_path, monkeypatch, tool_runner, failure):
    lock = tool_runner._load_tool_lock()
    source = tmp_path / lock["artifact"]
    source.parent.mkdir(parents=True)
    shutil.copyfile(ROOT / lock["artifact"], source)
    monkeypatch.setattr(tool_runner, "ROOT", tmp_path)
    monkeypatch.setattr(tool_runner, "_load_tool_lock", lambda: lock)
    cache = tmp_path / ".local/tools"
    if failure in {"cache-tamper", "classpath"}:
        cached = tool_runner._ensure_tools(cache)
        if failure == "cache-tamper":
            cached.write_bytes(b"corrupt cache")
        else:
            (cached.parent / "CommunityModules.jar").write_bytes(b"unreviewed dependency")
    elif failure == "missing":
        source.unlink()
    elif failure == "html":
        source.write_bytes(b"<html>upstream unavailable</html>")
    elif failure == "truncated":
        source.write_bytes(source.read_bytes()[:2000])
    else:
        payload = bytearray(source.read_bytes())
        payload[100] ^= 1
        source.write_bytes(payload)
    def forbidden(*args, **kwargs):
        pytest.fail("Java/native execution occurred before tool verification")
    monkeypatch.setattr(tool_runner.subprocess, "run", forbidden)
    monkeypatch.setattr(tool_runner.subprocess, "Popen", forbidden)
    with pytest.raises(tool_runner.ToolError) as raised:
        tool_runner._execute(MANIFEST["shards"][0], {}, tmp_path / "model.log")
    assert raised.value.state == ("TOOL_UNAVAILABLE" if failure == "missing" else "TOOL_INTEGRITY_FAILED")


@pytest.mark.parametrize("payload", [b"<html>error</html>", b"PK\x03\x04truncated"])
def test_matching_digest_is_not_a_substitute_for_jar_structure(tmp_path, tool_runner, payload):
    path = tmp_path / "invalid.jar"
    path.write_bytes(payload)
    fixture_lock = {"size": len(payload), "sha256": hashlib.sha256(payload).hexdigest()}
    with pytest.raises(tool_runner.ToolError, match="TOOL_INTEGRITY_FAILED"):
        tool_runner._verify_jar(path, fixture_lock)


@pytest.mark.parametrize("mode", ["local", "ci"])
def test_tool_failure_report_is_not_model_pass(tmp_path, monkeypatch, tool_runner, mode):
    monkeypatch.setattr(tool_runner, "ROOT", tmp_path)
    monkeypatch.setattr(tool_runner.sys, "argv", ["runner", "--shard", MANIFEST["shards"][0]["id"], "--output", mode])
    assert tool_runner.main() != 0
    reports = tmp_path / (".local/formal-results" if mode == "local" else "formal-results")
    report = json.loads(next(reports.glob("*.json")).read_text(encoding="utf-8"))
    assert report["result"] == "TOOL_UNAVAILABLE"
    assert report["tool_identity"]["sha256"] == tool_runner._load_tool_lock()["sha256"]


def test_model_interfaces_match_the_cpu_control_implementation() -> None:
    evidence = (ROOT / "scripts" / "worker_cpu_evidence.ps1").read_text(encoding="utf-8")
    controller = (ROOT / "scripts" / "xauusd_control_center.ps1").read_text(encoding="utf-8")
    for production_state in (
        "PROVIDER_EVIDENCE_PENDING", "PROVIDER_EVIDENCE_INSUFFICIENT",
        "HARD_FAILURE", "QUALIFIED", "QUALIFIED_WITH_PROVIDER_OMISSION",
        "CPU_OUTLIER_REVIEW_REQUIRED", "QUALIFIED_WITH_ISOLATED_CPU_OUTLIER",
    ):
        assert production_state in evidence + controller
    assert "qualification_key" in evidence
    assert "RetryCandidateValidation" in controller
    assert "CpuQualificationRequiredForPass" in (FORMAL / "ReleaseIntegration.tla").read_text(encoding="utf-8")
    assert "NoPromoteFromPendingCpu" in (FORMAL / "CoreRelease.tla").read_text(encoding="utf-8")


def test_release_integration_models_authoritative_receipt_waterfall() -> None:
    integration = (FORMAL / "ReleaseIntegration.tla").read_text(encoding="utf-8")
    config = (FORMAL / "ReleaseIntegrationSafety.cfg").read_text(encoding="utf-8")
    for contract in (
        "CompleteEvidenceRequiredForPass",
        "BehaviorKeyChangeInvalidatesReuse",
        "StaleLeaseCannotAuthorize",
        "TamperedReceiptCannotPromote",
        "DependencyDigestCannotBeReplaced",
        "ImmutableReusePreservesIdentity",
        "ReadPlanningDoesNotMutateProduction",
        "EvidenceTransactionIsSingle",
    ):
        assert contract in integration
        assert contract in config
    for unrelated_detail in (
        "CpuSamples", "accessReceipt", "stagingGeneration", "installCheckpoint",
    ):
        assert unrelated_detail not in integration


def test_runtime_read_model_formal_shard_separates_observation_from_authority() -> None:
    model = (FORMAL / "ReleaseRuntimeReadModel.tla").read_text(encoding="utf-8")
    config = (FORMAL / "ReleaseRuntimeReadModelSafety.cfg").read_text(encoding="utf-8")
    for contract in (
        "ActiveMismatchDoesNotMoveCommittedOrLkg",
        "ArtifactExistenceIndependentFromPlacement",
        "NotAssignedAloneDoesNotMeanArtifactMissing",
        "ReverseAttemptRequiresSafeAuthority",
        "FailedOrUnknownLookupFailsClosed",
        "UnknownActiveObservationFailsClosed",
        "ActiveDriftFailsClosed",
        "ReverseTransactionRequiresActualActiveCommittedEquality",
        "DegradedAuthorityAllowsReverse",
        "InvalidCommittedIdentityFailsClosed",
        "InvalidPreviousIdentityFailsClosed",
        "ArbitraryLegacyLabelFailsClosed",
        "ExactNarrowLegacyReachesArtifactEvaluation",
        "InvalidOwnershipFailsClosed",
        "MissingObservationStatusIsNotAvailable",
        "ReadObservationDoesNotMutateRelease",
        "ReadModelNeverChangesCommittedOrLkg",
    ):
        assert contract in model
        assert contract in config
    assert "CpuSamples" not in model
    assert "accessReceipt" not in model
    assert "stagingGeneration" not in model
    assert "CommitAfterSuccessfulObservation" not in model
    assert "activeMatchesCommitted" not in model
    assert '/\\ active = committed' in model
    assert "transaction => active = committed" in model


def test_recovery_hotfix_formal_shards_keep_mode_orthogonal_and_bounded() -> None:
    model = (FORMAL / "RecoveryHotfix.tla").read_text(encoding="utf-8")
    safety = (FORMAL / "RecoveryHotfixSafety.cfg").read_text(encoding="utf-8")
    liveness = (FORMAL / "RecoveryHotfixLiveness.cfg").read_text(encoding="utf-8")
    for contract in (
        "ActiveUnknownCannotBegin",
        "RestoreLkgDoesNotChangeCommitted",
        "HotfixCommitsOnlyAfterObservation",
        "FailedHotfixRestoresLkg",
        "SingleRecoveryTransaction",
        "ForbiddenFamilyCannotEnterHotfix",
        "RecoveryUsesEvidenceDag",
        "RecoveryModeAddsNoPhase",
        "DegradedActiveHasRecoveryPath",
        "DriftedActiveCanRestoreLkg",
    ):
        assert contract in model
        assert contract in safety
    assert "RecoveryEventuallyTerminates" in model
    assert "RecoveryEventuallyTerminates" in liveness
    assert '{"STABLE", "SWITCH", "OBSERVE"}' in model
    assert "hotfixReceipt" not in model
