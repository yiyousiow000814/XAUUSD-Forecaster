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
    assert "ReviewIsNotPassed" in model
    assert "StableChangesOnlyAfterObservation" in model
    assert "SwitchEventuallyTerminates" in model


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
    assert "argparse" not in runner
    assert 'shutil.which("java")' in runner
    assert "python scripts/run_tla_model.py" in workflow
    assert "name: Release Control TLC" in workflow
