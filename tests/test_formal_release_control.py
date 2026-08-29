from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_formal_release_lifecycle_is_simplification_first() -> None:
    model = (ROOT / "formal" / "release-control" / "ReleaseControl.tla").read_text(
        encoding="utf-8"
    )
    assert '{"STABLE", "PREPARE", "VERIFY", "SWITCH", "OBSERVE"}' in model
    for accidental_top_level_state in (
        'releasePhase = "PRECHECK"',
        'releasePhase = "CUTOVER"',
        'releasePhase = "REVERSE_OBSERVING"',
        'releasePhase = "RECOVERY_REQUIRED"',
    ):
        assert accidental_top_level_state not in model
    assert "DegradeHealth" in model
    assert "ObserveFailure" in model
    assert "ApplyRecoverySwitch" in model
    assert "ObserveRecovery" in model
    assert "StageLegacyInvalid" in model
    assert "FreshStagingCompatible" in model
    assert "VerifyAbandonedInstall" in model
    assert "StableChangesOnlyAfterObservation" in model
    assert "TransactionEventuallyTerminates" in model
    assert "RequireAccessReview" in model
    assert "RecordAccessReceipt" in model
    assert "ApproveAccessReceipt" in model
    assert "ApplicableAccessEvidence" in model
    assert "InvalidAccessReceiptCannotPass" in model
    assert "AccessApprovalIsIdempotent" in model
    assert "accessRepeatObserved" in model
    assert 'ChangeCpuArtifact ==\n    /\\ release.phase = "PREPARE"' in model
    assert 'AcceptCpuIndependentStages ==\n    /\\ release.phase = "PREPARE"' in model
    assert 'ReuseCpuQualification ==\n    /\\ release.phase = "VERIFY"' in model
    assert 'BeginCpuEvidence ==\n    /\\ release.phase = "VERIFY"' in model
    complete_prepare = model.split("CompletePrepare ==", 1)[1].split(
        "PassEvidence", 1
    )[0]
    assert "cpu.independentStages = CpuIndependentStages" in complete_prepare
    require_access = model.split("RequireAccessReview ==", 1)[1].split(
        "RecordAccessReceipt", 1
    )[0]
    assert "cpu.qualified" in require_access


def test_tlc_runner_and_ci_pin_one_verified_tool() -> None:
    runner = (ROOT / "scripts" / "run_tla_model.py").read_text(encoding="utf-8")
    workflow = (ROOT / ".github" / "workflows" / "formal-verification.yml").read_text(
        encoding="utf-8"
    )
    digest = "eabd140a70f49eb9305a3bd3f3df944eddf87e5a90d329789085f8953a80533a"
    assert 'TLA_TOOLS_VERSION = "v1.8.0"' in runner
    assert digest in runner
    assert "ReleaseControlSafety.cfg" in runner
    assert "ReleaseControlLiveness.cfg" in runner
    assert '"-noGenerateSpecTE"' in runner
    assert '"-coverage"' in runner
    assert "argparse" not in runner
    assert 'shutil.which("java")' in runner
    assert "python scripts/run_tla_model.py" in workflow
    assert "name: Release Control TLC" in workflow
    assert '"-coverage",\n                "10",' in runner
    assert '"-workers",\n                "auto",' in runner
    assert "timeout-minutes: 45" in workflow
