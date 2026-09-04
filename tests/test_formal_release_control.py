from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import re


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
    assert 'TLA_TOOLS_VERSION = "v1.8.0"' in runner
    assert "16b8cd970e07147ff91f126baecba7edd98202e5ab33220a42f8f4358ee94b2b" in runner
    assert '"-workers", "auto"' in runner
    assert '"-coverage"' not in runner
    assert 'choices=("local", "ci")' in runner
    assert 'add_argument("--report"' not in runner
    for field in ("elapsed_seconds", "generated_states", "distinct_states", "maximum_queue_depth", "properties"):
        assert field in runner


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
