"""File-only independent accounting and inference checks for finite research."""
import argparse
import csv
from datetime import timedelta
import hashlib
import json
import math
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from xauusd_forecaster.offline_market_methods import NEW_METHODS, method_prediction
from xauusd_forecaster.offline_model_paths import research_path
from xauusd_forecaster.offline_model_repair import action, timestamp, correlation


def read(path):
    return json.loads(research_path(path).read_text(encoding="utf-8"))


def digest(path):
    return hashlib.sha256(research_path(path).read_bytes()).hexdigest()


def run(args):
    root, panel_path = research_path(args.run), research_path(args.panel)
    execution = read(root / "execution.json")
    for name in ("results", "artifacts", "daily_marks"):
        if digest(root / (name + ".json")) != execution[name + "_sha256"]:
            raise ValueError("REPORT_INPUT_HASH_MISMATCH")
    if digest(panel_path) != execution["panel_sha256"]:
        raise ValueError("REPORT_PANEL_MISMATCH")
    panel = {r["source_decision_id"]: r for r in read(panel_path)["rows"]}
    results = read(root / "results.json")
    artifacts = {a["fold"]: a for a in read(root / "artifacts.json")}
    marks = read(root / "daily_marks.json")["marks"]
    plan = read(root / "frozen_plan.json")
    commission = -math.log1p(-60 / 1e6) * 10000
    n_ledgers = n_rows = n_returns = n_predictions = 0
    csv_rows = []
    for scenario, methods in results["results"].items():
        for name, metric in methods.items():
            rows = read(root / "ledgers" / f"{scenario}-{name}.json")
            assert len(rows) == 3646 and len({r["source_decision_id"] for r in rows}) == 3646
            previous = None
            closed_sum = 0
            for row in rows:
                p = panel[row["source_decision_id"]]
                if name in NEW_METHODS:
                    predicted = method_prediction(name, artifacts[row["fold"]]["models"], p)
                    assert predicted == row["prediction_u5"] and action(predicted, p) == row["action"]
                    n_predictions += 1
                if row["state"] != "CLOSED":
                    assert row["net_log_bps"] is None
                    continue
                order, entry, exit_ = map(timestamp, (row["order_time"], row["entry_time"], row["exit_time"]))
                target = timestamp(p["entry_time"]) + timedelta(minutes=30)
                assert order < entry <= order + timedelta(seconds=20)
                assert entry < target <= exit_
                assert previous is None or order > previous
                previous = exit_
                b, a = row["entry_quote"], row["exit_quote"]
                value = (math.log(a["bid"] / b["ask"]) if row["action"] == "LONG"
                         else math.log(b["bid"] / a["ask"])) * 10000 - commission
                assert math.isclose(value, row["net_log_bps"], abs_tol=1e-8)
                closed_sum += value
                n_returns += 1
            assert math.isclose(closed_sum, metric["net_log_bps"], abs_tol=1e-8)
            for index, daily in enumerate(metric["daily_mtm"]):
                multiplier, slippage = plan["costs"][index]
                expected = {d: 0.0 for d in daily["days"]}
                unknown = set()
                # Independent endpoint-to-endpoint daily increments. Cross-day
                # mark absence invalidates both adjoining increments, not 0.
                for row in rows:
                    if row["entry_quote"] is None:
                        if row["not_evaluable"]:
                            unknown.add(row["decision_time"][:10])
                        continue
                    entry = timestamp(row["entry_time"])
                    end = timestamp(row["exit_time"]) if row["state"] == "CLOSED" else timestamp(max(expected)+"T00:00:00+00:00")+timedelta(days=1)
                    points = [(entry, row["entry_quote"], 0.0)]
                    midnight = timestamp(entry.date().isoformat()+"T00:00:00+00:00")+timedelta(days=1)
                    while midnight < end:
                        points.append((midnight, marks.get(midnight.isoformat(), {}).get("quote"), None))
                        midnight += timedelta(days=1)
                    points.append((end, row["exit_quote"] if row["state"] == "CLOSED" else marks.get(end.isoformat(), {}).get("quote"), None))
                    for n in range(1, len(points)):
                        left, right = points[n-1], points[n]
                        day = left[0].date().isoformat()
                        if day not in expected:
                            continue
                        if left[1] is None or right[1] is None:
                            unknown.add(day)
                            continue
                        if n == 1:
                            value = (math.log(right[1]["bid"] / left[1]["ask"]) if row["action"] == "LONG"
                                     else math.log(left[1]["bid"] / right[1]["ask"])) * 10000
                        else:
                            value = (math.log(right[1]["bid"] / left[1]["bid"]) if row["action"] == "LONG"
                                     else math.log(left[1]["ask"] / right[1]["ask"])) * 10000
                        expected[day] += value
                    expected[entry.date().isoformat()] -= commission * multiplier + slippage
                for day, observed in daily["days"].items():
                    if day in unknown:
                        assert observed["net_log_bps"] is None
                    else:
                        assert math.isclose(expected[day], observed["net_log_bps"], abs_tol=1e-7)
                    csv_rows.append({"scenario": scenario, "strategy": name, "commission_multiplier": multiplier,
                        "assumed_slippage_log_bps": slippage, "day_utc": day,
                        "weekday_schedule_assumption": timestamp(day+"T00:00:00+00:00").weekday() < 5,
                        **observed})
            n_rows += len(rows)
            n_ledgers += 1
    for fold, artifact in artifacts.items():
        cutoff = timestamp(plan["fold_boundaries"][fold])
        for key in artifact["evidence"]["fit_ids"]:
            row = panel[key]
            assert timestamp(row["decision_time"]) < cutoff and timestamp(row["exit_time"]) < cutoff
            assert timestamp(row["label_available_at"]) < cutoff
    output = {"status": "PASS_ACCOUNTING_AND_FROZEN_INFERENCE_NOT_PIT_OR_PROFIT_QUALIFICATION",
              "ledgers": n_ledgers, "rows": n_rows, "closed_returns_checked": n_returns,
              "new_predictions_reinferred": n_predictions, "daily_rows_checked": len(csv_rows),
              "producer_source_files": execution["source_files_sha256"], "consumer_sha256": digest(Path(__file__))}
    research_path(root / "independent_verification.json").write_text(json.dumps(output, indent=2), encoding="utf-8")
    with research_path(root / "daily_mtm.csv").open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(csv_rows[0]))
        writer.writeheader(); writer.writerows(csv_rows)
    main = results["results"]["HISTORICAL_COMPLETION_CONDITIONAL-0ms"]
    correlations = []
    for i, name in enumerate(main):
        for other in list(main)[i+1:]:
            left, right = main[name]["daily_mtm"][0]["days"], main[other]["daily_mtm"][0]["days"]
            paired = [(left[d]["net_log_bps"], right[d]["net_log_bps"]) for d in left
                      if left[d]["net_log_bps"] is not None and right[d]["net_log_bps"] is not None]
            correlations.append({"left": name, "right": other, "paired_known_days": len(paired),
                                 "all_calendar_days": len(left), "correlation": correlation(paired),
                                 "interpretation": "DESCRIPTIVE_KNOWN_DAYS_ONLY_NOT_INDEPENDENT_SOURCE_PROOF"})
    research_path(root / "daily_correlations.json").write_text(json.dumps(correlations, indent=2), encoding="utf-8")
    text = ["# Finite market methods: retrospective conditional results", "",
        "No method is deployment-qualified. Raw market receipt linkage and historical consumer visibility remain UNKNOWN.",
        "The panel and old b501/ca9 reports are unchanged. These are log-bps simulations, not broker/account P&L.", "",
        "## Fixed actual execution", "",
        f"Plan `{execution['plan_sha256']}`; source `{execution['source_git_sha']}`; clean={execution['worktree_clean']}.",
        f"Exactly {sum(results['fit_counts'].values())} new fits: {results['fit_counts']}; 50 depth-one boosting rounds, no test-driven stopping or parameter selection.",
        "Original 8-feature Ridge artifacts are reused. State Ridge/boosting add nine deterministic contemporaneous transformations, not News/event hindsight.",
        "State calibration uses a past inner model's later matured predictions. Every fold and failed method is retained.", "",
        "## Same opportunity, cost and fixed original exit", "",
        "|Method|Closed-only net|Trades|MSE U5²|Rank corr.|Known-day status|Worst stress closed-only net|Retain?|",
        "|---|---:|---:|---:|---:|---|---:|---|"]
    for name, value in main.items():
        p = value["prediction"]
        text.append(f"|{name}|{value['net_log_bps']:.3f}|{value['trades']}|{p['MSE_u5_squared']}|{p['rank_correlation']}|{value['daily_mtm'][0]['unknown_days']} UNKNOWN days|{value['cost_stress'][-1]['net_log_bps']:.3f}|{value['retain_research_candidate']}|")
    text += ["", "Closed-only totals are partial when unknown exposure exists; they are not complete equity. Daily CSV retains missing values, all 23 calendar days and weekday-schedule assumptions separately from unknown broker session authority.",
             "Daily marks use real latest receipts no more than 20 seconds before UTC midnight, conflicts before quality. Liquidation MTM increments include commission once at entry and the original fixed exit. No cost is inferred for WAIT.",
             "Operating expenses and capital are unknown: no dollar equity, annualized Sharpe or full-account profit claim. Boundary drawdown is not intraday maximum drawdown.", "",
             "## Concentration, delays and losses", "",
             "|Method|Fold0|Fold1|Fold2|Fold3|Known daily complete?|Worst day|Without best day|1s net|5s net|",
             "|---|---:|---:|---:|---:|---|---:|---:|---:|---:|"]
    for name, value in main.items():
        d = value["daily_mtm"][0]
        text.append("|"+"|".join(map(str, [name, *[round(value["folds"][str(f)],3) for f in range(4)],d["complete"],d["worst_day_log_bps"],d["without_best_day_log_bps"],
            round(results["results"]["HISTORICAL_COMPLETION_CONDITIONAL-1000ms"][name]["net_log_bps"],3),
            round(results["results"]["HISTORICAL_COMPLETION_CONDITIONAL-5000ms"][name]["net_log_bps"],3)]))+"|")
    text += ["", "## Decision and limits", "",
        "Positive subsets are research observations, never exemptions from the frozen cost/coverage/concentration/exposure tests. Fewer trades and lower MSE are not sufficient evidence of alpha.",
        "All prior and new test blocks were already examined and remain retrospective development. Future untouched validation is NOT_RUN. No method is automatically activated or selected for production.",
        "Information experiments are NOT_EVALUABLE without genuinely historical event-surprise/consensus/PIT records. Portfolio fitting is NOT_RUN: independent validated sources have not been established. No unavailable fields are fabricated.",
        "Consumer checks passed for raw return/cost arithmetic, fixed exit, non-overlap, method re-inference, maturity markers and daily increments. They do not upgrade timing, session or complete-exposure evidence.",
        f"Actual command: `{execution['command']}`", "", "Production mutation = 0. Assistant PAUSED."]
    research_path(root / "REPORT.md").write_text("\n".join(text)+"\n", encoding="utf-8")
    print(json.dumps(output))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--panel", type=Path, required=True)
    run(parser.parse_args())
