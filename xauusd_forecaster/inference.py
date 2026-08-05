"""Fail-closed inference for versioned Shadow Challengers."""

from __future__ import annotations

import math
import json
from pathlib import Path

import numpy as np

from .factors import NEWS_FEATURES, aggregate_news_features
from .forward_ledger import ForwardLedger
from .ridge import RidgeArtifact
from .training import MARKET_FEATURES


MAX_ACTIVE_COHORTS = 4


def _updates(ledger: ForwardLedger, identity: str):
    return ledger.connection.execute(
        """SELECT * FROM model_updates
        WHERE model_identity=? AND training_cutoff IN (
            SELECT training_cutoff FROM model_updates
            GROUP BY training_cutoff ORDER BY training_cutoff DESC LIMIT ?)
        ORDER BY training_cutoff, model_version""",
        (identity, MAX_ACTIVE_COHORTS),
    ).fetchall()


def _recommended(direction: float, cost: float, uncertainty: float) -> str:
    long_ev = direction - cost
    short_ev = -direction - cost
    if long_ev == short_ev:
        return "WAIT"
    best = max(long_ev, short_ev)
    if best - 1.96 * uncertainty <= 0:
        return "WAIT"
    return "LONG" if long_ev > short_ev else "SHORT"


def _prediction(
    *,
    version: str,
    identity: str,
    direction: float | None,
    news_residual: float | None,
    cost: float,
    uncertainty: float | None,
    status: str,
) -> dict[str, object]:
    ready = direction is not None and uncertainty is not None and status == "READY"
    return {
        "model_version": version,
        "model_identity": identity,
        "recommended_action": _recommended(direction, cost, uncertainty) if ready else "WAIT",
        "prediction_status": status,
        "predicted_direction_u5": direction,
        "predicted_news_residual_u5": news_residual,
        "ev_long_u5": direction - cost if ready else None,
        "ev_short_u5": -direction - cost if ready else None,
        "uncertainty_u5": uncertainty,
    }


