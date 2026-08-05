"""Leakage-controlled Preview and Shadow training for repaired evidence."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

from .evidence_v2 import (
    ELIGIBILITY_VERSION, FEATURE_VERSION, LABEL_VERSION, NEWS_FEATURE_VERSION,
)
from .factors import NEWS_FEATURES
from .forward_ledger import canonical_hash
from .news_evidence import (
    BROAD_NEWS_FEATURES, EVIDENCE_POLICY_VERSION, event_evidence_rows,
)
from .news_features_v2 import SOURCE_RULES
from .ridge import RidgeArtifact, train_ridge
from .training import MARKET_FEATURES


UTC = timezone.utc
PREVIEW_ROWS = 96
SHADOW_ROWS = 200
RETRAIN_INTERVAL = 50
NEWS_MIN_EXPOSED_ROWS = 30
NEWS_MIN_CLUSTERS = 10
NEWS_EXPERIMENTAL_MIN_EVENT_DAYS = 1
NEWS_MIN_EVENT_DAYS = 3
CROSSFIT_VERSION = "expanding-market-purge30m-v1"
BROAD_MODEL_FEATURES = (*NEWS_FEATURES, *BROAD_NEWS_FEATURES)


def news_evidence_status(event_days: int) -> str:
    """Label early news models without blocking observable Shadow learning."""
    if event_days < NEWS_EXPERIMENTAL_MIN_EVENT_DAYS:
        return "INSUFFICIENT"
    if event_days >= NEWS_MIN_EVENT_DAYS:
        return "STANDARD"
    if event_days == 1:
        return "EXPERIMENTAL_SINGLE_DAY"
    return "EXPERIMENTAL_TWO_DAY"


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
        complete.append({
            "decision_id": row["source_decision_id"], "lane": row["evidence_lane"],
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


def train_due_v2(ledger, cutoff: datetime, artifact_root: str | Path) -> list[dict]:
    """Append due V2 models; never changes Champion or effective action."""
    rows = complete_training_rows(ledger, cutoff)
    count = len(rows)
    if count < PREVIEW_ROWS:
        return [{"status": "ENGINEERING" if count < 30 else "EARLY_LEARNING",
                 "complete_rows": count, "next_threshold": PREVIEW_ROWS}]
    stage = "SHADOW" if count >= SHADOW_ROWS else "PREVIEW_ONLY"
    training_rows = rows if stage == "PREVIEW_ONLY" else rows[: count - (count % RETRAIN_INTERVAL)]
    news_exposed = [row for row in training_rows if row["news_exposed"]]
    eligible_sources = [source for source, rule in SOURCE_RULES.items() if rule[0] == "MODEL_ELIGIBLE"]
    placeholders = ",".join("?" for _ in eligible_sources)
    coverage = ledger.connection.execute(
        f"""SELECT count(DISTINCT n.cluster_id) AS clusters,
                   count(DISTINCT substr(n.collector_first_seen_time,1,10)) AS event_days
        FROM news_revisions n
        JOIN news_annotations a USING(source,source_item_id,revision_number)
        WHERE n.source IN ({placeholders}) AND length(coalesce(n.body,''))>=200
          AND n.collector_first_seen_time<=? AND a.parsed_at<=?""",
        (*eligible_sources, cutoff.isoformat(), cutoff.isoformat()),
    ).fetchone()
    clusters = int(coverage["clusters"] or 0)
    event_days = int(coverage["event_days"] or 0)
    official_ready = (
        len(news_exposed) >= NEWS_MIN_EXPOSED_ROWS
        and clusters >= NEWS_MIN_CLUSTERS
        and event_days >= NEWS_EXPERIMENTAL_MIN_EVENT_DAYS
    )
    broad_exposed = [
        row for row in training_rows if row.get("broad_news_exposed", False)
    ]
    broad_events = [
        row for row in event_evidence_rows(ledger, cutoff)
        if row["broad_model_eligible"]
    ]
    broad_clusters = len(broad_events)
    broad_event_days = len({
        row["collector_first_seen_time"][:10] for row in broad_events
    })
    broad_ready = (
        len(broad_exposed) >= NEWS_MIN_EXPOSED_ROWS
        and broad_clusters >= NEWS_MIN_CLUSTERS
        and broad_event_days >= NEWS_EXPERIMENTAL_MIN_EVENT_DAYS
    )
    latest = ledger.connection.execute(
        """SELECT * FROM model_updates_v2 WHERE model_identity='MARKET_ONLY'
        AND model_stage=? ORDER BY training_rows DESC LIMIT 1""", (stage,)
    ).fetchone()
    paired_models = ledger.connection.execute(
        """SELECT DISTINCT model_identity FROM model_updates_v2
        WHERE model_identity IN ('FULL','BROAD_FULL')
          AND model_stage=? AND created_at>=?""",
        (stage, latest["created_at"]),
    ).fetchall() if latest is not None else []
    paired_identities = {row["model_identity"] for row in paired_models}
    latest_artifact_path = Path(latest["artifact_path"]) if latest is not None else None
    latest_artifact_invalid = bool(
        latest_artifact_path is not None
        and latest_artifact_path.suffix == ".json"
        and (
            not latest_artifact_path.is_absolute()
            or not latest_artifact_path.exists()
        )
    )
    bootstrap_news_pair = latest is not None and (
        (official_ready and "FULL" not in paired_identities)
        or (broad_ready and "BROAD_FULL" not in paired_identities)
        or latest_artifact_invalid
    )
    if (latest is not None
            and count < int(latest["training_rows"]) + RETRAIN_INTERVAL
            and not bootstrap_news_pair):
        return [{"status": "NOT_DUE", "complete_rows": count,
                 "next_threshold": int(latest["training_rows"]) + RETRAIN_INTERVAL}]

    now = datetime.now(UTC)
    root = Path(artifact_root).resolve()
    version, artifact, path, dataset_hash = _write_market_artifact(training_rows, root, cutoff, stage)
    seed_rows = sum(row["lane"] == "REPAIRED_SEED" for row in training_rows)
    live_rows = sum(row["lane"] == "LIVE_OOS" for row in training_rows)
    evidence_status = news_evidence_status(event_days)
    with ledger.connection:
        ledger.connection.execute(
            """INSERT INTO model_updates_v2 VALUES
            (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (version, "MARKET_ONLY", stage, now.isoformat(), cutoff.isoformat(),
             len(training_rows), seed_rows, live_rows, len(news_exposed), clusters, event_days,
             dataset_hash, FEATURE_VERSION, None, str(path), artifact.artifact_hash, "CHALLENGER"),
        )
        crossfit = chronological_crossfit_market(ledger, training_rows, root, now)

    statuses = [{"status": "TRAINED", "model_identity": "MARKET_ONLY",
                 "model_stage": stage, "model_version": version,
                 "training_rows": len(training_rows), "crossfit_rows": len(crossfit)}]
    if (len(news_exposed) < NEWS_MIN_EXPOSED_ROWS or clusters < NEWS_MIN_CLUSTERS
            or event_days < NEWS_EXPERIMENTAL_MIN_EVENT_DAYS):
        statuses.append({"status": "NEWS_EVIDENCE_INSUFFICIENT",
                         "news_exposed_rows": len(news_exposed),
                         "distinct_news_clusters": clusters, "distinct_event_days": event_days,
                         "news_evidence_status": evidence_status})
    else:
        crossfit_by_id = {row["decision_id"]: row for row in crossfit}
        residual_rows = [row for row in news_exposed if row["decision_id"] in crossfit_by_id]
        if len(residual_rows) < NEWS_MIN_EXPOSED_ROWS:
            statuses.append({"status": "NEWS_CROSSFIT_INSUFFICIENT",
                             "covered_exposed_rows": len(residual_rows),
                             "news_exposed_rows": len(news_exposed)})
        else:
            residual_receipts = [
                (row["decision_id"], row["receipt"], crossfit_by_id[row["decision_id"]]["artifact_hash"],
                 crossfit_by_id[row["decision_id"]]["residual"])
                for row in residual_rows
            ]
            residual_hash = canonical_hash(residual_receipts)
            news_version = (
                f"news-residual-{evidence_status.lower().replace('_', '-')}-"
                f"{stage.lower()}-{cutoff.astimezone(UTC).strftime('%Y%m%dT%H%M%SZ')}-"
                f"{residual_hash[:12]}"
            )
            news_artifact = train_ridge(
                np.asarray([row["news"] for row in residual_rows]),
                np.asarray([crossfit_by_id[row["decision_id"]]["residual"] for row in residual_rows]),
                NEWS_FEATURES, 100.0, residual_hash,
            )
            news_path = root / news_version / "model.json"
            if not news_path.exists():
                news_artifact.write(news_path)
            ledger.connection.execute(
                """INSERT INTO model_updates_v2 VALUES
                (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (news_version, "NEWS_RESIDUAL", stage, now.isoformat(), cutoff.isoformat(),
                 len(residual_rows), sum(row["lane"] == "REPAIRED_SEED" for row in residual_rows),
                 sum(row["lane"] == "LIVE_OOS" for row in residual_rows), len(residual_rows),
                 clusters, event_days, residual_hash, NEWS_FEATURE_VERSION, ELIGIBILITY_VERSION,
                 str(news_path), news_artifact.artifact_hash, "CHALLENGER"),
            )
            manifest = {
                "schema": "xauusd.phase2f.full-model.v2", "market_model_version": version,
                "market_artifact_path": str(path), "market_artifact_hash": artifact.artifact_hash,
                "news_model_version": news_version, "news_artifact_path": str(news_path),
                "news_artifact_hash": news_artifact.artifact_hash,
                "training_dataset_hash": dataset_hash, "news_training_hash": residual_hash,
            }
            full_hash = canonical_hash(manifest)
            full_version = (
                f"full-{evidence_status.lower().replace('_', '-')}-"
                f"{stage.lower()}-{cutoff.astimezone(UTC).strftime('%Y%m%dT%H%M%SZ')}-"
                f"{full_hash[:12]}"
            )
            full_path = root / full_version / "manifest.json"
            if not full_path.exists():
                full_path.parent.mkdir(parents=True, exist_ok=False)
                full_path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
            ledger.connection.execute(
                """INSERT INTO model_updates_v2 VALUES
                (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (full_version, "FULL", stage, now.isoformat(), cutoff.isoformat(), len(training_rows),
                 seed_rows, live_rows, len(residual_rows), clusters, event_days, dataset_hash,
                 f"{FEATURE_VERSION}+{NEWS_FEATURE_VERSION}", ELIGIBILITY_VERSION,
                 str(full_path), full_hash, "CHALLENGER"),
            )
            statuses.extend([
                {"status": "TRAINED", "model_identity": "NEWS_RESIDUAL",
                 "model_stage": stage, "model_version": news_version,
                 "training_rows": len(residual_rows), "crossfit_method": CROSSFIT_VERSION,
                 "news_evidence_status": evidence_status,
                 "distinct_event_days": event_days},
                {"status": "TRAINED", "model_identity": "FULL", "model_stage": stage,
                 "model_version": full_version, "training_rows": len(training_rows),
                 "news_evidence_status": evidence_status,
                 "distinct_event_days": event_days},
            ])
            ledger.connection.commit()

    broad_evidence_status = news_evidence_status(broad_event_days)
    if (len(broad_exposed) < NEWS_MIN_EXPOSED_ROWS
            or broad_clusters < NEWS_MIN_CLUSTERS
            or broad_event_days < NEWS_EXPERIMENTAL_MIN_EVENT_DAYS):
        statuses.append({
            "status": "BROAD_NEWS_EVIDENCE_INSUFFICIENT",
            "news_exposed_rows": len(broad_exposed),
            "distinct_news_clusters": broad_clusters,
            "distinct_event_days": broad_event_days,
            "news_evidence_status": broad_evidence_status,
        })
    else:
        crossfit_by_id = {row["decision_id"]: row for row in crossfit}
        residual_rows = [
            row for row in broad_exposed if row["decision_id"] in crossfit_by_id
        ]
        if len(residual_rows) < NEWS_MIN_EXPOSED_ROWS:
            statuses.append({
                "status": "BROAD_NEWS_CROSSFIT_INSUFFICIENT",
                "covered_exposed_rows": len(residual_rows),
                "news_exposed_rows": len(broad_exposed),
            })
        else:
            residual_receipts = [
                (
                    row["decision_id"], row["receipt"],
                    crossfit_by_id[row["decision_id"]]["artifact_hash"],
                    crossfit_by_id[row["decision_id"]]["residual"],
                    EVIDENCE_POLICY_VERSION,
                )
                for row in residual_rows
            ]
            residual_hash = canonical_hash(residual_receipts)
            evidence_slug = broad_evidence_status.lower().replace("_", "-")
            broad_version = (
                f"broad-news-residual-{evidence_slug}-{stage.lower()}-"
                f"{cutoff.astimezone(UTC).strftime('%Y%m%dT%H%M%SZ')}-"
                f"{residual_hash[:12]}"
            )
            broad_artifact = train_ridge(
                np.asarray([row["broad_news"] for row in residual_rows]),
                np.asarray([
                    crossfit_by_id[row["decision_id"]]["residual"]
                    for row in residual_rows
                ]),
                BROAD_MODEL_FEATURES, 100.0, residual_hash,
            )
            broad_path = root / broad_version / "model.json"
            if not broad_path.exists():
                broad_artifact.write(broad_path)
            broad_eligibility = f"{ELIGIBILITY_VERSION}+{EVIDENCE_POLICY_VERSION}"
            ledger.connection.execute(
                """INSERT INTO model_updates_v2 VALUES
                (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    broad_version, "BROAD_NEWS_RESIDUAL", stage, now.isoformat(),
                    cutoff.isoformat(), len(residual_rows),
                    sum(row["lane"] == "REPAIRED_SEED" for row in residual_rows),
                    sum(row["lane"] == "LIVE_OOS" for row in residual_rows),
                    len(residual_rows), broad_clusters, broad_event_days,
                    residual_hash, NEWS_FEATURE_VERSION, broad_eligibility,
                    str(broad_path), broad_artifact.artifact_hash, "CHALLENGER",
                ),
            )
            manifest = {
                "schema": "xauusd.phase2f.broad-full-model.v1",
                "market_model_version": version,
                "market_artifact_path": str(path),
                "market_artifact_hash": artifact.artifact_hash,
                "news_model_version": broad_version,
                "news_artifact_path": str(broad_path),
                "news_artifact_hash": broad_artifact.artifact_hash,
                "training_dataset_hash": dataset_hash,
                "news_training_hash": residual_hash,
                "evidence_policy_version": EVIDENCE_POLICY_VERSION,
            }
            full_hash = canonical_hash(manifest)
            broad_full_version = (
                f"broad-full-{evidence_slug}-{stage.lower()}-"
                f"{cutoff.astimezone(UTC).strftime('%Y%m%dT%H%M%SZ')}-"
                f"{full_hash[:12]}"
            )
            broad_full_path = root / broad_full_version / "manifest.json"
            if not broad_full_path.exists():
                broad_full_path.parent.mkdir(parents=True, exist_ok=False)
                broad_full_path.write_text(
                    json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8"
                )
            ledger.connection.execute(
                """INSERT INTO model_updates_v2 VALUES
                (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    broad_full_version, "BROAD_FULL", stage, now.isoformat(),
                    cutoff.isoformat(), len(training_rows), seed_rows, live_rows,
                    len(residual_rows), broad_clusters, broad_event_days, dataset_hash,
                    f"{FEATURE_VERSION}+{NEWS_FEATURE_VERSION}+{EVIDENCE_POLICY_VERSION}",
                    broad_eligibility, str(broad_full_path), full_hash, "CHALLENGER",
                ),
            )
            ledger.connection.commit()
            statuses.extend([
                {
                    "status": "TRAINED", "model_identity": "BROAD_NEWS_RESIDUAL",
                    "model_stage": stage, "model_version": broad_version,
                    "training_rows": len(residual_rows),
                    "crossfit_method": CROSSFIT_VERSION,
                    "news_evidence_status": broad_evidence_status,
                    "distinct_event_days": broad_event_days,
                },
                {
                    "status": "TRAINED", "model_identity": "BROAD_FULL",
                    "model_stage": stage, "model_version": broad_full_version,
                    "training_rows": len(training_rows),
                    "news_evidence_status": broad_evidence_status,
                    "distinct_event_days": broad_event_days,
                },
            ])
    return statuses
