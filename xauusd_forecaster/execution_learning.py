"""Independent Shadow learning for exposure multipliers and early exit checkpoints."""

from __future__ import annotations

import json
import math
import uuid
from types import SimpleNamespace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

from .forward_ledger import canonical_hash
from .market import MarketObservation
from .ridge import RidgeArtifact, train_ridge
from .training import MARKET_FEATURES


UTC = timezone.utc
LOT_IDENTITY = "LOT_RIDGE"
EXIT_IDENTITY = "EXIT_RIDGE"
LOT_FEATURES = (*MARKET_FEATURES, "direction_sign")
EXIT_FEATURES = (
    *MARKET_FEATURES, "direction_sign", "checkpoint_fraction",
    "current_return_u5", "mfe_u5", "mae_u5",
)
LOT_CANDIDATES = (0.5, 1.0, 2.0)
MIN_LOT_ROWS = 96
MIN_EXIT_ROWS = 30
SHADOW_ROWS = 200
RETRAIN_INTERVAL = 50
FEATURE_VERSION = "execution-causal-market-path-v1"
LOT_LABEL_VERSION = "risk-adjusted-exposure-u5-v1"
EXIT_LABEL_VERSION = "checkpoint-continuation-u5-v1"


def _uuid(kind: str, value: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"xauusd:{kind}:{value}"))


def _market_values(features: dict) -> list[float] | None:
    values = [features.get(name) for name in MARKET_FEATURES]
    if any(value is None for value in values):
        return None
    numeric = [float(value) for value in values]
    return numeric if np.isfinite(numeric).all() else None


def _direction_values(label, direction: str) -> tuple[float, float]:
    if direction == "LONG":
        return float(label.long_quote_return), float(label.long_mae)
    return float(label.short_quote_return), float(label.short_mae)


def _best_multiplier(return_u5: float, adverse_u5: float) -> float:
    """Frozen utility: reward return, quadratically penalize adverse path risk."""
    utilities = {
        size: size * return_u5 - 0.5 * (size * abs(adverse_u5)) ** 2
        for size in LOT_CANDIDATES
    }
    return max(LOT_CANDIDATES, key=lambda size: (utilities[size], -size))


