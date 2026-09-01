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
from .news_contracts import (
    CORE_EVIDENCE_STORAGE_LANE,
    CORE_MODEL_STORAGE_PERMISSION,
    CURRENT_NEWS_CONTRACT,
    generation_matches_contract,
)
from .news_evidence import BROAD_NEWS_FEATURES, EVIDENCE_POLICY_VERSION
from .news_features_v2 import EVIDENCE_GRADE_WEIGHT
from .ridge import RidgeArtifact, train_ridge
from .artifact_paths import (
    require_runtime_artifact_path,
    sqlite_runtime_forward_root,
)
from .training import MARKET_FEATURES


UTC = timezone.utc
PREVIEW_ROWS = 96
SHADOW_ROWS = 200
LIVE_GENERATION_STAGE = "SHADOW"
RETRAIN_INTERVAL = 50
MATERIALIZATION_BATCH_ROWS = 200
NEWS_MIN_EXPOSED_ROWS = 30
NEWS_MIN_CLUSTERS = 10
NEWS_EXPERIMENTAL_MIN_CLUSTERS = 1
NEWS_EXPERIMENTAL_MIN_EVENT_DAYS = 1
NEWS_MIN_EVENT_DAYS = 3
CROSSFIT_VERSION = "expanding-market-purge30m-v1"
EVENT_WEIGHTING_VERSION = "event-source-budget-v7-observable-zero-news"
SOURCE_WEIGHT_BUDGET = 1.0
BROAD_MODEL_FEATURES = (*NEWS_FEATURES, *BROAD_NEWS_FEATURES)
REQUIRED_GENERATION_IDENTITIES = frozenset({
    "MARKET_ONLY", "NEWS_RESIDUAL", "FULL",
    "BROAD_NEWS_RESIDUAL", "BROAD_FULL",
})
NEWS_TRAINING_ELIGIBLE_STATES = frozenset({
    "AVAILABLE", "DEGRADED", "QUIET",
})
TRAINING_MATERIALIZATION_CONTRACT = canonical_hash((
    "training-materialization-v2", FEATURE_VERSION, NEWS_FEATURE_VERSION,
    ELIGIBILITY_VERSION, LABEL_VERSION, EVIDENCE_POLICY_VERSION,
))


def news_input_state_is_training_eligible(state: object) -> bool:
    """Admit only frozen states that represent observable news input."""
    return str(state) in NEWS_TRAINING_ELIGIBLE_STATES


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


def _rows(ledger, cutoff: datetime, source_ids: list[str] | None = None):
    source_filter = ""
    parameters: list[object] = [
        cutoff.isoformat(), FEATURE_VERSION, NEWS_FEATURE_VERSION,
        ELIGIBILITY_VERSION, LABEL_VERSION,
    ]
    if source_ids is not None:
        if not source_ids:
            return []
        source_filter = (
            " AND e.source_decision_id IN ("
            + ",".join("?" for _ in source_ids) + ")"
        )
        parameters.extend(source_ids)
    return ledger.connection.execute(
        """SELECT e.source_decision_id, e.evidence_lane, m.decision_time,
                  m.features_json, m.u5, m.output_hash AS market_hash,
                  n.features_json AS news_json, n.news_exposed,
                  n.distinct_news_clusters, n.output_hash AS news_hash,
                  coalesce(
                    c.state,
                    CASE WHEN h.status='UNHEALTHY'
                         THEN 'UNAVAILABLE' ELSE 'AVAILABLE' END
                  ) AS news_input_state,
                  o.gross_midpoint_direction_move, o.long_quote_return,
                  o.short_quote_return, o.output_hash AS outcome_hash
        FROM training_eligibility_v2 e
        JOIN derived_market_snapshots m
          ON m.source_decision_id=e.source_decision_id
        JOIN derived_news_feature_snapshots n
          ON n.source_decision_id=e.source_decision_id
        JOIN derived_outcomes o
          ON o.source_decision_id=e.source_decision_id
        LEFT JOIN news_input_coverage_snapshots_v1 c
          ON c.source_decision_id=e.source_decision_id
        LEFT JOIN news_semantic_health_snapshots_v1 h
          ON h.source_decision_id=e.source_decision_id
        WHERE e.eligible_at <= ? AND o.outcome_status='VALID'
          AND m.feature_version=? AND n.feature_version=?
          AND n.eligibility_version=? AND o.label_version=?"""
        + source_filter
        + " ORDER BY m.decision_time, e.source_decision_id",
        parameters,
    ).fetchall()


