-- Run explicitly after both model producers and obsolete sync routes are retired.
-- Each statement is restartable. Do not apply as an automatic build migration.
DELETE FROM learning_records;
DELETE FROM learning_record_counts;
DELETE FROM chart_history_state;
DELETE FROM market_decisions;
DELETE FROM market_decision_overviews;
DELETE FROM dashboard_snapshots WHERE id IN (3, 6);
UPDATE dashboard_snapshots SET payload=json_remove(payload,
  '$.recent_decisions', '$.training', '$.learning_curves', '$.research_forecast',
  '$.outcome_summary', '$.u5', '$.news_input_coverage', '$.decisions',
  '$.training_markers', '$.prediction_history_start', '$.learning_resource',
  '$.audit_decisions_resource', '$.counts.decision_events', '$.counts.predictions',
  '$.counts.predictions_v2', '$.counts.outcomes', '$.counts.live_oos_model_groups',
  '$.market_chart.decisions', '$.market_chart.training_markers',
  '$.market_chart.prediction_history_start') WHERE json_valid(payload);
