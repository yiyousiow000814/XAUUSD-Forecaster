"""Fixed-prediction, opt-in execution replay. No fitting or production owner."""
from __future__ import annotations

from bisect import bisect_right
from collections import Counter
from datetime import datetime, timedelta, timezone
import math

from .execution_costs import ROUND_TRIP_COMMISSION_LOG_COST
from .offline_model_repair import input_availability, summary, timestamp, predict, predict_market, action
from .ridge import RidgeArtifact

MODES = ("EVIDENCE_ONLY", "HISTORICAL_COMPLETION_CONDITIONAL",
         "DEPENDENCY_MARKER_ASSUMED", "COMMON_MARKER_ASSUMED")
DELAYS_MS = (0, 1000, 5000)
COSTS = ((1.0, 0.0), (1.5, 0.5), (2.0, 1.0))


def verify_frozen_predictions(strategies, panel, baseline):
    """Re-infer frozen artifacts, never fit/select them or change stored rows."""
    artifacts = {(p["family"], p["fold"]): p["artifact"] for p in baseline["artifacts"]}
    for name, rows in strategies.items():
        for old in rows:
            row = panel[old["source_decision_id"]]
            margin = 0
            if name.startswith("ORIGINAL_"):
                p = row["predictions"][name.removeprefix("ORIGINAL_")]
                value, selected = p["predicted_u5"], p["recorded_action"]
            elif name == "ALWAYS_WAIT":
                value, selected = None, "WAIT"
            else:
                family = name.removeprefix("C_")
                artifact = artifacts[("A_MARKET" if name == "PAST_MEAN" else family, old["fold"])]
                if name == "PAST_MEAN":
                    value = artifact["intercept"]
                elif name.startswith("RIDGE_"):
                    payload = artifact["artifact"] if name == "RIDGE_SELECTED" else artifact
                    payload = {k: v for k, v in payload.items() if k != "schema"}
                    value = predict_market(RidgeArtifact(**payload), row)
                else:
                    value = predict(artifact, row)
                    margin = artifact["selected_margin"] if name.startswith("C_") else 0
                selected = action(value, row, margin)
            if ((value is None) != (old["prediction_u5"] is None)
                    or value is not None and not math.isclose(value, old["prediction_u5"], rel_tol=1e-12, abs_tol=1e-12)
                    or selected != old["action"]):
                raise ValueError("FROZEN_PREDICTION_OR_ACTION_CHANGED")


def dependencies(strategy):
    """Declared inputs, not post-fit coefficient pruning."""
    if strategy.startswith("ORIGINAL_"):
        return (strategy.removeprefix("ORIGINAL_"),)
    name = strategy.removeprefix("C_")
    mapping = {"A_MARKET": ("MARKET_ONLY",), "A_BROAD_FULL": ("BROAD_FULL",),
               "B_COMBINATION": ("MARKET_ONLY", "BROAD_NEWS_RESIDUAL")}
    if name in mapping:
        return mapping[name]
    if strategy in ("PAST_MEAN", "ALWAYS_WAIT") or strategy.startswith("RIDGE_"):
        return ()
    raise ValueError("UNKNOWN_FROZEN_STRATEGY")


def signal_time(row, strategy, mode, timing):
    """Never relabel caller-supplied start markers as observed completion."""
    if mode not in MODES:
        raise ValueError("UNKNOWN_TIMING_MODE")
    names = dependencies(strategy)
    required = [row.get("market_computed_at")]
    for name in names:
        prediction = row.get("predictions", {}).get(name, {})
        if (prediction.get("artifact_status") != "EXACT_HASH_VERIFIED"
                or prediction.get("replay_status") not in ("MATCH", "MATCH_HISTORICAL_RIDGE_CONTRACT")):
            return None, "INPUT_IDENTITY_UNKNOWN"
        required.append(prediction.get("prediction_created_at"))
    if not all(required):
        return None, "INPUT_TIMESTAMP_MISSING"
    if mode == "EVIDENCE_ONLY":
        # Only independently bound consumer visibility qualifies this mode.
        # The retained cohort has no such exact deployed-writer evidence.
        if timing.get("visibility_status") != "EXACT_DEPLOYED_WRITER_VERIFIED":
            return None, "CONSUMER_VISIBILITY_UNKNOWN"
        value = timing.get("consumer_visible_no_later_than")
        return (timestamp(value), "VERIFIED_COMPLETION_BOUND") if value else (None, "CONSUMER_VISIBILITY_UNKNOWN")
    if mode == "HISTORICAL_COMPLETION_CONDITIONAL":
        value = timing.get("finished_at")
        if not value:
            return None, "COLLECTOR_COMPLETION_MISSING"
        result = timestamp(value)
        if result < max(map(timestamp, required)):
            return None, "COMPLETION_PRECEDES_INPUT_MARKER"
        # Offline fitted policies did not historically execute. Add their
        # frozen assumed 100ms compute/publication time after the batch bound.
        if not strategy.startswith("ORIGINAL_"):
            result += timedelta(milliseconds=100)
        return result, "HISTORICAL_WRITER_CONDITIONAL_BOUND"
    if mode == "COMMON_MARKER_ASSUMED":
        marker = input_availability(row)
        if marker is None:
            return None, "COMMON_MARKER_MISSING"
    else:
        marker = max(required, key=timestamp)
    return timestamp(marker) + timedelta(milliseconds=100), "ASSUMED_100MS_AFTER_START_MARKER"


