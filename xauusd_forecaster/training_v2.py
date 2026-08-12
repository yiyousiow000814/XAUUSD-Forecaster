"""Leakage-controlled Preview and Shadow training for repaired evidence."""

from __future__ import annotations

import json
import uuid
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

from .evidence_v2 import (
    ELIGIBILITY_VERSION, FEATURE_VERSION, LABEL_VERSION, NEWS_FEATURE_VERSION,
)
from .factors import NEWS_FEATURES
from .forward_ledger import canonical_hash
from .news_contracts import CURRENT_NEWS_CONTRACT, generation_matches_contract
from .news_evidence import BROAD_NEWS_FEATURES, EVIDENCE_POLICY_VERSION
from .news_features_v2 import EVIDENCE_GRADE_WEIGHT, aggregate_news_features_v2
from .ridge import RidgeArtifact, train_ridge
from .training import MARKET_FEATURES


UTC = timezone.utc
PREVIEW_ROWS = 96
SHADOW_ROWS = 200
RETRAIN_INTERVAL = 50
NEWS_MIN_EXPOSED_ROWS = 30
NEWS_MIN_CLUSTERS = 10
NEWS_EXPERIMENTAL_MIN_CLUSTERS = 1
NEWS_EXPERIMENTAL_MIN_EVENT_DAYS = 1
NEWS_MIN_EVENT_DAYS = 3
CROSSFIT_VERSION = "expanding-market-purge30m-v1"
EVENT_WEIGHTING_VERSION = "event-and-source-budget-v6-canonical-origin"
SOURCE_WEIGHT_BUDGET = 1.0
BROAD_MODEL_FEATURES = (*NEWS_FEATURES, *BROAD_NEWS_FEATURES)


def news_evidence_status(event_days: int, clusters: int = NEWS_MIN_CLUSTERS) -> str:
    """Label early news models without blocking observable Shadow learning."""
    if (event_days < NEWS_EXPERIMENTAL_MIN_EVENT_DAYS
            or clusters < NEWS_EXPERIMENTAL_MIN_CLUSTERS):
        return "INSUFFICIENT"
    if event_days >= NEWS_MIN_EVENT_DAYS and clusters >= NEWS_MIN_CLUSTERS:
        return "STANDARD"
    if event_days == 1:
        return "EXPERIMENTAL_SINGLE_DAY"
    if event_days == 2:
        return "EXPERIMENTAL_TWO_DAY"
    return "EXPERIMENTAL_SPARSE_CLUSTERS"


def _rows(ledger, cutoff: datetime):
    return ledger.connection.execute(
        """SELECT e.source_decision_id, e.evidence_lane, m.decision_time,
                  m.features_json, m.u5, m.output_hash AS market_hash,
                  n.features_json AS news_json, n.news_exposed,
                  n.distinct_news_clusters, n.output_hash AS news_hash,
                  o.gross_midpoint_direction_move, o.long_quote_return,
                  o.short_quote_return, o.output_hash AS outcome_hash
        FROM training_eligibility_v2 e
        JOIN derived_market_snapshots m
          ON m.source_decision_id=e.source_decision_id
        JOIN derived_news_feature_snapshots n
          ON n.source_decision_id=e.source_decision_id
        JOIN derived_outcomes o
          ON o.source_decision_id=e.source_decision_id
        WHERE e.eligible_at <= ? AND o.outcome_status='VALID'
          AND m.feature_version=? AND n.feature_version=?
          AND n.eligibility_version=? AND o.label_version=?
        ORDER BY m.decision_time, e.source_decision_id""",
        (cutoff.isoformat(), FEATURE_VERSION, NEWS_FEATURE_VERSION,
         ELIGIBILITY_VERSION, LABEL_VERSION),
    ).fetchall()


