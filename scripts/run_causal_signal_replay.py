"""Replay frozen #457 actions against archived quotes; never fit a model."""
from __future__ import annotations

import argparse
from bisect import bisect_left, bisect_right
from collections import Counter
from datetime import timedelta
import gzip
import hashlib
import json
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from xauusd_forecaster.causal_signal_replay import (
    MODES, DELAYS_MS, first_after, quote_key, quote_quality, signal_time, replay, metrics, verify_frozen_predictions,
)
from xauusd_forecaster.market import parse_quote_line
from xauusd_forecaster.offline_model_paths import research_path
from xauusd_forecaster.offline_model_repair import timestamp


def digest(path):
    return hashlib.sha256(research_path(path).read_bytes()).hexdigest()


def write(path, value):
    research_path(path).write_text(json.dumps(value, indent=2, allow_nan=False), encoding="utf-8")


def freeze_and_select(source, output, orders, panel):
    """One day in memory; one pass per frozen closed archive, no repeated windows."""
    fills, endpoints, manifests = {}, {}, []
    by_day = {}
    for order in orders:
        by_day.setdefault(order[:10], []).append(timestamp(order))
    wanted = {}
    for key, row in panel.items():
        for field in ("entry_time", "exit_time"):
            if row.get(field):
                wanted.setdefault(row[field][:10], []).append((key, field, timestamp(row[field])))
    source = research_path(source)
    archive_dir = research_path(output / "archives")
    archive_dir.mkdir()
    order_times = sorted(timestamp(value) for value in orders)
    candidate_quotes = []
    endpoint_quotes = {}
    endpoint_requests = sorted((at, key, field) for requests in wanted.values() for key, field, at in requests)
    endpoint_times = [item[0] for item in endpoint_requests]
    days = set(by_day) | set(wanted)
    # Filenames are not receipt-clock authority. Include adjacent archives and
    # select globally by actual received_time, including midnight/skew edges.
    first_day = timestamp(min(days)+"T00:00:00+00:00")-timedelta(days=1)
    last_day = timestamp(max(days)+"T00:00:00+00:00")+timedelta(days=1)
    days = [(first_day+timedelta(days=n)).date().isoformat() for n in range((last_day-first_day).days+1)]
    if len(days) > 32:
        raise ValueError("REPLAY_DATE_BOUND_EXCEEDED")
    # Inputs must already be frozen research copies. No live runtime roots.
    for day in days:
        name = f"xauusd-quotes-{day.replace('-', '')}.jsonl.gz"
        original = research_path(source / name)
        if not original.is_file():
            manifests.append({"name": name, "status": "SOURCE_MISSING"})
            continue
        before = original.stat()
        destination = research_path(archive_dir / name)
        shutil.copyfile(original, destination)
        source_sha = digest(original)
        if digest(destination) != source_sha or original.stat().st_mtime_ns != before.st_mtime_ns:
            raise ValueError("QUOTE_SOURCE_CHANGED")
        rows, invalid = [], 0
        started = time.monotonic()
        with gzip.open(destination, "rt", encoding="utf-8") as handle:
            for number, line in enumerate(handle, 1):
                if number > 1_000_000 or time.monotonic()-started > 120:
                    raise ValueError("QUOTE_ARCHIVE_BOUND_EXCEEDED")
                try:
                    rows.append(parse_quote_line(line, destination))
                except (ValueError, KeyError, TypeError):
                    invalid += 1
        # Invalid unlocatable rows could hide the first post-order receipt.
        # Fail the extract, do not silently trade over a malformed source.
        if invalid:
            raise ValueError("MALFORMED_QUOTE_ARCHIVE")
        rows.sort(key=lambda q: (q.received_time, q.event_time))
        times = [q.received_time for q in rows]
        outside_day = sum(q.received_time.date().isoformat() != day for q in rows)
        # Group ALL relevant receipts across files before quality filtering.
        # A stale and valid conflicting copy at the same receipt is ambiguous,
        # not permission to forget the stale copy and choose the good price.
        for quote in rows:
            index = bisect_left(order_times, quote.received_time)-1
            if index >= 0 and quote.received_time <= order_times[index]+timedelta(seconds=20):
                candidate_quotes.append(quote)
                if len(candidate_quotes) > 1_000_000:
                    raise ValueError("CANDIDATE_QUOTE_BOUND_EXCEEDED")
        requests = endpoint_requests[bisect_left(endpoint_times, times[0]):bisect_right(endpoint_times, times[-1])] if times else []
        for at, key, field in requests:
            selected = rows[bisect_left(times, at):bisect_right(times, at)]
            endpoint_quotes.setdefault((key, field), []).extend(selected)
        manifests.append({"name": name, "sha256": source_sha, "size": before.st_size,
                          "rows": len(rows), "invalid": invalid, "receipt_outside_named_day": outside_day, "status": "FROZEN",
                          "elapsed_seconds": time.monotonic()-started})
        print(json.dumps({"phase": "quotes", "day": day, "rows": len(rows)}), flush=True)
    candidate_quotes.sort(key=lambda q: (q.received_time, q.event_time))
    candidate_times = [q.received_time for q in candidate_quotes]
    for order in order_times:
        fills[quote_key(order)] = first_after(candidate_quotes, candidate_times, order)
    for (key, field), selected in endpoint_quotes.items():
        identities = {(q.event_time, q.bid, q.ask) for q in selected}
        if len(identities) > 1:
            raise ValueError("AMBIGUOUS_OLD_LABEL_ENDPOINT")
        if len(identities) == 1 and quote_quality(selected[0]):
            q = selected[0]
            endpoints.setdefault(key, {})["old_"+field] = {
                "event_time": q.event_time.isoformat(), "received_time": q.received_time.isoformat(),
                "bid": q.bid, "ask": q.ask}
    import math
    for key, row in panel.items():
        endpoint = endpoints.setdefault(key, {})
        entry, exit_quote = endpoint.get("old_entry_time"), endpoint.get("old_exit_time")
        endpoint["label_reconciled"] = False
        endpoint["reason"] = "OLD_LABEL_ENDPOINT_MISSING"
        if entry and exit_quote and row.get("valid"):
            long = math.log(exit_quote["bid"]/entry["ask"])
            short = math.log(entry["bid"]/exit_quote["ask"])
            matches = (abs(long-row["long_log_return"]) < 1e-10
                       and abs(short-row["short_log_return"]) < 1e-10
                       and timestamp(exit_quote["received_time"]) >= timestamp(entry["received_time"])+timedelta(minutes=30))
            if not matches:
                raise ValueError("OLD_LABEL_QUOTE_RECONCILIATION_FAILED")
            endpoint.update(label_reconciled=True, exit_quote=exit_quote, reason="EXACT_LABEL_RECONCILED")
    return {"fills": fills, "endpoints": endpoints, "archives": manifests}


