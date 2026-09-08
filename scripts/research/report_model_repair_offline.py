"""Render the executed experiment, never manufacture missing experiment evidence."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from xauusd_forecaster.offline_model_paths import research_path


def render(directory):
    directory = research_path(directory)
    payload = json.loads((directory / "results.json").read_text(encoding="utf-8"))
    execution = json.loads((directory / "execution.json").read_text(encoding="utf-8"))
    if hashlib.sha256((directory / "results.json").read_bytes()).hexdigest() != execution["results_sha256"]:
        raise ValueError("RESULT_DIGEST_MISMATCH")
    results = payload["results"]
    def n(value):
        return "NA" if value is None else f"{value:.4f}"
    text = ["# Offline model repair: executed retrospective experiments", "",
            "No strategy is production-qualified. These are simulated recommendations, not broker P&L.", "",
            f"Source: `{execution['source_git_sha']}`; clean source: `{execution['worktree_clean']}`.",
            f"Panel SHA-256: `{execution['panel_sha256']}`.",
            f"Frozen plan SHA-256: `{execution['plan_sha256']}`.",
            f"Common test opportunities: {payload['opportunity_count']}; finite Ridge control executed: {payload['ridge_triggered']}.", "",
            "## What was actually changed", "",
            "An opt-in calibrator/combination experiment and finite pure-owner Ridge control, plus an availability-aware single-position simulator. No runtime inference, training activation, old scores or production model was changed. Historical raw-scale discrepancies belong to the already-fixed Ridge contract; no new current arithmetic defect was established.", "",
            "The plan was frozen before scores. Its pre-score execution amendment follows the discovered label-entry/input-persistence mismatch, not observed candidate profits. Primary trades require retained common input availability no later than the retained entry quote. Persistence is conservative evidence, not an exact inference completion timestamp. Rejected entries are not filled at invented prices. UNKNOWN session remains UNKNOWN; valid retained endpoints do not prove broker session history.", "",
            "## Same-opportunity results", "",
            "All values are log-bps, not dollars or account percentages. Clock CF is a single-position clock-time counterfactual that ignores delayed input availability; it is NOT a verified executable strategy. EVERY_5M/FIXED_30M recommendation scores remain in results.json and are not independent account curves.", "",
            "| Strategy | Trades | Bid/Ask before commission | Commission | Net | PF | Closed DD | Clock CF net | Research candidate |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---|"]
    for name, r in results.items():
        text.append(f"| {name} | {r['trades']} | {n(r['bidask_before_commission_log_bps'])} | {n(r['commission_log_bps'])} | {n(r['net_log_bps'])} | {n(r['PF'])} | {n(r['drawdown_log_bps_closed_trades'])} | {n(r['clock_time_counterfactual']['net_log_bps'])} | {r['research_candidate']} |")
    text += ["", "## Calibration, ranking and later time blocks", "",
             "Residual-lane error against the gross target is only a directional proxy, not residual-training-target calibration. WAIT participates in mature fitting; it has no transaction cost. Gross target/U5 is calibrated before decision-time cost is applied. Realized Bid/Ask labels already include spread, so only commission and assumed slippage are deducted from those labels.", "",
             "| Strategy | Gross-target MSE (U5 squared) | Rank correlation | Test-block net vector (log-bps) | Worst cost stress net | Net excluding best day |",
             "|---|---:|---:|---|---:|---:|"]
    for name, r in results.items():
        blocks = ", ".join(n(f["net_log_bps"]) for f in r["folds"].values())
        text.append(f"| {name} | {n(r['mse_u5'])} | {n(r['rank_correlation'])} | {blocks} | {n(min(s['net_log_bps'] for s in r['stress'].values()))} | {n(r['without_best_day'])} |")
    text += ["", "Only four dependent time blocks exist. There is insufficient independent history for reliable confidence intervals; overlapping 5-minute and 30-minute reports do not increase that sample count.", "",
             "## Parameters and attribution", "",
             "The full grid and inner-validation scores are retained in results.json. All fitted fold artifacts are in artifacts/. A smaller MSE, fewer trades, all-WAIT, or a positive clock-counterfactual total is not sufficient acceptance.", ""]
    for item in payload["artifacts"]:
        if item["family"] in ("A_MARKET", "A_BROAD_FULL", "B_COMBINATION"):
            a = item["artifact"]
            text.append(f"- {item['family']} fold {item['fold']}: " + (f"slopes={a['coefficients']}, centered intercept={a['intercept']}, penalty={a['penalty']}, selected margin={a['selected_margin']}, mature rows={a['rows']}." if a else "INSUFFICIENT training evidence."))
    text += ["", "Paired comparisons below use the same entire opportunity timeline, assigning no invented payoff to unresolved exposures. They are policy differences, NOT a causal News treatment effect.", ""]
    baseline = results["ORIGINAL_BROAD_FULL"]
    for name in ("A_MARKET", "A_BROAD_FULL", "B_COMBINATION", "C_B_COMBINATION", "RIDGE_SELECTED"):
        if name in results:
            r = results[name]
            text.append(f"- {name} versus original Broad Full: known-net difference {n(r['net_log_bps']-baseline['net_log_bps'])} log-bps; trade difference {r['trades']-baseline['trades']}. Action transitions, same/opposite-direction categories and their counts/net contributions are retained in results.json.")
    text += ["", "## Limits, decisions and files", ""]
    text.extend(f"- {line}." for line in payload["limitations"])
    text += ["- Unknown lineage remains explicitly counted; numeric historical predictions are not repaired into synthetic PIT receipts.",
             "- Slippage zero is ASSUMED. Cost stress is a deterministic scenario, not measured broker cost.",
             "- Actual execution actions in the retained system were WAIT. These simulated fills do not amend that history.",
             "- Each strategy JSON contains per-opportunity predictions, state, costs and closed net; separate clock-counterfactual JSON preserves the non-executable diagnostic. Daily/version/generation/fold contributions, all stress scenarios and parameter trials are in results.json.", ""]
    candidates = [name for name, r in results.items() if r["research_candidate"]]
    text.append("Research candidates: " + (", ".join(candidates) if candidates else "NONE. Reject all tested strategies for promotion. Keep the code/artifacts as reproducible failed research, not a winning model.") )
    text += ["", "Production mutation = 0. No broker actions, model activation, #456 edits, Control Plane installation or Promote. Assistant remains PAUSED. Recovery and original PR-intent work are separate and are not declared complete by this report.", ""]
    (directory / "REPORT.md").write_text("\n".join(text), encoding="utf-8")
    followup = {"status": "NOT_RUN", "candidate": candidates or None,
                "unseen_validation_exists": False, "fit_or_threshold_changes": "Require a new frozen hypothesis before use",
                "earliest_new_data": "Strictly after retained data coverage, with labels mature and exact artifact/PIT provenance",
                "execution_requirement": "Need quote prices after proven signal availability, exact session/receipt and fixed-exit facts; no invented price",
                "promotion": "NOT_AUTHORIZED", "artifact_use": "Offline reproduction only; never copy into active model directories"}
    (directory / "future_validation.json").write_text(json.dumps(followup, indent=2), encoding="utf-8")
    return directory / "REPORT.md"


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--results-dir", type=Path, required=True)
    render(parser.parse_args().results_dir)
    print("OFFLINE_MODEL_REPORT_WRITTEN")
