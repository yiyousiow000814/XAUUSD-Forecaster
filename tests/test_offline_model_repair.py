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
from xauusd_forecaster import causal_signal_replay as causal
from xauusd_forecaster.market import MarketObservation


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


def causal_fixture(i=0, **changes):
    value = row(i)
    for prediction in value["predictions"].values():
        prediction.update(artifact_status="EXACT_HASH_VERIFIED", replay_status="MATCH")
    value.update(changes)
    old = dict(source_decision_id=value["source_decision_id"], decision_time=value["decision_time"],
               generation=f"g{i}", action="LONG", state="INPUT_NOT_AVAILABLE_AT_ENTRY", fold=i,
               strategy_version="fixed", prediction_u5=.1)
    return value, old


@pytest.mark.parametrize("strategy", ["ORIGINAL_MARKET_ONLY", "A_MARKET", "C_A_MARKET", "RIDGE_SELECTED", "PAST_MEAN"])
def test_causal_market_inputs_do_not_depend_on_news_and_start_is_not_completion(strategy):
    value, _ = causal_fixture()
    value["predictions"]["BROAD_FULL"]["prediction_created_at"] = "2027-01-01T00:00:00+00:00"
    value["predictions"]["BROAD_NEWS_RESIDUAL"]["prediction_created_at"] = None
    at, reason = causal.signal_time(value, strategy, "DEPENDENCY_MARKER_ASSUMED", {})
    assert at == research.timestamp(value["decision_time"]) + timedelta(milliseconds=100)
    assert reason == "ASSUMED_100MS_AFTER_START_MARKER"
    assert causal.signal_time(value, strategy, "EVIDENCE_ONLY", {}) == (None, "CONSUMER_VISIBILITY_UNKNOWN")
    assert "NEWS" not in str(causal.dependencies(strategy))


def test_causal_quote_selection_is_strict_quality_bounded_and_not_optimistic():
    at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    q = lambda seconds, bid=100, skew=0: MarketObservation(at+timedelta(seconds=seconds-skew), at+timedelta(seconds=seconds), bid, bid+.1)
    quotes = [q(0), q(1, skew=30), q(2), q(22)]
    selected = causal.first_after(quotes, [v.received_time for v in quotes], at)
    assert research.timestamp(selected["quote"]["received_time"]) == at+timedelta(seconds=2)
    assert selected["rejected"] == 1
    duplicate = [q(1), q(1, bid=101), q(2)]
    assert causal.first_after(duplicate, [v.received_time for v in duplicate], at)["reason"] == "AMBIGUOUS_QUOTE_ORDER"
    assert causal.first_after([q(21)], [q(21).received_time], at)["quote"] is None
    infinite = MarketObservation(at+timedelta(seconds=1), at+timedelta(seconds=1), float("inf"), float("inf"))
    assert not causal.quote_quality(infinite)


def test_causal_fixed_target_one_owner_and_unresolved_exposure_survive_generation():
    values, old = zip(*(causal_fixture(i) for i in range(3)))
    panel = {v["source_decision_id"]: v for v in values}
    timing = {v["source_decision_id"]: {"finished_at": (research.timestamp(v["decision_time"])+timedelta(seconds=5)).isoformat()} for v in values}
    fills = {}
    for v in values:
        at = research.timestamp(v["decision_time"])+timedelta(seconds=5)
        fills[causal.quote_key(at)] = {"quote": dict(event_time=(at+timedelta(seconds=1)).isoformat(),
            received_time=(at+timedelta(seconds=1)).isoformat(), bid=100, ask=100.1), "reason": "REAL_QUOTE"}
    endpoints = {"0": {"label_reconciled": True, "exit_quote": dict(bid=101, ask=101.1,
                     received_time=values[0]["exit_time"], event_time=values[0]["exit_time"])}}
    output = causal.replay(old, panel, timing, fills, endpoints, "ORIGINAL_MARKET_ONLY", "HISTORICAL_COMPLETION_CONDITIONAL", 0)
    assert [v["state"] for v in output] == ["CLOSED", "OVERLAP_BLOCK", "OVERLAP_BLOCK"]
    assert output[0]["exit_time"] == values[0]["exit_time"]
    assert research.timestamp(output[0]["exit_time"])-research.timestamp(output[0]["entry_time"]) < timedelta(minutes=30)
    assert output[0]["net_log_bps"] == pytest.approx(np.log(101/100.1)*10000-research.ROUND_TRIP_COMMISSION_LOG_COST*10000)
    unresolved = causal.replay(old, panel, timing, fills, {}, "ORIGINAL_MARKET_ONLY", "HISTORICAL_COMPLETION_CONDITIONAL", 0)
    assert [v["state"] for v in unresolved] == ["UNRESOLVED_EXPOSURE", "OVERLAP_BLOCK", "OVERLAP_BLOCK"]
    assert unresolved[0]["net_log_bps"] is None
    assert causal.metrics(unresolved)["equity_complete"] is False
    assert sum(causal.metrics(output)["waterfall"].values()) == 3


def test_causal_orders_are_sorted_by_order_events_not_clock_or_generation():
    a, oa = causal_fixture(0)
    b, ob = causal_fixture(1)
    panel = {"0": a, "1": b}
    early = research.timestamp(b["decision_time"])+timedelta(seconds=1)
    timing = {"0": {"finished_at": (early+timedelta(seconds=10)).isoformat()}, "1": {"finished_at": early.isoformat()}}
    # A missing quote still reserves its pending-order interval.
    rows = causal.replay([oa, ob], panel, timing, {}, {}, "ORIGINAL_MARKET_ONLY", "HISTORICAL_COMPLETION_CONDITIONAL", 0)
    assert [r["source_decision_id"] for r in rows] == ["1", "0"]
    assert rows[0]["state"] == "NO_POST_SIGNAL_QUOTE" and rows[0]["net_log_bps"] is None
    assert rows[1]["final_reason"] == "PENDING_ORDER_OVERLAP"