def quote_key(value):
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds")


def quote_quality(quote):
    return (math.isfinite(quote.bid) and math.isfinite(quote.ask)
            and 0 < quote.bid <= quote.ask
            and abs((quote.received_time-quote.event_time).total_seconds()) <= 20)


def first_after(quotes, receipt_times, order):
    """First quality-valid, unambiguous receipt strictly after the order."""
    index = bisect_right(receipt_times, order)
    expiry = order + timedelta(seconds=20)
    rejected = 0
    while index < len(quotes) and quotes[index].received_time <= expiry:
        end = bisect_right(receipt_times, quotes[index].received_time)
        group = quotes[index:end]
        identities = {(q.event_time, q.bid, q.ask) for q in group}
        if len(identities) != 1:
            # Cannot pick a preferred price from an unordered conflicting group.
            return {"quote": None, "reason": "AMBIGUOUS_QUOTE_ORDER", "rejected": rejected,
                    "ambiguous_received_time": quotes[index].received_time.isoformat()}
        q = group[0]
        if quote_quality(q):
            return {"quote": {"event_time": q.event_time.isoformat(),
                              "received_time": q.received_time.isoformat(), "bid": q.bid, "ask": q.ask},
                    "reason": "REAL_QUOTE", "rejected": rejected}
        rejected += len(group)
        index = end
    return {"quote": None, "reason": "NO_QUALITY_QUOTE_WITHIN_20S", "rejected": rejected}


