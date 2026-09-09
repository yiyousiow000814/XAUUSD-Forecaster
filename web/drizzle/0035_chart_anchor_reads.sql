CREATE INDEX IF NOT EXISTS learning_records_chart_anchor_idx
ON learning_records(resource,json_extract(payload,'$.model_identity'),sort_epoch,record_key)
WHERE json_extract(payload,'$.chart_anchor')=1;
