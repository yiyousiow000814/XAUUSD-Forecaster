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


def _latest_update(ledger: ForwardLedger, identity: str):
    return ledger.connection.execute(
        "SELECT * FROM model_updates WHERE model_identity=? ORDER BY training_cutoff DESC LIMIT 1",
        (identity,),
    ).fetchone()


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
    market_update = _latest_update(ledger, "CHALLENGER_A")
    news_update = _latest_update(ledger, "CHALLENGER_B")
    if not healthy:
        status = "WAIT_DATA_HEALTH"
        predictions.extend([
            _prediction(version="market-ridge-untrained-v1", identity="CHALLENGER_A", direction=None, news_residual=None, cost=cost, uncertainty=None, status=status),
            _prediction(version="news-residual-ridge-untrained-v1", identity="CHALLENGER_B", direction=None, news_residual=None, cost=cost, uncertainty=None, status=status),
            _prediction(version="full-ridge-untrained-v1", identity="CHALLENGER_FULL", direction=None, news_residual=None, cost=cost, uncertainty=None, status=status),
        ])
        return predictions
    if market_update is None:
        predictions.extend([
            _prediction(version="market-ridge-untrained-v1", identity="CHALLENGER_A", direction=None, news_residual=None, cost=cost, uncertainty=None, status="WAIT_NO_TRAINING_ARTIFACT"),
            _prediction(version="news-residual-ridge-untrained-v1", identity="CHALLENGER_B", direction=None, news_residual=None, cost=cost, uncertainty=None, status="WAIT_NO_TRAINING_ARTIFACT"),
            _prediction(version="full-ridge-untrained-v1", identity="CHALLENGER_FULL", direction=None, news_residual=None, cost=cost, uncertainty=None, status="WAIT_NO_TRAINING_ARTIFACT"),
        ])
        return predictions
    market = RidgeArtifact.read(Path(market_update["artifact_path"]))
    market_values = [snapshot["features"].get(name) for name in MARKET_FEATURES]
    if any(value is None for value in market_values):
        predictions.extend([
            _prediction(version=market_update["model_version"], identity="CHALLENGER_A", direction=None, news_residual=None, cost=cost, uncertainty=None, status="WAIT_FEATURE_MISSING"),
            _prediction(version="news-residual-ridge-untrained-v1", identity="CHALLENGER_B", direction=None, news_residual=None, cost=cost, uncertainty=None, status="WAIT_FEATURE_MISSING"),
            _prediction(version="full-ridge-untrained-v1", identity="CHALLENGER_FULL", direction=None, news_residual=None, cost=cost, uncertainty=None, status="WAIT_FEATURE_MISSING"),
        ])
        return predictions
    market_direction = float(market.predict(np.asarray([market_values]))[0])
    predictions.append(_prediction(version=market_update["model_version"], identity="CHALLENGER_A", direction=market_direction, news_residual=None, cost=cost, uncertainty=market.residual_std, status="READY"))
    if news_update is None:
        predictions.extend([
            _prediction(version="news-residual-ridge-untrained-v1", identity="CHALLENGER_B", direction=None, news_residual=None, cost=cost, uncertainty=None, status="WAIT_NO_TRAINING_ARTIFACT"),
            _prediction(version="full-ridge-untrained-v1", identity="CHALLENGER_FULL", direction=None, news_residual=None, cost=cost, uncertainty=None, status="WAIT_NO_TRAINING_ARTIFACT"),
        ])
        return predictions
    news = RidgeArtifact.read(Path(news_update["artifact_path"]))
    vector = aggregate_news_features(ledger, decision_time)
    news_residual = float(news.predict(np.asarray([[vector[name] for name in NEWS_FEATURES]]))[0])
    predictions.append(_prediction(version=news_update["model_version"], identity="CHALLENGER_B", direction=None, news_residual=news_residual, cost=cost, uncertainty=news.residual_std, status="READY"))
    full_update = _latest_update(ledger, "CHALLENGER_FULL")
    if full_update is None:
        predictions.append(_prediction(version="full-ridge-untrained-v1", identity="CHALLENGER_FULL", direction=None, news_residual=None, cost=cost, uncertainty=None, status="WAIT_NO_TRAINING_ARTIFACT"))
        return predictions
    full_params = json.loads(full_update["hyperparameters_json"])
    full_market = ledger.connection.execute(
        "SELECT * FROM model_updates WHERE model_version=?",
        (full_params["market_model_version"],),
    ).fetchone()
    full_news = ledger.connection.execute(
        "SELECT * FROM model_updates WHERE model_version=?",
        (full_params["news_model_version"],),
    ).fetchone()
    if full_market is None or full_news is None:
        predictions.append(_prediction(version=full_update["model_version"], identity="CHALLENGER_FULL", direction=None, news_residual=None, cost=cost, uncertainty=None, status="WAIT_COMPONENT_MISSING"))
        return predictions
    full_market_artifact = RidgeArtifact.read(Path(full_market["artifact_path"]))
    full_news_artifact = RidgeArtifact.read(Path(full_news["artifact_path"]))
    full_market_direction = float(full_market_artifact.predict(np.asarray([market_values]))[0])
    full_news_residual = float(full_news_artifact.predict(np.asarray([[vector[name] for name in NEWS_FEATURES]]))[0])
    full_direction = full_market_direction + full_news_residual
    full_uncertainty = math.sqrt(full_market_artifact.residual_std ** 2 + full_news_artifact.residual_std ** 2)
    predictions.append(_prediction(version=full_update["model_version"], identity="CHALLENGER_FULL", direction=full_direction, news_residual=full_news_residual, cost=cost, uncertainty=full_uncertainty, status="READY"))
    return predictions