def build_shadow_predictions(
    ledger: ForwardLedger,
    snapshot: dict,
    decision_time,
) -> list[dict[str, object]]:
    predictions: list[dict[str, object]] = [
        {
            "model_version": "always-wait-v1",
            "model_identity": "CHAMPION_0",
            "recommended_action": "WAIT",
            "prediction_status": "READY",
            "predicted_direction_u5": 0.0,
            "predicted_news_residual_u5": 0.0,
            "ev_long_u5": 0.0,
            "ev_short_u5": 0.0,
            "uncertainty_u5": 0.0,
        }
    ]
    healthy = snapshot["data_health"] == "OK" and snapshot["u5_status"] == "READY"
    cost = 0.0
    if healthy and snapshot.get("bid") and snapshot.get("ask") and snapshot.get("u5"):
        cost = math.log(float(snapshot["ask"]) / float(snapshot["bid"])) / float(snapshot["u5"])
    market_updates = _updates(ledger, "CHALLENGER_A")
    news_updates = _updates(ledger, "CHALLENGER_B")
    full_updates = _updates(ledger, "CHALLENGER_FULL")
    known_updates = [*market_updates, *news_updates, *full_updates]
    if not healthy:
        status = "WAIT_DATA_HEALTH"
        if known_updates:
            predictions.extend(
                _prediction(version=row["model_version"], identity=row["model_identity"], direction=None, news_residual=None, cost=cost, uncertainty=None, status=status)
                for row in known_updates
            )
        else:
            predictions.extend([
                _prediction(version="market-ridge-untrained-v1", identity="CHALLENGER_A", direction=None, news_residual=None, cost=cost, uncertainty=None, status=status),
                _prediction(version="news-residual-ridge-untrained-v1", identity="CHALLENGER_B", direction=None, news_residual=None, cost=cost, uncertainty=None, status=status),
                _prediction(version="full-ridge-untrained-v1", identity="CHALLENGER_FULL", direction=None, news_residual=None, cost=cost, uncertainty=None, status=status),
            ])
        return predictions
    if not market_updates:
        predictions.extend([
            _prediction(version="market-ridge-untrained-v1", identity="CHALLENGER_A", direction=None, news_residual=None, cost=cost, uncertainty=None, status="WAIT_NO_TRAINING_ARTIFACT"),
            _prediction(version="news-residual-ridge-untrained-v1", identity="CHALLENGER_B", direction=None, news_residual=None, cost=cost, uncertainty=None, status="WAIT_NO_TRAINING_ARTIFACT"),
            _prediction(version="full-ridge-untrained-v1", identity="CHALLENGER_FULL", direction=None, news_residual=None, cost=cost, uncertainty=None, status="WAIT_NO_TRAINING_ARTIFACT"),
        ])
        return predictions
    market_values = [snapshot["features"].get(name) for name in MARKET_FEATURES]
    if any(value is None for value in market_values):
        predictions.extend(
            _prediction(version=row["model_version"], identity=row["model_identity"], direction=None, news_residual=None, cost=cost, uncertainty=None, status="WAIT_FEATURE_MISSING")
            for row in known_updates
        )
        return predictions
    market_records = {row["model_version"]: row for row in market_updates}
    market_artifacts = {
        version: RidgeArtifact.read(Path(row["artifact_path"]))
        for version, row in market_records.items()
    }
    for version, market in market_artifacts.items():
        market_direction = float(market.predict(np.asarray([market_values]))[0])
        predictions.append(_prediction(version=version, identity="CHALLENGER_A", direction=market_direction, news_residual=None, cost=cost, uncertainty=market.residual_std, status="READY"))
    if not news_updates:
        predictions.extend([
            _prediction(version="news-residual-ridge-untrained-v1", identity="CHALLENGER_B", direction=None, news_residual=None, cost=cost, uncertainty=None, status="WAIT_NO_TRAINING_ARTIFACT"),
            _prediction(version="full-ridge-untrained-v1", identity="CHALLENGER_FULL", direction=None, news_residual=None, cost=cost, uncertainty=None, status="WAIT_NO_TRAINING_ARTIFACT"),
        ])
        return predictions
    vector = aggregate_news_features(ledger, decision_time)
    news_vector = np.asarray([[vector[name] for name in NEWS_FEATURES]])
    news_records = {row["model_version"]: row for row in news_updates}
    news_artifacts = {
        version: RidgeArtifact.read(Path(row["artifact_path"]))
        for version, row in news_records.items()
    }
    for version, news in news_artifacts.items():
        news_residual = float(news.predict(news_vector)[0])
        predictions.append(_prediction(version=version, identity="CHALLENGER_B", direction=None, news_residual=news_residual, cost=cost, uncertainty=news.residual_std, status="READY"))
    if not full_updates:
        predictions.append(_prediction(version="full-ridge-untrained-v1", identity="CHALLENGER_FULL", direction=None, news_residual=None, cost=cost, uncertainty=None, status="WAIT_NO_TRAINING_ARTIFACT"))
        return predictions
    for full_update in full_updates:
        full_params = json.loads(full_update["hyperparameters_json"])
        full_market_artifact = market_artifacts.get(full_params["market_model_version"])
        full_news_artifact = news_artifacts.get(full_params["news_model_version"])
        if full_market_artifact is None or full_news_artifact is None:
            predictions.append(_prediction(version=full_update["model_version"], identity="CHALLENGER_FULL", direction=None, news_residual=None, cost=cost, uncertainty=None, status="WAIT_COMPONENT_MISSING"))
            continue
        full_market_direction = float(full_market_artifact.predict(np.asarray([market_values]))[0])
        full_news_residual = float(full_news_artifact.predict(news_vector)[0])
        full_direction = full_market_direction + full_news_residual
        full_uncertainty = math.sqrt(full_market_artifact.residual_std ** 2 + full_news_artifact.residual_std ** 2)
        predictions.append(_prediction(version=full_update["model_version"], identity="CHALLENGER_FULL", direction=full_direction, news_residual=full_news_residual, cost=cost, uncertainty=full_uncertainty, status="READY"))
    return predictions
