"""Execute frozen finite market methods against previously verified quote extracts."""
from __future__ import annotations

import argparse
from bisect import bisect_left
from datetime import timedelta
import gzip
import hashlib
import json
from pathlib import Path
import os
import subprocess
import sys
import time

os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("OMP_NUM_THREADS", "1")
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from xauusd_forecaster.causal_signal_replay import metrics, quote_quality, replay
from xauusd_forecaster.market import parse_quote_line
from xauusd_forecaster.offline_market_methods import NEW_METHODS, daily_mtm, fit_fold, method_prediction
from xauusd_forecaster.offline_model_paths import research_path
from xauusd_forecaster.offline_model_repair import action, correlation, opportunity, replay_qualified, sha, timestamp
import numpy as np

PLAN_SHA = "4aeea5eabac55076d3d8e2af18c2c7ead19a473d20d8369839ace37d2f0f9c11"
QUOTE_SHA = "1b355789fe0c8321eb9fd00c85ca80fc140416a1747b52d158c528aca079ac08"


def digest(path):
    with research_path(path).open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def read(path):
    return json.loads(research_path(path).read_text(encoding="utf-8"))


def write(path, value):
    research_path(path).write_text(json.dumps(value, separators=(",", ":"), allow_nan=False), encoding="utf-8")


def extract_marks(root, extract, boundaries):
    """One serial streaming pass, bounded and restricted to already copied files."""
    clocks = sorted(map(timestamp, boundaries))
    groups = {key: [] for key in boundaries}
    receipts = {key: None for key in boundaries}
    evidence = []
    for item in extract["archives"]:
        if item["status"] != "FROZEN":
            continue
        path = research_path(root / "archives" / item["name"])
        before = path.stat()
        if before.st_size != item["size"] or digest(path) != item["sha256"]:
            raise ValueError("QUOTE_ARCHIVE_IDENTITY_CHANGED")
        count = 0
        started = time.monotonic()
        print(json.dumps({"phase": "daily_marks", "archive": item["name"]}), flush=True)
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            for line in handle:
                count += 1
                if count > 1_000_000 or time.monotonic()-started > 120:
                    raise ValueError("DAILY_MARK_PASS_BOUND_EXCEEDED")
                q = parse_quote_line(line, path)
                i = bisect_left(clocks, q.received_time)
                if i == len(clocks) or (clocks[i]-q.received_time).total_seconds() > 20:
                    continue
                key = clocks[i].isoformat()
                previous = receipts[key]
                if previous is None or q.received_time > previous:
                    receipts[key], groups[key] = q.received_time, [q]
                elif q.received_time == previous:
                    groups[key].append(q)
        if count != item["rows"] or path.stat().st_mtime_ns != before.st_mtime_ns:
            raise ValueError("QUOTE_ARCHIVE_CHANGED_DURING_MARK_PASS")
        evidence.append({"name": item["name"], "sha256": item["sha256"], "rows": count,
                         "elapsed_seconds": time.monotonic()-started})
    marks = {}
    for key, values in groups.items():
        identities = {(q.event_time, q.bid, q.ask) for q in values}
        reason = "BOUNDARY_QUOTE_MISSING"
        quote = None
        if len(identities) > 1:
            reason = "AMBIGUOUS_BOUNDARY_QUOTE"
        elif values and quote_quality(values[0]):
            q = values[0]
            quote = {"bid": q.bid, "ask": q.ask, "received_time": q.received_time.isoformat(),
                     "event_time": q.event_time.isoformat()}
            reason = "REAL_FRESH_BOUNDARY_QUOTE"
        elif values:
            reason = "BOUNDARY_QUOTE_INVALID"
        marks[key] = {"quote": quote, "reason": reason}
    return {"schema": "offline-daily-marks-v1", "quote_extract_sha256": QUOTE_SHA,
            "marks": marks, "archives": evidence}