def complete_training_rows(ledger, cutoff: datetime) -> list[dict]:
    complete = []
    for row in _rows(ledger, cutoff):
        market = json.loads(row["features_json"])
        market_values = [market.get(name) for name in MARKET_FEATURES]
        if row["u5"] is None or any(value is None for value in market_values):
            continue
        target = float(row["gross_midpoint_direction_move"]) / float(row["u5"])
        values = [float(value) for value in market_values]
        if not np.isfinite(values).all() or not np.isfinite(target):
            continue
        decision_id = row["source_decision_id"]
        event_rows = ledger.connection.execute(
            """SELECT s.event_id,s.event_version_id,s.model_permission,s.raw_weight,
                       c.event_occurred_at,c.event_clock_source,c.event_time_precision,
                       c.evidence_grade,
                       coalesce(b.source_budget_id,c.canonical_source) AS source_budget_id
            FROM news_decision_event_snapshots_v1 s
            JOIN news_event_catalog_v1 c USING(event_version_id)
            LEFT JOIN news_event_source_budgets_v1 b USING(event_version_id)
            WHERE s.source_decision_id=? AND s.policy_version=?
            ORDER BY s.model_permission,s.event_id,s.event_version_id""",
            (decision_id, EVIDENCE_POLICY_VERSION),
        ).fetchall()
        if event_rows:
            event_snapshots = [dict(event) for event in event_rows]
        else:
            event_snapshots = aggregate_news_features_v2(
                ledger, datetime.fromisoformat(row["decision_time"])
            )["event_snapshots"]
        complete.append({
            "decision_id": decision_id, "lane": row["evidence_lane"],
            "decision_time": row["decision_time"], "market": values,
            "news": [float(json.loads(row["news_json"])[name]) for name in NEWS_FEATURES],
            "broad_news": [
                float(json.loads(row["news_json"]).get(name, 0.0))
                for name in BROAD_MODEL_FEATURES
            ],
            "target": target, "news_exposed": bool(row["news_exposed"]),
            "broad_news_exposed": bool(
                json.loads(row["news_json"]).get("broad_news_event_count", 0.0)
            ),
            "distinct_news_clusters": int(row["distinct_news_clusters"]),
            "official_events": [
                event for event in event_snapshots
                if event["model_permission"] == "OFFICIAL_MODEL"
            ],
            "broad_events": [
                event for event in event_snapshots
                if event["model_permission"] == "BROAD_MODEL"
            ],
            "receipt": (row["source_decision_id"], row["market_hash"], row["news_hash"], row["outcome_hash"]),
        })
    return complete


def _write_market_artifact(rows: list[dict], artifact_root: Path, cutoff: datetime,
                           stage: str, alpha: float = 100.0) -> tuple[str, RidgeArtifact, Path, str]:
    receipts = [row["receipt"] for row in rows]
    dataset_hash = canonical_hash(receipts)
    version = f"market-{stage.lower()}-{cutoff.astimezone(UTC).strftime('%Y%m%dT%H%M%SZ')}-{dataset_hash[:12]}"
    artifact = train_ridge(
        np.asarray([row["market"] for row in rows]), np.asarray([row["target"] for row in rows]),
        MARKET_FEATURES, alpha, dataset_hash,
    )
    path = artifact_root / version / "model.json"
    if not path.exists():
        artifact.write(path)
    return version, artifact, path, dataset_hash


def chronological_crossfit_market(ledger, rows: list[dict], artifact_root: Path,
                                  created_at: datetime) -> list[dict]:
    """Produce expanding-window predictions with a purged 30-minute boundary."""
    predictions = []
    minimum_train = 48
    fold_size = 24
    for start in range(minimum_train, len(rows), fold_size):
        test = rows[start:start + fold_size]
        if not test:
            break
        test_start = datetime.fromisoformat(test[0]["decision_time"])
        purge_cutoff = test_start - timedelta(minutes=30)
        train = [row for row in rows[:start] if datetime.fromisoformat(row["decision_time"]) < purge_cutoff]
        if len(train) < minimum_train:
            continue
        train_hash = canonical_hash([row["receipt"] for row in train])
        artifact = train_ridge(
            np.asarray([row["market"] for row in train]), np.asarray([row["target"] for row in train]),
            MARKET_FEATURES, 100.0, train_hash,
        )
        values = artifact.predict(np.asarray([row["market"] for row in test]))
        fold = start // fold_size
        for row, predicted in zip(test, values):
            residual = float(row["target"] - predicted)
            record = {
                "decision_id": row["decision_id"], "fold": fold,
                "training_cutoff": train[-1]["decision_time"], "purged_through": purge_cutoff.isoformat(),
                "prediction": float(predicted), "target": row["target"], "residual": residual,
                "artifact_hash": artifact.artifact_hash,
            }
            ledger.connection.execute(
                "INSERT OR IGNORE INTO market_crossfit_predictions VALUES (?,?,?,?,?,?,?,?,?,?)",
                (row["decision_id"], CROSSFIT_VERSION, fold, record["training_cutoff"],
                 record["purged_through"], record["prediction"], record["target"], residual,
                 artifact.artifact_hash, created_at.isoformat()),
            )
            predictions.append(record)
    return predictions


