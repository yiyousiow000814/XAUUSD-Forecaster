"""Independent ledger accounting consumer and source-bound replay report."""
import argparse
from collections import Counter
from datetime import datetime, timedelta
import csv
import hashlib
import json
import math
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from xauusd_forecaster.offline_model_paths import research_path


def read(path):
    return json.loads(research_path(path).read_text(encoding="utf-8"))


def digest(path):
    return hashlib.sha256(research_path(path).read_bytes()).hexdigest()


def table(lines, headings, rows):
    lines.extend(["", "| " + " | ".join(headings) + " |", "|" + "---|"*len(headings)])
    for row in rows:
        lines.append("| " + " | ".join(str(v) for v in row) + " |")


def compare(new, old):
    a = {r["source_decision_id"]: r for r in new if r["state"] == "CLOSED"}
    b = {r["source_decision_id"]: r for r in old if r["state"] == "CLOSED"}
    joint = a.keys() & b.keys()
    return {"matched_trades": len(joint),
            "matched_net_delta": sum(a[k]["net_log_bps"]-b[k]["net_log_bps"] for k in joint),
            "added_trades": len(a.keys()-b.keys()), "added_net": sum(a[k]["net_log_bps"] for k in a.keys()-b.keys()),
            "removed_trades": len(b.keys()-a.keys()), "removed_old_net": sum(b[k]["net_log_bps"] for k in b.keys()-a.keys())}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--frozen", type=Path, required=True)
    args = parser.parse_args()
    root, frozen = research_path(args.run), research_path(args.frozen)
    execution, data = read(root/"execution.json"), read(root/"results.json")
    if digest(root/"results.json") != execution["results_sha256"]:
        raise ValueError("RESULT_BYTES_CHANGED")
    if digest(root/"quote_extract.json") != execution["quote_extract_sha256"]:
        raise ValueError("QUOTE_EXTRACT_BYTES_CHANGED")
    extract = read(root/"quote_extract.json")
    names = list(data["baseline"])
    frozen_rows = {n: read(frozen/f"{n}.json") for n in names}
    for name in names:
        if digest(frozen/f"{name}.json") != execution["frozen_input_sha256"][name]:
            raise ValueError("FROZEN_PREDICTIONS_CHANGED")
    clock_rows = {n: read(frozen/f"{n}-clock-counterfactual.json") for n in names}
    ids = {r["source_decision_id"] for r in frozen_rows[names[0]]}
    verification, csv_rows, paired = [], [], {}
    primary_rows = {}
    commission = -math.log1p(-60/1_000_000)*10000
    for scenario, strategies in data["results"].items():
        for name, metric in strategies.items():
            rows = read(root/"ledgers"/f"{scenario}-{name}.json")
            assert len(rows) == len(ids) == 3646
            assert {r["source_decision_id"] for r in rows} == ids
            original = {r["source_decision_id"]: r for r in frozen_rows[name]}
            for r in rows:
                assert r["action"] == original[r["source_decision_id"]]["action"]
                assert r["prediction_u5"] == original[r["source_decision_id"]]["prediction_u5"]
                assert r["final_reason"]
            assert dict(Counter(r["state"] for r in rows)) == metric["states"]
            assert sum(metric["waterfall"].values()) == 3646
            closed = [r for r in rows if r["state"] == "CLOSED"]
            total = 0.
            until = None
            for r in closed:
                order, entry, exit_at = (datetime.fromisoformat(r[k]) for k in ("order_time", "entry_time", "exit_time"))
                assert order < entry <= order+timedelta(seconds=20)
                assert entry < datetime.fromisoformat(r["original_target_exit"]) <= exit_at
                assert until is None or order > until
                until = exit_at
                q, e = r["entry_quote"], r["exit_quote"]
                gross = math.log(e["bid"]/q["ask"]) if r["action"] == "LONG" else math.log(q["bid"]/e["ask"])
                net = gross*10000-commission
                assert abs(net-r["net_log_bps"]) < 1e-8
                total += net
            assert abs(total-metric["net_log_bps"]) < 1e-7
            assert all(r["net_log_bps"] is None for r in rows if r["state"] != "CLOSED")
            assert abs(sum(metric["daily"].values())-total) < 1e-7
            assert abs(sum(metric["folds"].values())-total) < 1e-7
            verification.append(dict(scenario=scenario, strategy=name, opportunities=len(rows), closed=len(closed), net_log_bps=total))
            for reason, count in metric["waterfall"].items():
                csv_rows.append(dict(scenario=scenario, strategy=name, final_reason=reason, count=count,
                                     total_opportunities=3646, late_old_entry_intermediate=metric["late_old_entry_intermediate"]))
            if scenario == "HISTORICAL_COMPLETION_CONDITIONAL-0ms":
                primary_rows[name] = rows
                paired[name] = compare(rows, clock_rows[name])
                p = paired[name]
                old_net = sum(r["net_log_bps"] for r in clock_rows[name] if r["state"] == "CLOSED")
                assert abs(total-old_net-(p["matched_net_delta"]+p["added_net"]-p["removed_old_net"])) < 1e-7
    (root/"independent_verification.json").write_text(json.dumps(dict(
        status="PASS_ACCOUNTING_NOT_TIMING_QUALIFICATION", producer_sha256=digest(Path(__file__)),
        ledger_checks=verification, paired_clock_decomposition=paired), indent=2), encoding="utf-8")
    with (root/"waterfalls.csv").open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(csv_rows[0]))
        writer.writeheader()
        writer.writerows(csv_rows)
    with (root/"all_scenario_costs.csv").open("w", encoding="utf-8", newline="") as f:
        fields = ["scenario", "strategy", "trades", "commission_multiplier", "assumed_slippage_log_bps", "net_log_bps"]
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for scenario, strategies in data["results"].items():
            for name, metric in strategies.items():
                for stress in metric["cost_stress"]:
                    writer.writerow(dict(scenario=scenario, strategy=name, trades=metric["trades"], **stress))
    primary = data["results"]["HISTORICAL_COMPLETION_CONDITIONAL-0ms"]
    lines = ["# Causal signal-time execution replay", "",
        "**Share with caveats: conditional retrospective execution, NOT live qualification or broker P&L.**",
        "No fitting, parameter search, production mutation or model activation. All b501da8a results and failures are preserved.", "",
        f"Replay source: `{execution['source_git_sha']}`; clean: `{execution['worktree_clean']}`. Frozen source: `b501da8ad73d2dc2efcd45e081ebff470c26371c`.",
        "3,646 common opportunities per strategy; 21 unchanged strategies. August 13–September 5, 2026 UTC (last retained decision September 4 20:25 UTC). All data was already viewed: retrospective, not untouched validation.",
        "",
        "## Timing audit and evidence limitations", "",
        "Historical caller takes the clock before work. All 5,813 feature recomputed_at and prediction created_at values equal collector.started_at. They are START aliases, not inference completion. Source receipt is quote arrival; decision_time is the scheduled grid, not consumer-visible signal time.",
        "Historical forward_engine -> append_live_decision_v2 commits predictions before sampling collector.completed_at. This supplies a conditional upper bound under that writer. The post-455 atomic writer samples completed_at BEFORE commit; it cannot use the same interpretation. Per-clock deployed SHA/PID and actual consumer read times are absent. EVIDENCE_ONLY therefore leaves every directional opportunity UNKNOWN, not zero-return trades.",
        "Market math does not depend on News. Historical MARKET publication nevertheless shared the combined transaction. There is no measured earlier Market-only completion. Dependency-marker and common-marker scenarios are explicitly hypothetical (+100ms computation/publication), not repaired historical timestamps. Fitted A/B/C/Ridge methods also did not run historically; their completion-bound scenario adds an assumed 100ms.",
        "Outcome recomputed_at is likewise a poll-start marker, not label commit. Previous fitting-maturity fields remain preserved but are NOT upgraded to exact commit proof. No refit was performed. News event PIT and session authority remain UNKNOWN.",
        "Source query, full timestamp records, historical owner hashes and atomic-change chronology are in timing_evidence.json. Timestamp producer and archived quote-copy provenance remain beside the frozen experiment inputs.",
        "",
        "## Full opportunity waterfall", "",
        "Old final states remain mutually exclusive in old_opportunity_waterfall.json. The table below shows every strategy; no row is silently dropped. Late entry is an intermediate state in the new simulation, because a later real fill is allowed. WAIT has priority; input-unknown, ownership, target and quote rejection follow in that order. No-post-quote and unresolved counts are shown even when zero."]
    table(lines, ["Strategy", "Old WAIT", "Old late-entry rejection", "Old unknown entry", "Old overlap", "Old trades"],
          [[n, *(data["old_waterfall"][n]["final_states"].get(k,0) for k in ("WAIT","INPUT_NOT_AVAILABLE_AT_ENTRY","ENTRY_UNVERIFIED","OVERLAP_BLOCK","CLOSED"))] for n in names])
    lines.extend(["", "Conditional historical-writer completion, zero added execution delay (fill strictly later than signal):"])
    table(lines, ["Strategy", "WAIT", "Input unknown", "Target unknown/expired", "No later quote", "Overlap", "Unresolved", "Closed", "Late old entry (intermediate)"],
          [[n, m["states"].get("WAIT",0),m["states"].get("INPUT_AVAILABILITY_UNKNOWN",0),m["states"].get("ENTRY_UNVERIFIED",0)+m["states"].get("SIGNAL_EXPIRED",0),m["states"].get("NO_POST_SIGNAL_QUOTE",0),m["states"].get("OVERLAP_BLOCK",0),m["states"].get("UNRESOLVED_EXPOSURE",0),m["trades"],m["late_old_entry_intermediate"]] for n,m in primary.items()])
    lines.extend(["", "EVIDENCE_ONLY: same WAIT counts; every remaining directional recommendation is CONSUMER_VISIBILITY_UNKNOWN. Every mode/delay/strategy's unique final reasons are in waterfalls.csv and its complete opportunity ledger.",
        "", "## Same fixed policy, different execution evidence", "",
        "All monetary numbers below are **log-bps**, not account percentages/dollars. Bid/Ask already includes spread; commission is charged once. Missing results are not booked as zero. Old and new trade masks differ; total changes are NOT pure latency estimates."])
    table(lines,["Strategy","Old label-limited net","Clock counterfactual net","Conditional trades","Bid/Ask gross","Net","PF","Added 1s net","Added 5s net","2x commission +1bp net"],
          [[n,f"{data['baseline'][n]['net_log_bps']:.3f}",f"{sum(r['net_log_bps'] for r in clock_rows[n] if r['state']=='CLOSED'):.3f}",m['trades'],f"{m['bidask_before_commission_log_bps']:.3f}",f"{m['net_log_bps']:.3f}", f"{m['PF']:.3f}" if m['PF'] is not None else "NA",
            f"{data['results']['HISTORICAL_COMPLETION_CONDITIONAL-1000ms'][n]['net_log_bps']:.3f}",f"{data['results']['HISTORICAL_COMPLETION_CONDITIONAL-5000ms'][n]['net_log_bps']:.3f}",f"{m['cost_stress'][-1]['net_log_bps']:.3f}"] for n,m in primary.items()])
    lines.extend(["", "Original fixed target is old entry receipt +30m, with its retained exit quote. Never new fill +30m. Fill first quality-valid quote strictly after signal+delay, expiry20s. One pending/position owner crosses every fold/generation. Unknown exit retains exposure. Same-timestamp conflicting quote groups are rejected.", "", "## Entry-price effect versus changed trade selection", "",
        "On all newly closed opportunities, compare new Bid/Ask entry to the original label entry with the SAME action and exit (paired entry delta). Separately decompose new-minus-clock total = matched trade net delta + added trade net - removed old trade net. This is policy accounting, not News causality."])
    table(lines,["Strategy","Paired entry delta, all new trades","Clock matched count","Matched delta","Added net","Removed old net"],
          [[n,f"{primary[n]['paired_entry_change_log_bps']:.3f}",p['matched_trades'],f"{p['matched_net_delta']:.3f}",f"{p['added_net']:.3f}",f"{p['removed_old_net']:.3f}"] for n,p in paired.items()])
    lines.extend(["", "## Prediction metrics and concentration", "", "Predictions/actions/versions are unchanged per opportunity; old gross-target U5 MSE/rank correlation remain in the frozen results. Residual directional scoring is not the residual's training-target calibration. Four dependent folds and 5-minute overlap are not independent account samples."])
    table(lines,["Strategy","Unchanged MSE U5 squared","Unchanged rank correlation"],
          [[n, f"{m['mse_u5']:.6f}" if m['mse_u5'] is not None else "NA",
            f"{m['rank_correlation']:.6f}" if m['rank_correlation'] is not None else "NA"] for n,m in data['baseline'].items()])
    table(lines,["Strategy","Net by four fixed blocks","Net excluding best day","Closed DD log-bps"],
          [[n,", ".join(f"{v:.2f}" for v in m['folds'].values()),f"{m['without_best_day']:.3f}" if m['without_best_day'] is not None else "NA",f"{m['drawdown_log_bps_closed_trades']:.3f}"] for n,m in primary.items()])
    lines.extend(["", "Daily, original-version, generation and fold contributions are retained in results.json/ledgers and the frozen input join. No broker fills exist; recorded effective actions remain WAIT. Closed drawdown excludes mark-to-market and is incomplete if exposure cannot be settled.",
        "", "## Decision", "",
        "The old tiny label-limited accepted subset mostly reflected unusable old label entry times; it was not a representative strategy performance sample. Allowing subsequent real quotes changes that conclusion about coverage, not the historical data itself.",
        "Market, Full/Broad Full and fixed A/B/Ridge are not established advantages; do not activate them from this run. Latency is not a uniform cost: paired entry deltas can help or hurt, and order-time ownership changes the traded subset. Net losses cannot all be blamed on latency or server failure.",
        "Broad residual's positive completion-bound total is a research residual-direction diagnostic, not an execution-model recommendation. Its small edge is fragile to costs and date removal. C policies have only 5–9 closed trades concentrated in three dates with no trades in two fixed blocks: insufficient evidence, even when stress remains positive. All positive cases remain retrospective and timing-conditional.",
        "Stop this parameter search. The next hypothesis to freeze (not implemented/trained here) is a prediction target measured from proven signal-publication time plus a fixed execution delay to the ORIGINAL target exit, using only inputs known at publication. This addresses the demonstrated mismatch between clock-label entry and usable signal, without claiming it will create an edge. It needs explicit completion/consumer-visible timing evidence and future untouched data before acceptance; larger models/more News are not justified by this replay.",
        "", "## Verification and reproducibility", "",
        f"{len(verification)} scenario/strategy ledgers independently reconciled; every ledger has 3,646 unique opportunities, frozen predictions/actions unchanged, entry/exit ordering, costs and day/fold sums checked.",
        f"Archived rows read: {sum(a.get('rows',0) for a in extract['archives']):,}; old valid endpoints reconciled: {sum(e.get('label_reconciled',False) for e in extract['endpoints'].values())}/3,646. Missing/invalid endpoints remain explicit.",
        "Complete commands and source/input SHA256 are in execution.json. Re-run scripts/run_causal_signal_replay.py with its recorded command and a NEW output directory, then scripts/report_causal_signal_replay.py --run <output> --frozen <b501da8a-results>. The quote inputs are frozen copies; no production reads are required for replay. all_scenario_costs.csv contains every delay x frozen-cost stress; no favorable combination was selected.",
        "Production mutation=0; training calls=0; model activation=0; broker operations=0; Assistant PAUSED. PR #456 and original30 remain separate and unresolved here."])
    (root/"REPORT.md").write_text("\n".join(lines)+"\n",encoding="utf-8")
    print("CAUSAL_ACCOUNTING_VERIFIED_REPORT_WRITTEN")


if __name__ == "__main__":
    main()
