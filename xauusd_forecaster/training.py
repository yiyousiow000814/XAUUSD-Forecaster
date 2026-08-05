"""Versioned Challenger training from matured Forward rows only."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from .factors import NEWS_FEATURES, aggregate_news_features
from .forward_ledger import ForwardLedger, canonical_hash
from .ridge import RidgeArtifact, train_ridge


UTC = timezone.utc
MARKET_FEATURES = (
    "return_1m",
    "return_5m",
    "return_15m",
    "return_30m",
    "return_60m",
    "tick_speed_5m_per_second",
    "quote_imbalance_60m",
    "realized_volatility_60m",
)


def train_market_challenger(
    ledger: ForwardLedger,
    cutoff: datetime,
    artifact_root: str | Path,
    *,
    minimum_rows: int = 200,
    alpha: float = 100.0,
) -> dict:
    evidence_hash, eligible_count = ledger.training_dataset_hash(cutoff)
    if eligible_count < minimum_rows:
        raise ValueError(
            f"market Challenger needs {minimum_rows} matured Forward rows; "
            f"found {eligible_count}"
        )
    rows = ledger.connection.execute(
        """SELECT d.decision_id, d.decision_time, s.features_json, s.u5,
                  o.direction_move
        FROM training_eligibility e
        JOIN decision_events d USING(decision_id)
        JOIN market_snapshots s USING(snapshot_id)
        JOIN outcomes o USING(decision_id)
        WHERE e.eligible_at <= ? AND d.decision_time >= ?
        ORDER BY d.decision_time, d.decision_id""",
        (cutoff.isoformat(), ledger.forward_epoch.isoformat()),
    ).fetchall()
    matrix: list[list[float]] = []
    targets: list[float] = []
    row_receipts: list[tuple[str, list[float], float]] = []
    for row in rows:
        features = json.loads(row["features_json"])
        values = [features.get(name) for name in MARKET_FEATURES]
        if row["u5"] is None or any(value is None for value in values):
            continue
        numeric = [float(value) for value in values]
        target = float(row["direction_move"]) / float(row["u5"])
        if np.isfinite(numeric).all() and np.isfinite(target):
            matrix.append(numeric)
            targets.append(target)
            row_receipts.append((row["decision_id"], numeric, target))
    if len(matrix) < minimum_rows:
        raise ValueError(
            f"market Challenger needs {minimum_rows} complete rows; found {len(matrix)}"
        )
    exact_training_hash = canonical_hash(row_receipts)
    version = (
        "market-ridge-"
        f"{cutoff.astimezone(UTC).strftime('%Y%m%dT%H%M%SZ')}-"
        f"{exact_training_hash[:12]}"
    )
    artifact = train_ridge(
        np.asarray(matrix), np.asarray(targets), MARKET_FEATURES, alpha,
        exact_training_hash,
    )
    directory = Path(artifact_root) / version
    artifact_path = directory / "model.json"
    artifact.write(artifact_path)
    record = {
        "model_version": version,
        "model_identity": "CHALLENGER_A",
        "created_at": datetime.now(UTC),
        "training_cutoff": cutoff,
        # Ledger gate binds the artifact to the entire eligible evidence set;
        # the artifact also carries its exact complete-row hash.
        "training_dataset_hash": evidence_hash,
        "feature_version": "forward-market-v1",
        "news_prompt_version": None,
        "hyperparameters": {
            "alpha": alpha,
            "minimum_rows": minimum_rows,
            "eligible_rows": eligible_count,
            "complete_rows": len(matrix),
            "exact_complete_row_hash": exact_training_hash,
        },
        "artifact_path": str(artifact_path),
        "artifact_hash": artifact.artifact_hash,
    }
    ledger.append_model_update(record)
    return record


def train_news_residual_challenger(
    ledger: ForwardLedger,
    cutoff: datetime,
    artifact_root: str | Path,
    market_record: dict,
    *,
    minimum_rows: int = 200,
    alpha: float = 100.0,
) -> dict:
    evidence_hash, eligible_count = ledger.training_dataset_hash(cutoff)
    if eligible_count < minimum_rows:
        raise ValueError(
            f"news Challenger needs {minimum_rows} matured Forward rows; "
            f"found {eligible_count}"
        )
    market = RidgeArtifact.read(market_record["artifact_path"])
    rows = ledger.connection.execute(
        """SELECT d.decision_id, d.decision_time, s.features_json, s.u5,
                  o.direction_move
        FROM training_eligibility e
        JOIN decision_events d USING(decision_id)
        JOIN market_snapshots s USING(snapshot_id)
        JOIN outcomes o USING(decision_id)
        WHERE e.eligible_at <= ? AND d.decision_time >= ?
        ORDER BY d.decision_time, d.decision_id""",
        (cutoff.isoformat(), ledger.forward_epoch.isoformat()),
    ).fetchall()
    matrix: list[list[float]] = []
    residuals: list[float] = []
    receipts: list[tuple[str, list[float], float]] = []
    for row in rows:
        market_features = json.loads(row["features_json"])
        market_values = [market_features.get(name) for name in MARKET_FEATURES]
        if row["u5"] is None or any(value is None for value in market_values):
            continue
        market_numeric = [float(value) for value in market_values]
        decision_time = datetime.fromisoformat(row["decision_time"])
        news = aggregate_news_features(ledger, decision_time)
        news_values = [float(news[name]) for name in NEWS_FEATURES]
        target = float(row["direction_move"]) / float(row["u5"])
        market_prediction = float(market.predict(np.asarray([market_numeric]))[0])
        residual = target - market_prediction
        if np.isfinite(news_values).all() and np.isfinite(residual):
            matrix.append(news_values)
            residuals.append(residual)
            receipts.append((row["decision_id"], news_values, residual))
    if len(matrix) < minimum_rows:
        raise ValueError(
            f"news Challenger needs {minimum_rows} complete rows; found {len(matrix)}"
        )
    exact_hash = canonical_hash(receipts)
    version = (
        "news-residual-ridge-"
        f"{cutoff.astimezone(UTC).strftime('%Y%m%dT%H%M%SZ')}-"
        f"{exact_hash[:12]}"
    )
    artifact = train_ridge(
        np.asarray(matrix), np.asarray(residuals), NEWS_FEATURES, alpha, exact_hash
    )
    artifact_path = Path(artifact_root) / version / "model.json"
    artifact.write(artifact_path)
    record = {
        "model_version": version,
        "model_identity": "CHALLENGER_B",
        "created_at": datetime.now(UTC),
        "training_cutoff": cutoff,
        "training_dataset_hash": evidence_hash,
        "feature_version": "forward-news-v1",
        "news_prompt_version": "news-json-v9-local-display-recovery+v8-compatible",
        "hyperparameters": {
            "alpha": alpha,
            "minimum_rows": minimum_rows,
            "eligible_rows": eligible_count,
            "complete_rows": len(matrix),
            "market_model_version": market_record["model_version"],
            "exact_complete_row_hash": exact_hash,
        },
        "artifact_path": str(artifact_path),
        "artifact_hash": artifact.artifact_hash,
    }
    ledger.append_model_update(record)
    return record


def create_full_challenger(
    ledger: ForwardLedger,
    cutoff: datetime,
    artifact_root: str | Path,
    market_record: dict,
    news_record: dict,
    *,
    minimum_rows: int = 200,
) -> dict:
    evidence_hash, eligible_count = ledger.training_dataset_hash(cutoff)
    version = (
        "full-ridge-"
        f"{cutoff.astimezone(UTC).strftime('%Y%m%dT%H%M%SZ')}-"
        f"{evidence_hash[:12]}"
    )
    manifest = {
        "schema": "xauusd.forward.full-challenger.v1",
        "market_model_version": market_record["model_version"],
        "market_artifact_hash": market_record["artifact_hash"],
        "news_model_version": news_record["model_version"],
        "news_artifact_hash": news_record["artifact_hash"],
        "training_dataset_hash": evidence_hash,
        "training_cutoff": cutoff.isoformat(),
        "combination": "market_direction_u5 + news_residual_u5",
    }
    directory = Path(artifact_root) / version
    directory.mkdir(parents=True, exist_ok=False)
    artifact_path = directory / "manifest.json"
    artifact_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8"
    )
    record = {
        "model_version": version,
        "model_identity": "CHALLENGER_FULL",
        "created_at": datetime.now(UTC),
        "training_cutoff": cutoff,
        "training_dataset_hash": evidence_hash,
        "feature_version": "forward-full-v1",
        "news_prompt_version": "news-json-v9-local-display-recovery+v8-compatible",
        "hyperparameters": {
            "minimum_rows": minimum_rows,
            "eligible_rows": eligible_count,
            "market_model_version": market_record["model_version"],
            "news_model_version": news_record["model_version"],
        },
        "artifact_path": str(artifact_path),
        "artifact_hash": canonical_hash(manifest),
    }
    ledger.append_model_update(record)
    return record


def _latest_record(ledger: ForwardLedger, identity: str):
    return ledger.connection.execute(
        "SELECT * FROM model_updates WHERE model_identity=? ORDER BY training_cutoff DESC LIMIT 1",
        (identity,),
    ).fetchone()


def _trained_complete_rows(record) -> int:
    if record is None:
        return 0
    parameters = json.loads(record["hyperparameters_json"])
    return int(parameters.get("complete_rows", parameters.get("eligible_rows", 0)))


def complete_market_training_rows(ledger: ForwardLedger, cutoff: datetime) -> int:
    rows = ledger.connection.execute(
        """SELECT s.features_json, s.u5
        FROM training_eligibility e
        JOIN decision_events d USING(decision_id)
        JOIN market_snapshots s USING(snapshot_id)
        WHERE e.eligible_at <= ? AND d.decision_time >= ?""",
        (cutoff.isoformat(), ledger.forward_epoch.isoformat()),
    ).fetchall()
    complete = 0
    for row in rows:
        features = json.loads(row["features_json"])
        values = [features.get(name) for name in MARKET_FEATURES]
        if row["u5"] is None or any(value is None for value in values):
            continue
        numeric = [float(value) for value in values]
        if np.isfinite(numeric).all() and np.isfinite(float(row["u5"])):
            complete += 1
    return complete


def auto_train_due(
    ledger: ForwardLedger,
    cutoff: datetime,
    artifact_root: str | Path,
    *,
    minimum_rows: int = 200,
    retrain_interval: int = 50,
    alpha: float = 100.0,
) -> list[dict[str, object]]:
    """Train due Shadow Challengers; never changes Champion or effective action."""
    if minimum_rows < 2 or retrain_interval < 1:
        raise ValueError("training thresholds are invalid")
    _, eligible_count = ledger.training_dataset_hash(cutoff)
    complete_count = complete_market_training_rows(ledger, cutoff)
    if complete_count < minimum_rows:
        return [{"status": "WAIT_MINIMUM_ROWS", "eligible_rows": eligible_count, "complete_rows": complete_count, "minimum_rows": minimum_rows}]
    statuses: list[dict[str, object]] = []
    market = _latest_record(ledger, "CHALLENGER_A")
    if market is None or complete_count >= _trained_complete_rows(market) + retrain_interval:
        record = train_market_challenger(
            ledger, cutoff, artifact_root, minimum_rows=minimum_rows, alpha=alpha
        )
        statuses.append({"status": "TRAINED", "model_identity": "CHALLENGER_A", "model_version": record["model_version"]})
        market = _latest_record(ledger, "CHALLENGER_A")
    news = _latest_record(ledger, "CHALLENGER_B")
    market_version = market["model_version"] if market else None
    news_params = json.loads(news["hyperparameters_json"]) if news else {}
    news_due = (
        news is None
        or complete_count >= _trained_complete_rows(news) + retrain_interval
        or news_params.get("market_model_version") != market_version
    )
    if news_due and market is not None:
        record = train_news_residual_challenger(
            ledger, cutoff, artifact_root, dict(market),
            minimum_rows=minimum_rows, alpha=alpha,
        )
        statuses.append({"status": "TRAINED", "model_identity": "CHALLENGER_B", "model_version": record["model_version"]})
        news = _latest_record(ledger, "CHALLENGER_B")
    full = _latest_record(ledger, "CHALLENGER_FULL")
    full_params = json.loads(full["hyperparameters_json"]) if full else {}
    full_due = (
        full is None
        or full_params.get("market_model_version") != (market["model_version"] if market else None)
        or full_params.get("news_model_version") != (news["model_version"] if news else None)
    )
    if full_due and market is not None and news is not None:
        record = create_full_challenger(
            ledger, cutoff, artifact_root, dict(market), dict(news),
            minimum_rows=minimum_rows,
        )
        statuses.append({"status": "TRAINED", "model_identity": "CHALLENGER_FULL", "model_version": record["model_version"]})
    if not statuses:
        statuses.append({"status": "NOT_DUE", "eligible_rows": eligible_count, "complete_rows": complete_count, "next_rows": min(_trained_complete_rows(market), _trained_complete_rows(news)) + retrain_interval})
    return statuses
