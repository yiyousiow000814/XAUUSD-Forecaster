"""Finite, opt-in market research. No runtime model or ledger mutation."""
from __future__ import annotations

from datetime import timedelta
import math

import numpy as np

from .offline_model_repair import action, finite, fit_affine, matured, predict, sha, timestamp
from .ridge import RidgeArtifact, train_ridge
from .training import MARKET_FEATURES
from .execution_costs import ROUND_TRIP_COMMISSION_LOG_COST

STATE_NAMES = tuple(f"return_{n}m_div_u5" for n in (1, 5, 15, 30, 60)) + (
    "realized_volatility_60m_div_u5", "trend_agreement_5m_30m", "utc_hour_sin", "utc_hour_cos")
NEW_METHODS = ("METHOD_STATE_RIDGE", "METHOD_BOOST8", "METHOD_BOOST_STATE",
               "METHOD_STATE_CALIBRATED", "METHOD_TREND", "METHOD_RANGE")


def features(row, state=False):
    values = [row.get("market_features", {}).get(k) for k in MARKET_FEATURES]
    if (not all(finite(v) for v in values) or not finite(row.get("u5")) or row["u5"] <= 0
            or (row.get("source_received_time")
                and timestamp(row["source_received_time"]) > timestamp(row["decision_time"]))):
        raise ValueError("MARKET_FEATURE_IDENTITY_OR_TIMING_INVALID")
    # Absent raw receipt remains UNKNOWN, not a fabricated timestamp. The CLI
    # binds retained feature bytes; these methods run only declared conditional
    # timing scenarios. Evidence-only replay cannot admit an order from this.
    if state:
        at = timestamp(row["decision_time"])
        # All retained timestamps are canonical UTC; reject offset reinterpretation.
        if at.utcoffset().total_seconds() != 0:
            raise ValueError("FEATURE_CLOCK_NOT_UTC")
        phase = 2 * math.pi * (at.hour + at.minute / 60 + at.second / 3600) / 24
        values += [v / row["u5"] for v in values[:5]] + [values[7] / row["u5"],
            float(np.sign(values[1]) * np.sign(values[3])), math.sin(phase), math.cos(phase)]
    return values


def fit_model(rows, plan, *, state, boost):
    names = MARKET_FEATURES + STATE_NAMES if state else MARKET_FEATURES
    x = np.asarray([features(r, state) for r in rows], dtype=float)
    y = np.asarray([r["gross_target_u5"] for r in rows], dtype=float)
    if len(y) < 100 or not np.isfinite(x).all() or not np.isfinite(y).all():
        raise ValueError("INSUFFICIENT_OR_INVALID_FIT_INPUT")
    identity = sha([[r["source_decision_id"], list(v), float(t)] for r, v, t in zip(rows, x, y)])
    if not boost:
        artifact = train_ridge(x, y, names, plan["ridge_alpha"], identity).as_dict()
        return {"kind": "ridge", "state": state, "artifact": artifact}
    # Deterministic depth-one boosting. Split candidates depend on training X
    # only; no external ML dependency or test-set early stopping.
    splits = []
    for column in range(x.shape[1]):
        for threshold in np.unique(np.quantile(x[:, column], np.linspace(0, 1, plan["boost_quantiles"] + 2)[1:-1])):
            mask = x[:, column] <= threshold
            if min(int(mask.sum()), int((~mask).sum())) >= plan["boost_min_leaf"]:
                splits.append((column, float(threshold), mask))
    fitted = np.full(len(y), y.mean())
    stumps = []
    for _ in range(plan["boost_rounds"]):
        residual = y - fitted
        best = None
        for column, threshold, mask in splits:
            left, right = float(residual[mask].mean()), float(residual[~mask].mean())
            update = np.where(mask, left, right)
            loss = float(np.mean((residual - update) ** 2))
            if best is None or (loss, column, threshold) < best[:3]:
                best = (loss, column, threshold, left, right, update)
        if best is None:
            break  # Constant predictor, not fabricated leaf support.
        _, column, threshold, left, right, update = best
        fitted += plan["boost_learning_rate"] * update
        stumps.append({"feature": column, "threshold": threshold, "left": left, "right": right})
    return {"kind": "stump_boost", "state": state, "feature_names": names,
            "intercept": float(y.mean()), "learning_rate": plan["boost_learning_rate"],
            "stumps": stumps, "training_rows": len(y), "training_dataset_hash": identity}


def infer(model, row):
    x = np.asarray([features(row, model["state"])])
    if model["kind"] == "ridge":
        return float(RidgeArtifact(**{k: v for k, v in model["artifact"].items() if k != "schema"}).predict(x)[0])
    return float(model["intercept"] + model["learning_rate"] * sum(
        s["left"] if x[0, s["feature"]] <= s["threshold"] else s["right"] for s in model["stumps"]))


