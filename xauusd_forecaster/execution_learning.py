"""Causal Shadow learning for sizing and exits on one frozen Live direction."""

from __future__ import annotations

import gzip
import json
import math
import uuid
from bisect import bisect_right
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

from .forward_ledger import canonical_hash
from .execution_costs import net_shadow_log_return
from .market import MarketObservation, parse_quote_line
from .ridge import RidgeArtifact, train_ridge
from .training import MARKET_FEATURES


UTC = timezone.utc
LOT_IDENTITY = "LOT_RIDGE"
EXIT_IDENTITY = "EXIT_RIDGE"
SOURCE_MODEL_IDENTITY = "BROAD_FULL"
LOT_FEATURES = (*MARKET_FEATURES, "direction_sign")
EXIT_FEATURES = (
    *MARKET_FEATURES, "direction_sign", "checkpoint_fraction",
    "current_return_u5", "mfe_u5", "mae_u5",
)
LOT_CANDIDATES = (0.5, 1.0, 2.0)
EXIT_CHECKPOINTS = (5, 10, 15, 20, 25)
MIN_TRAINING_DECISIONS = 48
SHADOW_DECISIONS = 200
RETRAIN_INTERVAL = 50
FEATURE_VERSION = "execution-follows-broad-full-v2"
LOT_LABEL_VERSION = "candidate-utility-u5-v2"
EXIT_LABEL_VERSION = "sequential-continuation-u5-v2"
EXECUTION_QUOTE_WINDOW = timedelta(minutes=31, seconds=20)


def _uuid(kind: str, value: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"xauusd:{kind}:{value}"))


def _market_values(features: dict) -> list[float] | None:
    values = [features.get(name) for name in MARKET_FEATURES]
    if any(value is None for value in values):
        return None
    numeric = [float(value) for value in values]
    return numeric if np.isfinite(numeric).all() else None


def _source_prediction(ledger, decision_id: str):
    return ledger.connection.execute(
        """SELECT * FROM predictions_v2
        WHERE source_decision_id=? AND model_identity=?
        ORDER BY created_at DESC LIMIT 1""",
        (decision_id, SOURCE_MODEL_IDENTITY),
    ).fetchone()


def _direction_return(label, direction: str) -> tuple[float, float]:
    if direction == "LONG":
        return float(label.long_quote_return), float(label.long_mae)
    return float(label.short_quote_return), float(label.short_mae)


def _checkpoint_payload(label, direction: str, u5: float) -> list[dict]:
    rows = []
    for checkpoint in label.checkpoint_path:
        minutes = int(checkpoint["minutes"])
        key = direction.lower()
        rows.append({
            "minutes": minutes,
            "received_time": (
                checkpoint["received_time"].isoformat()
                if isinstance(checkpoint["received_time"], datetime)
                else checkpoint["received_time"]
            ),
            "current_quote_return": float(checkpoint[f"{key}_return"]),
            "current_return_u5": float(checkpoint[f"{key}_return"]) / u5,
            "mfe_u5": float(checkpoint[f"{key}_mfe"]) / u5,
            "mae_u5": float(checkpoint[f"{key}_mae"]) / u5,
        })
    return rows