def _event_coverage(rows: list[dict], field: str) -> tuple[int, int]:
    versions: dict[str, dict] = {}
    for row in rows:
        for event in row.get(field, []):
            versions.setdefault(str(event["event_id"]), event)
    days = {
        str(event.get("event_occurred_at") or "")[:10]
        for event in versions.values() if event.get("event_occurred_at")
    }
    return len(versions), len(days)


def _event_budget_weights(
    rows: list[dict], field: str,
) -> tuple[np.ndarray, list[dict], list[dict], dict]:
    totals: dict[str, float] = defaultdict(float)
    event_budgets: dict[str, float] = defaultdict(float)
    reference_budgets: dict[str, float] = defaultdict(float)
    event_sources: dict[str, tuple[float, str]] = {}
    for row in rows:
        for event in row.get(field, []):
            event_id = str(event["event_id"])
            raw = max(0.0, float(event["raw_weight"]))
            grade = str(event.get("evidence_grade") or "PRIMARY")
            trust = EVIDENCE_GRADE_WEIGHT.get(grade, 1.0)
            totals[event_id] += raw
            # Reuse the existing freshness/confidence/novelty weight as the
            # event's total quality budget.  The previous equal-event
            # normalization cancelled decay across events and could give one
            # very late row more weight than several fresh rows.
            event_budgets[event_id] = max(event_budgets[event_id], raw)
            if trust > 0:
                reference_budgets[event_id] = max(
                    reference_budgets[event_id], raw / trust,
                )
            source_id = str(event.get("source_budget_id") or "unknown_source")
            if event_id not in event_sources or raw > event_sources[event_id][0]:
                event_sources[event_id] = (raw, source_id)
    if not totals:
        raise ValueError("news residual rows require at least one eligible event")
    source_unbounded: dict[str, float] = defaultdict(float)
    for event_id, budget in event_budgets.items():
        source_unbounded[event_sources[event_id][1]] += budget
    source_scales = {
        source_id: min(1.0, SOURCE_WEIGHT_BUDGET / total)
        for source_id, total in source_unbounded.items()
    }
    bounded_event_budgets = {
        event_id: budget * source_scales[event_sources[event_id][1]]
        for event_id, budget in event_budgets.items()
    }
    bounded_reference_budgets = {
        event_id: budget * source_scales[event_sources[event_id][1]]
        for event_id, budget in reference_budgets.items()
    }
    row_weights = []
    receipts = []
    for row in rows:
        budget = 0.0
        for event in row.get(field, []):
            event_id = str(event["event_id"])
            raw = float(event["raw_weight"])
            normalized = raw / totals[event_id] * bounded_event_budgets[event_id]
            budget += normalized
            receipts.append({
                "source_decision_id": row["decision_id"],
                "event_id": event_id,
                "event_version_id": str(event["event_version_id"]),
                "raw_weight": raw,
                "normalized_event_weight": normalized,
            })
        row_weights.append(budget)
    weights = np.asarray(row_weights, dtype=np.float64)
    reference_total = sum(bounded_reference_budgets.values())
    if reference_total <= 0:
        raise ValueError("news residual rows require trusted event evidence")
    weights *= len(weights) / reference_total
    effective_rows = float(weights.sum() ** 2 / np.square(weights).sum())
    total_event_budget = sum(bounded_event_budgets.values())
    shares = sorted(
        (budget / total_event_budget for budget in bounded_event_budgets.values()),
        reverse=True,
    )
    source_bounded = {
        source_id: total * source_scales[source_id]
        for source_id, total in source_unbounded.items()
    }
    source_shares = sorted(
        (budget / total_event_budget for budget in source_bounded.values()),
        reverse=True,
    )
    source_receipts = [{
        "source_budget_id": source_id,
        "unbounded_weight": source_unbounded[source_id],
        "bounded_weight": source_bounded[source_id],
    } for source_id in sorted(source_unbounded)]
    summary = {
        "raw_training_rows": len(rows),
        "distinct_event_count": len(totals),
        "effective_event_count": float(len(totals)),
        "effective_weighted_rows": effective_rows,
        "maximum_event_weight_share": shares[0],
        "top_three_event_weight_share": sum(shares[:3]),
        "distinct_source_count": len(source_bounded),
        "maximum_source_weight_share": source_shares[0],
        "top_three_source_weight_share": sum(source_shares[:3]),
        "total_sample_weight": float(weights.sum()),
    }
    return weights, receipts, source_receipts, summary