def fit_fold(past, cutoff, plan):
    rows = [r for r in past if matured(r, cutoff)]
    output = {}
    fits = {"ridge": 0, "boost": 0, "affine": 0}
    for name, state, boost in (("METHOD_STATE_RIDGE", True, False),
                               ("METHOD_BOOST8", False, True), ("METHOD_BOOST_STATE", True, True)):
        output[name] = fit_model(rows, plan, state=state, boost=boost)
        fits["boost" if boost else "ridge"] += 1
    inner = cutoff - timedelta(days=2)
    inner_rows = [r for r in rows if matured(r, inner)]
    validation = [r for r in rows if timestamp(r["decision_time"]) >= inner]
    if len(inner_rows) < 100 or len(validation) < 30:
        output["METHOD_STATE_CALIBRATED"] = None
    else:
        preliminary = fit_model(inner_rows, plan, state=True, boost=False)
        fits["ridge"] += 1
        examples = [{**r, "predictions": {"STATE": {"predicted_u5": infer(preliminary, r)}}} for r in validation]
        calibration = fit_affine(examples, ("STATE",), 0.1, (0, 2))
        fits["affine"] += 1
        output["METHOD_STATE_CALIBRATED"] = {"base": output["METHOD_STATE_RIDGE"], "calibration": calibration,
            "inner_model": preliminary, "inner_fit_cutoff": inner.isoformat(),
            "calibration_ids": [r["source_decision_id"] for r in validation]}
    return output, {"cutoff": cutoff.isoformat(), "rows": len(rows), "fits": fits,
                    "max_label_marker": max(r["label_available_at"] for r in rows),
                    "max_exit": max(r["exit_time"] for r in rows),
                    "fit_ids": [r["source_decision_id"] for r in rows]}


def method_prediction(name, models, row):
    features(row)  # Mechanistic controls obey the same known-source timing gate.
    if name == "METHOD_TREND":
        return row["market_features"]["return_30m"] / row["u5"]
    if name == "METHOD_RANGE":
        return -row["market_features"]["return_5m"] / row["u5"]
    if name == "METHOD_STATE_CALIBRATED":
        if models[name] is None:
            return None
        value = infer(models[name]["base"], row)
        return predict(models[name]["calibration"], {"predictions": {"STATE": {"predicted_u5": value}}})
    return infer(models[name], row)


def daily_mtm(records, marks, days, *, commission_multiplier=1.0, slippage=0.0):
    """Liquidation-value increments, costs once at entry; missing exposure != 0."""
    daily = {day: {"net_log_bps": 0.0, "bidask_log_bps": 0.0, "commission_log_bps": 0.0,
                   "assumed_slippage_log_bps": 0.0, "unknown_exposures": 0,
                   "entries": 0, "exposure_seconds": 0.0} for day in days}
    end = timestamp(days[-1] + "T00:00:00+00:00") + timedelta(days=1)
    for row in records:
        if row.get("entry_quote") is None:
            if row.get("not_evaluable") and row["decision_time"][:10] in daily:
                daily[row["decision_time"][:10]]["unknown_exposures"] += 1
            continue
        start = timestamp(row["entry_time"])
        closed = row["state"] == "CLOSED"
        stop = timestamp(row["exit_time"]) if closed else end
        previous = 0.0
        for day, item in daily.items():
            left = timestamp(day + "T00:00:00+00:00")
            right = left + timedelta(days=1)
            if start >= right or stop <= left:
                continue
            item["exposure_seconds"] += max(0, (min(stop, right) - max(start, left)).total_seconds())
            if left <= start < right:
                item["entries"] += 1
                item["commission_log_bps"] += ROUND_TRIP_COMMISSION_LOG_COST * 10000 * commission_multiplier
                item["assumed_slippage_log_bps"] += slippage
            at_exit = closed and stop <= right
            quote = row["exit_quote"] if at_exit else marks.get(right.isoformat(), {}).get("quote")
            current = None
            if quote:
                entry = row["entry_quote"]
                current = 10000 * (math.log(quote["bid"] / entry["ask"]) if row["action"] == "LONG"
                                   else math.log(entry["bid"] / quote["ask"]))
            if current is None or previous is None:
                item["unknown_exposures"] += 1
            else:
                item["bidask_log_bps"] += current - previous
            previous = current
    for item in daily.values():
        item["net_log_bps"] = (None if item["unknown_exposures"] else item["bidask_log_bps"]
                                - item["commission_log_bps"] - item["assumed_slippage_log_bps"])
    known = [v["net_log_bps"] for v in daily.values() if v["net_log_bps"] is not None]
    complete = len(known) == len(days) and not any(r["state"] == "UNRESOLVED_EXPOSURE" for r in records)
    equity = peak = drawdown = 0.0
    streak = longest = 0
    for value in (v["net_log_bps"] for v in daily.values()):
        if value is None:
            streak = 0
            continue
        equity += value
        peak = max(peak, equity)
        drawdown = max(drawdown, peak - equity)
        streak = streak + 1 if value < 0 else 0
        longest = max(longest, streak)
    return {"days": daily, "complete": complete, "unknown_days": len(days)-len(known),
            "profitable_days": sum(v > 0 for v in known), "loss_days": sum(v < 0 for v in known),
            "zero_days": sum(v == 0 for v in known), "average_daily_log_bps": float(np.mean(known)) if complete else None,
            "worst_day_log_bps": min(known) if complete else None,
            "p05_day_log_bps": float(np.quantile(known, .05)) if complete else None,
            "daily_boundary_drawdown_log_bps": drawdown if complete else None,
            "max_consecutive_loss_days": longest if complete else None,
            "without_best_day_log_bps": sum(known)-max(known) if complete else None,
            "open_at_end": sum(r["state"] == "UNRESOLVED_EXPOSURE" for r in records),
            "broker_session_authority": "UNKNOWN", "dollar_equity": None,
            "operating_costs": "UNKNOWN_NO_CAPITAL_OR_ACCOUNT_COST_RECORDS"}
