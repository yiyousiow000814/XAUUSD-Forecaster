-- Explicitly authorized removal of sizing and early-exit research only.
-- Apply after direction-only producer activation. Count triggers maintain totals.
DELETE FROM learning_records
WHERE resource IN ('execution-point','execution-result','exact-execution-point','exact-execution-result');
--> statement-breakpoint
UPDATE dashboard_snapshots SET payload=json_remove(payload,'$.execution_learning')
WHERE json_valid(payload) AND json_type(payload,'$.execution_learning') IS NOT NULL;