def _install_training_materialization_state(connection) -> None:
    installed = connection.execute(
        """SELECT 1 FROM sqlite_master
            WHERE type='trigger'
              AND name='trg_training_materialization_news_event_source_budgets_v1_delete'"""
    ).fetchone()
    if installed is not None:
        materialized_columns = {
            str(row[1])
            for row in connection.execute(
                "PRAGMA table_info(materialized_training_rows_v1)"
            )
        }
        dirty_columns = {
            str(row[1])
            for row in connection.execute(
                "PRAGMA table_info(training_materialization_dirty_v1)"
            )
        }
        if ("materialized_row_hash" in materialized_columns and
                "dirty_revision" in dirty_columns):
            return
    connection.executescript(
        """CREATE TABLE IF NOT EXISTS training_materialization_state_v1 (
            id INTEGER PRIMARY KEY CHECK(id=1),
            contract_hash TEXT NOT NULL,
            row_count INTEGER NOT NULL,
            cursor_decision_time TEXT,
            cursor_decision_id TEXT,
            state TEXT NOT NULL CHECK(state IN ('CLEAN','DIRTY')),
            materialization_mode TEXT NOT NULL,
            materialization_receipt_hash TEXT NOT NULL,
            last_success_at TEXT NOT NULL,
            rebuild_generation INTEGER NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS materialized_training_rows_v1 (
            source_decision_id TEXT PRIMARY KEY,
            decision_time TEXT NOT NULL,
            row_json TEXT NOT NULL,
            source_receipt_hash TEXT NOT NULL,
            materialized_row_hash TEXT NOT NULL,
            contract_hash TEXT NOT NULL,
            materialized_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_materialized_training_rows_v1_order
          ON materialized_training_rows_v1(decision_time,source_decision_id);
        CREATE TABLE IF NOT EXISTS training_materialization_dirty_v1 (
            source_decision_id TEXT PRIMARY KEY,
            dirty_revision INTEGER NOT NULL DEFAULT 1,
            source_table TEXT NOT NULL,
            change_kind TEXT NOT NULL,
            dirty_at TEXT NOT NULL
        );
        """
    )
    columns = {
        str(row[1])
        for row in connection.execute(
            "PRAGMA table_info(materialized_training_rows_v1)"
        )
    }
    if "materialized_row_hash" not in columns:
        connection.execute(
            "ALTER TABLE materialized_training_rows_v1 "
            "ADD COLUMN materialized_row_hash TEXT"
        )
    dirty_columns = {
        str(row[1])
        for row in connection.execute(
            "PRAGMA table_info(training_materialization_dirty_v1)"
        )
    }
    if "dirty_revision" not in dirty_columns:
        connection.execute(
            "ALTER TABLE training_materialization_dirty_v1 "
            "ADD COLUMN dirty_revision INTEGER NOT NULL DEFAULT 1"
        )
    direct_sources = (
        "training_eligibility_v2", "derived_market_snapshots",
        "derived_news_feature_snapshots", "derived_outcomes",
        "news_input_coverage_snapshots_v1",
        "news_semantic_health_snapshots_v1",
        "news_decision_event_snapshots_v1",
    )
    for table in direct_sources:
        for operation, reference in (
            ("INSERT", "NEW"), ("UPDATE", "NEW"), ("DELETE", "OLD"),
        ):
            trigger = f"trg_training_materialization_{table}_{operation.lower()}"
            connection.execute(f"DROP TRIGGER IF EXISTS {trigger}")
            connection.execute(
                f"""CREATE TRIGGER IF NOT EXISTS
                  {trigger}
                AFTER {operation} ON {table}
                BEGIN
                  INSERT INTO training_materialization_dirty_v1
                    (source_decision_id,dirty_revision,source_table,change_kind,dirty_at)
                  VALUES ({reference}.source_decision_id,1,'{table}','{operation}',
                          strftime('%Y-%m-%dT%H:%M:%fZ','now'))
                  ON CONFLICT(source_decision_id) DO UPDATE SET
                    dirty_revision=dirty_revision+1,
                    source_table=excluded.source_table,
                    change_kind=excluded.change_kind,
                    dirty_at=excluded.dirty_at;
                END"""
            )
    related_sources = {
        "news_event_catalog_v1": "event_version_id",
        "news_event_source_budgets_v1": "event_version_id",
    }
    for table, key in related_sources.items():
        for operation, reference in (
            ("INSERT", "NEW"), ("UPDATE", "NEW"), ("DELETE", "OLD"),
        ):
            trigger = f"trg_training_materialization_{table}_{operation.lower()}"
            connection.execute(f"DROP TRIGGER IF EXISTS {trigger}")
            connection.execute(
                f"""CREATE TRIGGER IF NOT EXISTS
                  {trigger}
                AFTER {operation} ON {table}
                BEGIN
                  INSERT INTO training_materialization_dirty_v1
                    (source_decision_id,dirty_revision,source_table,change_kind,dirty_at)
                  SELECT source_decision_id,1,'{table}','{operation}',
                         strftime('%Y-%m-%dT%H:%M:%fZ','now')
                    FROM news_decision_event_snapshots_v1
                   WHERE {key}={reference}.{key}
                  ON CONFLICT(source_decision_id) DO UPDATE SET
                    dirty_revision=dirty_revision+1,
                    source_table=excluded.source_table,
                    change_kind=excluded.change_kind,
                    dirty_at=excluded.dirty_at;
                END"""
            )


