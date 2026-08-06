"""Append-only Phase 2F V2 evidence lanes and migration schema."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone


UTC = timezone.utc
EVIDENCE_CONTRACT_VERSION = "phase2f-evidence-integrity-v2"
FEATURE_VERSION = "repaired-market-v2"
NEWS_FEATURE_VERSION = "eligible-news-event-evidence-v3"
LABEL_VERSION = "received-time-executable-30m-v2"
ELIGIBILITY_VERSION = "news-source-eligibility-v2-event-evidence"

V2_IMMUTABLE_TABLES = (
    "repair_batches",
    "evaluation_epochs",
    "evidence_lane_assignments",
    "source_eligibility_versions",
    "source_eligibility_rules",
    "derived_market_snapshots",
    "derived_news_feature_snapshots",
    "derived_outcomes",
    "training_eligibility_v2",
    "market_crossfit_predictions",
    "model_updates_v2",
    "predictions_v2",
    "prediction_scores_v2",
    "calibration_snapshots_v2",
    "execution_training_examples_v1",
    "execution_model_updates_v1",
    "execution_predictions_v1",
    "execution_prediction_scores_v1",
    "execution_training_examples_v2",
    "execution_model_updates_v2",
    "execution_predictions_v2",
    "execution_position_scores_v2",
)

V2_SCHEMA = """
CREATE TABLE IF NOT EXISTS repair_batches (
    repair_batch_id TEXT PRIMARY KEY,
    source_cutoff TEXT NOT NULL,
    old_contract_versions_json TEXT NOT NULL,
    new_contract_versions_json TEXT NOT NULL,
    code_commit TEXT NOT NULL,
    started_at TEXT NOT NULL,
    completed_at TEXT NOT NULL,
    source_evidence_hash TEXT NOT NULL,
    output_evidence_hash TEXT NOT NULL,
    repaired_row_count INTEGER NOT NULL,
    unrepaired_row_count INTEGER NOT NULL,
    unrepaired_reason_distribution_json TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('COMPLETED','COMPLETED_WITH_GAPS'))
);

