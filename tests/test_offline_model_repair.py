"""Causal fitting and honest single-position research simulation contracts."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

from xauusd_forecaster import offline_model_repair as research
from xauusd_forecaster.offline_model_paths import research_path


def row(i=0, **changes):
    start = datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(minutes=5*i)
    value = (i % 11 - 5) / 10
    result = dict(source_decision_id=str(i), decision_time=start.isoformat(),
                  clock_complete=True, evidence_lane="LIVE_OOS", valid=True,
                  u5=.01, bid=100., ask=100.01, session_state="OPEN",
                  active_generation="g1", gross_target_u5=value*.5+.02,
                  long_log_return=.001, short_log_return=-.0012,
                  market_computed_at=start.isoformat(), input_available_at=start.isoformat(),
                  entry_time=(start+timedelta(seconds=1)).isoformat(),
                  exit_time=(start+timedelta(minutes=30, seconds=1)).isoformat(),
                  label_available_at=(start+timedelta(minutes=31)).isoformat(),
                  market_features={k: value+n*.1 for n, k in enumerate(research.MARKET_FEATURES)},
                  predictions={k: dict(predicted_u5=value, recorded_action="LONG", model_version=k+"-v1",
                                       prediction_created_at=start.isoformat()) for k in research.IDENTITIES})
    result.update(changes)
    return result


def plan():
    return dict(fold_boundaries=["2026-01-02T00:00:00+00:00", "2026-01-03T00:00:00+00:00"],
                inner_validation_days=.25, minimum_fit_rows=10, minimum_inner_rows=10,
                affine_ridge_mean_loss_penalties=[.01, .1, 1.], coefficient_bounds=[0., 2.],
                extra_estimated_cost_margins=[0., .5, 1.], ridge_windows_days=[None, 14],
                ridge_alphas=[10., 100., 1000.], cost_stress_multipliers=[1., 2.], assumed_slippage_log_bps=[0., 1.])


def test_fit_is_past_matured_includes_wait_and_training_only_centering():
    rows = [row(i) for i in range(450)]
    cutoff = research.timestamp("2026-01-02T00:00:00+00:00")
    for r in rows:
        r["predictions"]["MARKET_ONLY"]["recorded_action"] = "WAIT"
    past = [r for r in rows if research.matured(r, cutoff)]
    fitted, trials = research.choose_affine(past, cutoff, ("MARKET_ONLY",), plan())
    assert fitted["rows"] == len(past)
    assert fitted["means"][0] == pytest.approx(np.mean([research.raw(r, ("MARKET_ONLY",))[0] for r in past]))
    changed = deepcopy(rows)
    for r in changed:
        if not research.matured(r, cutoff):
            r["gross_target_u5"] = 1e8
            r["predictions"]["MARKET_ONLY"]["predicted_u5"] = -1e9
    again, again_trials = research.choose_affine([r for r in changed if research.matured(r, cutoff)], cutoff, ("MARKET_ONLY",), plan())
    assert fitted == again and trials == again_trials
    assert not research.matured(row(0, label_available_at=cutoff.isoformat()), cutoff)
    assert not research.matured(row(0, exit_time=cutoff.isoformat()), cutoff)
    delayed_input = row(0)
    delayed_input["predictions"]["BROAD_FULL"]["prediction_created_at"] = cutoff.isoformat()
    assert not research.matured(delayed_input, cutoff)


@pytest.mark.parametrize("selected,expected", [("LONG", .001), ("SHORT", -.0012)])
def test_cost_is_once_and_wait_is_not_trade(selected, expected):
    entry = research.ledger([row()], [selected])[0]
    assert entry["net_log_bps"] == pytest.approx((expected-research.ROUND_TRIP_COMMISSION_LOG_COST)*10000)
    wait = research.ledger([row()], ["WAIT"])[0]
    assert wait["net_log_bps"] is None and wait["commission_log_bps"] == 0
    assert research.summary([wait])["PF"] is None


def test_generation_and_fold_do_not_reset_position_and_actual_exit_is_used():
    rows = [row(0), row(6, active_generation="g2"), row(7, active_generation="g3")]
    assert [r["state"] for r in research.ledger(rows, ["LONG"]*3)] == ["CLOSED", "OVERLAP_BLOCK", "CLOSED"]


@pytest.mark.parametrize("changes,state", [
    ({"session_state": "CLOSED"}, "SESSION_CLOSED"),
    ({"entry_time": None}, "ENTRY_UNVERIFIED"),
    ({"input_available_at": None}, "INPUT_AVAILABILITY_UNKNOWN"),
    ({"input_available_at": "2026-01-01T00:00:02+00:00"}, "INPUT_NOT_AVAILABLE_AT_ENTRY"),
    ({"valid": False}, "UNRESOLVED_EXPOSURE"),
    ({"long_log_return": None}, "UNRESOLVED_EXPOSURE"),
])
def test_missing_or_inaccessible_evidence_is_not_zero_profit(changes, state):
    item = research.ledger([row(**changes)], ["LONG"])[0]
    assert item["state"] == state and item["net_log_bps"] is None
    assert research.summary([item])["trades"] == 0


def test_unknown_exit_blocks_later_exposure_instead_of_inventing_close():
    records = research.ledger([row(exit_time=None), row(200)], ["LONG", "SHORT"])
    assert [r["state"] for r in records] == ["UNRESOLVED_EXPOSURE", "OVERLAP_BLOCK"]
    assert not research.summary(records)["equity_complete"]


def test_affine_bounds_and_market_training_do_not_activate_any_owner():
    rows = [row(i) for i in range(50)]
    fitted = research.fit_affine(rows, ("MARKET_ONLY", "BROAD_NEWS_RESIDUAL"), .1)
    assert all(0 <= v <= 2 for v in fitted["coefficients"])
    assert research.predict(fitted, rows[0]) == pytest.approx(research.predict(fitted, deepcopy(rows[0])))
    ridge = research.fit_market(rows, 100)
    assert ridge.training_rows == 50 and ridge.alpha == 100
    assert np.isfinite(research.predict_market(ridge, rows[0]))


def test_config_replay_is_deterministic_and_output_is_append_only(tmp_path):
    panel = {"rows": [row(i) for i in range(600)]}
    first = research.experiment(panel, plan(), tmp_path/"one")
    second = research.experiment(panel, plan(), tmp_path/"two")
    assert first == second
    assert first["ridge_triggered"]
    assert len([x for x in first["results"] if x.startswith("RIDGE_ALPHA")]) == 6
    assert first["production_mutation"] == 0
    assert json.loads((tmp_path/"one"/"experiment_plan.json").read_text()) == plan()
    with pytest.raises(ValueError, match="OUTPUT_MUST_BE_EMPTY"):
        research.experiment(panel, plan(), tmp_path/"one")


def test_invalid_action_universe_fails_instead_of_silent_zip_truncation():
    with pytest.raises(ValueError, match="INVALID_ACTION_UNIVERSE"):
        research.ledger([row()], [])


def test_research_paths_cannot_escape_declared_authority(tmp_path):
    allowed, outside = tmp_path/"allowed", tmp_path/"outside"
    allowed.mkdir()
    outside.mkdir()
    assert research_path(allowed/"run"/".."/"safe", roots=[allowed]) == allowed/"safe"
    for path in (outside, allowed/".."/"outside", tmp_path/"allowed-sibling"):
        with pytest.raises(ValueError, match="OUTSIDE_OFFLINE_RESEARCH_ROOT"):
            research_path(path, roots=[allowed])
    link = allowed/"escape"
    if sys.platform == "win32":
        result = subprocess.run(["cmd.exe", "/c", "mklink", "/J", str(link), str(outside)],
                                capture_output=True, timeout=10, creationflags=subprocess.CREATE_NO_WINDOW)
        assert result.returncode == 0, result.stderr
    else:
        link.symlink_to(outside, target_is_directory=True)
    try:
        with pytest.raises(ValueError, match="OUTSIDE_OFFLINE_RESEARCH_ROOT"):
            research_path(link/"payload.json", roots=[allowed])
    finally:
        if sys.platform == "win32":
            link.rmdir()
        else:
            link.unlink()
    assert outside.is_dir()


def test_real_cli_and_report_bind_inputs_and_reject_tampered_results(tmp_path):
    root = Path(__file__).resolve().parents[1]
    panel_path, plan_path, output = tmp_path/"panel.json", tmp_path/"plan.json", tmp_path/"output"
    panel_path.write_text(json.dumps({"rows": [row(i) for i in range(600)]}), encoding="utf-8")
    plan_path.write_text(json.dumps(plan()), encoding="utf-8")
    def run(script, *args):
        return subprocess.run([sys.executable, str(root/"scripts"/script), *map(str, args)],
                              capture_output=True, text=True, encoding="utf-8", timeout=30,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    completed = run("run_model_repair_offline.py", "--panel", panel_path, "--plan", plan_path, "--output", output)
    assert completed.returncode == 0, completed.stderr
    evidence = json.loads((output/"execution.json").read_text())
    assert evidence["panel_sha256"] and evidence["source_git_sha"] and evidence["production_mutation"] == 0
    report = run("report_model_repair_offline.py", "--results-dir", output)
    assert report.returncode == 0, report.stderr
    assert (output/"REPORT.md").is_file()
    (output/"results.json").write_text("{}")
    failure = run("report_model_repair_offline.py", "--results-dir", output)
    assert failure.returncode != 0 and "RESULT_DIGEST_MISMATCH" in failure.stderr