def refresh_training_materialization_state(ledger, cutoff: datetime) -> dict:
    """Materialize only changed training rows, or atomically rebuild on drift."""
    _install_training_materialization_state(ledger.connection)
    previous = ledger.connection.execute(
        "SELECT * FROM training_materialization_state_v1 WHERE id=1"
    ).fetchone()
    now = datetime.now(UTC).isoformat()
    rebuild = (
        previous is None
        or str(previous["contract_hash"]) != TRAINING_MATERIALIZATION_CONTRACT
        or str(previous["state"]) == "DIRTY"
        or ledger.connection.execute(
            """SELECT 1 FROM materialized_training_rows_v1
                WHERE contract_hash<>? LIMIT 1""",
            (TRAINING_MATERIALIZATION_CONTRACT,),
        ).fetchone() is not None
    )
    if rebuild:
        observed_dirty = [
            (str(row["source_decision_id"]), int(row["dirty_revision"]))
            for row in ledger.connection.execute(
                "SELECT source_decision_id,dirty_revision "
                "FROM training_materialization_dirty_v1"
            )
        ]
        rows = _build_training_rows(ledger, cutoff)
        try:
            _replace_materialized_training_rows(
                ledger, rows, observed_dirty, now,
            )
        except Exception:
            ledger.connection.rollback()
            raise
        generation = int(previous["rebuild_generation"] if previous else 0) + 1
        mode = "FULL"
        processed = len(rows)
        snapshot = {
            "row_count": len(rows),
            "cursor_decision_time": rows[-1]["decision_time"] if rows else None,
            "cursor_decision_id": rows[-1]["decision_id"] if rows else None,
        }
        receipt_hash = canonical_hash([row["receipt"] for row in rows])
    else:
        observed_dirty = [
            (str(row["source_decision_id"]), int(row["dirty_revision"]))
            for row in ledger.connection.execute(
                """SELECT d.source_decision_id,d.dirty_revision
                     FROM training_materialization_dirty_v1 d
                     LEFT JOIN training_eligibility_v2 e
                       USING(source_decision_id)
                     LEFT JOIN materialized_training_rows_v1 m
                       USING(source_decision_id)
                    WHERE m.source_decision_id IS NOT NULL
                       OR e.eligible_at<=?
                    ORDER BY d.dirty_at,d.source_decision_id LIMIT ?""",
                (cutoff.isoformat(), MATERIALIZATION_BATCH_ROWS),
            )
        ]
        dirty_ids = [source_id for source_id, _ in observed_dirty]
        if dirty_ids:
            placeholders = ",".join("?" for _ in dirty_ids)
            changed = ledger.connection.execute(
                f"""SELECT d.source_decision_id,m.source_decision_id AS materialized_id,
                            source.decision_time
                       FROM training_materialization_dirty_v1 d
                       LEFT JOIN materialized_training_rows_v1 m
                         USING(source_decision_id)
                       LEFT JOIN derived_market_snapshots source
                         USING(source_decision_id)
                      WHERE d.source_decision_id IN ({placeholders})""",
                dirty_ids,
            ).fetchall()
            source_deleted = any(
                row["materialized_id"] is not None and row["decision_time"] is None
                for row in changed
            )
            late_insertion = any(
                row["materialized_id"] is None
                and row["decision_time"] is not None
                and previous["cursor_decision_time"] is not None
                and (
                    str(row["decision_time"]), str(row["source_decision_id"])
                ) <= (
                    str(previous["cursor_decision_time"]),
                    str(previous["cursor_decision_id"]),
                )
                for row in changed
            )
            if source_deleted or late_insertion:
                with ledger.connection:
                    ledger.connection.execute(
                        "UPDATE training_materialization_state_v1 "
                        "SET state='DIRTY',updated_at=? WHERE id=1", (now,),
                    )
                return refresh_training_materialization_state(ledger, cutoff)
        old_materialized_count = 0
        if dirty_ids:
            placeholders = ",".join("?" for _ in dirty_ids)
            old_materialized_count = int(ledger.connection.execute(
                f"SELECT count(*) FROM materialized_training_rows_v1 "
                f"WHERE source_decision_id IN ({placeholders})",
                dirty_ids,
            ).fetchone()[0])
        rows = (
            _build_training_rows(ledger, cutoff, dirty_ids) if dirty_ids else []
        )
        try:
            _update_materialized_training_rows(
                ledger, observed_dirty, rows, now,
            )
        except Exception:
            ledger.connection.rollback()
            raise
        generation = int(previous["rebuild_generation"])
        mode = "INCREMENTAL" if dirty_ids else "NO_CHANGE"
        processed = len(dirty_ids)
        if dirty_ids:
            latest = ledger.connection.execute(
                """SELECT decision_time,source_decision_id
                     FROM materialized_training_rows_v1
                    ORDER BY decision_time DESC,source_decision_id DESC LIMIT 1"""
            ).fetchone()
            snapshot = {
                "row_count": (
                    int(previous["row_count"]) - old_materialized_count + len(rows)
                ),
                "cursor_decision_time": latest["decision_time"] if latest else None,
                "cursor_decision_id": latest["source_decision_id"] if latest else None,
            }
            receipt_hash = canonical_hash((
                str(previous["materialization_receipt_hash"]),
                [(source_id, next((row["receipt"] for row in rows
                                   if str(row["decision_id"]) == source_id), None))
                 for source_id in dirty_ids],
            ))
        else:
            snapshot = {
                "row_count": int(previous["row_count"]),
                "cursor_decision_time": previous["cursor_decision_time"],
                "cursor_decision_id": previous["cursor_decision_id"],
            }
            receipt_hash = str(previous["materialization_receipt_hash"])
    with ledger.connection:
        ledger.connection.execute(
            """INSERT INTO training_materialization_state_v1
            VALUES(1,?,?,?,?,'CLEAN',?,?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET contract_hash=excluded.contract_hash,
              row_count=excluded.row_count,
              cursor_decision_time=excluded.cursor_decision_time,
              cursor_decision_id=excluded.cursor_decision_id,state='CLEAN',
              materialization_mode=excluded.materialization_mode,
              materialization_receipt_hash=excluded.materialization_receipt_hash,
              last_success_at=excluded.last_success_at,
              rebuild_generation=excluded.rebuild_generation,
              updated_at=excluded.updated_at""",
            (TRAINING_MATERIALIZATION_CONTRACT, snapshot["row_count"],
             snapshot["cursor_decision_time"], snapshot["cursor_decision_id"],
             mode, receipt_hash, now, generation, now),
        )
    pending = int(ledger.connection.execute(
        """SELECT count(*)
             FROM training_materialization_dirty_v1 d
             LEFT JOIN training_eligibility_v2 e USING(source_decision_id)
             LEFT JOIN materialized_training_rows_v1 m USING(source_decision_id)
            WHERE m.source_decision_id IS NOT NULL OR e.eligible_at<=?""",
        (cutoff.isoformat(),),
    ).fetchone()[0])
    return {
        **snapshot, "state": "CLEAN",
        "contract_hash": TRAINING_MATERIALIZATION_CONTRACT,
        "materialization_mode": mode,
        "materialization_receipt_hash": receipt_hash,
        "last_success_at": now, "rebuild_generation": generation,
        "processed_source_rows": processed, "pending_source_rows": pending,
    }


