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
    assert "eabd140a70f49eb9305a3bd3f3df944eddf87e5a90d329789085f8953a80533a" in runner
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
    ):
        assert production_state in evidence + controller
    assert "qualification_key" in evidence
    assert "RetryCandidateValidation" in controller
    assert "CpuQualificationRequiredForPass" in (FORMAL / "ReleaseIntegration.tla").read_text(encoding="utf-8")
    assert "NoPromoteFromPendingCpu" in (FORMAL / "CoreRelease.tla").read_text(encoding="utf-8")