def test_causal_expiry_and_wait_are_not_missing_data_zero_profit():
    value, old = causal_fixture()
    timing = {"0": {"finished_at": value["exit_time"]}}
    result = causal.replay([old], {"0": value}, timing, {}, {}, "ORIGINAL_MARKET_ONLY", "HISTORICAL_COMPLETION_CONDITIONAL", 0)
    assert result[0]["state"] == "SIGNAL_EXPIRED" and result[0]["net_log_bps"] is None
    old["action"] = "WAIT"
    result = causal.replay([old], {"0": value}, {}, {}, {}, "ORIGINAL_MARKET_ONLY", "EVIDENCE_ONLY", 0)
    assert result[0]["final_reason"] == "WAIT" and result[0]["commission_log_bps"] == 0
    old["action"] = "LONG"
    value["session_state"] = "CLOSED"
    result = causal.replay([old], {"0": value}, timing, {}, {}, "ORIGINAL_MARKET_ONLY", "HISTORICAL_COMPLETION_CONDITIONAL", 0)
    assert result[0]["state"] == "SESSION_CLOSED" and result[0]["entry_quote"] is None


def test_causal_frozen_prediction_verification_rejects_drift_without_fitting(monkeypatch):
    value, old = causal_fixture()
    old["prediction_u5"] = value["predictions"]["MARKET_ONLY"]["predicted_u5"]
    monkeypatch.setattr(research, "train_ridge", lambda *a, **k: pytest.fail("no fitting allowed"))
    monkeypatch.setattr(research, "fit_affine", lambda *a, **k: pytest.fail("no fitting allowed"))
    causal.verify_frozen_predictions({"ORIGINAL_MARKET_ONLY": [old]}, {"0": value}, {"artifacts": []})
    old["action"] = "SHORT"
    with pytest.raises(ValueError, match="FROZEN_PREDICTION_OR_ACTION_CHANGED"):
        causal.verify_frozen_predictions({"ORIGINAL_MARKET_ONLY": [old]}, {"0": value}, {"artifacts": []})


def test_causal_quote_extract_real_gzip_and_cli_fail_closed(tmp_path):
    import gzip
    import importlib.util
    script = Path(__file__).resolve().parents[1]/"scripts/run_causal_signal_replay.py"
    spec = importlib.util.spec_from_file_location("causal_cli", script)
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    source, output = tmp_path/"quotes", tmp_path/"out"
    source.mkdir()
    output.mkdir()
    at = "2026-01-01T00:00:00+00:00"
    with gzip.open(source/"xauusd-quotes-20260101.jsonl.gz", "wt", encoding="utf-8") as f:
        f.write(json.dumps(dict(symbol="XAUUSD",event_time="2026-01-01T00:00:01+00:00", received_time="2026-01-01T00:00:01+00:00", bid=100, ask=100.1))+"\n")
    result = cli.freeze_and_select(source, output, {at}, {})
    assert result["fills"][causal.quote_key(research.timestamp(at))]["quote"]["bid"] == 100
    assert sum(r.get("rows", 0) for r in result["archives"]) == 1
    # A receipt just after midnight can live in the preceding named archive.
    second_output = tmp_path/"out2"
    second_output.mkdir()
    with gzip.open(source/"xauusd-quotes-20251231.jsonl.gz", "wt", encoding="utf-8") as f:
        f.write(json.dumps(dict(symbol="XAUUSD", event_time="2025-12-31T23:59:59+00:00", received_time="2026-01-01T00:00:00.5+00:00", bid=99, ask=99.1))+"\n")
    second = cli.freeze_and_select(source, second_output, {at}, {})
    assert second["fills"][causal.quote_key(research.timestamp(at))]["quote"]["bid"] == 99
    third_output = tmp_path/"out3"
    third_output.mkdir()
    with gzip.open(source/"xauusd-quotes-20251231.jsonl.gz", "wt", encoding="utf-8") as f:
        f.write(json.dumps(dict(symbol="XAUUSD", event_time="2025-12-31T23:59:00+00:00", received_time="2026-01-01T00:00:01+00:00", bid=99, ask=99.1))+"\n")
    third = cli.freeze_and_select(source, third_output, {at}, {})
    assert third["fills"][causal.quote_key(research.timestamp(at))]["reason"] == "AMBIGUOUS_QUOTE_ORDER"
    completed = subprocess.run([sys.executable, str(script), "--panel", str(source), "--frozen", str(source),
        "--timing", str(source), "--quotes", str(source), "--output", str(output)],
        capture_output=True, text=True, timeout=10,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    assert completed.returncode != 0 and "OUTPUT_MUST_BE_NEW" in completed.stderr


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
    assert report.stdout.strip() == "OFFLINE_MODEL_REPORT_WRITTEN"
    assert str(output) not in report.stdout
    assert (output/"REPORT.md").is_file()
    (output/"results.json").write_text("{}")
    failure = run("report_model_repair_offline.py", "--results-dir", output)
    assert failure.returncode != 0 and "RESULT_DIGEST_MISMATCH" in failure.stderr
