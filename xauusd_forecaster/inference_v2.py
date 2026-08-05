"""Prequential V2 inference with honest chronological OOS calibration."""

from __future__ import annotations

import json
import math
from datetime import datetime

import numpy as np

from .forward_ledger import canonical_hash
from .ridge import RidgeArtifact
from .training import MARKET_FEATURES


NEXT_BATCH_DECISIONS = 50
MIN_CALIBRATION_BLOCKS = 20


def _calibration(ledger, model_version: str, model_identity: str, decision_time: datetime) -> dict:
    rows = ledger.connection.execute(
        """SELECT s.residual_u5, p.decision_time
        FROM prediction_scores_v2 s JOIN predictions_v2 p
          USING(source_decision_id, model_version)
        WHERE s.model_version=? AND p.decision_time < ?
        ORDER BY p.decision_time""", (model_version, decision_time.isoformat())
    ).fetchall()
    days = {}
    for row in rows:
        if row["residual_u5"] is not None:
            days.setdefault(row["decision_time"][:10], []).append(abs(float(row["residual_u5"])))
    blocks = [float(np.quantile(values, 0.95)) for _, values in sorted(days.items())]
    half_width = float(np.quantile(blocks, 0.95)) if blocks else None
    status = "CALIBRATED" if len(blocks) >= MIN_CALIBRATION_BLOCKS else (
        "EARLY" if blocks else "UNCALIBRATED"
    )
    source_hash = canonical_hash([(row["decision_time"], row["residual_u5"]) for row in rows])
    version = f"block-day-oos-{model_version}-{source_hash[:12]}"
    ledger.connection.execute(
        """INSERT OR IGNORE INTO calibration_snapshots_v2 VALUES
        (?,?,?,?,?,?,?,?,?,?,?)""",
        (version, model_identity, decision_time.isoformat(), decision_time.isoformat(),
         "UTC_DAY_BLOCK_OOS_ABS_RESIDUAL_Q95", len(rows), len(blocks), len(days),
         half_width, status, source_hash),
    )
    return {"version": version, "rows": len(rows), "blocks": len(blocks),
            "days": len(days), "half_width": half_width, "status": status}


