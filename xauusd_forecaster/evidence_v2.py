"""Append-only Phase 2F V2 evidence lanes and migration schema."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from .news_contracts import CURRENT_NEWS_CONTRACT


UTC = timezone.utc
EVIDENCE_CONTRACT_VERSION = "phase2f-evidence-integrity-v2"
FEATURE_VERSION = "repaired-market-v2"
NEWS_FEATURE_VERSION = CURRENT_NEWS_CONTRACT.feature_version
LABEL_VERSION = "received-time-executable-30m-v2"
ELIGIBILITY_VERSION = CURRENT_NEWS_CONTRACT.eligibility_version

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
    "news_model_visibility_events_v1",
    "news_model_visibility_receipts_v1",
    "news_event_catalog_v1",
    "news_event_source_budgets_v1",
    "news_decision_event_snapshots_v1",
    "news_model_generations_v1",
    "news_model_generation_members_v1",
    "news_model_generation_aux_members_v1",
    "news_model_generation_activations_v1",
    "news_training_weight_receipts_v1",
    "news_training_source_budget_receipts_v1",
    "news_only_visibility_receipts_v1",
    "news_item_classifications_v1",
    "news_impact_assessments_v1",
    "news_event_identity_resolutions_v1",
    "news_impact_failures_v1",
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

CREATE TABLE IF NOT EXISTS news_model_visibility_receipts_v1 (
    receipt_id TEXT PRIMARY KEY,
    source_decision_id TEXT NOT NULL,
    decision_time TEXT NOT NULL,
    model_identity TEXT NOT NULL CHECK(model_identity IN (
        'NEWS_RESIDUAL','FULL','BROAD_NEWS_RESIDUAL','BROAD_FULL')),
    model_version TEXT NOT NULL,
    eligibility_version TEXT NOT NULL,
    evidence_policy_version TEXT NOT NULL,
    evidence_lane TEXT NOT NULL CHECK(evidence_lane IN ('OFFICIAL','BROAD')),
    event_key TEXT NOT NULL,
    event_source_hash TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    receipt_origin TEXT NOT NULL CHECK(receipt_origin IN ('LIVE','POINT_IN_TIME_REPLAY')),
    UNIQUE(source_decision_id, model_version, evidence_lane, event_key),
    FOREIGN KEY(source_decision_id, model_version)
      REFERENCES predictions_v2(source_decision_id, model_version)
);

CREATE TABLE IF NOT EXISTS news_model_visibility_events_v1 (
    event_source_hash TEXT PRIMARY KEY,
    event_key TEXT NOT NULL,
    canonical_headline TEXT NOT NULL,
    canonical_source TEXT NOT NULL,
    source_published_time TEXT,
    collector_first_seen_time TEXT NOT NULL,
    topics_json TEXT NOT NULL,
    evidence_grade TEXT NOT NULL,
    first_recorded_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS news_event_catalog_v1 (
    event_version_id TEXT PRIMARY KEY,
    event_id TEXT NOT NULL,
    policy_version TEXT NOT NULL,
    event_occurred_at TEXT NOT NULL,
    event_clock_source TEXT NOT NULL CHECK(event_clock_source IN (
        'EXPLICIT_BODY_TIME','OFFICIAL_RELEASE_TIME','SOURCE_STRUCTURED_TIME')),
    event_time_precision TEXT NOT NULL CHECK(event_time_precision='TIMESTAMP'),
    canonical_source TEXT NOT NULL,
    canonical_source_item_id TEXT NOT NULL,
    source_hash TEXT NOT NULL,
    evidence_grade TEXT NOT NULL,
    model_permissions_json TEXT NOT NULL,
    reason_codes_json TEXT NOT NULL,
    first_recorded_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS news_decision_event_snapshots_v1 (
    source_decision_id TEXT NOT NULL,
    decision_time TEXT NOT NULL,
    event_id TEXT NOT NULL,
    event_version_id TEXT NOT NULL REFERENCES news_event_catalog_v1(event_version_id),
    policy_version TEXT NOT NULL,
    model_permission TEXT NOT NULL CHECK(model_permission IN (
        'OFFICIAL_MODEL','BROAD_MODEL','DISPLAY_ONLY')),
    raw_weight REAL NOT NULL CHECK(raw_weight >= 0),
    age_minutes REAL NOT NULL CHECK(age_minutes >= 0),
    snapshot_hash TEXT NOT NULL,
    PRIMARY KEY(source_decision_id,event_version_id,model_permission)
);

CREATE TABLE IF NOT EXISTS news_event_source_budgets_v1 (
    event_version_id TEXT PRIMARY KEY REFERENCES news_event_catalog_v1(event_version_id),
    source_budget_id TEXT NOT NULL,
    identity_basis TEXT NOT NULL CHECK(identity_basis IN (
        'REPORTING_ORGANIZATION','COLLECTOR_SOURCE')),
    first_recorded_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS news_model_generations_v1 (
    generation_id TEXT PRIMARY KEY,
    model_stage TEXT NOT NULL CHECK(model_stage IN ('PREVIEW_ONLY','SHADOW')),
    created_at TEXT NOT NULL,
    training_cutoff TEXT NOT NULL,
    policy_version TEXT NOT NULL,
    feature_version TEXT NOT NULL,
    eligibility_version TEXT NOT NULL,
    event_snapshot_hash TEXT NOT NULL,
    market_dataset_hash TEXT NOT NULL,
    official_news_dataset_hash TEXT NOT NULL,
    broad_news_dataset_hash TEXT NOT NULL,
    weighting_version TEXT NOT NULL,
    member_count INTEGER NOT NULL CHECK(member_count=5),
    status TEXT NOT NULL CHECK(status='READY')
);

CREATE TABLE IF NOT EXISTS news_model_generation_members_v1 (
    generation_id TEXT NOT NULL REFERENCES news_model_generations_v1(generation_id),
    model_identity TEXT NOT NULL CHECK(model_identity IN (
        'MARKET_ONLY','NEWS_RESIDUAL','FULL','BROAD_NEWS_RESIDUAL','BROAD_FULL')),
    model_version TEXT NOT NULL REFERENCES model_updates_v2(model_version),
    PRIMARY KEY(generation_id,model_identity),
    UNIQUE(model_version)
);

CREATE TABLE IF NOT EXISTS news_model_generation_aux_members_v1 (
    generation_id TEXT NOT NULL REFERENCES news_model_generations_v1(generation_id),
    model_identity TEXT NOT NULL CHECK(model_identity='NEWS_ONLY'),
    model_version TEXT NOT NULL REFERENCES model_updates_v2(model_version),
    PRIMARY KEY(generation_id,model_identity),
    UNIQUE(model_version)
);

CREATE TABLE IF NOT EXISTS news_model_generation_activations_v1 (
    activation_id TEXT PRIMARY KEY,
    generation_id TEXT NOT NULL REFERENCES news_model_generations_v1(generation_id),
    previous_generation_id TEXT,
    activated_at TEXT NOT NULL,
    reason TEXT NOT NULL,
    UNIQUE(generation_id)
);

CREATE TABLE IF NOT EXISTS news_training_weight_receipts_v1 (
    generation_id TEXT NOT NULL REFERENCES news_model_generations_v1(generation_id),
    evidence_lane TEXT NOT NULL CHECK(evidence_lane IN ('OFFICIAL','BROAD')),
    source_decision_id TEXT NOT NULL,
    event_id TEXT NOT NULL,
    event_version_id TEXT NOT NULL,
    raw_weight REAL NOT NULL CHECK(raw_weight >= 0),
    normalized_event_weight REAL NOT NULL CHECK(normalized_event_weight >= 0),
    receipt_hash TEXT NOT NULL,
    PRIMARY KEY(generation_id,evidence_lane,source_decision_id,event_version_id)
);

CREATE TABLE IF NOT EXISTS news_training_source_budget_receipts_v1 (
    generation_id TEXT NOT NULL REFERENCES news_model_generations_v1(generation_id),
    evidence_lane TEXT NOT NULL CHECK(evidence_lane IN ('OFFICIAL','BROAD')),
    source_budget_id TEXT NOT NULL,
    unbounded_weight REAL NOT NULL CHECK(unbounded_weight >= 0),
    bounded_weight REAL NOT NULL CHECK(bounded_weight >= 0),
    receipt_hash TEXT NOT NULL,
    PRIMARY KEY(generation_id,evidence_lane,source_budget_id)
);

CREATE TABLE IF NOT EXISTS news_only_visibility_receipts_v1 (
    receipt_id TEXT PRIMARY KEY,
    source_decision_id TEXT NOT NULL,
    decision_time TEXT NOT NULL,
    model_identity TEXT NOT NULL CHECK(model_identity='NEWS_ONLY'),
    model_version TEXT NOT NULL,
    eligibility_version TEXT NOT NULL,
    evidence_policy_version TEXT NOT NULL,
    evidence_lane TEXT NOT NULL CHECK(evidence_lane='BROAD'),
    event_key TEXT NOT NULL,
    event_source_hash TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    receipt_origin TEXT NOT NULL CHECK(receipt_origin IN ('LIVE','POINT_IN_TIME_REPLAY')),
    UNIQUE(source_decision_id, model_version, evidence_lane, event_key),
    FOREIGN KEY(source_decision_id, model_version)
      REFERENCES predictions_v2(source_decision_id, model_version)
);

CREATE TABLE IF NOT EXISTS news_item_classifications_v1 (
    classification_id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    source_item_id TEXT NOT NULL,
    revision_number INTEGER,
    classified_at TEXT NOT NULL,
    policy_version TEXT NOT NULL,
    visibility_status TEXT NOT NULL CHECK(visibility_status IN (
        'DISPLAY_VISIBLE','MODEL_CANDIDATE','MODEL_ELIGIBLE','ARCHIVAL_ONLY',
        'CONTENT_UNAVAILABLE','DUPLICATE_DOCUMENT')),
    reason_code TEXT NOT NULL,
    source_hash TEXT NOT NULL,
    UNIQUE(source,source_item_id,revision_number,policy_version)
);

CREATE TABLE IF NOT EXISTS news_impact_assessments_v1 (
    assessment_id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    source_item_id TEXT NOT NULL,
    revision_number INTEGER NOT NULL,
    raw_content_hash TEXT NOT NULL,
    annotation_id TEXT NOT NULL,
    llm_model_version TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    parse_started_at TEXT NOT NULL,
    assessed_at TEXT NOT NULL,
    impact_class TEXT NOT NULL CHECK(impact_class IN (
        'IMMEDIATE','SAME_DAY','DATA_RELEASE','POLICY_SHIFT',
        'ONGOING_EVENT','BACKGROUND')),
    event_state TEXT NOT NULL CHECK(event_state IN (
        'ACTIVE','COMPLETED','UNCERTAIN','BACKGROUND')),
    update_type TEXT NOT NULL CHECK(update_type IN (
        'NEW_EVENT','MATERIAL_UPDATE','DUPLICATE_REPORT','COMMENTARY',
        'HISTORICAL_CONTEXT')),
    confidence REAL NOT NULL CHECK(confidence >= 0 AND confidence <= 1),
    reason_zh TEXT NOT NULL,
    FOREIGN KEY(annotation_id) REFERENCES news_annotations(annotation_id),
    FOREIGN KEY(source,source_item_id,revision_number)
      REFERENCES news_revisions(source,source_item_id,revision_number),
    UNIQUE(annotation_id,llm_model_version,prompt_version)
);

CREATE INDEX IF NOT EXISTS news_impact_assessments_lookup_v1
ON news_impact_assessments_v1(
    source,source_item_id,revision_number,assessed_at
);

CREATE TABLE IF NOT EXISTS news_event_identity_resolutions_v1 (
    resolution_id TEXT PRIMARY KEY,
    annotation_id TEXT NOT NULL,
    assessment_id TEXT NOT NULL,
    llm_model_version TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    resolved_at TEXT NOT NULL,
    identity_relation TEXT NOT NULL CHECK(identity_relation IN (
        'SAME_EVENT','SAME_EPISODE','NEW_EPISODE','UNRESOLVED')),
    matched_annotation_id TEXT,
    canonical_episode_id TEXT NOT NULL,
    canonical_event_id TEXT NOT NULL,
    FOREIGN KEY(annotation_id) REFERENCES news_annotations(annotation_id),
    FOREIGN KEY(assessment_id) REFERENCES news_impact_assessments_v1(assessment_id),
    FOREIGN KEY(matched_annotation_id) REFERENCES news_annotations(annotation_id),
    UNIQUE(annotation_id,llm_model_version,prompt_version)
);

CREATE INDEX IF NOT EXISTS news_event_identity_resolutions_lookup_v1
ON news_event_identity_resolutions_v1(
    canonical_episode_id,canonical_event_id,resolved_at
);

CREATE TABLE IF NOT EXISTS news_impact_failures_v1 (
    failure_id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    source_item_id TEXT NOT NULL,
    revision_number INTEGER NOT NULL,
    raw_content_hash TEXT NOT NULL,
    annotation_id TEXT NOT NULL,
    llm_model_version TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    attempt_number INTEGER NOT NULL CHECK(attempt_number >= 1),
    error_type TEXT NOT NULL,
    error_signature TEXT NOT NULL,
    error TEXT NOT NULL,
    failed_at TEXT NOT NULL,
    next_retry_at TEXT,
    is_terminal INTEGER NOT NULL CHECK(is_terminal IN (0,1)),
    FOREIGN KEY(annotation_id) REFERENCES news_annotations(annotation_id),
    UNIQUE(annotation_id,llm_model_version,prompt_version,attempt_number)
);

CREATE INDEX IF NOT EXISTS news_impact_failures_lookup_v1
ON news_impact_failures_v1(
    annotation_id,llm_model_version,prompt_version,attempt_number
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
CREATE INDEX IF NOT EXISTS news_visibility_event_v1
ON news_model_visibility_receipts_v1(event_key, decision_time);
CREATE INDEX IF NOT EXISTS news_visibility_decision_v1
ON news_model_visibility_receipts_v1(source_decision_id, model_identity);
CREATE INDEX IF NOT EXISTS news_visibility_catalog_event_v1
ON news_model_visibility_events_v1(event_key, collector_first_seen_time);
CREATE INDEX IF NOT EXISTS news_event_catalog_identity_v1
ON news_event_catalog_v1(event_id,event_occurred_at);
CREATE INDEX IF NOT EXISTS news_decision_event_time_v1
ON news_decision_event_snapshots_v1(decision_time,event_id);
CREATE INDEX IF NOT EXISTS news_event_source_budget_id_v1
ON news_event_source_budgets_v1(source_budget_id,event_version_id);
CREATE INDEX IF NOT EXISTS news_generation_activation_time_v1
ON news_model_generation_activations_v1(activated_at,generation_id);
CREATE INDEX IF NOT EXISTS news_only_visibility_event_v1
ON news_only_visibility_receipts_v1(event_key, decision_time);
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