def replay(frozen, panel, timing, fills, endpoints, strategy, mode, delay_ms):
    """One owner for pending and filled exposure, ordered by order-time events."""
    records = []
    for old in frozen:
        row = panel[old["source_decision_id"]]
        signal, authority = signal_time(row, strategy, mode, timing.get(old["source_decision_id"], {}))
        order = signal + timedelta(milliseconds=delay_ms) if signal else None
        old_entry = timestamp(row["entry_time"]) if row.get("entry_time") else None
        target = old_entry + timedelta(minutes=30) if old_entry else None
        retained = {k: old[k] for k in ("source_decision_id", "decision_time", "generation", "action",
                                       "fold", "prediction_u5", "strategy_version")}
        record = {**retained, "old_state": old["state"], "state": None, "final_reason": None,
                  "signal_time": signal.isoformat() if signal else None,
                  "order_time": order.isoformat() if order else None, "timing_authority": authority,
                  "dependencies": list(dependencies(strategy)), "old_entry_time": row.get("entry_time"),
                  "original_target_exit": target.isoformat() if target else None,
                  "signal_later_than_old_entry": signal > old_entry if signal and old_entry else None,
                  "entry_time": None, "exit_time": None, "net_log_bps": None, "bidask_log_bps": None,
                  "commission_log_bps": 0.0, "slippage_log_bps": 0.0,
                  "entry_quote": None, "exit_quote": None, "not_evaluable": False,
                  "session_authority": row.get("session_state", "UNKNOWN"),
                  "news_pit": "UNKNOWN" if any("NEWS" in n or "FULL" in n for n in dependencies(strategy)) else "NOT_REQUIRED"}
        records.append(record)
    records.sort(key=lambda r: (timestamp(r["order_time"]) if r["order_time"] else datetime.max.replace(tzinfo=timezone.utc),
                                r["source_decision_id"]))
    owned_until = None
    owner_reason = None
    for record in records:
        def reject(state, reason, unknown=True):
            record.update(state=state, final_reason=reason, not_evaluable=unknown)
        if record["action"] == "WAIT":
            reject("WAIT", "WAIT", False)
            continue
        if record["action"] not in ("LONG", "SHORT") or record["order_time"] is None:
            reject("INPUT_AVAILABILITY_UNKNOWN", record["timing_authority"])
            continue
        if record["session_authority"] == "CLOSED":
            reject("SESSION_CLOSED", "AUTHORITATIVE_SESSION_CLOSED", False)
            continue
        order = timestamp(record["order_time"])
        if owned_until is not None and order <= owned_until:
            reject("OVERLAP_BLOCK", owner_reason, False)
            continue
        if record["original_target_exit"] is None:
            reject("ENTRY_UNVERIFIED", "ORIGINAL_TARGET_UNKNOWN")
            continue
        target = timestamp(record["original_target_exit"])
        if order >= target:
            reject("SIGNAL_EXPIRED", "SIGNAL_AT_OR_AFTER_ORIGINAL_TARGET")
            continue
        selection = fills.get(quote_key(order), {"quote": None, "reason": "QUOTE_SOURCE_MISSING"})
        entry = selection["quote"]
        record["entry_selection"] = selection["reason"]
        record["quality_rejected"] = selection.get("rejected", 0)
        owned_until = min(order + timedelta(seconds=20), target)
        owner_reason = "PENDING_ORDER_OVERLAP"
        if entry is None:
            reject("NO_POST_SIGNAL_QUOTE", selection["reason"])
            continue
        entry_time = timestamp(entry["received_time"])
        if not (order < entry_time <= order + timedelta(seconds=20)):
            raise ValueError("NONCAUSAL_FILL")
        if entry_time >= target:
            reject("SIGNAL_EXPIRED", "FIRST_FILL_AT_OR_AFTER_ORIGINAL_TARGET")
            continue
        record.update(entry_time=entry["received_time"], entry_quote=entry)
        endpoint = endpoints.get(record["source_decision_id"], {})
        exit_quote = endpoint.get("exit_quote")
        # Missing/contradictory target receipt cannot release an actual fill.
        if exit_quote is None or not endpoint.get("label_reconciled"):
            owned_until = datetime.max.replace(tzinfo=timezone.utc)
            owner_reason = "UNRESOLVED_PRIOR_EXPOSURE"
            reject("UNRESOLVED_EXPOSURE", endpoint.get("reason", "EXIT_QUOTE_UNVERIFIED"))
            continue
        owned_until = timestamp(exit_quote["received_time"])
        if owned_until < target:
            raise ValueError("EXIT_BEFORE_ORIGINAL_TARGET")
        owner_reason = "POSITION_OVERLAP"
        value = (math.log(exit_quote["bid"]/entry["ask"]) if record["action"] == "LONG"
                 else math.log(entry["bid"]/exit_quote["ask"])) * 10000
        commission = ROUND_TRIP_COMMISSION_LOG_COST * 10000
        row = panel[record["source_decision_id"]]
        old_return = row["long_log_return" if record["action"] == "LONG" else "short_log_return"]
        record.update(state="CLOSED", final_reason="EXECUTED", exit_time=exit_quote["received_time"],
                      exit_quote=exit_quote, bidask_log_bps=value, commission_log_bps=commission,
                      net_log_bps=value-commission, old_same_opportunity_bidask_log_bps=old_return*10000,
                      paired_entry_change_log_bps=value-old_return*10000)
    if len({r["source_decision_id"] for r in records}) != len(frozen):
        raise ValueError("OPPORTUNITY_IDENTITY_DUPLICATED")
    return records


def metrics(records):
    result = summary(records)
    result["waterfall"] = dict(Counter(r["final_reason"] for r in records))
    result["late_old_entry_intermediate"] = sum(r["signal_later_than_old_entry"] is True for r in records)
    result["not_evaluable"] = sum(r["not_evaluable"] for r in records)
    result["fills_including_unresolved"] = sum(r["entry_quote"] is not None for r in records)
    result["paired_entry_change_log_bps"] = sum(r.get("paired_entry_change_log_bps", 0) for r in records)
    result["cost_stress"] = [{"commission_multiplier": m, "assumed_slippage_log_bps": s,
        "net_log_bps": sum(r["bidask_log_bps"]-r["commission_log_bps"]*m-s for r in records if r["state"] == "CLOSED")}
        for m, s in COSTS]
    result["folds"] = {str(f): sum(r["net_log_bps"] for r in records if r["state"] == "CLOSED" and r["fold"] == f)
                       for f in sorted({r["fold"] for r in records})}
    result["failure_dimensions"] = {"timing_evidence": "UNKNOWN" if any(r["timing_authority"] != "VERIFIED_COMPLETION_BOUND" for r in records) else "VERIFIED",
                                    "independent_sample_support": "INSUFFICIENT_FOUR_DEPENDENT_BLOCKS",
                                    "net_result": "NO_CLOSED_TRADES" if not result["trades"] else ("NET_LOSS" if result["net_log_bps"] <= 0 else "POSITIVE_RETROSPECTIVE_ONLY")}
    return result
