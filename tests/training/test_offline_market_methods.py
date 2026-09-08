"""Finite-method causality and daily accounting, not profitability fixtures."""
from copy import deepcopy
from datetime import datetime, timezone
import importlib.util
import json
import math
from pathlib import Path
import subprocess
import sys

import numpy as np
import pytest

from xauusd_forecaster import offline_market_methods as methods
from xauusd_forecaster.execution_costs import ROUND_TRIP_COMMISSION_LOG_COST
from xauusd_forecaster.training.materialization import MARKET_FEATURES


def row(i=0):
    return {"source_decision_id": str(i), "decision_time": "2026-08-01T12:00:00+00:00",
            "source_received_time": "2026-08-01T11:59:59+00:00", "u5": .001,
            "market_features": {k: (i + n + 1) * .001 for n, k in enumerate(MARKET_FEATURES)},
            "gross_target_u5": math.sin(i / 10)}


def test_features_ignore_future_labels_and_require_actual_source_clock():
    source = row()
    changed = deepcopy(source)
    changed.update(gross_target_u5=1e12, exit_time="2099-01-01T00:00:00+00:00")
    assert methods.features(source, True) == methods.features(changed, True)
    assert len(methods.features(source, True)) == 17
    changed["source_received_time"] = "2026-08-01T12:00:01+00:00"
    with pytest.raises(ValueError, match="TIMING_INVALID"):
        methods.features(changed, True)


@pytest.mark.parametrize("state,boost", [(False, True), (True, True), (True, False)])
def test_fitted_serialization_and_training_only_quantiles(state, boost):
    plan = {"ridge_alpha": 100., "boost_quantiles": 4, "boost_min_leaf": 30,
            "boost_rounds": 5, "boost_learning_rate": .05}
    rows = [row(i) for i in range(100)]
    model = methods.fit_model(rows, plan, state=state, boost=boost)
    restored = json.loads(json.dumps(model))
    assert methods.infer(model, row(200)) == pytest.approx(methods.infer(restored, row(200)))
    again = methods.fit_model(rows, plan, state=state, boost=boost)
    assert model == again
    if boost:
        assert len(model["stumps"]) <= 5
        for s in model["stumps"]:
            values = np.asarray([methods.features(r, state)[s["feature"]] for r in rows])
            assert min(sum(values <= s["threshold"]), sum(values > s["threshold"])) >= 30


def trade(end="2026-08-02T00:10:00+00:00"):
    commission = ROUND_TRIP_COMMISSION_LOG_COST * 10000
    return {"decision_time": "2026-08-01T23:39:00+00:00", "state": "CLOSED", "action": "LONG",
            "entry_time": "2026-08-01T23:40:00+00:00", "exit_time": end,
            "entry_quote": {"bid": 99., "ask": 100.}, "exit_quote": {"bid": 102., "ask": 103.},
            "commission_log_bps": commission, "net_log_bps": math.log(1.02)*10000-commission}


def test_daily_mtm_telescopes_cost_once_and_includes_flat_days():
    t = trade()
    marks = {"2026-08-02T00:00:00+00:00": {"quote": {"bid": 101., "ask": 102.}}}
    out = methods.daily_mtm([t], marks, ["2026-08-01", "2026-08-02", "2026-08-03"])
    assert out["complete"]
    assert sum(d["net_log_bps"] for d in out["days"].values()) == pytest.approx(t["net_log_bps"])
    assert out["days"]["2026-08-03"]["net_log_bps"] == 0
    assert out["days"]["2026-08-01"]["commission_log_bps"] > 0
    assert out["days"]["2026-08-02"]["commission_log_bps"] == 0
    assert sum(d["exposure_seconds"] for d in out["days"].values()) == 1800


@pytest.mark.parametrize("kind", ["missing_mark", "unresolved", "unknown_signal"])
def test_unknown_exposure_never_becomes_zero_day(kind):
    t = trade()
    if kind == "unresolved":
        t.update(state="UNRESOLVED_EXPOSURE", exit_time=None, exit_quote=None, commission_log_bps=0)
    if kind == "unknown_signal":
        t.update(entry_quote=None, state="INPUT_AVAILABILITY_UNKNOWN", not_evaluable=True)
    out = methods.daily_mtm([t], {}, ["2026-08-01", "2026-08-02"])
    assert not out["complete"]
    assert out["unknown_days"] > 0
    assert out["average_daily_log_bps"] is None
    if kind == "unresolved":
        assert out["days"]["2026-08-01"]["commission_log_bps"] > 0


def test_fit_fold_excludes_unmatured_rows_and_inner_labels(monkeypatch):
    calls = []
    cutoff = datetime(2026, 8, 13, tzinfo=timezone.utc)
    inputs = []
    for i in range(140):
        value = row(i)
        value.update(valid=True, exit_time="2026-08-01T12:30:00+00:00",
                     label_available_at="2026-08-01T12:30:01+00:00", market_computed_at=value["decision_time"],
                     predictions={k: {"prediction_created_at": value["decision_time"]}
                                  for k in ("MARKET_ONLY", "BROAD_FULL", "BROAD_NEWS_RESIDUAL")})
        if i >= 100:
            value["decision_time"] = "2026-08-12T00:00:00+00:00"
            value["label_available_at"] = "2026-08-12T00:31:00+00:00"
        inputs.append(value)
    inputs[-1]["label_available_at"] = cutoff.isoformat()
    def fitter(rows, plan, **kw):
        calls.append([r["source_decision_id"] for r in rows])
        return {"intercept": 0}
    monkeypatch.setattr(methods, "fit_model", fitter)
    monkeypatch.setattr(methods, "infer", lambda *a: .1)
    _, evidence = methods.fit_fold(inputs, cutoff, {})
    assert all("139" not in ids for ids in calls)
    assert len(calls[-1]) == 100
    assert evidence["rows"] == 139


def test_cli_rejects_existing_output_with_real_runtime(tmp_path):
    script = Path(__file__).resolve().parents[2] / "scripts/research/run_market_method_batch.py"
    completed = subprocess.run([sys.executable, str(script), "--panel", str(tmp_path),
        "--baseline", str(tmp_path), "--causal", str(tmp_path), "--plan", str(tmp_path),
        "--output", str(tmp_path)], capture_output=True, text=True, timeout=10,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    assert completed.returncode != 0 and "OUTPUT_MUST_BE_NEW" in completed.stderr
