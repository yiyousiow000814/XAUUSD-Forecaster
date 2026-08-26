CREATE TABLE IF NOT EXISTS `news_projection_receipts_v2` (
	`generation_id` text NOT NULL,
	`batch_kind` text NOT NULL,
	`batch_offset` integer NOT NULL,
	`item_count` integer NOT NULL,
	`payload_hash` text NOT NULL,
	`receipt_digest` text NOT NULL,
	`identity_digest` text NOT NULL,
	`updated_at` text NOT NULL,
	PRIMARY KEY(`generation_id`, `batch_kind`, `batch_offset`)
);
--> statement-breakpoint
CREATE TABLE IF NOT EXISTS `news_projection_counts` (
	`generation_id` text NOT NULL,
	`review_state` text NOT NULL,
	`category` text NOT NULL,
	`item_count` integer NOT NULL,
	`parsed_count` integer NOT NULL,
	`candidate_expiries` text NOT NULL,
	PRIMARY KEY(`generation_id`, `review_state`, `category`)
);
--> statement-breakpoint
CREATE INDEX IF NOT EXISTS `news_projection_index_review_page_idx`
	ON `news_projection_index` (
		`generation_id`,
		(CASE
			WHEN json_extract(`payload`, '$.annotation_status') IN ('READY','NOT_REQUIRED') THEN 'COMPLETED'
			WHEN json_extract(`payload`, '$.annotation_status') IN ('DEAD_LETTER','CONTENT_UNAVAILABLE') THEN 'ISOLATED'
			ELSE 'PROCESSING' END),
		`published_time` DESC, `collector_first_seen_time` DESC, `detail_key` DESC
	);
--> statement-breakpoint
CREATE INDEX IF NOT EXISTS `news_projection_index_review_category_page_idx`
	ON `news_projection_index` (
		`generation_id`,
		(CASE
			WHEN json_extract(`payload`, '$.annotation_status') IN ('READY','NOT_REQUIRED') THEN 'COMPLETED'
			WHEN json_extract(`payload`, '$.annotation_status') IN ('DEAD_LETTER','CONTENT_UNAVAILABLE') THEN 'ISOLATED'
			ELSE 'PROCESSING' END),
		`category`, `published_time` DESC, `collector_first_seen_time` DESC, `detail_key` DESC
	);
--> statement-breakpoint
INSERT INTO `news_projection_counts`
	(`generation_id`, `review_state`, `category`, `item_count`, `parsed_count`, `candidate_expiries`)
SELECT `generation_id`, `review_state`, `category`, count(*), sum(`parsed`), ''
  FROM (
	SELECT i.`generation_id`, i.`category`, i.`parsed`,
		CASE
			WHEN json_extract(i.`payload`, '$.annotation_status') IN ('READY','NOT_REQUIRED') THEN 'COMPLETED'
			WHEN json_extract(i.`payload`, '$.annotation_status') IN ('DEAD_LETTER','CONTENT_UNAVAILABLE') THEN 'ISOLATED'
			ELSE 'PROCESSING' END AS `review_state`
	  FROM `news_projection_index` i
	  JOIN `news_projection_state` s
		ON s.`id` = 1 AND s.`active_generation_id` = i.`generation_id`
  )
 GROUP BY `generation_id`, `review_state`, `category`
UNION ALL
SELECT `generation_id`, `review_state`, '', count(*), sum(`parsed`), ''
  FROM (
	SELECT i.`generation_id`, i.`parsed`,
		CASE
			WHEN json_extract(i.`payload`, '$.annotation_status') IN ('READY','NOT_REQUIRED') THEN 'COMPLETED'
			WHEN json_extract(i.`payload`, '$.annotation_status') IN ('DEAD_LETTER','CONTENT_UNAVAILABLE') THEN 'ISOLATED'
			ELSE 'PROCESSING' END AS `review_state`
	  FROM `news_projection_index` i
	  JOIN `news_projection_state` s
		ON s.`id` = 1 AND s.`active_generation_id` = i.`generation_id`
  )
 GROUP BY `generation_id`, `review_state`
UNION ALL
SELECT i.`generation_id`, 'ALL', '', count(*), sum(i.`parsed`),
	coalesce(group_concat(
		CASE WHEN i.`model_candidate` = 1 THEN i.`impact_expires_at` END,
		char(10) ORDER BY i.`impact_expires_at`
	), '')
  FROM `news_projection_index` i
  JOIN `news_projection_state` s
	ON s.`id` = 1 AND s.`active_generation_id` = i.`generation_id`
 GROUP BY i.`generation_id`
ON CONFLICT(`generation_id`, `review_state`, `category`) DO UPDATE SET
	`item_count` = `excluded`.`item_count`,
	`parsed_count` = `excluded`.`parsed_count`,
	`candidate_expiries` = `excluded`.`candidate_expiries`;