def run(args):
    started = time.monotonic()
    for name in ("panel", "baseline", "causal", "plan", "output"):
        setattr(args, name, research_path(getattr(args, name)))
    if args.output.exists():
        raise ValueError("OUTPUT_MUST_BE_NEW")
    if args.output.is_relative_to(Path(__file__).resolve().parents[2]) or any(
            args.output.is_relative_to(p) or p.is_relative_to(args.output)
            for p in (args.panel.parent, args.baseline, args.causal)):
        raise ValueError("OUTPUT_INPUT_OR_SOURCE_OVERLAP")
    if digest(args.plan) != PLAN_SHA:
        raise ValueError("FROZEN_PLAN_CHANGED")
    plan = read(args.plan)
    if (digest(args.panel) != plan["panel_sha256"]
            or digest(args.baseline / "results.json") != plan["baseline_results_sha256"]
            or digest(args.causal / "quote_extract.json") != QUOTE_SHA):
        raise ValueError("RESEARCH_INPUT_IDENTITY_MISMATCH")
    evidence = read(args.causal / "execution.json")
    if evidence["source_git_sha"] != plan["base_source"] or evidence["quote_extract_sha256"] != QUOTE_SHA:
        raise ValueError("CAUSAL_EXECUTION_IDENTITY_MISMATCH")
    panel_rows = read(args.panel)["rows"]
    all_rows = {r["source_decision_id"]: r for r in panel_rows}
    ledgers = {}
    for name in plan["baselines"]:
        path = research_path(args.baseline / (name + ".json"))
        if digest(path) != evidence["frozen_input_sha256"][name]:
            raise ValueError("FROZEN_BASELINE_LEDGER_CHANGED")
        ledgers[name] = read(path)
    authority = ledgers[plan["baselines"][0]]
    ids = [r["source_decision_id"] for r in authority]
    if len(ids) != plan["opportunity_count"] or len(set(ids)) != len(ids):
        raise ValueError("OPPORTUNITY_UNIVERSE_CHANGED")
    panel = {key: all_rows[key] for key in ids}
    timing_raw = read(args.causal / "timing_evidence.json")
    if digest(args.causal / "timing_evidence.json") != evidence["timing_sha256"]:
        # The causal producer reserializes JSON; semantic equality is bound by
        # the exact saved timing bytes only when the original bytes were copied.
        expected_timing = research_path(args.causal.parent / "causal-timing-b501.json")
        if digest(expected_timing) != evidence["timing_sha256"] or read(expected_timing) != timing_raw:
            raise ValueError("TIMING_EVIDENCE_CHANGED")
    timing = {r["source_decision_id"]: {"finished_at": r["historical_writer_conditional_upper_bound"],
        "visibility_status": r["strict_status"], "consumer_visible_no_later_than": r["strict_verified_signal_available_at"]}
        for r in timing_raw["records"]}
    extract = read(args.causal / "quote_extract.json")
    lower, upper = map(timestamp, (plan["fold_boundaries"][0], plan["fold_boundaries"][-1]))
    days = [(lower+timedelta(days=n)).date().isoformat() for n in range((upper-lower).days)]
    boundaries = [(lower+timedelta(days=n)).isoformat() for n in range(1, len(days)+1)]
    args.output.mkdir(parents=True)
    write(args.output / "frozen_plan.json", plan)
    if args.marks:
        marks_path = research_path(args.marks)
        if not args.marks_sha256 or digest(marks_path) != args.marks_sha256:
            raise ValueError("DAILY_MARK_BYTES_MISMATCH")
        mark_data = read(marks_path)
        if mark_data["quote_extract_sha256"] != QUOTE_SHA or set(mark_data["marks"]) != set(boundaries):
            raise ValueError("DAILY_MARK_IDENTITY_MISMATCH")
    else:
        mark_data = extract_marks(args.causal, extract, boundaries)
    write(args.output / "daily_marks.json", mark_data)
    if args.marks_only:
        print(json.dumps({"status": "MARKS_EXTRACTED_ONLY", "path": str(args.output / "daily_marks.json")}), flush=True)
        return
    artifacts, fits = [], {"ridge": 0, "boost": 0, "affine": 0}
    for name in NEW_METHODS:
        ledgers[name] = []
    # Same completed-clock opportunity condition as baseline; availability and
    # label maturity additionally gate fits. Invalid outcomes remain in test IDs.
    fit_rows = [r for r in panel_rows if opportunity(r) and replay_qualified(r)]
    for fold, boundary in enumerate(plan["fold_boundaries"][:-1]):
        cutoff = timestamp(boundary)
        print(json.dumps({"phase": "fit", "fold": fold, "cutoff": boundary}), flush=True)
        models, fit_evidence = fit_fold(fit_rows, cutoff, plan)
        for key in fits:
            fits[key] += fit_evidence["fits"][key]
        artifacts.append({"fold": fold, "models": models, "evidence": fit_evidence})
        for old in authority:
            if old["fold"] != fold:
                continue
            row = panel[old["source_decision_id"]]
            for name in NEW_METHODS:
                value = method_prediction(name, models, row)
                ledgers[name].append({**old, "action": action(value, row), "prediction_u5": value,
                    "strategy_version": sha(models[name]) if name in models else name,
                    "state": "RESEARCH_FROZEN_ACTION"})
    if sum(fits.values()) > plan["fit_budget"]["max_total_fits"]:
        raise ValueError("FIT_BUDGET_EXCEEDED")
    write(args.output / "artifacts.json", artifacts)
    result = {}
    ledger_dir = args.output / "ledgers"
    ledger_dir.mkdir()
    for mode in plan["execution_modes"]:
        for delay in plan["delays_ms"]:
            scenario = f"{mode}-{delay}ms"
            result[scenario] = {}
            for name, frozen in ledgers.items():
                if [r["source_decision_id"] for r in frozen] != ids:
                    raise ValueError("METHOD_OPPORTUNITY_IDENTITY_CHANGED")
                records = replay(frozen, panel, timing, extract["fills"], extract["endpoints"], name, mode, delay)
                value = metrics(records)
                pairs = [(r["prediction_u5"], panel[r["source_decision_id"]]["gross_target_u5"]) for r in records
                         if r["prediction_u5"] is not None and panel[r["source_decision_id"]]["valid"]]
                value["prediction"] = {"rows": len(pairs), "MSE_u5_squared": sum((p-y)**2 for p, y in pairs)/len(pairs) if pairs else None,
                                       "rank_correlation": correlation(pairs, rank=True)}
                value["daily_mtm"] = [daily_mtm(records, mark_data["marks"], days,
                    commission_multiplier=m, slippage=s) for m, s in plan["costs"]]
                value["retain_research_candidate"] = (value["trades"] >= 50
                    and all(d["complete"] and d["without_best_day_log_bps"] > 0 for d in value["daily_mtm"])
                    and sum(v > 0 for v in value["folds"].values()) >= 3)
                write(ledger_dir / f"{scenario}-{name}.json", records)
                result[scenario][name] = value
            print(json.dumps({"phase": "replay", "scenario": scenario}), flush=True)
    write(args.output / "results.json", {"schema": "offline-market-batch1-results-v1", "fit_counts": fits,
        "results": result, "future_validation": "NOT_RUN", "deployment_qualified": False,
        "raw_market_receipt_missing": sum(not r.get("source_received_time") for r in panel.values()),
        "market_feature_timing": "RETAINED_VALUES_WRITER_CONTRACT_CONDITIONAL_NOT_PER_ROW_RECEIPT_PROVEN",
        "information_gain": "NOT_EVALUABLE_NEWS_PIT_MISSING", "portfolio": "NOT_RUN_NO_INDEPENDENT_VALIDATED_SOURCE"})
    root = Path(__file__).resolve().parents[2]
    def git(*command):
        return subprocess.check_output(["git", *command], cwd=root, text=True, encoding="utf-8",
                                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).strip()
    files = ["scripts/research/run_market_method_batch.py", "xauusd_forecaster/offline_market_methods.py",
             "xauusd_forecaster/causal_signal_replay.py", "xauusd_forecaster/offline_model_repair.py",
             "xauusd_forecaster/training/ridge.py", "xauusd_forecaster/execution_costs.py", "docs/plans/MODEL_METHOD_BATCH1.json"]
    write(args.output / "execution.json", {"source_git_sha": git("rev-parse", "HEAD"),
        "worktree_clean": not bool(git("status", "--porcelain")), "source_files_sha256": {p: digest(root / p) for p in files},
        "plan_sha256": PLAN_SHA, "panel_sha256": digest(args.panel), "quote_extract_sha256": QUOTE_SHA,
        "daily_marks_sha256": digest(args.output / "daily_marks.json"), "results_sha256": digest(args.output / "results.json"),
        "artifacts_sha256": digest(args.output / "artifacts.json"), "command": sys.argv, "python": sys.version,
        "numpy_version": np.__version__, "numerical_threads": {k: os.environ.get(k) for k in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS")},
        "elapsed_seconds": time.monotonic()-started, "production_mutation": 0, "status": "DEVELOPMENT_RESEARCH_EXECUTED"})


def main():
    parser = argparse.ArgumentParser()
    for name in ("panel", "baseline", "causal", "plan", "output"):
        parser.add_argument("--" + name, required=True, type=Path)
    parser.add_argument("--marks", type=Path)
    parser.add_argument("--marks-sha256")
    parser.add_argument("--marks-only", action="store_true")
    run(parser.parse_args())


if __name__ == "__main__":
    main()