def _latest_generation(connection, stage: str):
    return connection.execute(
        """SELECT g.*,u.training_rows
        FROM news_model_generation_activations_v1 a
        JOIN news_model_generations_v1 g USING(generation_id)
        JOIN news_model_generation_members_v1 m USING(generation_id)
        JOIN model_updates_v2 u USING(model_version)
        WHERE g.model_stage=? AND m.model_identity='MARKET_ONLY'
        ORDER BY a.activated_at DESC LIMIT 1""",
        (stage,),
    ).fetchone()


def _write_manifest(path: Path, payload: dict) -> str:
    digest = canonical_hash(payload)
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=False)
        path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return digest


def _neutral_news_artifact(dataset_hash: str) -> RidgeArtifact:
    """Represent an evidence-empty official lane without inventing a signal."""
    return RidgeArtifact(
        feature_names=tuple(NEWS_FEATURES),
        means=tuple(0.0 for _ in NEWS_FEATURES),
        scales=tuple(1.0 for _ in NEWS_FEATURES),
        coefficients=tuple(0.0 for _ in NEWS_FEATURES),
        intercept=0.0,
        alpha=100.0,
        training_dataset_hash=dataset_hash,
        residual_std=0.0,
        training_rows=0,
        weighting_version=EVENT_WEIGHTING_VERSION,
        weight_summary={"status": "COLD_START_NO_OFFICIAL_EVIDENCE"},
    )


