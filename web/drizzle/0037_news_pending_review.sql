DROP INDEX IF EXISTS `news_projection_index_review_page_idx`;
--> statement-breakpoint
CREATE INDEX IF NOT EXISTS `news_projection_index_review_page_idx`
	ON `news_projection_index` (
		`generation_id`,
		(CASE
			WHEN json_extract(`payload`, '$.annotation_status') IN ('READY','NOT_REQUIRED') THEN 'COMPLETED'
			ELSE 'PROCESSING' END),
		`published_time` DESC, `collector_first_seen_time` DESC, `detail_key` DESC
	);
--> statement-breakpoint
DROP INDEX IF EXISTS `news_projection_index_review_category_page_idx`;
--> statement-breakpoint
CREATE INDEX IF NOT EXISTS `news_projection_index_review_category_page_idx`
	ON `news_projection_index` (
		`generation_id`,
		(CASE
			WHEN json_extract(`payload`, '$.annotation_status') IN ('READY','NOT_REQUIRED') THEN 'COMPLETED'
			ELSE 'PROCESSING' END),
		`category`, `published_time` DESC, `collector_first_seen_time` DESC, `detail_key` DESC
	);
--> statement-breakpoint
DROP INDEX IF EXISTS news_index_review_cursor_idx;
--> statement-breakpoint
CREATE INDEX IF NOT EXISTS news_index_review_cursor_idx
ON news_index (
  (CASE
    WHEN json_extract(payload, '$.annotation_status') IN ('READY','NOT_REQUIRED') THEN 'COMPLETED'
    ELSE 'PROCESSING' END),
  published_time DESC, collector_first_seen_time DESC, detail_key DESC
)
WHERE COALESCE(json_extract(payload, '$.annotation_status'), '') <> 'SUPERSEDED_CONTRACT';
--> statement-breakpoint
DROP INDEX IF EXISTS news_index_review_category_cursor_idx;
--> statement-breakpoint
CREATE INDEX IF NOT EXISTS news_index_review_category_cursor_idx
ON news_index (
  (CASE
    WHEN json_extract(payload, '$.annotation_status') IN ('READY','NOT_REQUIRED') THEN 'COMPLETED'
    ELSE 'PROCESSING' END),
  category, published_time DESC, collector_first_seen_time DESC, detail_key DESC
)
WHERE COALESCE(json_extract(payload, '$.annotation_status'), '') <> 'SUPERSEDED_CONTRACT';