def _persisted_training_row(row: dict, now: str) -> tuple:
    row_json = json.dumps(row, sort_keys=True, separators=(",", ":"))
    return (
        str(row["decision_id"]), str(row["decision_time"]),
        row_json, canonical_hash(row["receipt"]), canonical_hash(row),
        TRAINING_MATERIALIZATION_CONTRACT, now,
    )


def _acknowledge_dirty_revisions(ledger, observed_dirty: list[tuple[str, int]]) -> None:
    ledger.connection.executemany(
        "DELETE FROM training_materialization_dirty_v1 "
        "WHERE source_decision_id=? AND dirty_revision=?",
        observed_dirty,
    )


def _replace_materialized_training_rows(
    ledger, rows: list[dict], observed_dirty: list[tuple[str, int]], now: str,
) -> None:
    persisted = [_persisted_training_row(row, now) for row in rows]
    ledger.connection.execute("DELETE FROM materialized_training_rows_v1")
    ledger.connection.executemany(
        """INSERT INTO materialized_training_rows_v1
        (source_decision_id,decision_time,row_json,source_receipt_hash,
         materialized_row_hash,contract_hash,materialized_at)
        VALUES(?,?,?,?,?,?,?)""",
        persisted,
    )
    _acknowledge_dirty_revisions(ledger, observed_dirty)


def _update_materialized_training_rows(
    ledger, observed_dirty: list[tuple[str, int]], rows: list[dict], now: str,
) -> None:
    if not observed_dirty:
        return
    source_ids = [source_id for source_id, _ in observed_dirty]
    placeholders = ",".join("?" for _ in source_ids)
    persisted = [_persisted_training_row(row, now) for row in rows]
    ledger.connection.execute(
        f"DELETE FROM materialized_training_rows_v1 "
        f"WHERE source_decision_id IN ({placeholders})",
        source_ids,
    )
    ledger.connection.executemany(
        """INSERT INTO materialized_training_rows_v1
        (source_decision_id,decision_time,row_json,source_receipt_hash,
         materialized_row_hash,contract_hash,materialized_at)
        VALUES(?,?,?,?,?,?,?)""",
        persisted,
    )
    _acknowledge_dirty_revisions(ledger, observed_dirty)


