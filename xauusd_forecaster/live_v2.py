"""Append-only LIVE_OOS evidence after EVALUATION_EPOCH_V2."""

from __future__ import annotations

import json
import uuid
from datetime import datetime

from .evidence_v2 import (
    ELIGIBILITY_VERSION, FEATURE_VERSION, LABEL_VERSION, NEWS_FEATURE_VERSION,
    evaluation_epoch, install_v2_schema,
)
from .forward_ledger import canonical_hash
from .inference_v2 import append_live_predictions_v2
from .news_features_v2 import aggregate_news_features_v2
from .repair_v2 import LANE_RULE_VERSION, TRAINING_ELIGIBILITY_VERSION
from .training import MARKET_FEATURES
from .u5_state import U5_VERSION
from .execution_learning import append_execution_examples, append_lot_predictions


def _uuid(namespace: str, value: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"xauusd:{namespace}:{value}"))


def _lane(connection, kind: str, evidence_id: str, lane: str,
          at: datetime, source_hash: str) -> None:
    connection.execute(
        "INSERT OR IGNORE INTO evidence_lane_assignments VALUES (?,?,?,?,?,?,?,NULL)",
        (_uuid("lane", f"{kind}:{evidence_id}:{LANE_RULE_VERSION}"), kind, evidence_id,
         lane, at.isoformat(), LANE_RULE_VERSION, source_hash),
    )


def append_live_decision_v2(ledger, *, decision_id: str, decision_time: datetime,
                            created_at: datetime, snapshot: dict) -> list[dict]:
    install_v2_schema(ledger.connection)
    epoch = evaluation_epoch(ledger.connection)
    if epoch is None or decision_time < epoch:
        return []
    features = dict(snapshot["features"])
    features["decision_bid"] = snapshot["bid"]
    features["decision_ask"] = snapshot["ask"]
    source_hash = canonical_hash((snapshot["snapshot_hash"], snapshot["source_event_time"].isoformat()
                                  if snapshot["source_event_time"] else None,
                                  snapshot["source_received_time"].isoformat()
                                  if snapshot["source_received_time"] else None))
    payload = {
        "decision_id": decision_id, "decision_time": decision_time.isoformat(),
        "source_snapshot_hash": snapshot["snapshot_hash"], "feature_version": FEATURE_VERSION,
        "u5_version": U5_VERSION, "u5": snapshot["u5"], "features": features,
        "data_health": snapshot["data_health"], "reason_codes": snapshot["reason_codes"],
        "source_evidence_hash": source_hash,
    }
    market_hash = canonical_hash(payload)
    market_id = _uuid("derived-market", f"{decision_id}:{FEATURE_VERSION}")
    news = aggregate_news_features_v2(ledger, decision_time)
    news_payload = {"decision_id": decision_id, "decision_time": decision_time.isoformat(),
                    "feature_version": NEWS_FEATURE_VERSION,
                    "eligibility_version": ELIGIBILITY_VERSION, **news}
    news_hash = canonical_hash(news_payload)
    news_id = _uuid("derived-news", f"{decision_id}:{NEWS_FEATURE_VERSION}:{ELIGIBILITY_VERSION}")
    with ledger.connection:
        ledger.connection.execute(
            """INSERT INTO derived_market_snapshots VALUES
            (?,?,?,?,NULL,?,?,?,?,?,?,?,?,?,?)""",
            (market_id, decision_id, decision_time.isoformat(), snapshot["snapshot_hash"],
             "LIVE_OOS", created_at.isoformat(), FEATURE_VERSION, U5_VERSION, snapshot["u5"],
             json.dumps(features, sort_keys=True, separators=(",", ":")), snapshot["data_health"],
             json.dumps(snapshot["reason_codes"], separators=(",", ":")), source_hash, market_hash),
        )
        ledger.connection.execute(
            """INSERT INTO derived_news_feature_snapshots VALUES
            (?,?,?,NULL,?,?,?,?,?,?,?,?,?,?,?)""",
            (news_id, decision_id, decision_time.isoformat(), "LIVE_OOS", created_at.isoformat(),
             NEWS_FEATURE_VERSION, ELIGIBILITY_VERSION,
             json.dumps(news["features"], sort_keys=True, separators=(",", ":")),
             news["model_visible_items"], news["news_exposed"], news["distinct_news_clusters"],
             news["distinct_event_types"], news["source_evidence_hash"], news_hash),
        )
        _lane(ledger.connection, "DECISION", decision_id, "LIVE_OOS", created_at, market_hash)
        _lane(ledger.connection, "LEGACY_PREDICTIONS", decision_id, "LEGACY_ENGINEERING",
              created_at, snapshot["snapshot_hash"])
        _lane(ledger.connection, "DERIVED_MARKET", market_id, "LIVE_OOS", created_at, market_hash)
        _lane(ledger.connection, "DERIVED_NEWS", news_id, "LIVE_OOS", created_at, news_hash)
        predictions = append_live_predictions_v2(
            ledger, decision_id=decision_id, decision_time=decision_time,
            created_at=created_at,
            market_snapshot={"features_json": json.dumps(features), "u5": snapshot["u5"],
                             "data_health": snapshot["data_health"], "output_hash": market_hash},
            news_snapshot={"features_json": json.dumps(news["features"]), "output_hash": news_hash},
        )
        append_lot_predictions(
            ledger, decision_id=decision_id, decision_time=decision_time,
            created_at=created_at,
            market_snapshot={"features_json": json.dumps(features),
                             "data_health": snapshot["data_health"],
                             "output_hash": market_hash},
        )
    return predictions


