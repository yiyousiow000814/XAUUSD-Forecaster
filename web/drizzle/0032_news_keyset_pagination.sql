CREATE INDEX IF NOT EXISTS news_index_review_cursor_idx
ON news_index (
  (CASE
    WHEN json_extract(payload, '$.annotation_status') IN ('READY','NOT_REQUIRED') THEN 'COMPLETED'
    WHEN json_extract(payload, '$.annotation_status') IN ('DEAD_LETTER','CONTENT_UNAVAILABLE') THEN 'ISOLATED'
    ELSE 'PROCESSING' END),
  published_time DESC, collector_first_seen_time DESC, detail_key DESC
)
WHERE COALESCE(json_extract(payload, '$.annotation_status'), '') <> 'SUPERSEDED_CONTRACT';
--> statement-breakpoint
CREATE INDEX IF NOT EXISTS news_index_review_category_cursor_idx
ON news_index (
  (CASE
    WHEN json_extract(payload, '$.annotation_status') IN ('READY','NOT_REQUIRED') THEN 'COMPLETED'
    WHEN json_extract(payload, '$.annotation_status') IN ('DEAD_LETTER','CONTENT_UNAVAILABLE') THEN 'ISOLATED'
    ELSE 'PROCESSING' END),
  category, published_time DESC, collector_first_seen_time DESC, detail_key DESC
)
WHERE COALESCE(json_extract(payload, '$.annotation_status'), '') <> 'SUPERSEDED_CONTRACT';