def _build_training_rows(
    ledger, cutoff: datetime, source_ids: list[str] | None = None,
) -> list[dict]:
    complete = []
    rows = _rows(ledger, cutoff, source_ids)
    decision_ids = {str(row["source_decision_id"]) for row in rows}
    events_by_decision: dict[str, list[dict]] = defaultdict(list)
    if decision_ids:
        event_source_filter = ""
        event_parameters: list[object] = [
            EVIDENCE_POLICY_VERSION, cutoff.isoformat(), FEATURE_VERSION,
            NEWS_FEATURE_VERSION, ELIGIBILITY_VERSION, LABEL_VERSION,
        ]
        if source_ids is not None:
            event_source_filter = (
                " AND s.source_decision_id IN ("
                + ",".join("?" for _ in source_ids) + ")"
            )
            event_parameters.extend(source_ids)
        for event in ledger.connection.execute(
            """SELECT s.source_decision_id,s.event_id,s.event_version_id,
                      s.model_permission,s.raw_weight,c.event_occurred_at,
                      c.event_clock_source,c.event_time_precision,c.evidence_grade,
                      coalesce(b.source_budget_id,c.canonical_source) AS source_budget_id
               FROM news_decision_event_snapshots_v1 s
               JOIN news_event_catalog_v1 c USING(event_version_id)
               LEFT JOIN news_event_source_budgets_v1 b USING(event_version_id)
               JOIN training_eligibility_v2 e
                 ON e.source_decision_id=s.source_decision_id
               JOIN derived_market_snapshots m
                 ON m.source_decision_id=s.source_decision_id
               JOIN derived_news_feature_snapshots n
                 ON n.source_decision_id=s.source_decision_id
               JOIN derived_outcomes o
                 ON o.source_decision_id=s.source_decision_id
               WHERE s.policy_version=? AND e.eligible_at<=?
                 AND o.outcome_status='VALID' AND m.feature_version=?
                  AND n.feature_version=? AND n.eligibility_version=?
                  AND o.label_version=?"""
            + event_source_filter
            + """ ORDER BY s.source_decision_id,s.model_permission,s.event_id,
                         s.event_version_id""",
            event_parameters,
        ):
            decision_id = str(event["source_decision_id"])
            if decision_id in decision_ids:
                events_by_decision[decision_id].append(dict(event))
    for row in rows:
        market = json.loads(row["features_json"])
        market_values = [market.get(name) for name in MARKET_FEATURES]
        if row["u5"] is None or any(value is None for value in market_values):
            continue
        target = float(row["gross_midpoint_direction_move"]) / float(row["u5"])
        values = [float(value) for value in market_values]
        if not np.isfinite(values).all() or not np.isfinite(target):
            continue
        decision_id = row["source_decision_id"]
        event_rows = events_by_decision.get(str(decision_id), [])
        # Current derived-news snapshots and their zero-or-more event rows are
        # materialized atomically by live append or contract reconciliation.
        # An empty set is authoritative quiet evidence, not permission to
        # reconstruct every historical decision on the training hot path.
        event_snapshots = [dict(event) for event in event_rows]
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
            "news_input_state": str(row["news_input_state"]),
            "news_training_eligible": news_input_state_is_training_eligible(
                row["news_input_state"]
            ),
            "core_events": [
                event for event in event_snapshots
                if event["model_permission"] == CORE_MODEL_STORAGE_PERMISSION
            ],
            "broad_events": [
                event for event in event_snapshots
                if event["model_permission"] == "BROAD_MODEL"
            ],
            "receipt": (
                row["source_decision_id"], row["market_hash"], row["news_hash"],
                row["outcome_hash"],
            ),
            "news_receipt": (
                row["source_decision_id"], row["news_hash"],
                row["outcome_hash"], row["news_input_state"],
            ),
        })
    return complete


def complete_training_rows(ledger, cutoff: datetime) -> list[dict]:
    """Read the durable point-in-time rows after incremental reconciliation."""
    refresh_training_materialization_state(ledger, cutoff)
    rows = ledger.connection.execute(
        """SELECT row_json,source_receipt_hash,materialized_row_hash
             FROM materialized_training_rows_v1
            WHERE contract_hash=?
            ORDER BY decision_time,source_decision_id""",
        (TRAINING_MATERIALIZATION_CONTRACT,),
    ).fetchall()
    try:
        decoded = [json.loads(row["row_json"]) for row in rows]
        valid = all(
            canonical_hash(item) == str(row["materialized_row_hash"])
            and canonical_hash(item["receipt"]) == str(row["source_receipt_hash"])
            for row, item in zip(rows, decoded)
        )
    except (json.JSONDecodeError, KeyError, TypeError):
        valid = False
    if not valid:
        with ledger.connection:
            ledger.connection.execute(
                "UPDATE training_materialization_state_v1 "
                "SET state='DIRTY',updated_at=? WHERE id=1",
                (datetime.now(UTC).isoformat(),),
            )
        refresh_training_materialization_state(ledger, cutoff)
        return complete_training_rows(ledger, cutoff)
    for item in decoded:
        item["receipt"] = tuple(item["receipt"])
        item["news_receipt"] = tuple(item["news_receipt"])
    return decoded


def _news_learning_rows(rows: list[dict]) -> list[dict]:
    """Keep observable partial/quiet rows; exclude infrastructure outages."""
    return [row for row in rows if row.get("news_training_eligible", True)]


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


def _persisted_crossfit_record(record) -> dict:
    return {
        "decision_id": str(record["source_decision_id"]),
        "fold": int(record["fold_number"]),
        "training_cutoff": str(record["training_cutoff"]),
        "purged_through": str(record["purged_through"]),
        "prediction": float(record["predicted_direction_u5"]),
        "target": float(record["target_direction_u5"]),
        "residual": float(record["residual_u5"]),
        "artifact_hash": str(record["artifact_hash"]),
    }


