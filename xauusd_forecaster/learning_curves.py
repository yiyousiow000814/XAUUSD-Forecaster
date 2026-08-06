"""Read-only Live OOS learning curves; repaired seed never enters scores."""

from __future__ import annotations

import json
import math
import statistics
from collections import defaultdict
from datetime import datetime


def _stage(complete: int, live_rows: int, days: int) -> str:
    if complete < 30:
        return "ENGINEERING"
    if complete < 96:
        return "EARLY_LEARNING"
    if complete < 200:
        return "PREVIEW"
    if days < 20:
        return "INITIAL_SHADOW"
    if days < 60:
        return "RESEARCH_CANDIDATE"
    return "HIGHER_CONFIDENCE"


def _metrics(values: list[float], days: dict[str, float]) -> dict:
    gains = sum(value for value in values if value > 0)
    losses = sum(value for value in values if value < 0)
    pf = gains / abs(losses) if losses < 0 else None
    equity = peak = 0.0
    max_drawdown = 0.0
    for value in values:
        equity += value
        peak = max(peak, equity)
        max_drawdown = max(max_drawdown, peak - equity)
    sharpe = None
    daily = [value for _, value in sorted(days.items())]
    if len(daily) >= 5 and statistics.stdev(daily) > 0:
        sharpe = statistics.mean(daily) / statistics.stdev(daily) * math.sqrt(252.0)
    return {"cumulative_quote_return": sum(values), "average_quote_return": statistics.mean(values) if values else None,
            "profit_factor_quote_adjusted": pf, "max_drawdown_quote_return": max_drawdown,
            "sharpe_quote_adjusted": sharpe}


def _selection_metrics(rows) -> dict:
    """Score abstention without using hindsight to choose a live action."""
    if not rows:
        return {
            "coverage_rate": None, "average_oracle_regret": None,
            "wait_opportunity_cost": 0.0,
        }
    regrets = []
    wait_opportunity_cost = 0.0
    directional = 0
    for row in rows:
        long_value = float(row["long_quote_return"] or 0.0)
        short_value = float(row["short_quote_return"] or 0.0)
        oracle = max(0.0, long_value, short_value)
        policy = float(row["value_quote_return"] or 0.0)
        regrets.append(oracle - policy)
        if row["recommended_action"] == "WAIT":
            wait_opportunity_cost += oracle
        else:
            directional += 1
    return {
        "coverage_rate": directional / len(rows),
        "average_oracle_regret": statistics.mean(regrets),
        "wait_opportunity_cost": wait_opportunity_cost,
    }


