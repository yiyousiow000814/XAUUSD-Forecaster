"""Causal, append-only Shadow positions and model-version scorecards."""

from __future__ import annotations

import json
import math
import statistics
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable


UTC = timezone.utc
SIMULATION_VERSION = "fixed-30m-one-position-v1"
EQUITY_LOG_RETURN_PER_U5 = 0.01
EVALUATION_DECISIONS_PER_VERSION = 200
ACTION_MODELS = {"CHALLENGER_A", "CHALLENGER_FULL"}
COST_MODEL = "EXECUTABLE_BID_ASK;COMMISSION_0;SLIPPAGE_0"


def _iso(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return value.astimezone(UTC).isoformat(timespec="microseconds")


def append_shadow_intents(
    connection,
    decision_id: str,
    decision_time: datetime,
    recorded_at: datetime,
    predictions: Iterable[dict[str, Any]],
) -> None:
    """Freeze simulated admission before any 30-minute outcome is visible."""
    for prediction in predictions:
        identity = str(prediction["model_identity"])
        version = str(prediction["model_version"])
        recommended = str(prediction.get("recommended_action", "WAIT"))
        status = str(prediction["prediction_status"])
        action = "WAIT"
        planned_exit = None
        if identity not in ACTION_MODELS:
            admission = "NOT_ACTION_MODEL"
        elif status != "READY":
            admission = "PREDICTION_NOT_READY"
        elif recommended == "WAIT":
            admission = "MODEL_WAIT"
        else:
            active = connection.execute(
                """SELECT 1 FROM shadow_trade_intents
                WHERE model_version=? AND admission_status='ADMITTED'
                  AND planned_exit_time>?
                LIMIT 1""",
                (version, _iso(decision_time)),
            ).fetchone()
            if active is not None:
                admission = "OVERLAP_BLOCK"
            else:
                admission = "ADMITTED"
                action = recommended
                # Entry may arrive up to 20 seconds after the decision. Blocking
                # through that conservative fixed horizon prevents overlap.
                planned_exit = decision_time + timedelta(minutes=30, seconds=20)
        connection.execute(
            """INSERT INTO shadow_trade_intents VALUES
            (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                decision_id,
                version,
                identity,
                _iso(decision_time),
                _iso(recorded_at),
                recommended,
                action,
                admission,
                _iso(planned_exit) if planned_exit else None,
                SIMULATION_VERSION,
            ),
        )


def append_shadow_results(connection, outcome: dict[str, Any], u5: float | None) -> None:
    """Settle already-frozen intents from the executable 30-minute outcome."""
    intents = connection.execute(
        "SELECT * FROM shadow_trade_intents WHERE decision_id=? ORDER BY model_version",
        (outcome["decision_id"],),
    ).fetchall()
    for intent in intents:
        action = intent["simulated_action"]
        pnl_log = pnl_u5 = equity_log = mfe_u5 = mae_u5 = None
        if intent["admission_status"] != "ADMITTED":
            result_status = "NOT_TRADED"
        elif outcome["outcome_status"] != "VALID" or not u5:
            result_status = "INVALID"
        else:
            result_status = "VALID"
            if action == "LONG":
                pnl_log = float(outcome["long_return"])
                mfe = float(outcome["long_mfe"])
                mae = float(outcome["long_mae"])
            else:
                pnl_log = float(outcome["short_return"])
                mfe = float(outcome["short_mfe"])
                mae = float(outcome["short_mae"])
            pnl_u5 = pnl_log / float(u5)
            equity_log = pnl_u5 * EQUITY_LOG_RETURN_PER_U5
            mfe_u5 = mfe / float(u5)
            mae_u5 = mae / float(u5)
        connection.execute(
            """INSERT INTO shadow_trade_results VALUES
            (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                outcome["decision_id"],
                intent["model_version"],
                _iso(outcome["appended_at"]),
                result_status,
                action,
                pnl_log,
                pnl_u5,
                equity_log,
                mfe_u5,
                mae_u5,
                COST_MODEL,
                outcome["source_hash"],
                SIMULATION_VERSION,
            ),
        )


def _performance(rows) -> dict[str, Any]:
    valid = [row for row in rows if row["result_status"] == "VALID"]
    pnl = [float(row["pnl_u5"]) for row in valid]
    gross_profit = sum(value for value in pnl if value > 0)
    gross_loss = sum(value for value in pnl if value < 0)
    profit_factor = gross_profit / abs(gross_loss) if gross_loss < 0 else None

    daily_log: dict[str, float] = defaultdict(float)
    for row in rows:
        day = datetime.fromisoformat(row["decision_time"]).astimezone(UTC).date().isoformat()
        daily_log[day] += 0.0
    equity = peak = 1.0
    max_drawdown = 0.0
    for row in valid:
        equity *= math.exp(float(row["equity_log_return"]))
        peak = max(peak, equity)
        max_drawdown = max(max_drawdown, (peak - equity) / peak)
        day = datetime.fromisoformat(row["decision_time"]).astimezone(UTC).date().isoformat()
        daily_log[day] += float(row["equity_log_return"])
    daily_returns = [math.exp(value) - 1.0 for _, value in sorted(daily_log.items())]
    sharpe = None
    if len(daily_returns) >= 5:
        deviation = statistics.stdev(daily_returns)
        if deviation > 0:
            sharpe = statistics.mean(daily_returns) / deviation * math.sqrt(252.0)
    return {
        "trades": len(valid),
        "wins": sum(value > 0 for value in pnl),
        "losses": sum(value < 0 for value in pnl),
        "net_u5": sum(pnl),
        "profit_factor": profit_factor,
        "max_drawdown_pct": max_drawdown * 100.0,
        "sharpe": sharpe,
        "observed_days": len(daily_returns),
        "equity_index": equity * 100.0,
    }


def shadow_league(connection) -> dict[str, Any]:
    """Return same-clock scorecards for every frozen actionable model version."""
    updates = connection.execute(
        """SELECT * FROM model_updates
        WHERE model_identity IN ('CHALLENGER_A', 'CHALLENGER_FULL')
        ORDER BY training_cutoff, model_identity"""
    ).fetchall()
    if not updates:
        return {
            "simulation_version": SIMULATION_VERSION,
            "comparison_window_start": None,
            "evaluation_decisions_per_version": EVALUATION_DECISIONS_PER_VERSION,
            "cost_model": COST_MODEL,
            "equity_risk_per_u5_pct": EQUITY_LOG_RETURN_PER_U5 * 100.0,
            "models": [],
        }
    cutoffs = sorted({row["training_cutoff"] for row in updates})
    cohort_by_cutoff = {cutoff: index + 1 for index, cutoff in enumerate(cutoffs)}
    models = []
    for update in updates:
        rows = connection.execute(
            """SELECT i.decision_time, i.recommended_action, i.admission_status,
                      r.result_status, r.pnl_u5, r.equity_log_return
            FROM shadow_trade_intents i
            LEFT JOIN shadow_trade_results r
              USING(decision_id, model_version)
            WHERE i.model_version=?
            ORDER BY i.decision_time, i.decision_id
            LIMIT ?""",
            (update["model_version"], EVALUATION_DECISIONS_PER_VERSION),
        ).fetchall()
        metrics = _performance(rows)
        parameters = json.loads(update["hyperparameters_json"])
        recommended = sum(
            row["recommended_action"] in ("LONG", "SHORT") for row in rows
        )
        admitted = sum(row["admission_status"] == "ADMITTED" for row in rows)
        invalid = sum(row["result_status"] == "INVALID" for row in rows)
        if metrics["observed_days"] >= 180 and metrics["trades"] >= 200:
            stage = "正式审查"
        elif metrics["observed_days"] >= 60 and metrics["trades"] >= 100:
            stage = "候选审查"
        elif metrics["trades"]:
            stage = "收集中"
        else:
            stage = "等待信号"
        models.append(
            {
                "cohort": cohort_by_cutoff[update["training_cutoff"]],
                "model_identity": update["model_identity"],
                "model_version": update["model_version"],
                "training_cutoff": update["training_cutoff"],
                "training_rows": int(parameters.get("complete_rows", 0)),
                "decisions": len(rows),
                "recommended_signals": recommended,
                "admitted_signals": admitted,
                "invalid_trades": invalid,
                "stage": stage,
                **metrics,
            }
        )
    return {
        "simulation_version": SIMULATION_VERSION,
        "comparison_window_start": None,
        "evaluation_decisions_per_version": EVALUATION_DECISIONS_PER_VERSION,
        "cost_model": COST_MODEL,
        "equity_risk_per_u5_pct": EQUITY_LOG_RETURN_PER_U5 * 100.0,
        "models": models,
    }