def run(args):
    args.panel = research_path(args.panel)
    args.frozen = research_path(args.frozen)
    args.timing = research_path(args.timing)
    args.quotes = research_path(args.quotes)
    args.output = research_path(args.output)
    if research_path(args.output).exists():
        raise ValueError("OUTPUT_MUST_BE_NEW")
    if any(p.is_relative_to(args.output) for p in (args.panel, args.frozen, args.timing, args.quotes)):
        raise ValueError("OUTPUT_OWNS_INPUT")
    old_execution = json.loads(research_path(args.frozen / "execution.json").read_text(encoding="utf-8"))
    if (old_execution["source_git_sha"] != "b501da8ad73d2dc2efcd45e081ebff470c26371c"
            or digest(args.frozen / "results.json") != "999e57c4f973129d72307417f7a7439b2d011f4ad602d6997d2cf23eeb03d836"
            or digest(args.panel) != old_execution["panel_sha256"]
            or digest(args.frozen / "results.json") != old_execution["results_sha256"]):
        raise ValueError("FROZEN_BASELINE_IDENTITY_MISMATCH")
    baseline = json.loads(research_path(args.frozen / "results.json").read_text(encoding="utf-8"))
    strategies = {name: json.loads(research_path(args.frozen/f"{name}.json").read_text(encoding="utf-8"))
                  for name in baseline["results"]}
    ids = [r["source_decision_id"] for r in next(iter(strategies.values()))]
    if len(ids) != 3646 or len(set(ids)) != 3646:
        raise ValueError("FROZEN_OPPORTUNITY_COUNT_MISMATCH")
    if any([r["source_decision_id"] for r in rows] != ids for rows in strategies.values()):
        raise ValueError("FROZEN_OPPORTUNITY_UNIVERSE_MISMATCH")
    panel = {r["source_decision_id"]: r for r in json.loads(research_path(args.panel).read_text(encoding="utf-8"))["rows"]
             if r["source_decision_id"] in set(ids)}
    verify_frozen_predictions(strategies, panel, baseline)
    timing_payload = json.loads(research_path(args.timing).read_text(encoding="utf-8"))
    if (timing_payload["panel_sha256"] != digest(args.panel)
            or set(timing_payload["common_test_decision_ids"]) != set(ids)
            or not timing_payload["input_identity_unchanged"]):
        raise ValueError("TIMING_SOURCE_BINDING_MISMATCH")
    timing = {r["source_decision_id"]: {
        "finished_at": r["historical_writer_conditional_upper_bound"],
        "visibility_status": r["strict_status"],
        "consumer_visible_no_later_than": r["strict_verified_signal_available_at"],
    } for r in timing_payload["records"]}
    research_path(args.output).mkdir(parents=True)
    # FIRST output: exhaustive old waterfall, before quote reading/new returns.
    old_waterfall = {name: {"opportunities": len(rows), "final_states": dict(Counter(r["state"] for r in rows)),
                          "late_old_entry_intermediate": sum(
                              bool(panel[r["source_decision_id"]].get("entry_time") and r.get("input_available_at"))
                              and timestamp(r["input_available_at"]) > timestamp(panel[r["source_decision_id"]]["entry_time"])
                              for r in rows)} for name, rows in strategies.items()}
    write(args.output/"old_opportunity_waterfall.json", old_waterfall)
    orders = set()
    for strategy in strategies:
        for row in panel.values():
            for mode in MODES:
                signal, _ = signal_time(row, strategy, mode, timing.get(row["source_decision_id"], {}))
                if signal:
                    orders.update(quote_key(signal+timedelta(milliseconds=d)) for d in DELAYS_MS)
    extract = freeze_and_select(args.quotes, args.output, orders, panel)
    write(args.output/"quote_extract.json", extract)
    write(args.output/"timing_evidence.json", timing_payload)
    results = {}
    ledgers = research_path(args.output / "ledgers")
    ledgers.mkdir()
    for mode in MODES:
        for delay in DELAYS_MS:
            key = f"{mode}-{delay}ms"
            results[key] = {}
            for name, frozen in strategies.items():
                records = replay(frozen, panel, timing, extract["fills"], extract["endpoints"], name, mode, delay)
                results[key][name] = metrics(records)
                # Compact JSON preserves every opportunity without multiplying
                # indented formatting and old per-model metadata across scenarios.
                research_path(ledgers/f"{key}-{name}.json").write_text(
                    json.dumps(records, separators=(",", ":"), allow_nan=False), encoding="utf-8")
            print(json.dumps({"phase": "replay", "scenario": key}), flush=True)
    write(args.output/"results.json", {"schema": "causal-signal-time-research-v1", "old_waterfall": old_waterfall,
        "results": results, "production_mutation": 0, "training_calls": 0, "baseline": baseline["results"]})
    root = Path(__file__).resolve().parents[1]
    def git(*command):
        return subprocess.check_output(["git", *command], cwd=root, text=True, encoding="utf-8",
                                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).strip()
    source_files = ["scripts/run_causal_signal_replay.py", "xauusd_forecaster/causal_signal_replay.py",
                    "scripts/report_causal_signal_replay.py",
                    "xauusd_forecaster/market.py", "xauusd_forecaster/evidence/executable_label.py",
                    "xauusd_forecaster/offline_model_repair.py", "xauusd_forecaster/execution_costs.py",
                    "docs/plans/CAUSAL_SIGNAL_TIME_REPLAY.md"]
    write(args.output/"execution.json", {"source_git_sha": git("rev-parse", "HEAD"),
        "worktree_clean": not bool(git("status", "--porcelain")),
        "source_files_sha256": {p: digest(root/p) for p in source_files},
        "frozen_input_sha256": {name: digest(args.frozen/f"{name}.json") for name in strategies},
        "panel_sha256": digest(args.panel), "timing_sha256": digest(args.timing),
        "results_sha256": digest(args.output/"results.json"), "quote_extract_sha256": digest(args.output/"quote_extract.json"),
        "command": sys.argv, "python": sys.version, "platform": platform.platform(),
        "status": "EXECUTED_CONDITIONAL_RESEARCH_NOT_LIVE_QUALIFICATION",
        "frozen_predictions_reinferred_without_fit": len(ids)*len(strategies), "production_mutation": 0})


def main():
    parser = argparse.ArgumentParser()
    for field in ("panel", "frozen", "timing", "quotes", "output"):
        parser.add_argument("--"+field, type=Path, required=True)
    run(parser.parse_args())


if __name__ == "__main__":
    main()