def learning_curve_payload(connection) -> dict:
    epoch = connection.execute(
        "SELECT * FROM evaluation_epochs ORDER BY created_at DESC LIMIT 1"
    ).fetchone()
    counts = {row["evidence_lane"]: row["n"] for row in connection.execute(
        "SELECT evidence_lane,count(*) n FROM training_eligibility_v2 GROUP BY evidence_lane"
    )}
    complete = sum(counts.values())
    live_rows = counts.get("LIVE_OOS", 0)
    seed_rows = counts.get("REPAIRED_SEED", 0)
    distinct_days = connection.execute(
        "SELECT count(DISTINCT substr(decision_time,1,10)) FROM derived_outcomes WHERE outcome_status='VALID'"
    ).fetchone()[0]
    outcome_quality = {row["outcome_status"]: row["n"] for row in connection.execute(
        "SELECT outcome_status,count(*) n FROM derived_outcomes GROUP BY outcome_status"
    )}
    invalid_reason_counts = defaultdict(int)
    for row in connection.execute(
        "SELECT reason_codes_json FROM derived_outcomes WHERE outcome_status!='VALID'"
    ):
        for reason in json.loads(row["reason_codes_json"] or "[]"):
            invalid_reason_counts[reason] += 1
    news = connection.execute(
        """SELECT coalesce(sum(news_exposed),0), coalesce(max(distinct_news_clusters),0)
        FROM derived_news_feature_snapshots"""
    ).fetchone()
    updates = connection.execute(
        "SELECT * FROM model_updates_v2 ORDER BY created_at,model_identity"
    ).fetchall()
    # A model artifact can be rebuilt from the same immutable dataset during
    # recovery.  That is not a new learning generation and must not create a
    # fake reset/upgrade in the UI.
    market_training_hashes = []
    for update in updates:
        if update["model_identity"] == "MARKET_ONLY" \
                and update["training_dataset_hash"] not in market_training_hashes:
            market_training_hashes.append(update["training_dataset_hash"])
    training_run_count = sum(
        update["model_identity"] == "MARKET_ONLY" for update in updates
    )
    active_versions: dict[str, list[str]] = defaultdict(list)
    for update in reversed(updates):
        identity = update["model_identity"]
        if len(active_versions[identity]) < 2:
            active_versions[identity].append(update["model_version"])

    models = []
    for update in updates:
        rows = connection.execute(
            """SELECT p.decision_time,p.recommended_action,p.interval_width,
                      p.calibration_status,p.calibration_rows,p.calibration_effective_blocks,
                      p.calibration_distinct_days,s.value_quote_return,s.squared_error,
                      s.direction_correct,s.high_confidence_error,
                      o.long_quote_return,o.short_quote_return
            FROM predictions_v2 p LEFT JOIN prediction_scores_v2 s
              USING(source_decision_id,model_version)
            LEFT JOIN derived_outcomes o USING(source_decision_id)
            WHERE p.model_version=? AND p.decision_time>?
            ORDER BY p.decision_time""", (update["model_version"], update["created_at"])
        ).fetchall()
        scored = [row for row in rows if row["value_quote_return"] is not None]
        values = [float(row["value_quote_return"]) for row in scored]
        daily = defaultdict(float)
        for row in scored:
            daily[row["decision_time"][:10]] += float(row["value_quote_return"])
        metrics = _metrics(values, daily)
        latest = rows[-1] if rows else None
        active_rank = (
            active_versions[update["model_identity"]].index(update["model_version"]) + 1
            if update["model_version"] in active_versions[update["model_identity"]]
            else None
        )
        models.append({
            "model_version": update["model_version"], "model_identity": update["model_identity"],
            "model_stage": update["model_stage"], "training_rows": update["training_rows"],
            "training_cutoff": update["training_cutoff"], "created_at": update["created_at"],
            "subsequent_oos_rows": len(scored), "effective_blocks": len(daily),
            "distinct_days": len(daily), "wait_rate": (
                sum(row["recommended_action"] == "WAIT" for row in rows) / len(rows) if rows else None
            ),
            "long_frequency": sum(row["recommended_action"] == "LONG" for row in rows),
            "short_frequency": sum(row["recommended_action"] == "SHORT" for row in rows),
            "mean_squared_error": (
                statistics.mean(float(row["squared_error"]) for row in scored if row["squared_error"] is not None)
                if any(row["squared_error"] is not None for row in scored) else None
            ),
            "direction_accuracy": (
                statistics.mean(int(row["direction_correct"]) for row in scored if row["direction_correct"] is not None)
                if any(row["direction_correct"] is not None for row in scored) else None
            ),
            "high_confidence_errors": sum(int(row["high_confidence_error"] or 0) for row in scored),
            "interval_width": latest["interval_width"] if latest else None,
            "calibration_status": latest["calibration_status"] if latest else "NO_LIVE_OOS",
            "calibration_rows": latest["calibration_rows"] if latest else 0,
            "calibration_effective_blocks": latest["calibration_effective_blocks"] if latest else 0,
            "calibration_distinct_days": latest["calibration_distinct_days"] if latest else 0,
            "news_event_days": int(update["distinct_event_days"] or 0),
            "news_evidence_status": (
                "NOT_APPLICABLE" if update["model_identity"] == "MARKET_ONLY"
                else "EXPERIMENTAL_SINGLE_DAY" if int(update["distinct_event_days"] or 0) == 1
                else "EXPERIMENTAL_TWO_DAY" if int(update["distinct_event_days"] or 0) == 2
                else "STANDARD" if int(update["distinct_event_days"] or 0) >= 3
                else "INSUFFICIENT"
            ),
            "active_rank": active_rank,
            "lifecycle_status": (
                "LATEST" if active_rank == 1 else "PREVIOUS" if active_rank == 2 else "ARCHIVED"
            ),
            **_selection_metrics(scored),
            **metrics,
        })

    version_groups = []
    group_keys = []
    for update in updates:
        key = (update["model_identity"], update["training_dataset_hash"])
        if key not in group_keys:
            group_keys.append(key)
    identity_group_counts = defaultdict(int)
    for identity, _ in group_keys:
        identity_group_counts[identity] += 1
    identity_group_seen = defaultdict(int)
    for identity, dataset_hash in group_keys:
        identity_group_seen[identity] += 1
        group_updates = [
            row for row in updates
            if row["model_identity"] == identity
            and row["training_dataset_hash"] == dataset_hash
        ]
        versions = [row["model_version"] for row in group_updates]
        placeholders = ",".join("?" for _ in versions)
        rows = connection.execute(
            f"""WITH ranked AS (
                SELECT p.source_decision_id,p.decision_time,p.recommended_action,
                       s.value_quote_return,o.long_quote_return,o.short_quote_return,
                       row_number() OVER (
                           PARTITION BY p.source_decision_id,p.model_identity
                           ORDER BY u.created_at DESC,u.model_version DESC
                       ) AS version_rank
                FROM predictions_v2 p
                JOIN model_updates_v2 u USING(model_version)
                LEFT JOIN prediction_scores_v2 s USING(source_decision_id,model_version)
                LEFT JOIN derived_outcomes o USING(source_decision_id)
                WHERE p.model_version IN ({placeholders})
                  AND p.decision_time>?
            )
            SELECT * FROM ranked WHERE version_rank=1 ORDER BY decision_time""",
            (*versions, group_updates[0]["created_at"]),
        ).fetchall()
        scored = [row for row in rows if row["value_quote_return"] is not None]
        values = [float(row["value_quote_return"]) for row in scored]
        daily = defaultdict(float)
        for row in scored:
            daily[row["decision_time"][:10]] += float(row["value_quote_return"])
        group_number = identity_group_seen[identity]
        total_groups = identity_group_counts[identity]
        version_groups.append({
            "model_identity": identity,
            "training_dataset_hash": dataset_hash,
            "generation": group_number,
            "lifecycle_status": (
                "LATEST" if group_number == total_groups else
                "PREVIOUS" if group_number == total_groups - 1 else "ARCHIVED"
            ),
            "created_at": group_updates[0]["created_at"],
            "latest_rebuild_at": group_updates[-1]["created_at"],
            "training_rows": group_updates[0]["training_rows"],
            "artifact_rebuilds": max(0, len(group_updates) - 1),
            "model_versions": versions,
            "subsequent_oos_rows": len(scored),
            "distinct_days": len(daily),
            **_selection_metrics(scored),
            **_metrics(values, daily),
        })

    rolling_processes = []
    for identity in (
        "MARKET_ONLY", "NEWS_RESIDUAL", "FULL",
        "BROAD_NEWS_RESIDUAL", "BROAD_FULL",
    ):
        rows = connection.execute(
            """WITH ranked AS (
                SELECT p.source_decision_id,p.decision_time,p.recommended_action,
                       p.interval_width,p.calibration_status,
                       p.calibration_rows,p.calibration_effective_blocks,
                       p.calibration_distinct_days,s.value_quote_return,s.squared_error,
                       s.direction_correct,s.high_confidence_error,
                       o.long_quote_return,o.short_quote_return,
                       row_number() OVER (
                           PARTITION BY p.source_decision_id,p.model_identity
                           ORDER BY u.created_at DESC,u.model_version DESC
                       ) AS version_rank
                FROM predictions_v2 p
                JOIN prediction_scores_v2 s USING(source_decision_id,model_version)
                JOIN model_updates_v2 u USING(model_version)
                LEFT JOIN derived_outcomes o USING(source_decision_id)
                WHERE p.model_identity=? AND p.decision_time>u.created_at
            )
            SELECT * FROM ranked WHERE version_rank=1 ORDER BY decision_time""",
            (identity,),
        ).fetchall()
        values = [float(row["value_quote_return"]) for row in rows]
        daily = defaultdict(float)
        for row in rows:
            daily[row["decision_time"][:10]] += float(row["value_quote_return"])
        latest = rows[-1] if rows else None
        rolling_processes.append({
            "model_identity": identity,
            "active_model_versions": active_versions.get(identity, []),
            "oos_rows": len(rows),
            "distinct_days": len(daily),
            "calibration_status": latest["calibration_status"] if latest else "NO_LIVE_OOS",
            "calibration_rows": latest["calibration_rows"] if latest else 0,
            "calibration_effective_blocks": (
                latest["calibration_effective_blocks"] if latest else 0
            ),
            "calibration_distinct_days": latest["calibration_distinct_days"] if latest else 0,
            **_selection_metrics(rows),
            **_metrics(values, daily),
        })

    identity_curves = []
    for identity in (
        "CHAMPION_0", "MARKET_ONLY", "NEWS_RESIDUAL", "FULL",
        "BROAD_NEWS_RESIDUAL", "BROAD_FULL",
    ):
        if identity == "CHAMPION_0":
            rows = connection.execute(
                """SELECT p.decision_time,s.value_quote_return FROM predictions_v2 p
                JOIN prediction_scores_v2 s USING(source_decision_id,model_version)
                WHERE p.model_identity=? ORDER BY p.decision_time""", (identity,)
            ).fetchall()
        else:
            rows = connection.execute(
                """WITH ranked AS (
                    SELECT p.source_decision_id,p.decision_time,p.model_version,
                           u.training_dataset_hash,u.training_rows,s.value_quote_return,
                           row_number() OVER (
                               PARTITION BY p.source_decision_id,p.model_identity
                               ORDER BY u.created_at DESC,u.model_version DESC
                           ) AS version_rank
                    FROM predictions_v2 p
                    JOIN prediction_scores_v2 s USING(source_decision_id,model_version)
                    JOIN model_updates_v2 u USING(model_version)
                    WHERE p.model_identity=? AND p.decision_time>u.created_at
                )
                SELECT decision_time,model_version,training_dataset_hash,training_rows,
                       value_quote_return FROM ranked
                WHERE version_rank=1 ORDER BY decision_time""", (identity,)
            ).fetchall()
        cumulative = 0.0
        points = []
        previous_generation = None
        for row in rows:
            cumulative += float(row["value_quote_return"])
            model_version = (
                row["model_version"] if identity != "CHAMPION_0" else "always-wait-v1"
            )
            point = {
                "decision_time": row["decision_time"],
                "cumulative_quote_return": cumulative,
            }
            generation = (
                row["training_dataset_hash"] if identity != "CHAMPION_0"
                else "always-wait"
            )
            if generation != previous_generation:
                point["model_version"] = model_version
                point["training_dataset_hash"] = generation
                point["training_rows"] = (
                    row["training_rows"] if identity != "CHAMPION_0" else 0
                )
                previous_generation = generation
            points.append(point)
        identity_curves.append({"model_identity": identity, "points": points})

    paired = connection.execute(
        """WITH ranked AS (
            SELECT p.source_decision_id,p.decision_time,p.model_identity,
                   s.value_quote_return,
                   row_number() OVER (
                       PARTITION BY p.source_decision_id,p.model_identity
                       ORDER BY u.created_at DESC,u.model_version DESC
                   ) AS version_rank
            FROM predictions_v2 p
            JOIN prediction_scores_v2 s USING(source_decision_id,model_version)
            JOIN model_updates_v2 u USING(model_version)
            WHERE p.model_identity IN ('FULL','BROAD_FULL','MARKET_ONLY')
              AND p.decision_time>u.created_at
        ), latest AS (
            SELECT * FROM ranked WHERE version_rank=1
        )
        SELECT f.decision_time,f.value_quote_return-m.value_quote_return AS delta
        FROM latest f JOIN latest m USING(source_decision_id)
        WHERE f.model_identity='FULL' AND m.model_identity='MARKET_ONLY'
        ORDER BY f.decision_time"""
    ).fetchall()
    cumulative = 0.0
    incremental = []
    for row in paired:
        cumulative += float(row["delta"])
        incremental.append({"decision_time": row["decision_time"], "paired_delta": row["delta"],
                            "cumulative_delta": cumulative})

    broad_paired = connection.execute(
        """WITH ranked AS (
            SELECT p.source_decision_id,p.decision_time,p.model_identity,s.value_quote_return,
                   row_number() OVER (
                       PARTITION BY p.source_decision_id,p.model_identity
                       ORDER BY u.created_at DESC,u.model_version DESC
                   ) AS version_rank
            FROM predictions_v2 p
            JOIN prediction_scores_v2 s USING(source_decision_id,model_version)
            JOIN model_updates_v2 u USING(model_version)
            WHERE p.model_identity IN ('BROAD_FULL','FULL')
              AND p.decision_time>u.created_at
        ), latest AS (SELECT * FROM ranked WHERE version_rank=1)
        SELECT b.decision_time,b.value_quote_return-o.value_quote_return AS delta
        FROM latest b JOIN latest o USING(source_decision_id)
        WHERE b.model_identity='BROAD_FULL' AND o.model_identity='FULL'
        ORDER BY b.decision_time"""
    ).fetchall()
    broad_cumulative = 0.0
    broad_incremental = []
    for row in broad_paired:
        broad_cumulative += float(row["delta"])
        broad_incremental.append({
            "decision_time": row["decision_time"], "paired_delta": row["delta"],
            "cumulative_delta": broad_cumulative,
        })

    latest_models = {row["model_stage"]: row["model_version"] for row in connection.execute(
        "SELECT * FROM model_updates_v2 WHERE model_identity='MARKET_ONLY' ORDER BY created_at"
    )}
    return {
        "collection_epoch": epoch["collection_epoch"] if epoch else None,
        "evaluation_epoch_v2": epoch["evaluation_epoch"] if epoch else None,
        "legacy_engineering_rows": connection.execute("SELECT count(*) FROM training_eligibility").fetchone()[0],
        "repaired_seed_rows": seed_rows, "live_oos_rows": live_rows,
        "raw_matured_rows": connection.execute("SELECT count(*) FROM outcomes").fetchone()[0],
        "effective_30m_blocks": connection.execute(
            "SELECT count(DISTINCT substr(decision_time,1,13)||printf('%02d',(cast(substr(decision_time,15,2) as int)/30)*30)) FROM derived_outcomes WHERE outcome_status='VALID'"
        ).fetchone()[0],
        "distinct_trading_days": distinct_days, "news_exposed_rows": int(news[0]),
        "outcome_quality": {
            "valid": int(outcome_quality.get("VALID", 0)),
            "invalid": int(sum(
                count for status, count in outcome_quality.items() if status != "VALID"
            )),
            "reason_counts": dict(invalid_reason_counts),
        },
        "distinct_news_clusters": int(news[1]), "learning_stage": _stage(complete, live_rows, distinct_days),
        "current_preview_version": latest_models.get("PREVIEW_ONLY"),
        "current_shadow_version": latest_models.get("SHADOW"),
        "training_generation_count": len(market_training_hashes),
        "training_run_count": training_run_count,
        "recovery_rebuild_count": max(0, training_run_count - len(market_training_hashes)),
        "next_training_threshold": 96 if complete < 96 else 200 if complete < 200 else ((complete // 50) + 1) * 50,
        "commission_status": "UNCONFIGURED", "slippage_status": "UNAVAILABLE_SHADOW",
        "models": models, "version_groups": version_groups,
        "rolling_processes": rolling_processes,
        "zero_return_baseline": {
            "label": "零收益安全基准", "model_identity": "CHAMPION_0",
            "cumulative_quote_return": 0.0, "trained": False, "uses_ai": False,
        },
        "identity_curves": identity_curves, "full_minus_market": incremental,
        "broad_full_minus_official_full": broad_incremental,
        "disclaimer": "早期曲线用于观察学习过程，不代表已证明盈利。",
    }