def chronological_crossfit_market(ledger, rows: list[dict], artifact_root: Path,
                                  created_at: datetime) -> list[dict]:
    """Produce expanding-window predictions with a purged 30-minute boundary."""
    predictions = []
    persisted = {
        str(row["source_decision_id"]): row
        for row in ledger.connection.execute(
            """SELECT * FROM market_crossfit_predictions
            WHERE crossfit_version=?""",
            (CROSSFIT_VERSION,),
        )
    }
    minimum_train = 48
    fold_size = 24
    for start in range(minimum_train, len(rows), fold_size):
        test = rows[start:start + fold_size]
        if not test:
            break
        test_start = datetime.fromisoformat(test[0]["decision_time"])
        purge_cutoff = test_start - timedelta(minutes=30)
        cached = [persisted.get(str(row["decision_id"])) for row in test]
        if all(record is not None for record in cached):
            predictions.extend(_persisted_crossfit_record(record) for record in cached)
            continue
        train = [
            row for row in rows[:start]
            if datetime.fromisoformat(row["decision_time"]) < purge_cutoff
        ]
        if len(train) < minimum_train:
            continue
        train_hash = canonical_hash([row["receipt"] for row in train])
        artifact = train_ridge(
            np.asarray([row["market"] for row in train]), np.asarray([row["target"] for row in train]),
            MARKET_FEATURES, 100.0, train_hash,
        )
        values = artifact.predict(np.asarray([row["market"] for row in test]))
        fold = start // fold_size
        for row, predicted, cached_record in zip(test, values, cached, strict=True):
            if cached_record is not None:
                predictions.append(_persisted_crossfit_record(cached_record))
                continue
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


def _has_positive_event_weight(rows: list[dict], field: str) -> bool:
    return any(
        max(0.0, float(event["raw_weight"])) > 0
        for row in rows for event in row.get(field, [])
    )


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
        source_id: (
            min(1.0, SOURCE_WEIGHT_BUDGET / total) if total > 0 else 0.0
        )
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
    zero_event_rows = [row for row in rows if not row.get(field)]
    positive_event_budgets = [
        budget for budget in bounded_event_budgets.values() if budget > 0
    ]
    zero_environment_budget = (
        min(positive_event_budgets)
        if zero_event_rows and positive_event_budgets else 0.0
    )
    zero_row_weight = (
        zero_environment_budget / len(zero_event_rows) if zero_event_rows else 0.0
    )
    row_weights = []
    receipts = []
    for row in rows:
        budget = 0.0
        for event in row.get(field, []):
            event_id = str(event["event_id"])
            raw = float(event["raw_weight"])
            event_total = totals[event_id]
            normalized = (
                raw / event_total * bounded_event_budgets[event_id]
                if event_total > 0 else 0.0
            )
            budget += normalized
            receipts.append({
                "source_decision_id": row["decision_id"],
                "event_id": event_id,
                "event_version_id": str(event["event_version_id"]),
                "raw_weight": raw,
                "normalized_event_weight": normalized,
            })
        row_weights.append(budget if row.get(field) else zero_row_weight)
    weights = np.asarray(row_weights, dtype=np.float64)
    reference_total = sum(bounded_reference_budgets.values()) + zero_environment_budget
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
        "observable_zero_news_rows": len(zero_event_rows),
        "observable_zero_news_budget": zero_environment_budget,
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


def require_current_contract_generation(connection) -> str:
    """Return the active live-stage generation or fail before decisions run."""
    generation = connection.execute(
        """SELECT g.* FROM news_model_generation_activations_v1 a
        JOIN news_model_generations_v1 g USING(generation_id)
        ORDER BY a.activated_at DESC,a.activation_id DESC LIMIT 1"""
    ).fetchone()
    if not generation_matches_contract(generation, CURRENT_NEWS_CONTRACT):
        raise RuntimeError(
            "current Core/Broad news contract has no active generation"
        )
    if generation["model_stage"] != LIVE_GENERATION_STAGE:
        raise RuntimeError(
            f"live collector requires a {LIVE_GENERATION_STAGE} generation; "
            f"latest active generation is {generation['model_stage']}"
        )
    members = {
        str(row["model_identity"]): str(row["artifact_path"])
        for row in connection.execute(
            """SELECT m.model_identity,u.artifact_path
            FROM news_model_generation_members_v1 m
            JOIN model_updates_v2 u USING(model_version)
            WHERE m.generation_id=?""",
            (generation["generation_id"],),
        )
    }
    if set(members) != REQUIRED_GENERATION_IDENTITIES:
        raise RuntimeError("active Core/Broad generation is incomplete")
    auxiliary = connection.execute(
        """SELECT u.artifact_path FROM news_model_generation_aux_members_v1 m
        JOIN model_updates_v2 u USING(model_version)
        WHERE m.generation_id=? AND m.model_identity='NEWS_ONLY'""",
        (generation["generation_id"],),
    ).fetchone()
    runtime_forward_root = sqlite_runtime_forward_root(connection)
    paths = [
        require_runtime_artifact_path(
            path, runtime_forward_root=runtime_forward_root,
        )
        for path in members.values()
    ]
    if auxiliary is None:
        raise RuntimeError("active Core/Broad generation has no NEWS_ONLY diagnostic")
    paths.append(require_runtime_artifact_path(
        str(auxiliary["artifact_path"]),
        runtime_forward_root=runtime_forward_root,
    ))
    return str(generation["generation_id"])


def _write_manifest(path: Path, payload: dict) -> str:
    digest = canonical_hash(payload)
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=False)
        path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return digest


