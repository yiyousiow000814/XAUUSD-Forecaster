"""Prequential V2 inference with honest chronological OOS calibration."""

from __future__ import annotations

import json
import math
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import numpy as np

from .evidence_v2 import ELIGIBILITY_VERSION, FEATURE_VERSION, NEWS_FEATURE_VERSION
from .execution_costs import ROUND_TRIP_COMMISSION_LOG_COST
from .forward_ledger import canonical_hash
from .news_evidence import EVIDENCE_POLICY_VERSION
from .ridge import RidgeArtifact
from .training import MARKET_FEATURES


MIN_CALIBRATION_BLOCKS = 20
ACTIVE_VERSIONS_PER_IDENTITY = 2
MODEL_IDENTITIES = frozenset({
    "MARKET_ONLY", "NEWS_RESIDUAL", "FULL",
    "BROAD_NEWS_RESIDUAL", "BROAD_FULL",
})
NEWS_MODEL_IDENTITIES = frozenset({
    "NEWS_RESIDUAL", "FULL", "BROAD_NEWS_RESIDUAL", "BROAD_FULL",
})


def _row_value(row, key: str, default=None):
    return row[key] if key in row.keys() else default


def _expected_news_contract(identity: str) -> tuple[str, str] | None:
    if identity == "NEWS_RESIDUAL":
        return NEWS_FEATURE_VERSION, ELIGIBILITY_VERSION
    if identity == "FULL":
        return f"{FEATURE_VERSION}+{NEWS_FEATURE_VERSION}", ELIGIBILITY_VERSION
    broad_eligibility = f"{ELIGIBILITY_VERSION}+{EVIDENCE_POLICY_VERSION}"
    if identity == "BROAD_NEWS_RESIDUAL":
        return NEWS_FEATURE_VERSION, broad_eligibility
    if identity == "BROAD_FULL":
        return (
            f"{FEATURE_VERSION}+{NEWS_FEATURE_VERSION}+{EVIDENCE_POLICY_VERSION}",
            broad_eligibility,
        )
    return None


def _news_contract_matches(update) -> bool:
    expected = _expected_news_contract(update["model_identity"])
    if expected is None:
        return True
    return (
        _row_value(update, "feature_version") == expected[0]
        and _row_value(update, "eligibility_version") == expected[1]
    )


def _news_generation_ready(
    newest: dict, available_news_eligibilities: set[str] | None = None,
) -> bool:
    """Require one complete, runnable generation for every news identity."""
    return all(
        identity in newest
        and _news_contract_matches(newest[identity])
        and (
            available_news_eligibilities is None
            or _row_value(newest[identity], "eligibility_version")
                in available_news_eligibilities
        )
        and (
            "artifact_path" not in newest[identity].keys()
            or (
                Path(newest[identity]["artifact_path"]).is_absolute()
                and Path(newest[identity]["artifact_path"]).exists()
            )
        )
        for identity in NEWS_MODEL_IDENTITIES
    )


def news_model_activation_status(updates) -> list[dict]:
    """Explain whether each news identity has a current-policy runnable artifact."""
    newest = {}
    for update in updates:
        newest.setdefault(update["model_identity"], update)
    generation_ready = _news_generation_ready(newest)
    result = []
    for identity in ("NEWS_RESIDUAL", "FULL", "BROAD_NEWS_RESIDUAL", "BROAD_FULL"):
        expected_feature, expected_eligibility = _expected_news_contract(identity)
        update = newest.get(identity)
        if update is None:
            status, reason = "NOT_TRAINED", "尚未训练当前新闻模型"
        elif not _news_contract_matches(update):
            status, reason = "POLICY_MISMATCH", "最新版不符合当前新闻规则"
        else:
            artifact = _row_value(update, "artifact_path")
            artifact_valid = True
            if artifact:
                path = Path(artifact)
                artifact_valid = path.is_absolute() and path.exists()
            status = "ACTIVE" if artifact_valid else "ARTIFACT_UNAVAILABLE"
            reason = "当前规则版本已激活" if artifact_valid else "当前模型文件不可用"
            if status == "ACTIVE" and not generation_ready:
                status = "GENERATION_WAIT"
                reason = "等待同一规则版本的四套新闻模型全部生成"
        result.append({
            "model_identity": identity,
            "status": status,
            "reason": reason,
            "model_version": _row_value(update, "model_version") if update else None,
            "actual_feature_version": _row_value(update, "feature_version") if update else None,
            "actual_eligibility_version": _row_value(update, "eligibility_version") if update else None,
            "expected_feature_version": expected_feature,
            "expected_eligibility_version": expected_eligibility,
        })
    return result


def _recommended_action(
    ev_long: float | None, ev_short: float | None, half_width: float | None,
) -> str:
    """Choose the positive post-cost EV direction; uncertainty remains diagnostic."""
    del half_width
    if ev_long is None or ev_short is None:
        return "WAIT"
    if ev_long == ev_short:
        return "WAIT"
    if ev_long > ev_short and ev_long > 0:
        return "LONG"
    if ev_short > ev_long and ev_short > 0:
        return "SHORT"
    return "WAIT"