CREATE TABLE IF NOT EXISTS evaluation_epochs (
    epoch_id TEXT PRIMARY KEY,
    collection_epoch TEXT NOT NULL,
    evaluation_epoch TEXT NOT NULL UNIQUE,
    source_cutoff TEXT NOT NULL,
    created_at TEXT NOT NULL,
    code_commit TEXT NOT NULL,
    contract_version TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS evidence_lane_assignments (
    assignment_id TEXT PRIMARY KEY,
    evidence_type TEXT NOT NULL,
    evidence_id TEXT NOT NULL,
    lane TEXT NOT NULL CHECK(lane IN ('LEGACY_ENGINEERING','REPAIRED_SEED','LIVE_OOS')),
    assigned_at TEXT NOT NULL,
    rule_version TEXT NOT NULL,
    source_hash TEXT NOT NULL,
    repair_batch_id TEXT,
    UNIQUE(evidence_type, evidence_id, rule_version)
);

CREATE TABLE IF NOT EXISTS source_eligibility_versions (
    eligibility_version TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    frozen_config_hash TEXT NOT NULL,
    description TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS source_eligibility_rules (
    eligibility_version TEXT NOT NULL,
    source TEXT NOT NULL,
    maximum_tier TEXT NOT NULL CHECK(maximum_tier IN (
        'COLLECT_ONLY','DISPLAY_ONLY','RESEARCH_CANDIDATE','MODEL_ELIGIBLE')),
    requires_publisher_body INTEGER NOT NULL CHECK(requires_publisher_body IN (0,1)),
    minimum_body_characters INTEGER NOT NULL,
    rationale TEXT NOT NULL,
    PRIMARY KEY(eligibility_version, source),
    FOREIGN KEY(eligibility_version) REFERENCES source_eligibility_versions(eligibility_version)
);

CREATE TABLE IF NOT EXISTS derived_market_snapshots (
    derived_snapshot_id TEXT PRIMARY KEY,
    source_decision_id TEXT NOT NULL,
    decision_time TEXT NOT NULL,
    source_snapshot_hash TEXT NOT NULL,
    repair_batch_id TEXT,
    evidence_lane TEXT NOT NULL CHECK(evidence_lane IN ('REPAIRED_SEED','LIVE_OOS')),
    recomputed_at TEXT NOT NULL,
    feature_version TEXT NOT NULL,
    u5_version TEXT NOT NULL,
    u5 REAL,
    features_json TEXT NOT NULL,
    data_health TEXT NOT NULL,
    reason_codes_json TEXT NOT NULL,
    source_evidence_hash TEXT NOT NULL,
    output_hash TEXT NOT NULL,
    UNIQUE(source_decision_id, feature_version)
);

CREATE TABLE IF NOT EXISTS derived_news_feature_snapshots (
    derived_news_snapshot_id TEXT PRIMARY KEY,
    source_decision_id TEXT NOT NULL,
    decision_time TEXT NOT NULL,
    repair_batch_id TEXT,
    evidence_lane TEXT NOT NULL CHECK(evidence_lane IN ('REPAIRED_SEED','LIVE_OOS')),
    recomputed_at TEXT NOT NULL,
    feature_version TEXT NOT NULL,
    eligibility_version TEXT NOT NULL,
    features_json TEXT NOT NULL,
    model_visible_items INTEGER NOT NULL,
    news_exposed INTEGER NOT NULL CHECK(news_exposed IN (0,1)),
    distinct_news_clusters INTEGER NOT NULL,
    distinct_event_types INTEGER NOT NULL,
    source_evidence_hash TEXT NOT NULL,
    output_hash TEXT NOT NULL,
    UNIQUE(source_decision_id, feature_version, eligibility_version)
);

CREATE TABLE IF NOT EXISTS derived_outcomes (
    derived_outcome_id TEXT PRIMARY KEY,
    source_decision_id TEXT NOT NULL,
    decision_time TEXT NOT NULL,
    repair_batch_id TEXT,
    evidence_lane TEXT NOT NULL CHECK(evidence_lane IN ('REPAIRED_SEED','LIVE_OOS')),
    recomputed_at TEXT NOT NULL,
    label_version TEXT NOT NULL,
    outcome_status TEXT NOT NULL CHECK(outcome_status IN ('VALID','UNREPAIRABLE')),
    reason_codes_json TEXT NOT NULL,
    entry_event_time TEXT,
    entry_received_time TEXT,
    entry_receipt_delay_seconds REAL,
    exit_event_time TEXT,
    exit_received_time TEXT,
    exit_receipt_delay_seconds REAL,
    maximum_event_gap REAL,
    maximum_receipt_gap REAL,
    quote_coverage REAL,
    ambiguity_state TEXT NOT NULL,
    gross_midpoint_direction_move REAL,
    long_quote_return REAL,
    short_quote_return REAL,
    spread_quote_cost REAL,
    long_mfe REAL,
    long_mae REAL,
    short_mfe REAL,
    short_mae REAL,
    maximum_spread REAL,
    break_even_commission_long REAL,
    break_even_commission_short REAL,
    commission_status TEXT NOT NULL CHECK(commission_status='UNCONFIGURED'),
    slippage_status TEXT NOT NULL CHECK(slippage_status='UNAVAILABLE_SHADOW'),
    source_evidence_hash TEXT NOT NULL,
    output_hash TEXT NOT NULL,
    UNIQUE(source_decision_id, label_version)
);

CREATE TABLE IF NOT EXISTS training_eligibility_v2 (
    eligibility_id TEXT PRIMARY KEY,
    source_decision_id TEXT NOT NULL,
    evidence_lane TEXT NOT NULL CHECK(evidence_lane IN ('REPAIRED_SEED','LIVE_OOS')),
    eligible_at TEXT NOT NULL,
    eligibility_version TEXT NOT NULL,
    derived_snapshot_hash TEXT NOT NULL,
    derived_outcome_hash TEXT NOT NULL,
    derived_news_hash TEXT,
    UNIQUE(source_decision_id, eligibility_version)
);

CREATE TABLE IF NOT EXISTS market_crossfit_predictions (
    source_decision_id TEXT NOT NULL,
    crossfit_version TEXT NOT NULL,
    fold_number INTEGER NOT NULL,
    training_cutoff TEXT NOT NULL,
    purged_through TEXT NOT NULL,
    predicted_direction_u5 REAL NOT NULL,
    target_direction_u5 REAL NOT NULL,
    residual_u5 REAL NOT NULL,
    artifact_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY(source_decision_id, crossfit_version)
);

CREATE TABLE IF NOT EXISTS model_updates_v2 (
    model_version TEXT PRIMARY KEY,
    model_identity TEXT NOT NULL,
    model_stage TEXT NOT NULL CHECK(model_stage IN ('PREVIEW_ONLY','SHADOW')),
    created_at TEXT NOT NULL,
    training_cutoff TEXT NOT NULL,
    training_rows INTEGER NOT NULL,
    repaired_seed_rows INTEGER NOT NULL,
    live_oos_rows INTEGER NOT NULL,
    news_exposed_rows INTEGER NOT NULL,
    distinct_news_clusters INTEGER NOT NULL,
    distinct_event_days INTEGER NOT NULL,
    training_dataset_hash TEXT NOT NULL,
    feature_version TEXT NOT NULL,
    eligibility_version TEXT,
    artifact_path TEXT NOT NULL,
    artifact_hash TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status='CHALLENGER')
);

CREATE TABLE IF NOT EXISTS predictions_v2 (
    source_decision_id TEXT NOT NULL,
    model_version TEXT NOT NULL,
    model_identity TEXT NOT NULL,
    decision_time TEXT NOT NULL,
    created_at TEXT NOT NULL,
    evidence_lane TEXT NOT NULL CHECK(evidence_lane='LIVE_OOS'),
    feature_snapshot_hash TEXT NOT NULL,
    predicted_direction_u5 REAL,
    predicted_news_residual_u5 REAL,
    ev_long_u5 REAL,
    ev_short_u5 REAL,
    lcb_long_u5 REAL,
    lcb_short_u5 REAL,
    uncertainty_method TEXT NOT NULL,
    calibration_version TEXT,
    calibration_rows INTEGER NOT NULL,
    calibration_effective_blocks INTEGER NOT NULL,
    calibration_distinct_days INTEGER NOT NULL,
    interval_width REAL,
    calibration_status TEXT NOT NULL,
    recommended_action TEXT NOT NULL CHECK(recommended_action IN ('LONG','SHORT','WAIT')),
    effective_action TEXT NOT NULL CHECK(effective_action='WAIT'),
    prediction_status TEXT NOT NULL,
    PRIMARY KEY(source_decision_id, model_version)
);

CREATE TABLE IF NOT EXISTS prediction_scores_v2 (
    source_decision_id TEXT NOT NULL,
    model_version TEXT NOT NULL,
    scored_at TEXT NOT NULL,
    value_quote_return REAL NOT NULL,
    target_direction_u5 REAL,
    residual_u5 REAL,
    squared_error REAL,
    direction_correct INTEGER,
    high_confidence_error INTEGER,
    score_hash TEXT NOT NULL,
    PRIMARY KEY(source_decision_id, model_version),
    FOREIGN KEY(source_decision_id, model_version)
      REFERENCES predictions_v2(source_decision_id, model_version)
);

CREATE TABLE IF NOT EXISTS calibration_snapshots_v2 (
    calibration_version TEXT PRIMARY KEY,
    model_identity TEXT NOT NULL,
    created_at TEXT NOT NULL,
    cutoff TEXT NOT NULL,
    method TEXT NOT NULL,
    rows INTEGER NOT NULL,
    effective_blocks INTEGER NOT NULL,
    distinct_days INTEGER NOT NULL,
    interval_half_width_u5 REAL,
    status TEXT NOT NULL,
    source_hash TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS execution_training_examples_v1 (
    example_id TEXT PRIMARY KEY,
    source_decision_id TEXT NOT NULL,
    direction TEXT NOT NULL CHECK(direction IN ('LONG','SHORT')),
    checkpoint_minutes INTEGER NOT NULL CHECK(checkpoint_minutes IN (0,5,10,15,20,25)),
    observed_at TEXT NOT NULL,
    evidence_lane TEXT NOT NULL CHECK(evidence_lane IN ('REPAIRED_SEED','LIVE_OOS')),
    feature_json TEXT NOT NULL,
    target_value REAL NOT NULL,
    target_action TEXT NOT NULL,
    source_hash TEXT NOT NULL,
    UNIQUE(source_decision_id,direction,checkpoint_minutes)
);

CREATE TABLE IF NOT EXISTS execution_model_updates_v1 (
    model_version TEXT PRIMARY KEY,
    model_identity TEXT NOT NULL CHECK(model_identity IN ('LOT_RIDGE','EXIT_RIDGE')),
    model_stage TEXT NOT NULL CHECK(model_stage IN ('PREVIEW_ONLY','SHADOW')),
    created_at TEXT NOT NULL,
    training_cutoff TEXT NOT NULL,
    training_rows INTEGER NOT NULL,
    training_dataset_hash TEXT NOT NULL,
    feature_version TEXT NOT NULL,
    label_version TEXT NOT NULL,
    artifact_path TEXT NOT NULL,
    artifact_hash TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status='CHALLENGER')
);

CREATE TABLE IF NOT EXISTS execution_predictions_v1 (
    source_decision_id TEXT NOT NULL,
    model_version TEXT NOT NULL,
    model_identity TEXT NOT NULL CHECK(model_identity IN ('LOT_RIDGE','EXIT_RIDGE')),
    direction TEXT NOT NULL CHECK(direction IN ('LONG','SHORT')),
    checkpoint_minutes INTEGER NOT NULL,
    prediction_time TEXT NOT NULL,
    created_at TEXT NOT NULL,
    predicted_value REAL NOT NULL,
    recommended_action TEXT NOT NULL,
    prediction_status TEXT NOT NULL,
    feature_hash TEXT NOT NULL,
    PRIMARY KEY(source_decision_id,model_version,direction,checkpoint_minutes)
);

CREATE TABLE IF NOT EXISTS execution_prediction_scores_v1 (
    source_decision_id TEXT NOT NULL,
    model_version TEXT NOT NULL,
    direction TEXT NOT NULL,
    checkpoint_minutes INTEGER NOT NULL,
    scored_at TEXT NOT NULL,
    target_value REAL NOT NULL,
    selected_utility REAL NOT NULL,
    squared_error REAL NOT NULL,
    score_hash TEXT NOT NULL,
    PRIMARY KEY(source_decision_id,model_version,direction,checkpoint_minutes),
    FOREIGN KEY(source_decision_id,model_version,direction,checkpoint_minutes)
      REFERENCES execution_predictions_v1(source_decision_id,model_version,direction,checkpoint_minutes)
);

CREATE TABLE IF NOT EXISTS execution_training_examples_v2 (
    source_decision_id TEXT PRIMARY KEY,
    source_model_identity TEXT NOT NULL CHECK(source_model_identity='BROAD_FULL'),
    source_model_version TEXT NOT NULL,
    direction TEXT NOT NULL CHECK(direction IN ('LONG','SHORT')),
    observed_at TEXT NOT NULL,
    evidence_lane TEXT NOT NULL CHECK(evidence_lane IN ('REPAIRED_SEED','LIVE_OOS')),
    feature_json TEXT NOT NULL,
    u5 REAL NOT NULL,
    final_quote_return REAL NOT NULL,
    adverse_u5 REAL NOT NULL,
    checkpoint_path_json TEXT NOT NULL,
    source_hash TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS execution_model_updates_v2 (
    model_version TEXT PRIMARY KEY,
    model_identity TEXT NOT NULL CHECK(model_identity IN ('LOT_RIDGE','EXIT_RIDGE')),
    model_stage TEXT NOT NULL CHECK(model_stage IN ('PREVIEW_ONLY','SHADOW')),
    created_at TEXT NOT NULL,
    training_cutoff TEXT NOT NULL,
    training_decisions INTEGER NOT NULL,
    training_observations INTEGER NOT NULL,
    training_dataset_hash TEXT NOT NULL,
    feature_version TEXT NOT NULL,
    label_version TEXT NOT NULL,
    artifact_paths_json TEXT NOT NULL,
    artifact_hash TEXT NOT NULL,
    source_model_identity TEXT NOT NULL CHECK(source_model_identity='BROAD_FULL'),
    status TEXT NOT NULL CHECK(status='CHALLENGER')
);

CREATE TABLE IF NOT EXISTS execution_predictions_v2 (
    source_decision_id TEXT NOT NULL,
    model_version TEXT NOT NULL,
    model_identity TEXT NOT NULL CHECK(model_identity IN ('LOT_RIDGE','EXIT_RIDGE')),
    source_model_identity TEXT NOT NULL CHECK(source_model_identity='BROAD_FULL'),
    source_model_version TEXT NOT NULL,
    direction TEXT NOT NULL CHECK(direction IN ('LONG','SHORT')),
    checkpoint_minutes INTEGER NOT NULL CHECK(checkpoint_minutes IN (0,5,10,15,20,25)),
    prediction_time TEXT NOT NULL,
    created_at TEXT NOT NULL,
    predicted_value REAL NOT NULL,
    recommended_action TEXT NOT NULL,
    current_quote_return REAL,
    prediction_status TEXT NOT NULL CHECK(prediction_status='SHADOW_ONLY'),
    feature_hash TEXT NOT NULL,
    PRIMARY KEY(source_decision_id,model_version,checkpoint_minutes)
);

CREATE TABLE IF NOT EXISTS execution_position_scores_v2 (
    source_decision_id TEXT NOT NULL,
    model_version TEXT NOT NULL,
    model_identity TEXT NOT NULL CHECK(model_identity IN ('LOT_RIDGE','EXIT_RIDGE')),
    scored_at TEXT NOT NULL,
    direction TEXT NOT NULL CHECK(direction IN ('LONG','SHORT')),
    selected_action TEXT NOT NULL,
    exit_minutes INTEGER NOT NULL,
    selected_quote_return REAL NOT NULL,
    baseline_quote_return REAL NOT NULL,
    delta_quote_return REAL NOT NULL,
    score_hash TEXT NOT NULL,
    PRIMARY KEY(source_decision_id,model_version),
    FOREIGN KEY(model_version)
      REFERENCES execution_model_updates_v2(model_version)
);

CREATE INDEX IF NOT EXISTS derived_market_time_v2
ON derived_market_snapshots(decision_time, evidence_lane);
CREATE INDEX IF NOT EXISTS derived_outcome_time_v2
ON derived_outcomes(decision_time, evidence_lane, outcome_status);
CREATE INDEX IF NOT EXISTS prediction_v2_time
ON predictions_v2(model_identity, decision_time);
CREATE INDEX IF NOT EXISTS execution_examples_time_v1
ON execution_training_examples_v1(checkpoint_minutes, observed_at);
CREATE INDEX IF NOT EXISTS execution_predictions_time_v1
ON execution_predictions_v1(model_identity, prediction_time);
CREATE INDEX IF NOT EXISTS execution_examples_time_v2
ON execution_training_examples_v2(observed_at);
CREATE INDEX IF NOT EXISTS execution_predictions_time_v2
ON execution_predictions_v2(model_identity, prediction_time);
"""


def _repair_execution_score_foreign_key(connection: sqlite3.Connection) -> None:
    """Replace the invalid two-column FK shipped with the initial V2 table.

    ``execution_predictions_v2`` is unique by decision, model, and checkpoint.
    A two-column reference to only decision and model is therefore invalid in
    SQLite and causes outcome settlement to abort as soon as a score is added.
    Score rows retain the decision identity while their enforceable parent is
    the immutable model artifact that produced the prediction.
    """
    row = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' "
        "AND name='execution_position_scores_v2'"
    ).fetchone()
    if row is None or "REFERENCES execution_predictions_v2" not in str(row[0]):
        return
    connection.executescript(
        """
        ALTER TABLE execution_position_scores_v2
          RENAME TO execution_position_scores_v2_invalid_fk;
        CREATE TABLE execution_position_scores_v2 (
            source_decision_id TEXT NOT NULL,
            model_version TEXT NOT NULL,
            model_identity TEXT NOT NULL CHECK(model_identity IN ('LOT_RIDGE','EXIT_RIDGE')),
            scored_at TEXT NOT NULL,
            direction TEXT NOT NULL CHECK(direction IN ('LONG','SHORT')),
            selected_action TEXT NOT NULL,
            exit_minutes INTEGER NOT NULL,
            selected_quote_return REAL NOT NULL,
            baseline_quote_return REAL NOT NULL,
            delta_quote_return REAL NOT NULL,
            score_hash TEXT NOT NULL,
            PRIMARY KEY(source_decision_id,model_version),
            FOREIGN KEY(model_version)
              REFERENCES execution_model_updates_v2(model_version)
        );
        INSERT INTO execution_position_scores_v2
          SELECT * FROM execution_position_scores_v2_invalid_fk;
        DROP TABLE execution_position_scores_v2_invalid_fk;
        """
    )


def install_v2_schema(connection: sqlite3.Connection) -> None:
    """Create V2 structures and append-only guards; never mutate old rows."""
    connection.executescript(V2_SCHEMA)
    _repair_execution_score_foreign_key(connection)
    for table in V2_IMMUTABLE_TABLES:
        for operation in ("UPDATE", "DELETE"):
            connection.execute(
                f"""CREATE TRIGGER IF NOT EXISTS prevent_{operation.lower()}_{table}
                BEFORE {operation} ON {table}
                BEGIN SELECT RAISE(ABORT, '{table} is append-only'); END"""
            )


def evaluation_epoch(connection: sqlite3.Connection) -> datetime | None:
    row = connection.execute(
        "SELECT evaluation_epoch FROM evaluation_epochs ORDER BY created_at DESC LIMIT 1"
    ).fetchone()
    return datetime.fromisoformat(row[0]) if row else None
