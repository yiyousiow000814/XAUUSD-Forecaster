"""Read-only Live OOS learning curves; repaired seed never enters scores."""

from __future__ import annotations

import json
import math
import statistics
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from .execution_costs import COMMISSION_STATUS, SLIPPAGE_STATUS, net_shadow_log_return
from .news_contracts import (
    CURRENT_NEWS_CONTRACT,
    generation_matches_contract,
)


MAX_CURVE_POINTS = 1200
OUTCOME_SETTLEMENT_WINDOW = timedelta(minutes=35)


def _bounded_curve(points: list[dict], max_points: int = MAX_CURVE_POINTS) -> list[dict]:
    """Bound dashboard transfer size without changing the append-only ledger.

    The first/last point, every model-generation boundary, and local extrema are
    retained.  This is a display envelope only; cumulative scores remain based
    on every matured OOS row.
    """
    if len(points) <= max_points:
        return points
    mandatory = {0, len(points) - 1}
    mandatory.update(
        index for index, point in enumerate(points)
        if point.get("model_version")
    )
    if len(mandatory) >= max_points:
        ordered = sorted(mandatory)
        stride = (len(ordered) - 1) / max(1, max_points - 1)
        return [points[ordered[round(index * stride)]] for index in range(max_points)]

    candidates = [index for index in range(1, len(points) - 1) if index not in mandatory]
    bucket_count = max(1, (max_points - len(mandatory)) // 2)
    selected = set(mandatory)
    for bucket in range(bucket_count):
        start = bucket * len(candidates) // bucket_count
        end = (bucket + 1) * len(candidates) // bucket_count
        indexes = candidates[start:end]
        if not indexes:
            continue
        selected.add(min(indexes, key=lambda index: points[index]["cumulative_quote_return"]))
        selected.add(max(indexes, key=lambda index: points[index]["cumulative_quote_return"]))
    return [points[index] for index in sorted(selected)]


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
        long_value = net_shadow_log_return(row["long_quote_return"] or 0.0)
        short_value = net_shadow_log_return(row["short_quote_return"] or 0.0)
        oracle = max(0.0, long_value, short_value)
        policy = _net_row_value(row)
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


def _net_row_value(row) -> float:
    value = float(row["value_quote_return"] or 0.0)
    return 0.0 if row["recommended_action"] == "WAIT" else net_shadow_log_return(value)


def _net_direction_correct(row) -> int | None:
    if row["recommended_action"] == "WAIT" or row["value_quote_return"] is None:
        return None
    return int(_net_row_value(row) > 0.0)


def _is_fixed_30m_grid(value: str) -> bool:
    """Return True for the predeclared non-overlapping :00/:30 evaluation grid."""
    try:
        instant = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return False
    return instant.minute in (0, 30) and instant.second == 0


def _cadence_metrics(rows) -> dict:
    """Expose overlapping 5m and fixed non-overlapping 30m evaluations side by side."""
    result = {}
    for name, cadence_rows in (
        ("EVERY_5M", list(rows)),
        ("FIXED_30M", [row for row in rows if _is_fixed_30m_grid(row["decision_time"])]),
    ):
        values = [_net_row_value(row) for row in cadence_rows]
        daily = defaultdict(float)
        for row in cadence_rows:
            daily[row["decision_time"][:10]] += _net_row_value(row)
        result[name] = {
            "oos_rows": len(cadence_rows),
            "distinct_days": len(daily),
            **_selection_metrics(cadence_rows),
            **_metrics(values, daily),
        }
    return result


def _version_cadence_metrics(
    rows, lifecycle_status: str, observed_at: datetime,
) -> dict:
    """Describe whether each cadence has results, pending scores, or no run."""
    all_rows = list(rows)
    scored_rows = [row for row in all_rows if row["value_quote_return"] is not None]
    result = _cadence_metrics(scored_rows)
    for name, cadence_rows in (
        ("EVERY_5M", all_rows),
        ("FIXED_30M", [
            row for row in all_rows if _is_fixed_30m_grid(row["decision_time"])
        ]),
    ):
        scored_count = sum(
            row["value_quote_return"] is not None for row in cadence_rows
        )
        overdue_count = 0
        for row in cadence_rows:
            if row["value_quote_return"] is not None:
                continue
            decision_time = datetime.fromisoformat(
                row["decision_time"].replace("Z", "+00:00")
            )
            if decision_time.tzinfo is None:
                decision_time = decision_time.replace(tzinfo=timezone.utc)
            if decision_time + OUTCOME_SETTLEMENT_WINDOW <= observed_at:
                overdue_count += 1
        if scored_count:
            evaluation_status = "HAS_RESULTS"
        elif cadence_rows and overdue_count == len(cadence_rows):
            evaluation_status = "OUTCOME_UNAVAILABLE"
        elif cadence_rows:
            evaluation_status = "AWAITING_OUTCOME"
        elif lifecycle_status == "LATEST":
            evaluation_status = "AWAITING_FIRST_PREDICTION"
        else:
            evaluation_status = "NO_PREDICTIONS"
        result[name].update({
            "prediction_rows": len(cadence_rows),
            "unscored_oos_rows": len(cadence_rows) - scored_count,
            "overdue_oos_rows": overdue_count,
            "evaluation_status": evaluation_status,
        })
    return result


def learning_curve_payload(connection, observed_at: datetime | None = None) -> dict:
    from xauusd_forecaster.decision.inference import news_model_activation_status
    from .training_v2 import NEWS_MIN_EXPOSED_ROWS
    observed_at = observed_at or datetime.now(timezone.utc)
    if observed_at.tzinfo is None:
        observed_at = observed_at.replace(tzinfo=timezone.utc)
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
    active_generation_row = connection.execute(
        """SELECT g.* FROM news_model_generation_activations_v1 a
        JOIN news_model_generations_v1 g USING(generation_id)
        ORDER BY a.activated_at DESC,a.activation_id DESC LIMIT 1"""
    ).fetchone()
    active_generation = dict(active_generation_row) if active_generation_row else None
    if active_generation is not None:
        auxiliary_members = connection.execute(
            "SELECT count(*) FROM news_model_generation_aux_members_v1 "
            "WHERE generation_id=?",
            (active_generation["generation_id"],),
        ).fetchone()[0]
        active_generation["auxiliary_member_count"] = int(auxiliary_members)
    active_is_current = generation_matches_contract(
        active_generation, CURRENT_NEWS_CONTRACT,
    )
    active_updates = (
        connection.execute(
            """SELECT u.* FROM (
                SELECT generation_id,model_version FROM news_model_generation_members_v1
                UNION ALL
                SELECT generation_id,model_version FROM news_model_generation_aux_members_v1
            ) m
            JOIN model_updates_v2 u USING(model_version)
            WHERE m.generation_id=? ORDER BY u.model_identity""",
            (active_generation["generation_id"],),
        ).fetchall()
        if active_generation else list(reversed(updates))
    )
    news_activation = news_model_activation_status(active_updates)
    current_exposed_rows = connection.execute(
        """SELECT count(DISTINCT n.source_decision_id)
        FROM derived_news_feature_snapshots n
        JOIN training_eligibility_v2 e USING(source_decision_id)
        JOIN derived_outcomes o USING(source_decision_id)
        WHERE n.feature_version=? AND n.eligibility_version=?
          AND n.news_exposed=1 AND o.outcome_status='VALID'""",
        (
            CURRENT_NEWS_CONTRACT.feature_version,
            CURRENT_NEWS_CONTRACT.eligibility_version,
        ),
    ).fetchone()[0]
    current_event_count = connection.execute(
        """SELECT count(DISTINCT event_id)
        FROM news_decision_event_snapshots_v1 WHERE policy_version=?""",
        (CURRENT_NEWS_CONTRACT.policy_version,),
    ).fetchone()[0]
    transition_state = (
        "NO_ACTIVE_GENERATION" if active_generation is None
        else "CURRENT" if active_is_current
        else "BLOCKED_RETIRED_GENERATION"
    )
    news_contract_transition = {
        "state": transition_state,
        "active_contract": ({
            "feature_version": active_generation["feature_version"],
            "eligibility_version": active_generation["eligibility_version"],
            "policy_version": active_generation["policy_version"],
        } if active_generation else None),
        "target_contract": {
            "name": CURRENT_NEWS_CONTRACT.name,
            "feature_version": CURRENT_NEWS_CONTRACT.feature_version,
            "eligibility_version": CURRENT_NEWS_CONTRACT.eligibility_version,
            "policy_version": CURRENT_NEWS_CONTRACT.policy_version,
        },
        "current_contract_exposed_rows": int(current_exposed_rows),
        "minimum_exposed_rows": NEWS_MIN_EXPOSED_ROWS,
        "missing_exposed_rows": max(
            0, NEWS_MIN_EXPOSED_ROWS - int(current_exposed_rows)
        ),
        "current_contract_distinct_events": int(current_event_count),
    }
    generation_by_version = {
        row["model_version"]: row["generation_id"]
        for row in connection.execute(
            """SELECT generation_id,model_version FROM news_model_generation_members_v1
            UNION ALL SELECT generation_id,model_version
            FROM news_model_generation_aux_members_v1"""
        )
    }
    active_generation_versions = {
        row["model_version"] for row in connection.execute(
            """SELECT model_version FROM news_model_generation_members_v1
            WHERE generation_id=? UNION ALL SELECT model_version
            FROM news_model_generation_aux_members_v1 WHERE generation_id=?""",
            (active_generation["generation_id"], active_generation["generation_id"]),
        )
    } if active_generation else set()
    weight_rows = connection.execute(
        """SELECT evidence_lane,count(*) exposure_count,
                  count(DISTINCT event_id) event_count,
                  sum(normalized_event_weight) normalized_weight
        FROM news_training_weight_receipts_v1
        WHERE generation_id=? GROUP BY evidence_lane""",
        (active_generation["generation_id"] if active_generation else "",),
    ).fetchall()
    weight_summary = {
        row["evidence_lane"]: {
            "decision_event_exposures": int(row["exposure_count"]),
            "effective_event_count": int(row["event_count"]),
            "maximum_event_weight_share": (
                1.0 / int(row["event_count"]) if int(row["event_count"]) else None
            ),
            "total_event_budget": float(row["normalized_weight"] or 0.0),
        }
        for row in weight_rows
    }
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
        if active_generation:
            if update["model_version"] in active_generation_versions:
                active_versions[identity].append(update["model_version"])
        elif not active_versions[identity]:
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
        cadence_metrics = _cadence_metrics(scored)
        values = [_net_row_value(row) for row in scored]
        daily = defaultdict(float)
        for row in scored:
            daily[row["decision_time"][:10]] += _net_row_value(row)
        metrics = _metrics(values, daily)
        latest = rows[-1] if rows else None
        active_rank = (
            active_versions[update["model_identity"]].index(update["model_version"]) + 1
            if update["model_version"] in active_versions[update["model_identity"]]
            else None
        )
        models.append({
            "model_version": update["model_version"], "model_identity": update["model_identity"],
            "generation_id": generation_by_version.get(update["model_version"]),
            "model_stage": update["model_stage"], "training_rows": update["training_rows"],
            "training_cutoff": update["training_cutoff"], "created_at": update["created_at"],
            "cadence_metrics": cadence_metrics,
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
                statistics.mean(
                    result for row in scored
                    if (result := _net_direction_correct(row)) is not None
                )
                if any(_net_direction_correct(row) is not None for row in scored) else None
            ),
            "high_confidence_errors": sum(
                int(
                    row["calibration_status"] == "CALIBRATED"
                    and _net_direction_correct(row) == 0
                )
                for row in scored
            ),
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
                "LATEST" if active_rank == 1 else "ARCHIVED"
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
        values = [_net_row_value(row) for row in scored]
        daily = defaultdict(float)
        for row in scored:
            daily[row["decision_time"][:10]] += _net_row_value(row)
        group_number = identity_group_seen[identity]
        total_groups = identity_group_counts[identity]
        lifecycle_status = (
            "LATEST" if group_number == total_groups else
            "PREVIOUS" if group_number == total_groups - 1 else "ARCHIVED"
        )
        cadence_metrics = _version_cadence_metrics(
            rows, lifecycle_status, observed_at,
        )
        primary_metrics = cadence_metrics["EVERY_5M"]
        version_groups.append({
            "model_identity": identity,
            "training_dataset_hash": dataset_hash,
            "generation": group_number,
            "lifecycle_status": lifecycle_status,
            "created_at": group_updates[0]["created_at"],
            "latest_rebuild_at": group_updates[-1]["created_at"],
            "training_rows": group_updates[0]["training_rows"],
            "artifact_rebuilds": max(0, len(group_updates) - 1),
            "model_versions": versions,
            "subsequent_oos_rows": primary_metrics["oos_rows"],
            "subsequent_prediction_rows": primary_metrics["prediction_rows"],
            "unscored_oos_rows": primary_metrics["unscored_oos_rows"],
            "overdue_oos_rows": primary_metrics["overdue_oos_rows"],
            "evaluation_status": primary_metrics["evaluation_status"],
            "distinct_days": primary_metrics["distinct_days"],
            "cadence_metrics": cadence_metrics,
            **_selection_metrics(scored),
            **_metrics([_net_row_value(row) for row in scored], {
                day: sum(_net_row_value(row) for row in scored if row["decision_time"][:10] == day)
                for day in {row["decision_time"][:10] for row in scored}
            }),
        })

    rolling_processes = []
    for identity in (
        "MARKET_ONLY", "NEWS_RESIDUAL", "FULL",
        "BROAD_NEWS_RESIDUAL", "BROAD_FULL", "NEWS_ONLY",
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
        cadence_metrics = _cadence_metrics(rows)
        primary_metrics = cadence_metrics["EVERY_5M"]
        latest = rows[-1] if rows else None
        rolling_processes.append({
            "model_identity": identity,
            "history_cutoff": None,
            "active_model_versions": active_versions.get(identity, []),
            "oos_rows": primary_metrics["oos_rows"],
            "distinct_days": primary_metrics["distinct_days"],
            "cadence_metrics": cadence_metrics,
            "calibration_status": latest["calibration_status"] if latest else "NO_LIVE_OOS",
            "calibration_rows": latest["calibration_rows"] if latest else 0,
            "calibration_effective_blocks": (
                latest["calibration_effective_blocks"] if latest else 0
            ),
            "calibration_distinct_days": latest["calibration_distinct_days"] if latest else 0,
            **_selection_metrics(rows),
            **_metrics(
                [_net_row_value(row) for row in rows],
                {day: sum(_net_row_value(row) for row in rows if row["decision_time"][:10] == day)
                 for day in {row["decision_time"][:10] for row in rows}},
            ),
        })

    identity_curves = []
    for identity in (
        "CHAMPION_0", "MARKET_ONLY", "NEWS_RESIDUAL", "FULL",
        "BROAD_NEWS_RESIDUAL", "BROAD_FULL", "NEWS_ONLY",
    ):
        if identity == "CHAMPION_0":
            rows = connection.execute(
                """SELECT p.decision_time,p.recommended_action,s.value_quote_return
                FROM predictions_v2 p
                JOIN prediction_scores_v2 s USING(source_decision_id,model_version)
                WHERE p.model_identity=? ORDER BY p.decision_time""", (identity,)
            ).fetchall()
        else:
            rows = connection.execute(
                """WITH ranked AS (
                    SELECT p.source_decision_id,p.decision_time,p.model_version,
                           p.recommended_action,
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
                SELECT decision_time,model_version,recommended_action,
                       training_dataset_hash,training_rows,
                       value_quote_return FROM ranked
                WHERE version_rank=1 ORDER BY decision_time""", (identity,)
            ).fetchall()
        def build_points(source_rows):
            cumulative = 0.0
            result = []
            previous_generation = None
            for row in source_rows:
                cumulative += _net_row_value(row)
                model_version = row["model_version"] if identity != "CHAMPION_0" else "always-wait-v1"
                point = {"decision_time": row["decision_time"], "cumulative_quote_return": cumulative}
                generation = row["training_dataset_hash"] if identity != "CHAMPION_0" else "always-wait"
                if generation != previous_generation:
                    point["model_version"] = model_version
                    point["training_dataset_hash"] = generation
                    point["training_rows"] = row["training_rows"] if identity != "CHAMPION_0" else 0
                    previous_generation = generation
                result.append(point)
            return result

        points = build_points(rows)
        points_30m = build_points([row for row in rows if _is_fixed_30m_grid(row["decision_time"])])
        bounded_points = _bounded_curve(points)
        bounded_points_30m = _bounded_curve(points_30m)
        identity_curves.append({
            "model_identity": identity,
            "source_point_count": len(points),
            "chart_point_count": len(bounded_points),
            "chart_downsampled": len(bounded_points) < len(points),
            "points": bounded_points,
            "source_point_count_30m": len(points_30m),
            "chart_point_count_30m": len(bounded_points_30m),
            "chart_downsampled_30m": len(bounded_points_30m) < len(points_30m),
            "points_30m": bounded_points_30m,
        })

    paired = connection.execute(
        """WITH ranked AS (
            SELECT p.source_decision_id,p.decision_time,p.model_identity,
                   p.recommended_action,s.value_quote_return,
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
        SELECT f.decision_time,f.value_quote_return AS full_value,
               f.recommended_action AS full_action,
               m.value_quote_return AS market_value,
               m.recommended_action AS market_action
        FROM latest f JOIN latest m USING(source_decision_id)
        WHERE f.model_identity='FULL' AND m.model_identity='MARKET_ONLY'
        ORDER BY f.decision_time"""
    ).fetchall()
    cumulative = 0.0
    incremental = []
    for row in paired:
        delta = (
            (0.0 if row["full_action"] == "WAIT" else net_shadow_log_return(row["full_value"]))
            - (0.0 if row["market_action"] == "WAIT" else net_shadow_log_return(row["market_value"]))
        )
        cumulative += delta
        incremental.append({"decision_time": row["decision_time"], "paired_delta": delta,
                            "cumulative_delta": cumulative})

    broad_paired = connection.execute(
        """WITH ranked AS (
            SELECT p.source_decision_id,p.decision_time,p.model_identity,
                   p.recommended_action,s.value_quote_return,
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
        SELECT b.decision_time,b.value_quote_return AS broad_value,
               b.recommended_action AS broad_action,
               o.value_quote_return AS core_value,
               o.recommended_action AS core_action
        FROM latest b JOIN latest o USING(source_decision_id)
        WHERE b.model_identity='BROAD_FULL' AND o.model_identity='FULL'
        ORDER BY b.decision_time"""
    ).fetchall()
    broad_cumulative = 0.0
    broad_incremental = []
    for row in broad_paired:
        delta = (
            (0.0 if row["broad_action"] == "WAIT" else net_shadow_log_return(row["broad_value"]))
            - (0.0 if row["core_action"] == "WAIT" else net_shadow_log_return(row["core_value"]))
        )
        broad_cumulative += delta
        broad_incremental.append({
            "decision_time": row["decision_time"], "paired_delta": delta,
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
        "training_generation_count": connection.execute(
            "SELECT count(*) FROM news_model_generations_v1"
        ).fetchone()[0] or len(market_training_hashes),
        "training_run_count": training_run_count,
        "recovery_rebuild_count": max(0, training_run_count - len(market_training_hashes)),
        "next_training_threshold": 96 if complete < 96 else 200 if complete < 200 else ((complete // 50) + 1) * 50,
        "commission_status": COMMISSION_STATUS, "slippage_status": SLIPPAGE_STATUS,
        "models": models, "version_groups": version_groups,
        "active_generation": active_generation,
        "news_contract_transition": news_contract_transition,
        "news_training_evidence": {
            "raw_article_revisions": connection.execute(
                "SELECT count(*) FROM news_revisions"
            ).fetchone()[0],
            "distinct_articles": connection.execute(
                "SELECT count(*) FROM (SELECT DISTINCT source,source_item_id FROM news_revisions)"
            ).fetchone()[0],
            "eligible_event_versions": connection.execute(
                "SELECT count(*) FROM news_event_catalog_v1"
            ).fetchone()[0],
            "distinct_eligible_events": connection.execute(
                "SELECT count(DISTINCT event_id) FROM news_event_catalog_v1"
            ).fetchone()[0],
            "decision_event_exposures": connection.execute(
                "SELECT count(*) FROM news_decision_event_snapshots_v1"
            ).fetchone()[0],
            "active_generation_weights": weight_summary,
        },
        "rolling_processes": rolling_processes,
        "news_model_activation": news_activation,
        "zero_return_baseline": {
            "label": "零收益安全基准", "model_identity": "CHAMPION_0",
            "cumulative_quote_return": 0.0, "trained": False, "uses_ai": False,
        },
        "identity_curves": identity_curves, "full_minus_market": incremental,
        "broad_full_minus_core_full": broad_incremental,
        "disclaimer": "早期曲线用于观察学习过程，不代表已证明盈利。",
    }