def _insert_prediction(ledger, *, decision_id: str, decision_time: datetime,
                       created_at: datetime, model_version: str, model_identity: str,
                       feature_hash: str, predicted: float | None,
                       news_residual: float | None, ev_long: float | None,
                       ev_short: float | None, calibration: dict,
                       recommended: str, status: str) -> None:
    width = calibration["half_width"]
    lcb_long = ev_long - width if width is not None and ev_long is not None else None
    lcb_short = ev_short - width if width is not None and ev_short is not None else None
    ledger.connection.execute(
        """INSERT INTO predictions_v2 VALUES
        (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (decision_id, model_version, model_identity, decision_time.isoformat(), created_at.isoformat(),
         "LIVE_OOS", feature_hash, predicted, news_residual, ev_long, ev_short,
         lcb_long, lcb_short, "UTC_DAY_BLOCK_OOS_ABS_RESIDUAL_Q95",
         calibration["version"], calibration["rows"], calibration["blocks"],
         calibration["days"], width * 2 if width is not None else None,
         calibration["status"], recommended, "WAIT", status),
    )


def append_live_predictions_v2(ledger, *, decision_id: str, decision_time: datetime,
                               created_at: datetime, market_snapshot: dict,
                               news_snapshot: dict) -> list[dict]:
    """Append only models that existed before this decision; never backfill."""
    created = []
    empty_cal = {"version": "always-wait-no-calibration", "rows": 0, "blocks": 0,
                 "days": 0, "half_width": None, "status": "NOT_APPLICABLE"}
    _insert_prediction(
        ledger, decision_id=decision_id, decision_time=decision_time, created_at=created_at,
        model_version="always-wait-v1", model_identity="CHAMPION_0",
        feature_hash=market_snapshot["output_hash"], predicted=0.0, news_residual=None,
        ev_long=0.0, ev_short=0.0, calibration=empty_cal, recommended="WAIT", status="READY",
    )
    created.append({"model_identity": "CHAMPION_0", "model_version": "always-wait-v1"})

    updates = ledger.connection.execute(
        """SELECT * FROM model_updates_v2
        WHERE created_at < ? ORDER BY created_at DESC""", (decision_time.isoformat(),)
    ).fetchall()
    seen = set()
    features = json.loads(market_snapshot["features_json"])
    news_features = json.loads(news_snapshot["features_json"])
    values = [features.get(name) for name in MARKET_FEATURES]
    for update in updates:
        identity = update["model_identity"]
        if identity in seen:
            continue
        # Retain each frozen version for its next 50 true forward decisions.
        count = ledger.connection.execute(
            "SELECT count(*) FROM predictions_v2 WHERE model_version=?",
            (update["model_version"],),
        ).fetchone()[0]
        if count >= NEXT_BATCH_DECISIONS:
            continue
        seen.add(identity)
        calibration = _calibration(ledger, update["model_version"], identity, decision_time)
        if market_snapshot["data_health"] != "OK" or market_snapshot["u5"] is None \
                or any(value is None for value in values):
            _insert_prediction(
                ledger, decision_id=decision_id, decision_time=decision_time, created_at=created_at,
                model_version=update["model_version"], model_identity=identity,
                feature_hash=market_snapshot["output_hash"], predicted=None, news_residual=None,
                ev_long=None, ev_short=None, calibration=calibration,
                recommended="WAIT", status="DATA_UNHEALTHY",
            )
            continue
        news_residual = None
        if identity == "MARKET_ONLY":
            artifact = RidgeArtifact.read(update["artifact_path"])
            predicted = float(artifact.predict(np.asarray([[float(v) for v in values]]))[0])
        elif identity == "NEWS_RESIDUAL":
            artifact = RidgeArtifact.read(update["artifact_path"])
            news_residual = float(artifact.predict(np.asarray([
                [float(news_features[name]) for name in artifact.feature_names]
            ]))[0])
            predicted = news_residual
        elif identity == "FULL":
            manifest = json.loads(open(update["artifact_path"], encoding="utf-8").read())
            market_artifact = RidgeArtifact.read(manifest["market_artifact_path"])
            news_artifact = RidgeArtifact.read(manifest["news_artifact_path"])
            market_prediction = float(market_artifact.predict(
                np.asarray([[float(v) for v in values]])
            )[0])
            news_residual = float(news_artifact.predict(np.asarray([
                [float(news_features[name]) for name in news_artifact.feature_names]
            ]))[0])
            predicted = market_prediction + news_residual
        else:
            continue
        bid = float(features["decision_bid"])
        ask = float(features["decision_ask"])
        u5 = float(market_snapshot["u5"])
        quote_cost_estimate_u5 = math.log(ask / bid) * 2.0 / u5
        ev_long = predicted - quote_cost_estimate_u5
        ev_short = -predicted - quote_cost_estimate_u5
        recommended = "LONG" if ev_long >= ev_short and ev_long > 0 else (
            "SHORT" if ev_short > 0 else "WAIT"
        )
        _insert_prediction(
            ledger, decision_id=decision_id, decision_time=decision_time, created_at=created_at,
            model_version=update["model_version"], model_identity=identity,
            feature_hash=canonical_hash((market_snapshot["output_hash"], news_snapshot["output_hash"])),
            predicted=predicted, news_residual=news_residual,
            ev_long=ev_long, ev_short=ev_short, calibration=calibration,
            recommended=recommended, status=("READY" if calibration["status"] == "CALIBRATED" else "PROVISIONAL"),
        )
        created.append({"model_identity": identity, "model_version": update["model_version"],
                        "recommended_action": recommended, "calibration_status": calibration["status"]})
    return created
