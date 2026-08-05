"""Append-only SQLite prediction ledger."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from .models import Decision, OutcomeLabel


SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS decisions (
    decision_id TEXT PRIMARY KEY,
    decision_time TEXT NOT NULL,
    model_version TEXT NOT NULL,
    feature_snapshot_hash TEXT NOT NULL,
    ev_long_u5 REAL NOT NULL,
    ev_short_u5 REAL NOT NULL,
    lcb_long_u5 REAL NOT NULL,
    lcb_short_u5 REAL NOT NULL,
    uncertainty_long_u5 REAL NOT NULL,
    uncertainty_short_u5 REAL NOT NULL,
    estimated_cost_long_u5 REAL NOT NULL,
    estimated_cost_short_u5 REAL NOT NULL,
    data_health TEXT NOT NULL,
    signal_expiry_seconds INTEGER NOT NULL,
    reason_codes_json TEXT NOT NULL,
    recommended_action TEXT NOT NULL,
    effective_action TEXT NOT NULL,
    decision_reason TEXT NOT NULL,
    active_until TEXT
);

CREATE TABLE IF NOT EXISTS outcome_labels (
    decision_id TEXT PRIMARY KEY REFERENCES decisions(decision_id),
    label_time TEXT NOT NULL,
    label_contract_version TEXT NOT NULL,
    long_return_u5 REAL NOT NULL,
    short_return_u5 REAL NOT NULL,
    mfe_long_u5 REAL NOT NULL,
    mae_long_u5 REAL NOT NULL,
    mfe_short_u5 REAL NOT NULL,
    mae_short_u5 REAL NOT NULL,
    maximum_spread REAL NOT NULL,
    quote_coverage REAL NOT NULL,
    ambiguity_state TEXT NOT NULL
);

CREATE TRIGGER IF NOT EXISTS decisions_no_update
BEFORE UPDATE ON decisions
BEGIN
    SELECT RAISE(ABORT, 'decisions are append-only');
END;

CREATE TRIGGER IF NOT EXISTS decisions_no_delete
BEFORE DELETE ON decisions
BEGIN
    SELECT RAISE(ABORT, 'decisions are append-only');
END;

CREATE TRIGGER IF NOT EXISTS labels_no_update
BEFORE UPDATE ON outcome_labels
BEGIN
    SELECT RAISE(ABORT, 'outcome labels are append-only');
END;

CREATE TRIGGER IF NOT EXISTS labels_no_delete
BEFORE DELETE ON outcome_labels
BEGIN
    SELECT RAISE(ABORT, 'outcome labels are append-only');
END;
"""


class PredictionLedger:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path)
        self.connection.executescript(SCHEMA)

    def close(self) -> None:
        self.connection.close()

    def append_decision(self, decision: Decision) -> None:
        forecast = decision.forecast
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO decisions VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                """,
                (
                    forecast.decision_id,
                    forecast.decision_time.isoformat(),
                    forecast.model_version,
                    forecast.feature_snapshot_hash,
                    forecast.ev_long_u5,
                    forecast.ev_short_u5,
                    forecast.lcb_long_u5,
                    forecast.lcb_short_u5,
                    forecast.uncertainty_long_u5,
                    forecast.uncertainty_short_u5,
                    forecast.estimated_cost_long_u5,
                    forecast.estimated_cost_short_u5,
                    forecast.data_health.value,
                    forecast.signal_expiry_seconds,
                    json.dumps(forecast.reason_codes, separators=(",", ":")),
                    decision.recommended_action.value,
                    decision.effective_action.value,
                    decision.decision_reason,
                    decision.active_until.isoformat()
                    if decision.active_until is not None
                    else None,
                ),
            )
    def append_outcome(self, outcome: OutcomeLabel) -> None:
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO outcome_labels VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                """,
                (
                    outcome.decision_id,
                    outcome.label_time.isoformat(),
                    outcome.label_contract_version,
                    outcome.long_return_u5,
                    outcome.short_return_u5,
                    outcome.mfe_long_u5,
                    outcome.mae_long_u5,
                    outcome.mfe_short_u5,
                    outcome.mae_short_u5,
                    outcome.maximum_spread,
                    outcome.quote_coverage,
                    outcome.ambiguity_state,
                ),
            )