def append_execution_examples(ledger, *, decision_id: str, appended_at: datetime,
                              label, source_hash: str) -> int:
    """Append one position example following the frozen BROAD_FULL action."""
    if label.outcome_status != "VALID":
        return 0
    source = _source_prediction(ledger, decision_id)
    if source is None or source["recommended_action"] not in {"LONG", "SHORT"}:
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
    direction = source["recommended_action"]
    sign = 1.0 if direction == "LONG" else -1.0
    final_return, adverse = _direction_return(label, direction)
    u5 = float(market["u5"])
    checkpoints = _checkpoint_payload(label, direction, u5)
    receipt = canonical_hash((
        source_hash, source["model_version"], direction, base, final_return,
        adverse / u5, checkpoints, FEATURE_VERSION,
    ))
    with ledger.connection:
        cursor = ledger.connection.execute(
            """INSERT OR IGNORE INTO execution_training_examples_v2 VALUES
            (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (decision_id, SOURCE_MODEL_IDENTITY, source["model_version"], direction,
             appended_at.isoformat(), market["evidence_lane"],
             json.dumps([*base, sign], separators=(",", ":")), u5,
             final_return, adverse / u5,
             json.dumps(checkpoints, sort_keys=True, separators=(",", ":")), receipt),
        )
    score_execution_predictions(ledger, decision_id=decision_id, scored_at=appended_at)
    return cursor.rowcount


def _quote_days(start: datetime, end: datetime) -> tuple[str, ...]:
    current = start.astimezone(UTC).date()
    final = end.astimezone(UTC).date()
    days = []
    while current <= final:
        days.append(current.strftime("%Y%m%d"))
        current += timedelta(days=1)
    return tuple(days)


def _read_execution_quote_windows(
    quote_root: Path, decision_times: list[datetime], cutoff: datetime,
) -> dict[datetime, list[MarketObservation]]:
    """Read only UTC-day quote partitions needed by execution label windows."""
    required_days = {
        day
        for decision_time in decision_times
        for day in _quote_days(decision_time, decision_time + EXECUTION_QUOTE_WINDOW)
    }
    partitions: dict[str, tuple[list[datetime], list[MarketObservation]]] = {}
    for day in sorted(required_days):
        rows = []
        sources = [
            source
            for source in (
                quote_root / f"xauusd-quotes-{day}.jsonl",
                quote_root / f"xauusd-quotes-{day}.jsonl.gz",
            )
            if source.exists()
        ]
        for source in sources:
            opener = gzip.open if source.suffix == ".gz" else open
            with opener(source, "rt", encoding="utf-8") as handle:
                for line in handle:
                    quote = parse_quote_line(line, source)
                    if quote.received_time <= cutoff:
                        rows.append(quote)
        rows.sort(key=lambda row: (row.received_time, row.event_time))
        partitions[day] = ([row.received_time for row in rows], rows)

    windows = {}
    for decision_time in decision_times:
        end = decision_time + EXECUTION_QUOTE_WINDOW
        visible = []
        for day in _quote_days(decision_time, end):
            received_times, rows = partitions[day]
            left = bisect_right(received_times, decision_time)
            right = bisect_right(received_times, end)
            visible.extend(rows[left:right])
        windows[decision_time] = sorted(
            visible, key=lambda row: (row.received_time, row.event_time)
        )
    return windows


def bootstrap_execution_examples(ledger, quote_root: str | Path, cutoff: datetime) -> int:
    """Build training material only from frozen predictions and retained quotes."""
    from .executable_label import build_executable_label_v2

    missing = ledger.connection.execute(
        """SELECT o.source_decision_id,o.decision_time,o.recomputed_at,o.source_evidence_hash
        FROM derived_outcomes o
        JOIN predictions_v2 p ON p.source_decision_id=o.source_decision_id
          AND p.model_identity=? AND p.recommended_action IN ('LONG','SHORT')
        LEFT JOIN execution_training_examples_v2 e
          ON e.source_decision_id=o.source_decision_id
        WHERE o.outcome_status='VALID' AND o.decision_time<=?
          AND e.source_decision_id IS NULL
        ORDER BY o.decision_time""",
        (SOURCE_MODEL_IDENTITY, cutoff.isoformat()),
    ).fetchall()
    if not missing:
        return 0
    decision_times = [datetime.fromisoformat(row["decision_time"]) for row in missing]
    quote_windows = _read_execution_quote_windows(
        Path(quote_root), decision_times, cutoff
    )
    inserted = 0
    for row, decision_time in zip(missing, decision_times, strict=True):
        label = build_executable_label_v2(
            decision_time=decision_time, quotes=quote_windows[decision_time]
        )
        inserted += append_execution_examples(
            ledger, decision_id=row["source_decision_id"],
            appended_at=datetime.fromisoformat(row["recomputed_at"]), label=label,
            source_hash=row["source_evidence_hash"],
        )
    return inserted


def _training_rows(ledger, cutoff: datetime) -> list:
    return ledger.connection.execute(
        """SELECT * FROM execution_training_examples_v2
        WHERE observed_at<=? ORDER BY observed_at,source_decision_id""",
        (cutoff.isoformat(),),
    ).fetchall()


def _execution_due_statuses(ledger, cutoff: datetime) -> list[dict] | None:
    """Return both NOT_DUE states without reading or parsing training rows."""
    counts = ledger.connection.execute(
        """SELECT count(*) AS lot_count,
        sum(CASE WHEN json_valid(checkpoint_path_json)
                  AND json_array_length(checkpoint_path_json)=5 THEN 1 ELSE 0 END)
            AS exit_count
        FROM execution_training_examples_v2 WHERE observed_at<=?""",
        (cutoff.isoformat(),),
    ).fetchone()
    statuses = []
    for identity, count in (
        (LOT_IDENTITY, int(counts["lot_count"] or 0)),
        (EXIT_IDENTITY, int(counts["exit_count"] or 0)),
    ):
        latest = ledger.connection.execute(
            """SELECT training_decisions FROM execution_model_updates_v2
            WHERE model_identity=? ORDER BY training_decisions DESC,created_at DESC LIMIT 1""",
            (identity,),
        ).fetchone()
        if latest is None:
            if count >= MIN_TRAINING_DECISIONS:
                return None
            statuses.append({"model_identity": identity, "status": "COLLECTING",
                             "complete_rows": count,
                             "next_threshold": MIN_TRAINING_DECISIONS})
            continue
        if count >= int(latest["training_decisions"]) + RETRAIN_INTERVAL:
            return None
        statuses.append({"model_identity": identity, "status": "NOT_DUE",
                         "complete_rows": count,
                         "next_threshold": int(latest["training_decisions"]) + RETRAIN_INTERVAL})
    return statuses


def _latest_model(ledger, identity: str, before: datetime):
    return ledger.connection.execute(
        """SELECT * FROM execution_model_updates_v2
        WHERE model_identity=? AND created_at<? ORDER BY created_at DESC LIMIT 1""",
        (identity, before.isoformat()),
    ).fetchone()


def train_due_execution(ledger, cutoff: datetime, artifact_root: str | Path,
                        quote_root: str | Path | None = None) -> list[dict]:
    if quote_root is not None:
        bootstrap_execution_examples(ledger, quote_root, cutoff)
    not_due = _execution_due_statuses(ledger, cutoff)
    if not_due is not None:
        return not_due
    rows = _training_rows(ledger, cutoff)
    statuses = []
    root = Path(artifact_root).resolve().with_name("execution-models-v2")
    for identity, label_version in (
        (LOT_IDENTITY, LOT_LABEL_VERSION), (EXIT_IDENTITY, EXIT_LABEL_VERSION),
    ):
        usable = rows if identity == LOT_IDENTITY else [
            row for row in rows if len(json.loads(row["checkpoint_path_json"])) == 5
        ]
        decision_count = len(usable)
        if decision_count < MIN_TRAINING_DECISIONS:
            statuses.append({"model_identity": identity, "status": "COLLECTING",
                             "complete_rows": decision_count,
                             "next_threshold": MIN_TRAINING_DECISIONS})
            continue
        training_count = (decision_count if decision_count < SHADOW_DECISIONS else
                          decision_count - decision_count % RETRAIN_INTERVAL)
        selected = usable[:training_count]
        latest = ledger.connection.execute(
            """SELECT * FROM execution_model_updates_v2 WHERE model_identity=?
            ORDER BY training_decisions DESC,created_at DESC LIMIT 1""", (identity,),
        ).fetchone()
        if latest is not None and training_count < int(latest["training_decisions"]) + RETRAIN_INTERVAL:
            statuses.append({"model_identity": identity, "status": "NOT_DUE",
                             "complete_rows": decision_count,
                             "next_threshold": int(latest["training_decisions"]) + RETRAIN_INTERVAL})
            continue
        dataset_hash = canonical_hash([(row["source_decision_id"], row["source_hash"]) for row in selected])
        stage = "SHADOW" if training_count >= SHADOW_DECISIONS else "PREVIEW_ONLY"
        version = (f"{identity.lower().replace('_', '-')}-{stage.lower()}-"
                   f"{cutoff.astimezone(UTC).strftime('%Y%m%dT%H%M%SZ')}-{dataset_hash[:12]}")
        artifacts: dict[str, str] = {}
        hashes: dict[str, str] = {}
        observations = 0
        if identity == LOT_IDENTITY:
            matrix = np.asarray([json.loads(row["feature_json"]) for row in selected])
            for size in LOT_CANDIDATES:
                target = np.asarray([
                    size * (net_shadow_log_return(row["final_quote_return"]) / float(row["u5"]))
                    - 0.5 * (size * abs(float(row["adverse_u5"]))) ** 2
                    for row in selected
                ])
                artifact = train_ridge(matrix, target, LOT_FEATURES, 100.0,
                                       canonical_hash((dataset_hash, size)))
                path = root / version / f"lot-{size:.1f}" / "model.json"
                if not path.exists():
                    artifact.write(path)
                artifacts[f"{size:.1f}X"] = str(path)
                hashes[f"{size:.1f}X"] = artifact.artifact_hash
            observations = training_count
        else:
            matrix_rows, targets = [], []
            for row in selected:
                base = json.loads(row["feature_json"])
                final_u5 = net_shadow_log_return(row["final_quote_return"]) / float(row["u5"])
                for checkpoint in json.loads(row["checkpoint_path_json"]):
                    cost_u5 = (
                        float(checkpoint["current_return_u5"])
                        - net_shadow_log_return(checkpoint["current_quote_return"]) / float(row["u5"])
                    )
                    current_u5 = float(checkpoint["current_return_u5"]) - cost_u5
                    matrix_rows.append([
                        *base, checkpoint["minutes"] / 30.0,
                        current_u5, checkpoint["mfe_u5"] - cost_u5,
                        checkpoint["mae_u5"] - cost_u5,
                    ])
                    targets.append(final_u5 - current_u5)
            artifact = train_ridge(np.asarray(matrix_rows), np.asarray(targets),
                                   EXIT_FEATURES, 100.0, dataset_hash)
            path = root / version / "exit" / "model.json"
            if not path.exists():
                artifact.write(path)
            artifacts["CONTINUATION"] = str(path)
            hashes["CONTINUATION"] = artifact.artifact_hash
            observations = len(matrix_rows)
        artifact_hash = canonical_hash(hashes)
        with ledger.connection:
            ledger.connection.execute(
                """INSERT OR IGNORE INTO execution_model_updates_v2 VALUES
                (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (version, identity, stage, cutoff.isoformat(), cutoff.isoformat(),
                 training_count, observations, dataset_hash, FEATURE_VERSION,
                 label_version, json.dumps(artifacts, sort_keys=True), artifact_hash,
                 SOURCE_MODEL_IDENTITY, "CHALLENGER"),
            )
        statuses.append({"model_identity": identity, "status": "TRAINED",
                         "model_version": version,
                         "training_decisions": training_count,
                         "training_observations": observations})
    return statuses


def prepare_lot_prediction(ledger, *, decision_id: str, decision_time: datetime,
                           created_at: datetime, market_snapshot: dict, source) -> tuple | None:
    if source is None or source["recommended_action"] not in {"LONG", "SHORT"}:
        return None
    model = _latest_model(ledger, LOT_IDENTITY, decision_time)
    if model is None or market_snapshot["data_health"] != "OK":
        return None
    base = _market_values(json.loads(market_snapshot["features_json"]))
    if base is None:
        return None
    direction = source["recommended_action"]
    values = [*base, 1.0 if direction == "LONG" else -1.0]
    paths = json.loads(model["artifact_paths_json"])
    expected = {
        action: float(RidgeArtifact.read(path).predict(np.asarray([values]))[0])
        for action, path in paths.items()
    }
    selected = max(expected, key=lambda action: (expected[action], -float(action[:-1])))
    feature_hash = canonical_hash((market_snapshot["output_hash"], source["model_version"],
                                   direction, values, expected))
    return (decision_id, model["model_version"], LOT_IDENTITY,
            SOURCE_MODEL_IDENTITY, source["model_version"], direction, 0,
            decision_time.isoformat(), created_at.isoformat(), expected[selected],
            selected, None, "SHADOW_ONLY", feature_hash)


def append_lot_predictions(ledger, *, decision_id: str, decision_time: datetime,
                           created_at: datetime, market_snapshot: dict) -> int:
    row = prepare_lot_prediction(
        ledger, decision_id=decision_id, decision_time=decision_time,
        created_at=created_at, market_snapshot=market_snapshot,
        source=_source_prediction(ledger, decision_id),
    )
    if row is None:
        return 0
    with ledger.connection:
        return ledger.connection.execute(
            "INSERT OR IGNORE INTO execution_predictions_v2 VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            row,
        ).rowcount


def _checkpoint_features(ledger, decision_id: str, direction: str, minutes: int,
                         quotes: list[MarketObservation]):
    market = ledger.connection.execute(
        "SELECT * FROM derived_market_snapshots WHERE source_decision_id=?", (decision_id,),
    ).fetchone()
    if market is None or market["u5"] is None or market["data_health"] != "OK":
        return None
    base = _market_values(json.loads(market["features_json"]))
    if base is None:
        return None
    decision_time = datetime.fromisoformat(market["decision_time"])
    entries = [row for row in quotes if decision_time < row.received_time <= decision_time + timedelta(seconds=20)]
    if not entries:
        return None
    entry = min(entries, key=lambda row: (row.received_time, row.event_time))
    target = entry.received_time + timedelta(minutes=minutes)
    checkpoints = [row for row in quotes if row.received_time >= target]
    if not checkpoints:
        return None
    checkpoint = min(checkpoints, key=lambda row: (row.received_time, row.event_time))
    path = [row for row in quotes if entry.received_time <= row.received_time <= checkpoint.received_time]
    if direction == "LONG":
        returns = [math.log(row.bid / entry.ask) for row in path]
        sign = 1.0
    else:
        returns = [math.log(entry.bid / row.ask) for row in path]
        sign = -1.0
    u5 = float(market["u5"])
    values = [
        *base, sign, minutes / 30.0, net_shadow_log_return(returns[-1]) / u5,
        net_shadow_log_return(max(returns)) / u5,
        net_shadow_log_return(min(returns)) / u5,
    ]
    return values, returns[-1], checkpoint.received_time, canonical_hash((
        market["output_hash"], direction, minutes, values,
    ))


def append_due_exit_predictions(ledger, *, checkpoint_time: datetime,
                                created_at: datetime,
                                quotes: list[MarketObservation]) -> int:
    """Advance each real Shadow position through at most one causal checkpoint."""
    recent_floor = created_at - timedelta(seconds=30)
    positions = ledger.connection.execute(
        """SELECT l.source_decision_id,l.direction,l.source_model_version
        FROM execution_predictions_v2 l
        WHERE l.model_identity=? AND l.prediction_time>=?
        ORDER BY l.prediction_time""",
        (LOT_IDENTITY, (checkpoint_time - timedelta(minutes=31)).isoformat()),
    ).fetchall()
    inserted = 0
    for position in positions:
        prior = ledger.connection.execute(
            """SELECT checkpoint_minutes,recommended_action FROM execution_predictions_v2
            WHERE source_decision_id=? AND model_identity=? ORDER BY checkpoint_minutes""",
            (position["source_decision_id"], EXIT_IDENTITY),
        ).fetchall()
        if any(row["recommended_action"] == "EXIT" for row in prior):
            continue
        completed = {int(row["checkpoint_minutes"]) for row in prior}
        minutes = next((value for value in EXIT_CHECKPOINTS if value not in completed), None)
        if minutes is None:
            continue
        built = _checkpoint_features(ledger, position["source_decision_id"],
                                     position["direction"], minutes, quotes)
        if built is None:
            continue
        values, current_return, visible_at, feature_hash = built
        if visible_at < recent_floor or visible_at > created_at:
            continue
        model = _latest_model(ledger, EXIT_IDENTITY, visible_at)
        if model is None:
            continue
        path = json.loads(model["artifact_paths_json"])["CONTINUATION"]
        predicted = float(RidgeArtifact.read(path).predict(np.asarray([values]))[0])
        action = "HOLD" if predicted > 0.0 else "EXIT"
        with ledger.connection:
            cursor = ledger.connection.execute(
                """INSERT OR IGNORE INTO execution_predictions_v2 VALUES
                (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (position["source_decision_id"], model["model_version"], EXIT_IDENTITY,
                 SOURCE_MODEL_IDENTITY, position["source_model_version"],
                 position["direction"], minutes, visible_at.isoformat(),
                 created_at.isoformat(), predicted, action, current_return,
                 "SHADOW_ONLY", feature_hash),
            )
        inserted += cursor.rowcount
    return inserted


def score_execution_predictions(ledger, *, decision_id: str, scored_at: datetime) -> int:
    example = ledger.connection.execute(
        "SELECT * FROM execution_training_examples_v2 WHERE source_decision_id=?",
        (decision_id,),
    ).fetchone()
    if example is None:
        return 0
    inserted = 0
    lot = ledger.connection.execute(
        """SELECT * FROM execution_predictions_v2 WHERE source_decision_id=?
        AND model_identity=?""", (decision_id, LOT_IDENTITY),
    ).fetchone()
    if lot is not None:
        size = float(lot["recommended_action"].removesuffix("X"))
        baseline = net_shadow_log_return(example["final_quote_return"])
        selected = size * baseline
        inserted += _append_score(ledger, lot, scored_at, lot["recommended_action"], 30,
                                  selected, baseline)
    exit_rows = ledger.connection.execute(
        """SELECT * FROM execution_predictions_v2 WHERE source_decision_id=?
        AND model_identity=? ORDER BY checkpoint_minutes""",
        (decision_id, EXIT_IDENTITY),
    ).fetchall()
    if exit_rows:
        terminal = next((row for row in exit_rows if row["recommended_action"] == "EXIT"), None)
        completed_checkpoints = {
            int(row["checkpoint_minutes"]) for row in exit_rows
        }
        # A non-terminal path represents HOLD_TO_30M only after every causal
        # checkpoint was actually observed.  A quote gap must not turn a
        # partial path into a completed OOS position.
        if terminal is None and completed_checkpoints != set(EXIT_CHECKPOINTS):
            return inserted
        if terminal is not None:
            selected = net_shadow_log_return(terminal["current_quote_return"])
            minutes = int(terminal["checkpoint_minutes"])
            action = f"EXIT_{minutes}M"
            scoring_row = terminal
        else:
            selected = net_shadow_log_return(example["final_quote_return"])
            minutes = 30
            action = "HOLD_TO_30M"
            scoring_row = exit_rows[-1]
        inserted += _append_score(
            ledger, scoring_row, scored_at, action, minutes, selected,
            net_shadow_log_return(example["final_quote_return"]),
        )
    return inserted


def _append_score(ledger, prediction, scored_at: datetime, action: str,
                  exit_minutes: int, selected: float, baseline: float) -> int:
    score_hash = canonical_hash((prediction["source_decision_id"], prediction["model_version"],
                                 action, exit_minutes, selected, baseline))
    with ledger.connection:
        cursor = ledger.connection.execute(
            """INSERT OR IGNORE INTO execution_position_scores_v2 VALUES
            (?,?,?,?,?,?,?,?,?,?,?)""",
            (prediction["source_decision_id"], prediction["model_version"],
             prediction["model_identity"], scored_at.isoformat(), prediction["direction"],
             action, exit_minutes, selected, baseline, selected - baseline, score_hash),
        )
    return cursor.rowcount


def execution_learning_status(ledger) -> dict:
    training = _training_rows(ledger, datetime.max.replace(tzinfo=UTC))
    models = []
    for identity in (LOT_IDENTITY, EXIT_IDENTITY):
        latest = ledger.connection.execute(
            """SELECT * FROM execution_model_updates_v2 WHERE model_identity=?
            ORDER BY created_at DESC LIMIT 1""", (identity,),
        ).fetchone()
        predictions = ledger.connection.execute(
            "SELECT count(*) FROM execution_predictions_v2 WHERE model_identity=?", (identity,),
        ).fetchone()[0]
        action_counts = {
            str(row["recommended_action"]): int(row["count"])
            for row in ledger.connection.execute(
                """SELECT recommended_action,count(*) AS count
                FROM execution_predictions_v2 WHERE model_identity=?
                GROUP BY recommended_action""",
                (identity,),
            ).fetchall()
        }
        scores = ledger.connection.execute(
            "SELECT * FROM execution_position_scores_v2 WHERE model_identity=? ORDER BY scored_at",
            (identity,),
        ).fetchall()
        prediction_times = {
            (row["source_decision_id"], row["model_version"]): row["prediction_time"]
            for row in ledger.connection.execute(
                """SELECT source_decision_id,model_version,prediction_time
                FROM execution_predictions_v2 WHERE model_identity=?""",
                (identity,),
            ).fetchall()
        }
        selected_total = baseline_total = 0.0
        points, result_rows = [], []
        for row in scores:
            baseline = net_shadow_log_return(row["baseline_quote_return"])
            if identity == LOT_IDENTITY:
                size = float(str(row["selected_action"]).removesuffix("X"))
                selected = float(row["selected_quote_return"]) - (
                    size * (float(row["baseline_quote_return"]) - baseline)
                )
            else:
                selected = net_shadow_log_return(row["selected_quote_return"])
            selected_total += selected
            baseline_total += baseline
            point = {
                "time": row["scored_at"], "model_version": row["model_version"],
                "selected_cumulative_return": selected_total,
                "baseline_cumulative_return": baseline_total,
            }
            points.append(point)
            result_rows.append({
                "decision_id": row["source_decision_id"],
                "decision_time": prediction_times.get(
                    (row["source_decision_id"], row["model_version"]),
                    row["scored_at"],
                ),
                "scored_at": row["scored_at"], "time": row["scored_at"],
                "model_version": row["model_version"],
                "direction": row["direction"], "selected_action": row["selected_action"],
                "exit_minutes": row["exit_minutes"],
                "selected_quote_return": selected,
                "baseline_quote_return": baseline,
                "delta_quote_return": selected - baseline,
            })
        training_decisions = int(latest["training_decisions"]) if latest else 0
        chart_points = _bounded_execution_curve(points)
        models.append({
            "model_identity": identity, "status": "RUNNING" if latest else "COLLECTING",
            "training_rows": training_decisions,
            "training_decisions": training_decisions,
            "training_observations": int(latest["training_observations"]) if latest else 0,
            "available_examples": len(training),
            "next_training_threshold": training_decisions + RETRAIN_INTERVAL if latest else MIN_TRAINING_DECISIONS,
            "model_version": latest["model_version"] if latest else None,
            "predictions": int(predictions), "scores": len(scores),
            "action_counts": action_counts,
            "evaluation": {
                "score_count": len(scores),
                "selected_cumulative_return": selected_total,
                "baseline_cumulative_return": baseline_total,
                "delta_cumulative_return": selected_total - baseline_total,
                "points": chart_points,
                "chart_source_count": len(points),
                "chart_point_count": len(chart_points),
                "chart_downsampled": len(points) > EXECUTION_CHART_MAX_POINTS,
                "results": result_rows[-100:], "unit": "QUOTE_RETURN",
            },
        })
    return {
        "models": models, "shadow_only": True,
        "source_model_identity": SOURCE_MODEL_IDENTITY,
        "source_model_label": "黄金＋大视野新闻 Ridge",
        "training_contract": "one frozen Live direction equals one position",
        "lot_candidates": list(LOT_CANDIDATES),
        "exit_checkpoints_minutes": list(EXIT_CHECKPOINTS),
    }


EXECUTION_CHART_MAX_POINTS = 600


def _bounded_execution_curve(points: list[dict]) -> list[dict]:
    """Keep long-running cumulative curves bounded without hiding reversals.

    SQLite remains the append-only authority.  The dashboard receives the first
    and last point plus the high/low turning points of both cumulative series in
    deterministic chronological buckets.  This prevents a 10,000-position
    history from becoming a 10,000-node SVG or a multi-megabyte sync payload.
    """
    if len(points) <= EXECUTION_CHART_MAX_POINTS:
        return points
    interior = points[1:-1]
    bucket_count = max(1, (EXECUTION_CHART_MAX_POINTS - 2) // 4)
    bucket_width = max(1, math.ceil(len(interior) / bucket_count))
    selected_indices = {0, len(points) - 1}
    keys = ("selected_cumulative_return", "baseline_cumulative_return")
    for offset in range(0, len(interior), bucket_width):
        start = offset + 1
        stop = min(len(points) - 1, start + bucket_width)
        indices = range(start, stop)
        for key in keys:
            selected_indices.add(min(indices, key=lambda index: float(points[index][key])))
            selected_indices.add(max(indices, key=lambda index: float(points[index][key])))
    return [points[index] for index in sorted(selected_indices)]