def append_live_outcome_v2(ledger, *, decision_id: str, decision_time: datetime,
                           appended_at: datetime, label, source_evidence_hash: str) -> bool:
    epoch = evaluation_epoch(ledger.connection)
    if epoch is None or decision_time < epoch:
        return False
    existing = ledger.connection.execute(
        """SELECT 1 FROM derived_outcomes
        WHERE source_decision_id=? AND label_version=?""",
        (decision_id, LABEL_VERSION),
    ).fetchone()
    if existing is not None:
        return False
    values = label.payload()
    hashable_values = {
        key: (value.isoformat() if isinstance(value, datetime) else value)
        for key, value in values.items()
    }
    payload = {"decision_id": decision_id, "decision_time": decision_time.isoformat(),
               "label_version": LABEL_VERSION, **hashable_values,
               "source_evidence_hash": source_evidence_hash}
    output_hash = canonical_hash(payload)
    outcome_id = _uuid("derived-outcome", f"{decision_id}:{LABEL_VERSION}")
    with ledger.connection:
        ledger.connection.execute(
            f"INSERT INTO derived_outcomes VALUES ({','.join('?' for _ in range(34))})",
            (outcome_id, decision_id, decision_time.isoformat(), None, "LIVE_OOS", appended_at.isoformat(),
             LABEL_VERSION, values["outcome_status"],
             json.dumps(values["reason_codes"], separators=(",", ":")),
             values["entry_event_time"].isoformat() if values["entry_event_time"] else None,
             values["entry_received_time"].isoformat() if values["entry_received_time"] else None,
             values["entry_receipt_delay_seconds"],
             values["exit_event_time"].isoformat() if values["exit_event_time"] else None,
             values["exit_received_time"].isoformat() if values["exit_received_time"] else None,
             values["exit_receipt_delay_seconds"], values["maximum_event_gap"],
             values["maximum_receipt_gap"], values["quote_coverage"], values["ambiguity_state"],
             values["gross_midpoint_direction_move"], values["long_quote_return"],
             values["short_quote_return"], values["spread_quote_cost"], values["long_mfe"],
             values["long_mae"], values["short_mfe"], values["short_mae"], values["maximum_spread"],
             values["break_even_commission_long"], values["break_even_commission_short"],
             values["commission_status"], values["slippage_status"], source_evidence_hash, output_hash),
        )
        _lane(ledger.connection, "DERIVED_OUTCOME", outcome_id, "LIVE_OOS", appended_at, output_hash)
        predictions = ledger.connection.execute(
            "SELECT * FROM predictions_v2 WHERE source_decision_id=? ORDER BY model_version",
            (decision_id,),
        ).fetchall()
        if values["outcome_status"] == "VALID":
            market = ledger.connection.execute(
                "SELECT * FROM derived_market_snapshots WHERE source_decision_id=? AND feature_version=?",
                (decision_id, FEATURE_VERSION),
            ).fetchone()
            u5 = float(market["u5"]) if market and market["u5"] else None
            target = values["gross_midpoint_direction_move"] / u5 if u5 else None
            for prediction in predictions:
                action = prediction["recommended_action"]
                value = (values["long_quote_return"] if action == "LONG" else
                         values["short_quote_return"] if action == "SHORT" else 0.0)
                predicted = prediction["predicted_direction_u5"]
                residual = target - predicted if target is not None and predicted is not None else None
                squared = residual * residual if residual is not None else None
                correct = None if action == "WAIT" else int(value > 0)
                high_error = int(correct == 0 and prediction["calibration_status"] == "CALIBRATED")
                score_hash = canonical_hash((decision_id, prediction["model_version"], value,
                                             target, residual, squared, correct, high_error))
                ledger.connection.execute(
                    "INSERT INTO prediction_scores_v2 VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (decision_id, prediction["model_version"], appended_at.isoformat(), value,
                     target, residual, squared, correct, high_error, score_hash),
                )
            features = json.loads(market["features_json"]) if market else {}
            if (market and values["ambiguity_state"] == "NONE"
                    and market["u5"] is not None and market["data_health"] == "OK"
                    and all(features.get(name) is not None for name in MARKET_FEATURES)
                    and len(predictions) == ledger.connection.execute(
                        "SELECT count(*) FROM prediction_scores_v2 WHERE source_decision_id=?",
                        (decision_id,),
                    ).fetchone()[0]):
                news = ledger.connection.execute(
                    "SELECT * FROM derived_news_feature_snapshots WHERE source_decision_id=?",
                    (decision_id,),
                ).fetchone()
                ledger.connection.execute(
                    "INSERT INTO training_eligibility_v2 VALUES (?,?,?,?,?,?,?,?)",
                    (_uuid("training-v2", decision_id), decision_id, "LIVE_OOS",
                     appended_at.isoformat(), TRAINING_ELIGIBILITY_VERSION, market["output_hash"],
                     output_hash, news["output_hash"] if news else None),
                )
            append_execution_examples(
                ledger, decision_id=decision_id, appended_at=appended_at,
                label=label, source_hash=source_evidence_hash,
            )
    return True
