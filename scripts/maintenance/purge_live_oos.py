"""Remove retired forecast data from an offline evidence database.

The default is an inventory. Run only after all old model producers are stopped.
The explicit table allowlist excludes news, annotations, events and source rules.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path


RETIRED_TABLES = frozenset({
    "calibration_snapshots_v2", "collector_runs", "decision_events",
    "derived_market_snapshots", "derived_news_feature_snapshots", "derived_outcomes",
    "evaluation_epochs", "evidence_lane_assignments", "market_crossfit_predictions",
    "market_snapshots", "model_updates", "model_updates_v2",
    "news_decision_event_snapshots_v1", "news_input_coverage_snapshots_v1",
    "news_model_generation_activations_v1", "news_model_generation_aux_members_v1",
    "news_model_generation_members_v1", "news_model_generations_v1",
    "news_model_visibility_events_v1", "news_model_visibility_receipts_v1",
    "news_only_visibility_receipts_v1", "news_semantic_health_snapshots_v1",
    "news_training_source_budget_receipts_v1", "news_training_weight_receipts_v1",
    "outcomes", "prediction_scores", "prediction_scores_v2", "predictions",
    "predictions_v2", "promotion_approvals", "repair_batches",
    "shadow_trade_intents", "shadow_trade_results", "training_eligibility",
    "training_eligibility_v2", "background_training_owner_v1",
    "training_materialization_state_v1", "training_materialization_dirty_v1",
    "dashboard_chart_records_v1", "dashboard_chart_state_v1",
})


def purge(connection: sqlite3.Connection, *, apply: bool = False) -> dict[str, int]:
    if connection.in_transaction:
        raise ValueError("cleanup requires its own transaction")
    connection.execute("BEGIN IMMEDIATE" if apply else "BEGIN")
    try:
        existing = {row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
        retired = RETIRED_TABLES & existing
        counts = {table: connection.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0]
                  for table in sorted(retired)}
        if not apply:
            connection.rollback()
            return counts
        # A retained table must never lose a referenced row indirectly.
        for table in existing - retired:
            for foreign_key in connection.execute(f'PRAGMA foreign_key_list("{table}")'):
                if foreign_key[2] in retired:
                    raise ValueError(f"retained dependency: {table} -> {foreign_key[2]}")
        connection.execute("PRAGMA defer_foreign_keys=ON")
        triggers = list(connection.execute(
            "SELECT name,tbl_name,sql FROM sqlite_master WHERE type='trigger'"
        ))
        removed = []
        for name, table, sql in triggers:
            if table in retired or name.startswith("trg_training_materialization_"):
                connection.execute('DROP TRIGGER "' + name.replace('"', '""') + '"')
                if not name.startswith("trg_training_materialization_"):
                    removed.append(sql)
        for table in sorted(retired):
            connection.execute(f'DELETE FROM "{table}"')
        if "dashboard_table_counts_v1" in existing:
            connection.executemany(
                "UPDATE dashboard_table_counts_v1 SET row_count=0 WHERE table_name=?",
                [(table,) for table in retired],
            )
        for table in ("dashboard_optional_read_models_v1", "dashboard_optional_read_model_state_v1"):
            if table in existing:
                connection.execute(f"DELETE FROM {table} WHERE resource IN ('learning','audit','market_chart')")
        if "dashboard_latest_activity_v1" in existing:
            connection.execute("DELETE FROM dashboard_latest_activity_v1 WHERE activity_name IN ('decision_events','outcomes','decision_time')")
        if "dashboard_valid_outcome_summary_v1" in existing:
            connection.execute("UPDATE dashboard_valid_outcome_summary_v1 SET sample_count=0,long_return_sum=0,short_return_sum=0,quote_coverage_sum=0")
        for sql in removed:
            connection.execute(sql)
        if connection.execute("PRAGMA foreign_key_check").fetchone():
            raise ValueError("cleanup would leave invalid references")
        connection.commit()
        return counts
    except BaseException:
        connection.rollback()
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    mode = "rw" if args.apply else "ro"
    connection = sqlite3.connect(f"file:{args.database.resolve()}?mode={mode}", uri=True)
    try:
        print(json.dumps({"applied": args.apply, "rows": purge(connection, apply=args.apply)}, sort_keys=True))
    finally:
        connection.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
