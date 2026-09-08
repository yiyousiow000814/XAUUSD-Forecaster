"""Opt-in retrospective research; never imported by a production model owner."""
from __future__ import annotations

import csv
from datetime import datetime, timedelta
import hashlib
import itertools
import json
from pathlib import Path

import numpy as np

from .execution_costs import ROUND_TRIP_COMMISSION_LOG_COST
from xauusd_forecaster.training.ridge import train_ridge
from xauusd_forecaster.training.materialization import MARKET_FEATURES
from .offline_model_paths import research_path

IDENTITIES = ("MARKET_ONLY", "FULL", "BROAD_FULL", "NEWS_ONLY", "NEWS_RESIDUAL", "BROAD_NEWS_RESIDUAL")
FEATURES = {
    "A_MARKET": ("MARKET_ONLY",), "A_BROAD_FULL": ("BROAD_FULL",),
    "B_COMBINATION": ("MARKET_ONLY", "BROAD_NEWS_RESIDUAL"),
}


def timestamp(value):
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("TIMESTAMP_ZONE_REQUIRED")
    return result


def sha(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def correlation(pairs, rank=False):
    if len(pairs) < 3:
        return None
    data = np.asarray(pairs)
    if rank:
        for axis in range(2):
            _, inverse, counts = np.unique(data[:, axis], return_inverse=True, return_counts=True)
            data[:, axis] = (np.cumsum(counts)-counts+(counts-1)/2)[inverse]
    if not all(np.std(data, axis=0) > 0):
        return None
    return float(np.corrcoef(data.T)[0, 1])


def finite(value):
    return value is not None and np.isfinite(float(value))


def raw(row, names):
    return [row["predictions"][name]["predicted_u5"] for name in names]


def opportunity(row):
    return (row.get("evidence_lane") == "LIVE_OOS" and row.get("clock_complete")
            and all(finite(row.get(k)) and row[k] > 0 for k in ("u5", "bid", "ask"))
            and row["ask"] >= row["bid"] and all(
                finite(row.get("predictions", {}).get(i, {}).get("predicted_u5"))
                for i in ("MARKET_ONLY", "BROAD_FULL", "BROAD_NEWS_RESIDUAL")))


def input_availability(row):
    times = [row.get("market_computed_at")] + [row.get("predictions", {}).get(i, {}).get("prediction_created_at")
             for i in ("MARKET_ONLY", "BROAD_FULL", "BROAD_NEWS_RESIDUAL")]
    if not all(times):
        return None
    if row.get("input_available_at"):
        times.append(row["input_available_at"])
    return max(times, key=timestamp)


def replay_qualified(row):
    return all(row["predictions"][i].get("replay_status") in ("MATCH", "MATCH_HISTORICAL_RIDGE_CONTRACT")
               and row["predictions"][i].get("artifact_status") == "EXACT_HASH_VERIFIED"
               for i in ("MARKET_ONLY", "BROAD_FULL", "BROAD_NEWS_RESIDUAL"))


def matured(row, cutoff):
    return (row.get("valid") and finite(row.get("gross_target_u5"))
            and row.get("label_available_at") and row.get("exit_time")
            and input_availability(row) is not None and timestamp(input_availability(row)) < cutoff
            and timestamp(row["label_available_at"]) < cutoff
            and timestamp(row["exit_time"]) < cutoff
            and timestamp(row["decision_time"]) < cutoff)


def fit_affine(rows, names, penalty, bounds=(0.0, 2.0)):
    """Exact active-set solution for one/two bounded slopes and free intercept.

    Loss is mean squared U5 error + penalty * squared raw-unit slopes.
    Centering is fitted on training data only. No test-set normalization.
    """
    x = np.asarray([raw(r, names) for r in rows], dtype=float)
    y = np.asarray([r["gross_target_u5"] for r in rows], dtype=float)
    if len(rows) < 2 or not np.isfinite(x).all() or not np.isfinite(y).all():
        raise ValueError("INVALID_FIT_INPUT")
    mean, target_mean = x.mean(axis=0), float(y.mean())
    z, t = x - mean, y - target_mean
    gram, rhs = z.T @ z / len(y) + penalty * np.eye(len(names)), z.T @ t / len(y)
    best = None
    for states in itertools.product((-1, 0, 1), repeat=len(names)):
        b = np.asarray([bounds[0] if s == -1 else bounds[1] if s == 1 else 0 for s in states], dtype=float)
        free = [i for i, s in enumerate(states) if s == 0]
        fixed = [i for i, s in enumerate(states) if s != 0]
        if free:
            b[free] = np.linalg.solve(gram[np.ix_(free, free)], rhs[free] - gram[np.ix_(free, fixed)] @ b[fixed])
        if np.any(b < bounds[0] - 1e-10) or np.any(b > bounds[1] + 1e-10):
            continue
        loss = float(np.mean((t - z @ b) ** 2) + penalty * (b @ b))
        if best is None or loss < best[0]:
            best = loss, b.copy()
    return {"kind": "bounded_affine", "names": list(names), "means": mean.tolist(),
            "coefficients": best[1].tolist(), "intercept": target_mean,
            "penalty": penalty, "rows": len(rows), "target": "gross_midpoint_log/U5"}


def predict(artifact, row):
    return float(artifact["intercept"] + (np.asarray(raw(row, artifact["names"])) - artifact["means"]) @ artifact["coefficients"])


def action(value, row, margin=0.0):
    if not finite(value):
        return "UNAVAILABLE"
    cost = (2 * np.log(row["ask"] / row["bid"]) + ROUND_TRIP_COMMISSION_LOG_COST) / row["u5"]
    if abs(value) <= cost * (1 + margin):
        return "WAIT"
    return "LONG" if value > 0 else "SHORT"


def ledger(rows, actions, commission_multiplier=1.0, slippage_bps=0.0, overlap=True, enforce_availability=True):
    """One state per strategy over the entire horizon, not per generation/fold."""
    if len(rows) != len(actions) or any(a not in ("LONG", "SHORT", "WAIT", "UNAVAILABLE") for a in actions):
        raise ValueError("INVALID_ACTION_UNIVERSE")
    if commission_multiplier < 0 or slippage_bps < 0:
        raise ValueError("INVALID_COST")
    until = None
    records = []
    for row, selected in zip(rows, actions):
        now = timestamp(row["decision_time"])
        result = {"source_decision_id": row["source_decision_id"], "decision_time": row["decision_time"],
                  "generation": row.get("active_generation"), "action": selected, "state": selected,
                  "entry_time": None, "exit_time": None, "net_log_bps": None, "bidask_log_bps": None,
                  "commission_log_bps": 0.0, "slippage_log_bps": 0.0}
        if selected in ("WAIT", "UNAVAILABLE"):
            records.append(result)
            continue
        if overlap and until is not None and now < until:
            result["state"] = "OVERLAP_BLOCK"
        elif row.get("session_state") == "CLOSED":
            result["state"] = "SESSION_CLOSED"
        elif not row.get("entry_time") or timestamp(row["entry_time"]) < now:
            result["state"] = "ENTRY_UNVERIFIED"
        elif enforce_availability and not row.get("input_available_at"):
            result["state"] = "INPUT_AVAILABILITY_UNKNOWN"
        elif enforce_availability and timestamp(row["input_available_at"]) > timestamp(row["entry_time"]):
            result["state"] = "INPUT_NOT_AVAILABLE_AT_ENTRY"
        else:
            entry = timestamp(row["entry_time"])
            end = timestamp(row["exit_time"]) if row.get("exit_time") else None
            # Unknown exit blocks subsequent exposure, rather than assuming a close.
            until = end if end is not None and end >= entry else datetime.max.replace(tzinfo=now.tzinfo)
            result.update(entry_time=row["entry_time"], exit_time=row.get("exit_time"))
            value = row.get("long_log_return" if selected == "LONG" else "short_log_return")
            if not row.get("valid") or end is None or end < entry or not finite(value):
                result["state"] = "UNRESOLVED_EXPOSURE"
            else:
                cost = ROUND_TRIP_COMMISSION_LOG_COST * commission_multiplier * 10000
                result.update(state="CLOSED", bidask_log_bps=value * 10000, commission_log_bps=cost,
                              slippage_log_bps=slippage_bps, net_log_bps=value * 10000 - cost - slippage_bps)
        records.append(result)
    return records


def summary(records):
    closed = [r for r in records if r["state"] == "CLOSED"]
    values = [r["net_log_bps"] for r in closed]
    gain, loss = sum(max(0, x) for x in values), -sum(min(0, x) for x in values)
    equity = peak = drawdown = 0.0
    daily, generations, versions = {}, {}, {}
    for r in closed:
        v = r["net_log_bps"]
        equity += v
        peak = max(peak, equity)
        drawdown = max(drawdown, peak - equity)
        day = r["decision_time"][:10]
        daily[day] = daily.get(day, 0.0) + v
        gen = r["generation"] or "UNKNOWN"
        generations[gen] = generations.get(gen, 0.0) + v
        version = r.get("strategy_version", "UNKNOWN")
        versions[version] = versions.get(version, 0.0) + v
    states = {s: sum(r["state"] == s for r in records) for s in sorted({r["state"] for r in records})}
    return {"opportunities": len(records), "trades": len(closed), "net_log_bps": sum(values),
            "bidask_before_commission_log_bps": sum(r["bidask_log_bps"] for r in closed),
            "commission_log_bps": sum(r["commission_log_bps"] for r in closed),
            "slippage_log_bps": sum(r["slippage_log_bps"] for r in closed),
            "net_per_opportunity": sum(values) / len(records) if records else None,
            "net_per_trade": sum(values) / len(values) if values else None,
            "PF": gain / loss if loss else None, "drawdown_log_bps_closed_trades": drawdown,
            "states": states, "daily": daily, "generations": generations, "versions": versions,
            "without_best_day": sum(values) - max(daily.values()) if daily else None,
            "coverage": len(closed) / len(records) if records else 0,
            "equity_complete": not any(states.get(s, 0) for s in ("UNRESOLVED_EXPOSURE", "ENTRY_UNVERIFIED"))}


def market_complete(row):
    return all(finite(row.get("market_features", {}).get(k)) for k in MARKET_FEATURES)


def fit_market(rows, alpha):
    """Reuse the pure production Ridge owner; never its generation/activation entrypoint."""
    x = np.asarray([[r["market_features"][k] for k in MARKET_FEATURES] for r in rows])
    y = np.asarray([r["gross_target_u5"] for r in rows])
    return train_ridge(x, y, MARKET_FEATURES, alpha, sha([
        [r["source_decision_id"], list(values), float(target)] for r, values, target in zip(rows, x, y)]))


def predict_market(fitted, row):
    return float(fitted.predict(np.asarray([[row["market_features"][k] for k in MARKET_FEATURES]]))[0]) if market_complete(row) else None


def choose_market(past, cutoff, plan):
    inner = cutoff - timedelta(days=plan["inner_validation_days"])
    validation = [r for r in past if timestamp(r["decision_time"]) >= inner and market_complete(r)]
    trials = []
    for window in plan["ridge_windows_days"]:
        def train_at(end):
            return [r for r in past if matured(r, end) and market_complete(r)
                    and (window is None or timestamp(r["decision_time"]) >= end - timedelta(days=window))]
        train = train_at(inner)
        if len(train) < plan["minimum_fit_rows"] or len(validation) < plan["minimum_inner_rows"]:
            continue
        for alpha in plan["ridge_alphas"]:
            fitted = fit_market(train, alpha)
            values = [predict_market(fitted, r) for r in validation]
            trials.append({"alpha": alpha, "window_days": window,
                           "mse": float(np.mean([(p-r["gross_target_u5"])**2 for p, r in zip(values, validation)])),
                           "inner_net_log_bps": summary(ledger(validation, [action(p, r) for p, r in zip(values, validation)]))["net_log_bps"]})
    if not trials:
        return None, trials
    selected = min(trials, key=lambda t: (t["mse"], -t["alpha"], t["window_days"] or 100000))
    window = selected["window_days"]
    train = [r for r in past if market_complete(r) and (window is None or timestamp(r["decision_time"]) >= cutoff-timedelta(days=window))]
    fitted = fit_market(train, selected["alpha"])
    return {"kind": "market_ridge", "fit_cutoff": cutoff.isoformat(), "window_days": window,
            "artifact": fitted.as_dict(), "artifact_hash": fitted.artifact_hash}, trials


def choose_affine(past, cutoff, names, plan):
    inner = cutoff - timedelta(days=plan["inner_validation_days"])
    train = [r for r in past if matured(r, inner)]
    validation = [r for r in past if timestamp(r["decision_time"]) >= inner and matured(r, cutoff)]
    if len(train) < plan["minimum_fit_rows"] or len(validation) < plan["minimum_inner_rows"]:
        return None, []
    trials = []
    for penalty in plan["affine_ridge_mean_loss_penalties"]:
        fitted = fit_affine(train, names, penalty, plan["coefficient_bounds"])
        predicted = [predict(fitted, r) for r in validation]
        mse = float(np.mean([(p - r["gross_target_u5"]) ** 2 for p, r in zip(predicted, validation)]))
        for margin in plan["extra_estimated_cost_margins"]:
            stats = summary(ledger(validation, [action(p, r, margin) for p, r in zip(predicted, validation)]))
            trials.append({"penalty": penalty, "margin": margin, "mse": mse,
                           "net_log_bps": stats["net_log_bps"], "trades": stats["trades"]})
    selected_penalty = min(trials, key=lambda t: (t["mse"], -t["penalty"]))["penalty"]
    selected_margin = max((t for t in trials if t["penalty"] == selected_penalty),
                          key=lambda t: (t["net_log_bps"], -t["trades"], t["margin"]))["margin"]
    artifact = fit_affine(past, names, selected_penalty, plan["coefficient_bounds"])
    artifact.update(selected_margin=selected_margin, fit_cutoff=cutoff.isoformat(),
                    training_keys_digest=sha([r["source_decision_id"] for r in past]))
    return artifact, trials


def experiment(panel, plan, output):
    output = research_path(output)
    if output.exists() and any(output.iterdir()):
        raise ValueError("OUTPUT_MUST_BE_EMPTY")
    output.mkdir(parents=True, exist_ok=True)
    rows = sorted([dict(r) for r in panel["rows"] if opportunity(r)], key=lambda r: r["decision_time"])
    for row in rows:
        row["input_available_at"] = input_availability(row)
    if len({r["source_decision_id"] for r in rows}) != len(rows):
        raise ValueError("DUPLICATE_OPPORTUNITY")
    bounds = [timestamp(s) for s in plan["fold_boundaries"]]
    test_rows = [r for r in rows if bounds[0] <= timestamp(r["decision_time"]) < bounds[-1]]
    names = ["ORIGINAL_" + i for i in IDENTITIES] + ["ALWAYS_WAIT", "PAST_MEAN"] + list(FEATURES) + ["C_" + f for f in FEATURES]
    series = {name: [] for name in names}
    params, all_trials = [], []
    for index, (start, end) in enumerate(zip(bounds, bounds[1:])):
        past = [r for r in rows if matured(r, start)]
        tests = [r for r in test_rows if start <= timestamp(r["decision_time"]) < end]
        print(f"FOLD {index} mature_train={len(past)} test={len(tests)}", flush=True)
        fits = {}
        for family, sources in FEATURES.items():
            fits[family], trials = choose_affine(past, start, sources, plan)
            all_trials.extend({"fold": index, "family": family, **t} for t in trials)
            params.append({"fold": index, "family": family, "artifact": fits[family]})
        mean = float(np.mean([r["gross_target_u5"] for r in past])) if len(past) >= plan["minimum_fit_rows"] else None
        for r in tests:
            for name in names:
                value, selected = None, None
                if name.startswith("ORIGINAL_"):
                    p = r["predictions"].get(name.removeprefix("ORIGINAL_"), {})
                    value, selected = p.get("predicted_u5"), p.get("recorded_action", "UNAVAILABLE")
                elif name == "ALWAYS_WAIT":
                    selected = "WAIT"
                elif name == "PAST_MEAN":
                    value = mean
                else:
                    family = name.removeprefix("C_")
                    fitted = fits[family]
                    if fitted:
                        value = predict(fitted, r)
                        selected = action(value, r, fitted["selected_margin"] if name.startswith("C_") else 0)
                series[name].append({"decision": r["source_decision_id"], "fold": index, "prediction_u5": value,
                                     "action": selected or action(value, r)})
    results = {}

    def score(name, predictions):
        actions = [p["action"] for p in predictions]
        records = ledger(test_rows, actions)
        for rec, pred, row in zip(records, predictions, test_rows):
            rec.update(fold=pred["fold"], prediction_u5=pred["prediction_u5"],
                       original_versions={i: p["model_version"] for i, p in row["predictions"].items()},
                       strategy_version=(row["predictions"].get(name.removeprefix("ORIGINAL_"), {}).get("model_version", "UNKNOWN")
                                         if name.startswith("ORIGINAL_") else f"{name}:fold-{pred['fold']}"),
                       input_active_common=row.get("active_common", False),
                       input_replay={i: p.get("replay_status", "UNKNOWN") for i, p in row["predictions"].items()})
            rec.update(gross_target_u5=row.get("gross_target_u5"),
                       target_valid=row.get("valid"), u5=row["u5"],
                       input_available_at=row["input_available_at"],
                       expected_roundtrip_cost_u5=float((2*np.log(row["ask"]/row["bid"])+ROUND_TRIP_COMMISSION_LOG_COST)/row["u5"]))
        result = summary(records)
        result["folds"] = {str(i): summary([r for r in records if r["fold"] == i]) for i in range(len(bounds) - 1)}
        result["stress"] = {f"commission_{c}_slippage_{s}": summary(ledger(test_rows, actions, c, s))
                            for c in plan["cost_stress_multipliers"] for s in plan["assumed_slippage_log_bps"]}
        result["clock_time_counterfactual"] = summary(ledger(test_rows, actions, enforce_availability=False))
        result["EVERY_5M"] = summary(ledger(test_rows, actions, overlap=False, enforce_availability=False))
        fixed = [(r, a) for r, a in zip(test_rows, actions) if timestamp(r["decision_time"]).minute in (0, 30)]
        result["FIXED_30M"] = summary(ledger([r for r, _ in fixed], [a for _, a in fixed], overlap=False, enforce_availability=False))
        scored = [(p["prediction_u5"], r["gross_target_u5"]) for p, r in zip(predictions, test_rows)
                  if finite(p["prediction_u5"]) and r.get("valid") and finite(r.get("gross_target_u5"))]
        result["mse_u5"] = float(np.mean([(p - y) ** 2 for p, y in scored])) if scored else None
        result["prediction_correlation"] = correlation(scored)
        result["rank_correlation"] = correlation(scored, rank=True)
        result["composition"] = {}
        for category in ("SAME_DIRECTION", "OPPOSITE_DIRECTION", "ONE_DIRECTION", "BOTH_WAIT"):
            subset = []
            for rec, row in zip(records, test_rows):
                market = row["predictions"]["MARKET_ONLY"]["recorded_action"]
                news = row["predictions"]["BROAD_NEWS_RESIDUAL"]["recorded_action"]
                category_here = ("BOTH_WAIT" if market == news == "WAIT" else "ONE_DIRECTION" if "WAIT" in (market, news)
                                 else "SAME_DIRECTION" if market == news else "OPPOSITE_DIRECTION")
                if category == category_here:
                    subset.append(rec)
            result["composition"][category] = summary(subset)
        result["action_transitions_from_broad_full"] = {}
        for rec, row in zip(records, test_rows):
            key = row["predictions"]["BROAD_FULL"]["recorded_action"]+"->"+rec["action"]
            bucket = result["action_transitions_from_broad_full"].setdefault(key, {"count": 0, "known_net_log_bps": 0.})
            bucket["count"] += 1
            if rec["net_log_bps"] is not None:
                bucket["known_net_log_bps"] += rec["net_log_bps"]
        result["research_candidate"] = bool(result["net_log_bps"] > 0 and result["trades"] >= 50
            and result["equity_complete"] and result["without_best_day"] > 0
            and not result["states"].get("INPUT_AVAILABILITY_UNKNOWN", 0)
            and sum(f["net_log_bps"] > 0 for f in result["folds"].values()) >= 3
            and all(s["net_log_bps"] > 0 for s in result["stress"].values()))
        result["source_replay_unqualified_opportunities"] = sum(not replay_qualified(r) for r in rows)
        result["research_candidate"] = result["research_candidate"] and result["source_replay_unqualified_opportunities"] == 0
        result["mse_target_interpretation"] = ("gross-direction proxy only, not residual-model training target"
                                              if name in ("ORIGINAL_NEWS_RESIDUAL", "ORIGINAL_BROAD_NEWS_RESIDUAL") else "gross midpoint/U5")
        results[name] = result
        (output / (name + ".json")).write_text(json.dumps(records, indent=2, allow_nan=False), encoding="utf-8")
        (output / (name + "-clock-counterfactual.json")).write_text(json.dumps(
            ledger(test_rows, actions, enforce_availability=False), indent=2, allow_nan=False), encoding="utf-8")
    for name, predictions in series.items():
        score(name, predictions)
    # The trigger and the entire six-point control were fixed before any scores.
    ridge_triggered = not any(results[n]["research_candidate"] for n in names if n in FEATURES or n.startswith("C_"))
    if ridge_triggered:
        ridge_series = {"RIDGE_SELECTED": []}
        ridge_series.update({f"RIDGE_ALPHA_{a}_WINDOW_{w}": [] for a in plan["ridge_alphas"] for w in plan["ridge_windows_days"]})
        for index, (start, end) in enumerate(zip(bounds, bounds[1:])):
            past = [r for r in rows if matured(r, start)]
            tests = [r for r in test_rows if start <= timestamp(r["decision_time"]) < end]
            selected, trials = choose_market(past, start, plan)
            all_trials.extend({"fold": index, "family": "RIDGE", **t} for t in trials)
            params.append({"fold": index, "family": "RIDGE_SELECTED", "artifact": selected})
            for name in ridge_series:
                if name == "RIDGE_SELECTED":
                    alpha = selected["artifact"]["alpha"] if selected else None
                    window = selected["window_days"] if selected else None
                else:
                    _, _, a, _, w = name.split("_")
                    alpha, window = float(a), None if w == "None" else int(w)
                eligible = [r for r in past if market_complete(r) and (window is None or timestamp(r["decision_time"]) >= start-timedelta(days=window))]
                fitted = fit_market(eligible, alpha) if alpha is not None and len(eligible) >= plan["minimum_fit_rows"] else None
                if name != "RIDGE_SELECTED":
                    params.append({"fold": index, "family": name, "artifact": fitted.as_dict() if fitted else None})
                for row in tests:
                    value = predict_market(fitted, row) if fitted else None
                    ridge_series[name].append({"fold": index, "prediction_u5": value, "action": action(value, row)})
        for name, predictions in ridge_series.items():
            score(name, predictions)
    payload = {"status": "RETROSPECTIVE_RESEARCH_ONLY", "plan_digest": sha(plan), "panel_digest": sha(panel),
               "opportunity_count": len(test_rows), "input_rows": len(panel["rows"]), "results": results,
               "artifacts": params, "inner_trials": all_trials, "production_mutation": 0,
               "ridge_triggered": ridge_triggered,
               "limitations": ["Historical development data, not untouched validation", "Broker P&L unavailable",
                               "News event receipt PIT not replayed", "Session UNKNOWN uses retained valid quote endpoints only",
                               "Closed-trade drawdown is incomplete when exposure unresolved; no account-capital model",
                               "Four dependent outer time blocks, insufficient for reliable confidence intervals"]}
    (output / "experiment_plan.json").write_text(json.dumps(plan, indent=2), encoding="utf-8")
    artifact_dir = output / "artifacts"
    artifact_dir.mkdir()
    for item in params:
        (artifact_dir / f"{item['family']}-fold-{item['fold']}.json").write_text(json.dumps(item, indent=2), encoding="utf-8")
    (output / "results.json").write_text(json.dumps(payload, indent=2, allow_nan=False), encoding="utf-8")
    with (output / "comparison.csv").open("w", newline="", encoding="utf-8") as f:
        keys = ["strategy", "trades", "net_log_bps", "PF", "drawdown_log_bps_closed_trades", "research_candidate"]
        writer = csv.DictWriter(f, fieldnames=keys)
        writer.writeheader()
        writer.writerows({"strategy": k, **{f: v[f] for f in keys[1:]}} for k, v in results.items())
    return payload