def train_due_v2(ledger, cutoff: datetime, artifact_root: str | Path) -> list[dict]:
    """Build and atomically activate five core models plus one diagnostic."""
    rows = complete_training_rows(ledger, cutoff)
    count = len(rows)
    if count < PREVIEW_ROWS:
        return [{"status": "ENGINEERING" if count < 30 else "EARLY_LEARNING",
                 "complete_rows": count, "next_threshold": PREVIEW_ROWS}]
    stage = "SHADOW" if count >= SHADOW_ROWS else "PREVIEW_ONLY"
    training_rows = rows if stage == "PREVIEW_ONLY" else rows[: count - (count % RETRAIN_INTERVAL)]
    official_rows = [row for row in training_rows if row.get("official_events")]
    broad_rows = [row for row in training_rows if row.get("broad_events")]
    official_events, official_days = _event_coverage(official_rows, "official_events")
    broad_events, broad_days = _event_coverage(broad_rows, "broad_events")
    evidence_status = news_evidence_status(official_days, official_events)
    broad_evidence_status = news_evidence_status(broad_days, broad_events)
    latest = _latest_generation(ledger.connection, stage)
    latest_uses_current_contract = generation_matches_contract(
        latest, CURRENT_NEWS_CONTRACT,
    )
    if (
        latest is not None
        and latest_uses_current_contract
        and len(training_rows) < int(latest["training_rows"]) + RETRAIN_INTERVAL
    ):
        return [{"status": "NOT_DUE", "complete_rows": count,
                 "generation_id": latest["generation_id"],
                 "next_threshold": int(latest["training_rows"]) + RETRAIN_INTERVAL}]
    official_cold_start = (
        len(official_rows) < NEWS_MIN_EXPOSED_ROWS or not official_events
    )
    if len(broad_rows) < NEWS_MIN_EXPOSED_ROWS or not broad_events:
        return [{
            "status": "NEWS_GENERATION_EVIDENCE_INSUFFICIENT",
            "official_exposed_rows": len(official_rows),
            "broad_exposed_rows": len(broad_rows),
            "official_events": official_events,
            "broad_events": broad_events,
            "official_evidence_status": evidence_status,
            "broad_evidence_status": broad_evidence_status,
            "active_generation_id": latest["generation_id"] if latest else None,
            "active_contract_current": latest_uses_current_contract,
            "target_feature_version": NEWS_FEATURE_VERSION,
            "target_eligibility_version": ELIGIBILITY_VERSION,
            "target_policy_version": EVIDENCE_POLICY_VERSION,
            "minimum_exposed_rows": NEWS_MIN_EXPOSED_ROWS,
        }]
    now = datetime.now(UTC)
    root = Path(artifact_root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    seed_rows = sum(row["lane"] == "REPAIRED_SEED" for row in training_rows)
    live_rows = sum(row["lane"] == "LIVE_OOS" for row in training_rows)
    market_version, market_artifact, market_path, market_hash = _write_market_artifact(
        training_rows, root, cutoff, stage
    )
    with ledger.connection:
        crossfit = chronological_crossfit_market(ledger, training_rows, root, now)
    crossfit_by_id = {row["decision_id"]: row for row in crossfit}
    official_residual = [row for row in official_rows if row["decision_id"] in crossfit_by_id]
    broad_residual = [row for row in broad_rows if row["decision_id"] in crossfit_by_id]
    if len(broad_residual) < NEWS_MIN_EXPOSED_ROWS or (
        not official_cold_start
        and len(official_residual) < NEWS_MIN_EXPOSED_ROWS
    ):
        return [{"status": "NEWS_GENERATION_CROSSFIT_INSUFFICIENT",
                 "official_rows": len(official_residual), "broad_rows": len(broad_residual)}]

    if official_cold_start:
        official_weights = np.asarray([], dtype=np.float64)
        official_weight_receipts = []
        official_source_receipts = []
        official_summary = {"status": "COLD_START_NO_OFFICIAL_EVIDENCE"}
    else:
        (official_weights, official_weight_receipts,
         official_source_receipts, official_summary) = _event_budget_weights(
            official_residual, "official_events"
        )
    (broad_weights, broad_weight_receipts,
     broad_source_receipts, broad_summary) = _event_budget_weights(
        broad_residual, "broad_events"
    )
    official_hash = canonical_hash(
        (
            "COLD_START_NO_OFFICIAL_EVIDENCE",
            EVIDENCE_POLICY_VERSION,
            NEWS_FEATURE_VERSION,
            ELIGIBILITY_VERSION,
            market_hash,
        )
        if official_cold_start else [
            (row["decision_id"], row["receipt"],
             crossfit_by_id[row["decision_id"]]["artifact_hash"],
             crossfit_by_id[row["decision_id"]]["residual"], receipt)
            for row, receipt in zip(official_residual, official_weights.tolist())
        ]
    )
    broad_hash = canonical_hash([
        (row["decision_id"], row["receipt"], crossfit_by_id[row["decision_id"]]["artifact_hash"],
         crossfit_by_id[row["decision_id"]]["residual"], receipt)
        for row, receipt in zip(broad_residual, broad_weights.tolist())
    ])
    news_only_hash = canonical_hash([
        (row["decision_id"], row["receipt"], row["target"], receipt)
        for row, receipt in zip(broad_residual, broad_weights.tolist())
    ])
    event_snapshot_hash = canonical_hash(sorted({
        (event["event_id"], event["event_version_id"])
        for row in training_rows for field in ("official_events", "broad_events")
        for event in row.get(field, [])
    }))
    generation_seed = canonical_hash((
        stage, cutoff.isoformat(), EVIDENCE_POLICY_VERSION, NEWS_FEATURE_VERSION,
        ELIGIBILITY_VERSION, EVENT_WEIGHTING_VERSION, event_snapshot_hash,
        market_hash, official_hash, broad_hash, news_only_hash,
    ))
    generation_id = str(uuid.uuid5(uuid.NAMESPACE_URL, generation_seed))
    slug = generation_id.split("-")[0]

    official_version = (
        f"news-residual-{evidence_status.lower().replace('_', '-')}-"
        f"{stage.lower()}-{slug}-{official_hash[:12]}"
    )
    official_artifact = (
        _neutral_news_artifact(official_hash)
        if official_cold_start else
        train_ridge(
            np.asarray([row["news"] for row in official_residual]),
            np.asarray([
                crossfit_by_id[row["decision_id"]]["residual"]
                for row in official_residual
            ]),
            NEWS_FEATURES, 100.0, official_hash, official_weights,
            EVENT_WEIGHTING_VERSION, official_summary,
        )
    )
    official_path = root / official_version / "model.json"
    if not official_path.exists():
        official_artifact.write(official_path)

    broad_version = (
        f"broad-news-residual-{broad_evidence_status.lower().replace('_', '-')}-"
        f"{stage.lower()}-{slug}-{broad_hash[:12]}"
    )
    broad_artifact = train_ridge(
        np.asarray([row["broad_news"] for row in broad_residual]),
        np.asarray([crossfit_by_id[row["decision_id"]]["residual"] for row in broad_residual]),
        BROAD_MODEL_FEATURES, 100.0, broad_hash, broad_weights,
        EVENT_WEIGHTING_VERSION, broad_summary,
    )
    broad_path = root / broad_version / "model.json"
    if not broad_path.exists():
        broad_artifact.write(broad_path)

    news_only_version = (
        f"news-only-{broad_evidence_status.lower().replace('_', '-')}-"
        f"{stage.lower()}-{slug}-{news_only_hash[:12]}"
    )
    news_only_artifact = train_ridge(
        np.asarray([row["broad_news"] for row in broad_residual]),
        np.asarray([row["target"] for row in broad_residual]),
        BROAD_MODEL_FEATURES, 100.0, news_only_hash, broad_weights,
        EVENT_WEIGHTING_VERSION, broad_summary,
    )
    news_only_path = root / news_only_version / "model.json"
    if not news_only_path.exists():
        news_only_artifact.write(news_only_path)

    full_manifest = {
        "schema": "xauusd.phase2f.full-model.v3", "generation_id": generation_id,
        "market_model_version": market_version, "market_artifact_path": str(market_path),
        "market_artifact_hash": market_artifact.artifact_hash,
        "news_model_version": official_version, "news_artifact_path": str(official_path),
        "news_artifact_hash": official_artifact.artifact_hash,
        "training_dataset_hash": market_hash, "news_training_hash": official_hash,
        "event_snapshot_hash": event_snapshot_hash,
        "event_weighting_version": EVENT_WEIGHTING_VERSION,
    }
    full_version = f"full-{stage.lower()}-{slug}-{canonical_hash(full_manifest)[:12]}"
    full_path = root / full_version / "manifest.json"
    full_artifact_hash = _write_manifest(full_path, full_manifest)
    broad_manifest = {
        **full_manifest,
        "schema": "xauusd.phase2f.broad-full-model.v2",
        "news_model_version": broad_version,
        "news_artifact_path": str(broad_path),
        "news_artifact_hash": broad_artifact.artifact_hash,
        "news_training_hash": broad_hash,
        "evidence_policy_version": EVIDENCE_POLICY_VERSION,
    }
    broad_full_version = (
        f"broad-full-{stage.lower()}-{slug}-{canonical_hash(broad_manifest)[:12]}"
    )
    broad_full_path = root / broad_full_version / "manifest.json"
    broad_full_hash = _write_manifest(broad_full_path, broad_manifest)
    required_paths = (
        market_path, official_path, full_path, broad_path, broad_full_path,
        news_only_path,
    )
    if any(not path.is_absolute() or not path.exists() for path in required_paths):
        return [{"status": "GENERATION_ARTIFACT_VALIDATION_FAILED",
                 "generation_id": generation_id}]

    updates = (
        (market_version, "MARKET_ONLY", len(training_rows), len(official_residual),
         official_events, official_days, market_hash, FEATURE_VERSION, None,
         market_path, market_artifact.artifact_hash),
        (official_version, "NEWS_RESIDUAL", len(official_residual), len(official_residual),
         official_events, official_days, official_hash, NEWS_FEATURE_VERSION,
         ELIGIBILITY_VERSION, official_path, official_artifact.artifact_hash),
        (full_version, "FULL", len(training_rows), len(official_residual),
         official_events, official_days, market_hash,
         f"{FEATURE_VERSION}+{NEWS_FEATURE_VERSION}", ELIGIBILITY_VERSION,
         full_path, full_artifact_hash),
        (broad_version, "BROAD_NEWS_RESIDUAL", len(broad_residual), len(broad_residual),
         broad_events, broad_days, broad_hash, NEWS_FEATURE_VERSION,
         ELIGIBILITY_VERSION, broad_path, broad_artifact.artifact_hash),
        (broad_full_version, "BROAD_FULL", len(training_rows), len(broad_residual),
         broad_events, broad_days, market_hash,
         f"{FEATURE_VERSION}+{NEWS_FEATURE_VERSION}+{EVIDENCE_POLICY_VERSION}",
         ELIGIBILITY_VERSION, broad_full_path, broad_full_hash),
        (news_only_version, "NEWS_ONLY", len(broad_residual), len(broad_residual),
         broad_events, broad_days, news_only_hash,
         f"{NEWS_FEATURE_VERSION}+{EVIDENCE_POLICY_VERSION}",
         ELIGIBILITY_VERSION, news_only_path, news_only_artifact.artifact_hash),
    )
    previous = ledger.connection.execute(
        """SELECT generation_id FROM news_model_generation_activations_v1
        ORDER BY activated_at DESC LIMIT 1"""
    ).fetchone()
    with ledger.connection:
        for update in updates:
            (model_version, identity, model_rows, exposed_rows, events, days,
             dataset_hash, feature_version, eligibility, artifact_path, artifact_hash) = update
            ledger.connection.execute(
                """INSERT INTO model_updates_v2 VALUES
                (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (model_version, identity, stage, now.isoformat(), cutoff.isoformat(),
                 model_rows, seed_rows, live_rows, exposed_rows, events, days,
                 dataset_hash, feature_version, eligibility, str(artifact_path),
                 artifact_hash, "CHALLENGER"),
            )
        ledger.connection.execute(
            """INSERT INTO news_model_generations_v1 VALUES
            (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (generation_id, stage, now.isoformat(), cutoff.isoformat(),
             EVIDENCE_POLICY_VERSION, NEWS_FEATURE_VERSION, ELIGIBILITY_VERSION,
             event_snapshot_hash, market_hash, official_hash, broad_hash,
             EVENT_WEIGHTING_VERSION, 5, "READY"),
        )
        for update in updates:
            member_table = (
                "news_model_generation_aux_members_v1"
                if update[1] == "NEWS_ONLY"
                else "news_model_generation_members_v1"
            )
            ledger.connection.execute(
                f"INSERT INTO {member_table} VALUES (?,?,?)",
                (generation_id, update[1], update[0]),
            )
        ledger.connection.execute(
            "INSERT INTO news_model_generation_activations_v1 VALUES (?,?,?,?,?)",
            (str(uuid.uuid5(uuid.NAMESPACE_URL, f"activate:{generation_id}")), generation_id,
             previous["generation_id"] if previous else None, now.isoformat(),
             "COMPLETE_FIVE_MODEL_GENERATION_WITH_NEWS_ONLY_DIAGNOSTIC"),
        )
        for lane, receipts in (
            ("OFFICIAL", official_weight_receipts), ("BROAD", broad_weight_receipts),
        ):
            for receipt in receipts:
                receipt_hash = canonical_hash((generation_id, lane, receipt))
                ledger.connection.execute(
                    """INSERT INTO news_training_weight_receipts_v1 VALUES
                    (?,?,?,?,?,?,?,?)""",
                    (generation_id, lane, receipt["source_decision_id"],
                     receipt["event_id"], receipt["event_version_id"],
                     receipt["raw_weight"], receipt["normalized_event_weight"], receipt_hash),
                )
        for lane, receipts in (
            ("OFFICIAL", official_source_receipts),
            ("BROAD", broad_source_receipts),
        ):
            for receipt in receipts:
                receipt_hash = canonical_hash((generation_id, lane, receipt))
                ledger.connection.execute(
                    """INSERT INTO news_training_source_budget_receipts_v1
                    VALUES (?,?,?,?,?,?)""",
                    (
                        generation_id, lane, receipt["source_budget_id"],
                        receipt["unbounded_weight"], receipt["bounded_weight"],
                        receipt_hash,
                    ),
                )
    return [{
        "status": "TRAINED", "model_identity": update[1],
        "model_stage": stage, "model_version": update[0],
        "generation_id": generation_id, "training_cutoff": cutoff.isoformat(),
        "event_snapshot_hash": event_snapshot_hash,
        "weighting_version": EVENT_WEIGHTING_VERSION,
        "news_evidence_status": (
            broad_evidence_status
            if update[1] in {"BROAD_NEWS_RESIDUAL", "BROAD_FULL", "NEWS_ONLY"}
            else "COLD_START_NO_OFFICIAL_EVIDENCE"
            if official_cold_start and update[1] in {"NEWS_RESIDUAL", "FULL"}
            else evidence_status
        ),
    } for update in updates]