def _active_updates(updates, available_news_eligibilities: set[str] | None = None) -> list:
    """Select models, activating news only as one complete policy generation."""
    if available_news_eligibilities is None:
        available_news_eligibilities = {
            ELIGIBILITY_VERSION,
            f"{ELIGIBILITY_VERSION}+{EVIDENCE_POLICY_VERSION}",
        }
    newest_by_identity = {}
    for update in updates:
        newest_by_identity.setdefault(update["model_identity"], update)
    news_generation_ready = _news_generation_ready(
        newest_by_identity, available_news_eligibilities
    )

    counts: dict[str, int] = defaultdict(int)
    newest_seen: set[str] = set()
    blocked_news: set[str] = set()
    active = []
    for update in updates:
        identity = update["model_identity"]
        if identity not in MODEL_IDENTITIES or counts[identity] >= ACTIVE_VERSIONS_PER_IDENTITY:
            continue
        if identity in NEWS_MODEL_IDENTITIES and not news_generation_ready:
            # A rule generation is usable only after all four news identities
            # have compatible artifacts.  This prevents a policy deployment
            # from exposing a partial or silently mixed model generation.
            continue
        if identity in blocked_news:
            continue
        if identity in NEWS_MODEL_IDENTITIES:
            eligibility = _row_value(update, "eligibility_version")
            compatible = (
                _news_contract_matches(update)
                and eligibility in available_news_eligibilities
            )
            if identity not in newest_seen and not compatible:
                # The newest artifact defines the identity's lifecycle.  Never
                # fall through to an older rule version just because its
                # historical snapshot still exists.
                blocked_news.add(identity)
                newest_seen.add(identity)
                continue
            if not compatible:
                continue
        newest_seen.add(identity)
        if "artifact_path" in update.keys():
            artifact_path = Path(update["artifact_path"])
            if not artifact_path.is_absolute() or not artifact_path.exists():
                if identity in NEWS_MODEL_IDENTITIES:
                    blocked_news.add(identity)
                continue
        counts[identity] += 1
        active.append(update)
    return active


def _calibration(ledger, model_identity: str, decision_time: datetime) -> dict:
    """Calibrate the rolling lineage using the newest model at each prior decision."""
    rows = ledger.connection.execute(
        """WITH ranked AS (
            SELECT s.residual_u5,p.decision_time,
                   row_number() OVER (
                       PARTITION BY p.source_decision_id,p.model_identity
                       ORDER BY u.created_at DESC,u.model_version DESC
                   ) AS version_rank
            FROM prediction_scores_v2 s
            JOIN predictions_v2 p USING(source_decision_id,model_version)
            JOIN model_updates_v2 u USING(model_version)
            WHERE p.model_identity=? AND p.decision_time>u.created_at
              AND p.decision_time<?
        )
        SELECT residual_u5,decision_time FROM ranked
        WHERE version_rank=1 ORDER BY decision_time""",
        (model_identity, decision_time.isoformat()),
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
    version = f"rolling-lineage-day-oos-{model_identity.lower()}-{source_hash[:12]}"
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
    expected_action = _recommended_action(ev_long, ev_short, width)
    if recommended != expected_action:
        raise ValueError(
            f"prediction action violates frozen post-cost EV policy: "
            f"recorded={recommended}, expected={expected_action}"
        )
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
                               news_snapshot: dict,
                               news_snapshots: dict[str, dict] | None = None) -> list[dict]:
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
    features = json.loads(market_snapshot["features_json"])
    snapshots = dict(news_snapshots or {})
    snapshots.setdefault(ELIGIBILITY_VERSION, news_snapshot)
    snapshots.setdefault(f"{ELIGIBILITY_VERSION}+{EVIDENCE_POLICY_VERSION}", news_snapshot)
    values = [features.get(name) for name in MARKET_FEATURES]
    for update in _active_updates(updates, set(snapshots)):
        identity = update["model_identity"]
        update_eligibility = update["eligibility_version"]
        selected_news_snapshot = snapshots.get(update_eligibility, news_snapshot)
        news_features = json.loads(selected_news_snapshot["features_json"])
        calibration = _calibration(ledger, identity, decision_time)
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
        elif identity in {"NEWS_RESIDUAL", "BROAD_NEWS_RESIDUAL"}:
            artifact = RidgeArtifact.read(update["artifact_path"])
            news_residual = float(artifact.predict(np.asarray([
                [float(news_features[name]) for name in artifact.feature_names]
            ]))[0])
            predicted = news_residual
        elif identity in {"FULL", "BROAD_FULL"}:
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
        quote_cost_estimate_u5 = (
            math.log(ask / bid) * 2.0 + ROUND_TRIP_COMMISSION_LOG_COST
        ) / u5
        ev_long = predicted - quote_cost_estimate_u5
        ev_short = -predicted - quote_cost_estimate_u5
        recommended = _recommended_action(
            ev_long, ev_short, calibration["half_width"],
        )
        _insert_prediction(
            ledger, decision_id=decision_id, decision_time=decision_time, created_at=created_at,
            model_version=update["model_version"], model_identity=identity,
            feature_hash=canonical_hash((
                market_snapshot["output_hash"], selected_news_snapshot["output_hash"],
                update_eligibility,
            )),
            predicted=predicted, news_residual=news_residual,
            ev_long=ev_long, ev_short=ev_short, calibration=calibration,
            recommended=recommended, status=(
                "READY" if calibration["status"] == "CALIBRATED"
                else "PROVISIONAL_POST_COST_EV"
            ),
        )
        created.append({"model_identity": identity, "model_version": update["model_version"],
                        "eligibility_version": update_eligibility,
                        "recommended_action": recommended, "calibration_status": calibration["status"]})
    return created