def _neutral_core_news_artifact(dataset_hash: str) -> RidgeArtifact:
    """Represent an evidence-empty Core lane without inventing a signal."""
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
        weight_summary={"status": "COLD_START_NO_CORE_EVIDENCE"},
    )


def train_due_v2(ledger, cutoff: datetime, artifact_root: str | Path) -> list[dict]:
    """Build and atomically activate five core models plus one diagnostic."""
    materialization = refresh_training_materialization_state(ledger, cutoff)
    eligible_count = int(materialization["row_count"])
    if int(materialization.get("pending_source_rows", 0)):
        return [{"status": "MATERIALIZING", "complete_rows": eligible_count,
                 "remaining_rows": int(materialization["pending_source_rows"])}]
    if eligible_count < PREVIEW_ROWS:
        return [{"status": "ENGINEERING" if eligible_count < 30 else "EARLY_LEARNING",
                 "complete_rows": eligible_count, "next_threshold": PREVIEW_ROWS}]
    candidate_stage = "SHADOW" if eligible_count >= SHADOW_ROWS else "PREVIEW_ONLY"
    candidate_latest = _latest_generation(ledger.connection, candidate_stage)
    if (
        candidate_latest is not None
        and generation_matches_contract(candidate_latest, CURRENT_NEWS_CONTRACT)
        and materialization["state"] == "CLEAN"
        and eligible_count < int(candidate_latest["training_rows"]) + RETRAIN_INTERVAL
    ):
        return [{"status": "NOT_DUE", "complete_rows": eligible_count,
                 "generation_id": candidate_latest["generation_id"],
                 "next_threshold": int(candidate_latest["training_rows"]) + RETRAIN_INTERVAL}]
    rows = complete_training_rows(ledger, cutoff)
    count = len(rows)
    if count < PREVIEW_ROWS:
        return [{"status": "ENGINEERING" if count < 30 else "EARLY_LEARNING",
                 "complete_rows": count, "next_threshold": PREVIEW_ROWS}]
    stage = "SHADOW" if count >= SHADOW_ROWS else "PREVIEW_ONLY"
    training_rows = rows if stage == "PREVIEW_ONLY" else rows[: count - (count % RETRAIN_INTERVAL)]
    news_training_rows = _news_learning_rows(training_rows)
    core_rows = [row for row in news_training_rows if row.get("core_events")]
    broad_rows = [row for row in news_training_rows if row.get("broad_events")]
    core_events, core_days = _event_coverage(core_rows, "core_events")
    broad_events, broad_days = _event_coverage(broad_rows, "broad_events")
    core_evidence_status = news_evidence_status(core_days, core_events)
    broad_evidence_status = news_evidence_status(broad_days, broad_events)
    core_has_positive_weight = _has_positive_event_weight(core_rows, "core_events")
    broad_has_positive_weight = _has_positive_event_weight(
        broad_rows, "broad_events"
    )
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
    core_cold_start = (
        len(core_rows) < NEWS_MIN_EXPOSED_ROWS
        or not core_events
        or not core_has_positive_weight
    )
    if (
        len(broad_rows) < NEWS_MIN_EXPOSED_ROWS
        or not broad_events
        or not broad_has_positive_weight
    ):
        return [{
            "status": "NEWS_GENERATION_EVIDENCE_INSUFFICIENT",
            "core_exposed_rows": len(core_rows),
            "broad_exposed_rows": len(broad_rows),
            "core_events": core_events,
            "broad_events": broad_events,
            "core_evidence_status": core_evidence_status,
            "broad_evidence_status": broad_evidence_status,
            "core_has_positive_weight": core_has_positive_weight,
            "broad_has_positive_weight": broad_has_positive_weight,
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
    core_residual = [
        row for row in news_training_rows if row["decision_id"] in crossfit_by_id
    ]
    broad_residual = list(core_residual)
    core_exposed_residual = [row for row in core_residual if row.get("core_events")]
    broad_exposed_residual = [row for row in broad_residual if row.get("broad_events")]
    if len(broad_exposed_residual) < NEWS_MIN_EXPOSED_ROWS or (
        not core_cold_start
        and len(core_exposed_residual) < NEWS_MIN_EXPOSED_ROWS
    ):
        return [{"status": "NEWS_GENERATION_CROSSFIT_INSUFFICIENT",
                 "core_rows": len(core_exposed_residual),
                 "broad_rows": len(broad_exposed_residual)}]

    if core_cold_start:
        core_weights = np.asarray([], dtype=np.float64)
        core_weight_receipts = []
        core_source_receipts = []
        core_summary = {"status": "COLD_START_NO_CORE_EVIDENCE"}
    else:
        (core_weights, core_weight_receipts,
         core_source_receipts, core_summary) = _event_budget_weights(
            core_residual, "core_events"
        )
    (broad_weights, broad_weight_receipts,
     broad_source_receipts, broad_summary) = _event_budget_weights(
        broad_residual, "broad_events"
    )
    core_hash = canonical_hash(
        (
            "COLD_START_NO_CORE_EVIDENCE",
            EVIDENCE_POLICY_VERSION,
            NEWS_FEATURE_VERSION,
            ELIGIBILITY_VERSION,
            market_hash,
        )
        if core_cold_start else [
            (row["decision_id"], row.get("news_receipt", row["receipt"]),
             crossfit_by_id[row["decision_id"]]["artifact_hash"],
             crossfit_by_id[row["decision_id"]]["residual"], receipt)
            for row, receipt in zip(core_residual, core_weights.tolist())
        ]
    )
    broad_hash = canonical_hash([
        (row["decision_id"], row.get("news_receipt", row["receipt"]), crossfit_by_id[row["decision_id"]]["artifact_hash"],
         crossfit_by_id[row["decision_id"]]["residual"], receipt)
        for row, receipt in zip(broad_residual, broad_weights.tolist())
    ])
    news_only_hash = canonical_hash([
        (row["decision_id"], row.get("news_receipt", row["receipt"]), row["target"], receipt)
        for row, receipt in zip(broad_residual, broad_weights.tolist())
    ])
    event_snapshot_hash = canonical_hash(sorted({
        (event["event_id"], event["event_version_id"])
        for row in training_rows for field in ("core_events", "broad_events")
        for event in row.get(field, [])
    }))
    generation_seed = canonical_hash((
        stage, cutoff.isoformat(), EVIDENCE_POLICY_VERSION, NEWS_FEATURE_VERSION,
        ELIGIBILITY_VERSION, EVENT_WEIGHTING_VERSION, event_snapshot_hash,
        market_hash, core_hash, broad_hash, news_only_hash,
    ))
    generation_id = str(uuid.uuid5(uuid.NAMESPACE_URL, generation_seed))
    slug = generation_id.split("-")[0]

    core_version = (
        f"core-news-residual-{core_evidence_status.lower().replace('_', '-')}-"
        f"{stage.lower()}-{slug}-{core_hash[:12]}"
    )
    core_artifact = (
        _neutral_core_news_artifact(core_hash)
        if core_cold_start else
        train_ridge(
            np.asarray([row["news"] for row in core_residual]),
            np.asarray([
                crossfit_by_id[row["decision_id"]]["residual"]
                for row in core_residual
            ]),
            NEWS_FEATURES, 100.0, core_hash, core_weights,
            EVENT_WEIGHTING_VERSION, core_summary,
        )
    )
    core_path = root / core_version / "model.json"
    if not core_path.exists():
        core_artifact.write(core_path)

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
        "schema": "xauusd.phase2f.core-full-model.v4", "generation_id": generation_id,
        "market_model_version": market_version, "market_artifact_path": str(market_path),
        "market_artifact_hash": market_artifact.artifact_hash,
        "news_model_version": core_version, "news_artifact_path": str(core_path),
        "news_artifact_hash": core_artifact.artifact_hash,
        "training_dataset_hash": market_hash, "news_training_hash": core_hash,
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
        market_path, core_path, full_path, broad_path, broad_full_path,
        news_only_path,
    )
    if any(not path.is_absolute() or not path.exists() for path in required_paths):
        return [{"status": "GENERATION_ARTIFACT_VALIDATION_FAILED",
                 "generation_id": generation_id}]

    updates = (
        (market_version, "MARKET_ONLY", len(training_rows), len(core_rows),
         core_events, core_days, market_hash, FEATURE_VERSION, None,
         market_path, market_artifact.artifact_hash),
        (core_version, "NEWS_RESIDUAL", len(core_residual), len(core_rows),
         core_events, core_days, core_hash, NEWS_FEATURE_VERSION,
         ELIGIBILITY_VERSION, core_path, core_artifact.artifact_hash),
        (full_version, "FULL", len(training_rows), len(core_rows),
         core_events, core_days, market_hash,
         f"{FEATURE_VERSION}+{NEWS_FEATURE_VERSION}", ELIGIBILITY_VERSION,
         full_path, full_artifact_hash),
        (broad_version, "BROAD_NEWS_RESIDUAL", len(broad_residual), len(broad_rows),
         broad_events, broad_days, broad_hash, NEWS_FEATURE_VERSION,
         ELIGIBILITY_VERSION, broad_path, broad_artifact.artifact_hash),
        (broad_full_version, "BROAD_FULL", len(training_rows), len(broad_rows),
         broad_events, broad_days, market_hash,
         f"{FEATURE_VERSION}+{NEWS_FEATURE_VERSION}+{EVIDENCE_POLICY_VERSION}",
         ELIGIBILITY_VERSION, broad_full_path, broad_full_hash),
        (news_only_version, "NEWS_ONLY", len(broad_residual), len(broad_rows),
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
             event_snapshot_hash, market_hash, core_hash, broad_hash,
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
             "COMPLETE_CORE_BROAD_GENERATION_WITH_NEWS_ONLY_DIAGNOSTIC"),
        )
        for lane, receipts in (
            (CORE_EVIDENCE_STORAGE_LANE, core_weight_receipts),
            ("BROAD", broad_weight_receipts),
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
            (CORE_EVIDENCE_STORAGE_LANE, core_source_receipts),
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
            else "COLD_START_NO_CORE_EVIDENCE"
            if core_cold_start and update[1] in {"NEWS_RESIDUAL", "FULL"}
            else core_evidence_status
        ),
    } for update in updates]