def append_execution_examples(ledger, *, decision_id: str, appended_at: datetime,
                              label, source_hash: str) -> int:
    """Append counterfactual LONG and SHORT examples after the 30m outcome matures."""
    if label.outcome_status != "VALID":
        return 0
    market = ledger.connection.execute(
        "SELECT * FROM derived_market_snapshots WHERE source_decision_id=?",
        (decision_id,),
    ).fetchone()
    if market is None or market["u5"] is None or market["data_health"] != "OK":
        return 0
    features = json.loads(market["features_json"])
    base = _market_values(features)
    if base is None:
        return 0
    u5 = float(market["u5"])
    lane = market["evidence_lane"]
    inserted = 0
    with ledger.connection:
        for direction, sign in (("LONG", 1.0), ("SHORT", -1.0)):
            final_return, adverse = _direction_values(label, direction)
            lot_features = [*base, sign]
            target_size = _best_multiplier(final_return / u5, adverse / u5)
            receipt = canonical_hash((source_hash, direction, 0, lot_features,
                                      target_size, LOT_LABEL_VERSION))
            cursor = ledger.connection.execute(
                """INSERT OR IGNORE INTO execution_training_examples_v1
                VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (_uuid("execution-example", f"{decision_id}:{direction}:0"), decision_id,
                 direction, 0, appended_at.isoformat(), lane,
                 json.dumps(lot_features, separators=(",", ":")), target_size,
                 f"{target_size:.1f}X", receipt),
            )
            inserted += cursor.rowcount
            for checkpoint in label.checkpoint_path:
                minutes = int(checkpoint["minutes"])
                current = float(checkpoint[f"{direction.lower()}_return"])
                mfe = float(checkpoint[f"{direction.lower()}_mfe"])
                mae = float(checkpoint[f"{direction.lower()}_mae"])
                exit_features = [
                    *base, sign, minutes / 30.0,
                    current / u5, mfe / u5, mae / u5,
                ]
                continuation = (final_return - current) / u5
                target_action = "HOLD" if continuation > 0.0 else "EXIT"
                checkpoint_hash = canonical_hash((
                    source_hash, direction, minutes, exit_features,
                    continuation, EXIT_LABEL_VERSION,
                ))
                cursor = ledger.connection.execute(
                    """INSERT OR IGNORE INTO execution_training_examples_v1
                    VALUES (?,?,?,?,?,?,?,?,?,?)""",
                    (_uuid("execution-example", f"{decision_id}:{direction}:{minutes}"),
                     decision_id, direction, minutes, checkpoint["received_time"], lane,
                     json.dumps(exit_features, separators=(",", ":")), continuation,
                     target_action, checkpoint_hash),
                )
                inserted += cursor.rowcount
    score_execution_predictions(ledger, decision_id=decision_id, scored_at=appended_at)
    return inserted


def bootstrap_lot_examples(ledger) -> int:
    """Derive lot-only training examples from already frozen valid 30m outcomes."""
    rows = ledger.connection.execute(
        """SELECT o.*,m.evidence_lane FROM derived_outcomes o
        JOIN derived_market_snapshots m USING(source_decision_id)
        WHERE o.outcome_status='VALID' ORDER BY o.decision_time"""
    ).fetchall()
    inserted = 0
    for row in rows:
        label = SimpleNamespace(
            outcome_status="VALID", long_quote_return=row["long_quote_return"],
            short_quote_return=row["short_quote_return"], long_mae=row["long_mae"],
            short_mae=row["short_mae"], checkpoint_path=(),
        )
        inserted += append_execution_examples(
            ledger, decision_id=row["source_decision_id"],
            appended_at=datetime.fromisoformat(row["recomputed_at"]), label=label,
            source_hash=row["source_evidence_hash"],
        )
    return inserted


def bootstrap_checkpoint_examples_from_quotes(ledger, quote_root: str | Path,
                                               cutoff: datetime) -> int:
    """Create training-only checkpoint labels from retained forward quote receipts.

    This never creates or backfills a prediction.  It only reconstructs labels
    that were knowable after each already-matured 30-minute observation ended.
    """
    from .executable_label import build_executable_label_v2
    from .repair_v2 import _read_quotes

    quotes = _read_quotes(Path(quote_root), cutoff)
    rows = ledger.connection.execute(
        """SELECT o.source_decision_id,o.decision_time,o.recomputed_at,
                  o.source_evidence_hash
        FROM derived_outcomes o
        WHERE o.outcome_status='VALID'
          AND NOT EXISTS (
            SELECT 1 FROM execution_training_examples_v1 e
            WHERE e.source_decision_id=o.source_decision_id
              AND e.checkpoint_minutes>0
          )
        ORDER BY o.decision_time"""
    ).fetchall()
    inserted = 0
    for row in rows:
        decision_time = datetime.fromisoformat(row["decision_time"])
        end = decision_time + timedelta(minutes=31, seconds=20)
        visible = [quote for quote in quotes if decision_time < quote.received_time <= end]
        label = build_executable_label_v2(decision_time=decision_time, quotes=visible)
        if label.outcome_status != "VALID" or len(label.checkpoint_path) != 5:
            continue
        inserted += append_execution_examples(
            ledger, decision_id=row["source_decision_id"],
            appended_at=datetime.fromisoformat(row["recomputed_at"]), label=label,
            source_hash=row["source_evidence_hash"],
        )
    return inserted


def _training_rows(ledger, identity: str, cutoff: datetime) -> list:
    clause = "checkpoint_minutes=0" if identity == LOT_IDENTITY else "checkpoint_minutes>0"
    return ledger.connection.execute(
        f"""SELECT * FROM execution_training_examples_v1
        WHERE {clause} AND observed_at<=? ORDER BY observed_at,example_id""",
        (cutoff.isoformat(),),
    ).fetchall()


def train_due_execution(ledger, cutoff: datetime, artifact_root: str | Path,
                        quote_root: str | Path | None = None) -> list[dict]:
    bootstrap_lot_examples(ledger)
    if quote_root is not None:
        exit_count = ledger.connection.execute(
            "SELECT count(*) FROM execution_training_examples_v1 WHERE checkpoint_minutes>0"
        ).fetchone()[0]
        if exit_count < MIN_EXIT_ROWS:
            bootstrap_checkpoint_examples_from_quotes(ledger, quote_root, cutoff)
    statuses = []
    root = Path(artifact_root).resolve()
    for identity, minimum, names, label_version in (
        (LOT_IDENTITY, MIN_LOT_ROWS, LOT_FEATURES, LOT_LABEL_VERSION),
        (EXIT_IDENTITY, MIN_EXIT_ROWS, EXIT_FEATURES, EXIT_LABEL_VERSION),
    ):
        rows = _training_rows(ledger, identity, cutoff)
        if len(rows) < minimum:
            statuses.append({"model_identity": identity, "status": "COLLECTING",
                             "complete_rows": len(rows), "next_threshold": minimum})
            continue
        training_count = len(rows) if len(rows) < SHADOW_ROWS else len(rows) - len(rows) % RETRAIN_INTERVAL
        selected = rows[:training_count]
        latest = ledger.connection.execute(
            """SELECT * FROM execution_model_updates_v1
            WHERE model_identity=? ORDER BY training_rows DESC,created_at DESC LIMIT 1""",
            (identity,),
        ).fetchone()
        if latest is not None and training_count < int(latest["training_rows"]) + RETRAIN_INTERVAL:
            statuses.append({"model_identity": identity, "status": "NOT_DUE",
                             "complete_rows": len(rows),
                             "next_threshold": int(latest["training_rows"]) + RETRAIN_INTERVAL})
            continue
        receipts = [(row["example_id"], row["source_hash"]) for row in selected]
        dataset_hash = canonical_hash(receipts)
        stage = "SHADOW" if training_count >= SHADOW_ROWS else "PREVIEW_ONLY"
        version = (
            f"{identity.lower().replace('_', '-')}-{stage.lower()}-"
            f"{cutoff.astimezone(UTC).strftime('%Y%m%dT%H%M%SZ')}-{dataset_hash[:12]}"
        )
        matrix = np.asarray([json.loads(row["feature_json"]) for row in selected])
        target = np.asarray([float(row["target_value"]) for row in selected])
        artifact = train_ridge(matrix, target, names, 100.0, dataset_hash)
        path = root / version / "model.json"
        if not path.exists():
            artifact.write(path)
        with ledger.connection:
            ledger.connection.execute(
                """INSERT OR IGNORE INTO execution_model_updates_v1 VALUES
                (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (version, identity, stage, datetime.now(UTC).isoformat(), cutoff.isoformat(),
                 training_count, dataset_hash, FEATURE_VERSION, label_version,
                 str(path), artifact.artifact_hash, "CHALLENGER"),
            )
        statuses.append({"model_identity": identity, "status": "TRAINED",
                         "model_version": version, "training_rows": training_count})
    return statuses


def _latest_model(ledger, identity: str, before: datetime):
    return ledger.connection.execute(
        """SELECT * FROM execution_model_updates_v1
        WHERE model_identity=? AND created_at<? ORDER BY created_at DESC LIMIT 1""",
        (identity, before.isoformat()),
    ).fetchone()


def append_lot_predictions(ledger, *, decision_id: str, decision_time: datetime,
                           created_at: datetime, market_snapshot: dict) -> int:
    model = _latest_model(ledger, LOT_IDENTITY, decision_time)
    if model is None or market_snapshot["data_health"] != "OK":
        return 0
    features = json.loads(market_snapshot["features_json"])
    base = _market_values(features)
    if base is None:
        return 0
    artifact = RidgeArtifact.read(model["artifact_path"])
    inserted = 0
    with ledger.connection:
        for direction, sign in (("LONG", 1.0), ("SHORT", -1.0)):
            values = [*base, sign]
            predicted = float(artifact.predict(np.asarray([values]))[0])
            selected = min(LOT_CANDIDATES, key=lambda size: (abs(size - predicted), size))
            feature_hash = canonical_hash((market_snapshot["output_hash"], direction, values))
            cursor = ledger.connection.execute(
                """INSERT OR IGNORE INTO execution_predictions_v1 VALUES
                (?,?,?,?,?,?,?,?,?,?,?)""",
                (decision_id, model["model_version"], LOT_IDENTITY, direction, 0,
                 decision_time.isoformat(), created_at.isoformat(), predicted,
                 f"{selected:.1f}X", "SHADOW_ONLY", feature_hash),
            )
            inserted += cursor.rowcount
    return inserted


def _checkpoint_features(ledger, decision_id: str, direction: str, minutes: int,
                         quotes: list[MarketObservation]) -> tuple[list[float], str, datetime] | None:
    market = ledger.connection.execute(
        "SELECT * FROM derived_market_snapshots WHERE source_decision_id=?",
        (decision_id,),
    ).fetchone()
    if market is None or market["u5"] is None or market["data_health"] != "OK":
        return None
    features = json.loads(market["features_json"])
    base = _market_values(features)
    if base is None:
        return None
    decision_time = datetime.fromisoformat(market["decision_time"])
    entry_candidates = [row for row in quotes if decision_time < row.received_time <= decision_time + timedelta(seconds=20)]
    if not entry_candidates:
        return None
    entry = min(entry_candidates, key=lambda row: (row.received_time, row.event_time))
    target = entry.received_time + timedelta(minutes=minutes)
    checkpoint_candidates = [row for row in quotes if row.received_time >= target]
    if not checkpoint_candidates:
        return None
    checkpoint = min(checkpoint_candidates, key=lambda row: (row.received_time, row.event_time))
    path = [row for row in quotes if entry.received_time <= row.received_time <= checkpoint.received_time]
    if not path:
        return None
    if direction == "LONG":
        returns = [math.log(row.bid / entry.ask) for row in path]
        sign = 1.0
    else:
        returns = [math.log(entry.bid / row.ask) for row in path]
        sign = -1.0
    u5 = float(market["u5"])
    values = [*base, sign, minutes / 30.0, returns[-1] / u5,
              max(returns) / u5, min(returns) / u5]
    return (
        values,
        canonical_hash((market["output_hash"], direction, minutes, values)),
        checkpoint.received_time,
    )


def append_due_exit_predictions(ledger, *, checkpoint_time: datetime,
                                created_at: datetime,
                                quotes: list[MarketObservation]) -> int:
    """Append only checkpoints that became visible during the current poll.

    A five-minute decision is normally received a few seconds after the wall-clock
    boundary.  Polling only on the next exact boundary therefore misses every
    checkpoint.  This scanner uses receipt time, considers the last poll window,
    and refuses to backfill older checkpoints as if they had been predicted live.
    """
    recent_floor = created_at - timedelta(seconds=30)
    candidates = ledger.connection.execute(
        """SELECT source_decision_id FROM derived_market_snapshots
        WHERE decision_time>=? AND decision_time<=? AND data_health='OK'
        ORDER BY decision_time""",
        ((checkpoint_time - timedelta(minutes=31)).isoformat(),
         (checkpoint_time - timedelta(minutes=4)).isoformat()),
    ).fetchall()
    artifacts: dict[str, RidgeArtifact] = {}
    inserted = 0
    with ledger.connection:
        for row in candidates:
            for direction in ("LONG", "SHORT"):
                for minutes in (5, 10, 15, 20, 25):
                    built = _checkpoint_features(
                        ledger, row["source_decision_id"], direction, minutes, quotes
                    )
                    if built is None:
                        continue
                    values, feature_hash, visible_at = built
                    if visible_at < recent_floor or visible_at > created_at:
                        continue
                    model = _latest_model(ledger, EXIT_IDENTITY, visible_at)
                    if model is None:
                        continue
                    artifact = artifacts.get(model["model_version"])
                    if artifact is None:
                        artifact = RidgeArtifact.read(model["artifact_path"])
                        artifacts[model["model_version"]] = artifact
                    predicted = float(artifact.predict(np.asarray([values]))[0])
                    recommendation = "HOLD" if predicted > 0.0 else "EXIT"
                    cursor = ledger.connection.execute(
                        """INSERT OR IGNORE INTO execution_predictions_v1 VALUES
                        (?,?,?,?,?,?,?,?,?,?,?)""",
                        (row["source_decision_id"], model["model_version"], EXIT_IDENTITY,
                         direction, minutes, visible_at.isoformat(), created_at.isoformat(),
                         predicted, recommendation, "SHADOW_ONLY", feature_hash),
                    )
                    inserted += cursor.rowcount
    return inserted


def score_execution_predictions(ledger, *, decision_id: str, scored_at: datetime) -> int:
    predictions = ledger.connection.execute(
        """SELECT p.*,e.target_value,e.target_action
        FROM execution_predictions_v1 p
        JOIN execution_training_examples_v1 e
          ON e.source_decision_id=p.source_decision_id AND e.direction=p.direction
         AND e.checkpoint_minutes=p.checkpoint_minutes
        LEFT JOIN execution_prediction_scores_v1 s
          ON s.source_decision_id=p.source_decision_id AND s.model_version=p.model_version
         AND s.direction=p.direction AND s.checkpoint_minutes=p.checkpoint_minutes
        WHERE p.source_decision_id=? AND s.source_decision_id IS NULL""",
        (decision_id,),
    ).fetchall()
    inserted = 0
    with ledger.connection:
        for row in predictions:
            target = float(row["target_value"])
            predicted = float(row["predicted_value"])
            if row["model_identity"] == LOT_IDENTITY:
                selected = float(row["recommended_action"].removesuffix("X"))
                utility = -abs(selected - target)
            else:
                utility = target if row["recommended_action"] == "HOLD" else 0.0
            score_hash = canonical_hash((decision_id, row["model_version"], row["direction"],
                                         row["checkpoint_minutes"], target, utility, predicted))
            cursor = ledger.connection.execute(
                "INSERT INTO execution_prediction_scores_v1 VALUES (?,?,?,?,?,?,?,?,?)",
                (decision_id, row["model_version"], row["direction"],
                 row["checkpoint_minutes"], scored_at.isoformat(), target, utility,
                 (target - predicted) ** 2, score_hash),
            )
            inserted += cursor.rowcount
    return inserted


def execution_learning_status(ledger) -> dict:
    def lot_evaluation() -> dict:
        rows = ledger.connection.execute(
            """SELECT p.prediction_time,p.model_version,p.direction,
                      p.recommended_action,s.target_value,s.squared_error,
                      CASE p.direction WHEN 'LONG' THEN o.long_quote_return
                           ELSE o.short_quote_return END AS quote_return
               FROM execution_predictions_v1 p
               JOIN execution_prediction_scores_v1 s
                 USING(source_decision_id,model_version,direction,checkpoint_minutes)
               JOIN derived_outcomes o USING(source_decision_id)
               WHERE p.model_identity=? AND o.outcome_status='VALID'
               ORDER BY p.prediction_time,p.direction""",
            (LOT_IDENTITY,),
        ).fetchall()
        selected_total = 0.0
        baseline_total = 0.0
        exact = 0
        points = []
        for row in rows:
            selected = float(str(row["recommended_action"]).removesuffix("X"))
            target = float(row["target_value"])
            quote_return = float(row["quote_return"])
            selected_total += selected * quote_return
            baseline_total += quote_return
            exact += int(math.isclose(selected, target))
            points.append({
                "time": row["prediction_time"],
                "model_version": row["model_version"],
                "selected_cumulative_return": selected_total,
                "baseline_cumulative_return": baseline_total,
            })
        return {
            "score_count": len(rows),
            "exact_choice_rate": exact / len(rows) if rows else None,
            "mean_squared_error": (
                sum(float(row["squared_error"]) for row in rows) / len(rows)
                if rows else None
            ),
            "selected_cumulative_return": selected_total,
            "baseline_cumulative_return": baseline_total,
            "points": points,
            "unit": "QUOTE_RETURN",
        }

    def exit_evaluation() -> dict:
        rows = ledger.connection.execute(
            """SELECT p.prediction_time,p.model_version,p.direction,
                      p.checkpoint_minutes,p.recommended_action,
                      s.target_value,s.selected_utility,s.squared_error,
                      e.target_action
               FROM execution_predictions_v1 p
               JOIN execution_prediction_scores_v1 s
                 USING(source_decision_id,model_version,direction,checkpoint_minutes)
               JOIN execution_training_examples_v1 e
                 USING(source_decision_id,direction,checkpoint_minutes)
               WHERE p.model_identity=?
               ORDER BY p.prediction_time,p.direction,p.checkpoint_minutes""",
            (EXIT_IDENTITY,),
        ).fetchall()
        selected_total = 0.0
        hold_total = 0.0
        correct = 0
        points = []
        for row in rows:
            selected_total += float(row["selected_utility"])
            hold_total += float(row["target_value"])
            correct += int(row["recommended_action"] == row["target_action"])
            points.append({
                "time": row["prediction_time"],
                "model_version": row["model_version"],
                "selected_cumulative_utility_u5": selected_total,
                "always_hold_cumulative_utility_u5": hold_total,
            })
        return {
            "score_count": len(rows),
            "action_accuracy": correct / len(rows) if rows else None,
            "mean_squared_error": (
                sum(float(row["squared_error"]) for row in rows) / len(rows)
                if rows else None
            ),
            "selected_cumulative_utility_u5": selected_total,
            "always_hold_cumulative_utility_u5": hold_total,
            "points": points,
            "unit": "CONTINUATION_U5",
        }

    evaluations = {
        LOT_IDENTITY: lot_evaluation(),
        EXIT_IDENTITY: exit_evaluation(),
    }
    models = []
    for identity, minimum in ((LOT_IDENTITY, MIN_LOT_ROWS), (EXIT_IDENTITY, MIN_EXIT_ROWS)):
        rows = _training_rows(ledger, identity, datetime.max.replace(tzinfo=UTC))
        latest = ledger.connection.execute(
            """SELECT * FROM execution_model_updates_v1 WHERE model_identity=?
            ORDER BY created_at DESC LIMIT 1""", (identity,),
        ).fetchone()
        predictions = ledger.connection.execute(
            "SELECT count(*) FROM execution_predictions_v1 WHERE model_identity=?", (identity,),
        ).fetchone()[0]
        scores = ledger.connection.execute(
            """SELECT count(*) FROM execution_prediction_scores_v1 s
            JOIN execution_predictions_v1 p USING(source_decision_id,model_version,direction,checkpoint_minutes)
            WHERE p.model_identity=?""", (identity,),
        ).fetchone()[0]
        models.append({
            "model_identity": identity,
            "status": "RUNNING" if latest else "COLLECTING",
            "training_rows": int(latest["training_rows"]) if latest else 0,
            "available_examples": len(rows),
            "next_training_threshold": (
                int(latest["training_rows"]) + RETRAIN_INTERVAL if latest else minimum
            ),
            "model_version": latest["model_version"] if latest else None,
            "predictions": int(predictions), "scores": int(scores),
            "evaluation": evaluations[identity],
        })
    return {"models": models, "shadow_only": True,
            "lot_candidates": list(LOT_CANDIDATES),
            "exit_checkpoints_minutes": [5, 10, 15, 20, 25]}
