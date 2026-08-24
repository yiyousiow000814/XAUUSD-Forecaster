CREATE TABLE IF NOT EXISTS `news_projection_generations` (
	`generation_id` text PRIMARY KEY NOT NULL,
	`snapshot_id` text NOT NULL,
	`state` text NOT NULL,
	`contract_version` text NOT NULL,
	`window_start` text NOT NULL,
	`watermark` text NOT NULL,
	`expected_index_count` integer NOT NULL,
	`expected_detail_count` integer NOT NULL,
	`withdrawal_count` integer NOT NULL,
	`source_digest` text NOT NULL,
	`expected_receipt_digest` text NOT NULL,
	`receipt_digest` text NOT NULL,
	`next_detail_offset` integer NOT NULL,
	`next_index_offset` integer NOT NULL,
	`staged_detail_count` integer NOT NULL,
	`staged_index_count` integer NOT NULL,
	`missing_detail_count` integer NOT NULL,
	`invariant_violation_count` integer NOT NULL,
	`created_at` text NOT NULL,
	`updated_at` text NOT NULL,
	`activated_at` text
);
--> statement-breakpoint
CREATE INDEX IF NOT EXISTS `news_projection_generations_state_idx`
	ON `news_projection_generations` (`state`, `updated_at`);
--> statement-breakpoint
CREATE TABLE IF NOT EXISTS `news_projection_index` (
	`generation_id` text NOT NULL,
	`detail_key` text NOT NULL,
	`ordinal` integer NOT NULL,
	`category` text NOT NULL,
	`cluster_id` text NOT NULL,
	`published_time` text NOT NULL,
	`collector_first_seen_time` text NOT NULL,
	`parsed` integer NOT NULL,
	`model_candidate` integer NOT NULL,
	`impact_expires_at` text,
	`mirror_contract` text NOT NULL,
	`payload_hash` text NOT NULL,
	`payload` text NOT NULL,
	`received_at` text NOT NULL,
	PRIMARY KEY(`generation_id`, `detail_key`)
);
--> statement-breakpoint
CREATE UNIQUE INDEX IF NOT EXISTS `news_projection_index_ordinal_idx`
	ON `news_projection_index` (`generation_id`, `ordinal`);
--> statement-breakpoint
CREATE INDEX IF NOT EXISTS `news_projection_index_page_idx`
	ON `news_projection_index` (`generation_id`, `published_time`, `collector_first_seen_time`, `detail_key`);
--> statement-breakpoint
CREATE INDEX IF NOT EXISTS `news_projection_index_category_idx`
	ON `news_projection_index` (`generation_id`, `category`, `published_time`, `detail_key`);
--> statement-breakpoint
CREATE TABLE IF NOT EXISTS `news_projection_details` (
	`generation_id` text NOT NULL,
	`detail_key` text NOT NULL,
	`detail_hash` text NOT NULL,
	`payload` text NOT NULL,
	`received_at` text NOT NULL,
	PRIMARY KEY(`generation_id`, `detail_key`)
);
--> statement-breakpoint
CREATE TABLE IF NOT EXISTS `news_projection_batches` (
	`generation_id` text NOT NULL,
	`batch_kind` text NOT NULL,
	`batch_offset` integer NOT NULL,
	`item_count` integer NOT NULL,
	`payload_hash` text NOT NULL,
	`receipt_digest` text NOT NULL,
	`updated_at` text NOT NULL,
	PRIMARY KEY(`generation_id`, `batch_kind`, `batch_offset`)
);
--> statement-breakpoint
CREATE TABLE IF NOT EXISTS `news_projection_state` (
	`id` integer PRIMARY KEY NOT NULL,
	`active_generation_id` text NOT NULL,
	`snapshot_id` text NOT NULL,
	`contract_version` text NOT NULL,
	`source_digest` text NOT NULL,
	`receipt_digest` text NOT NULL,
	`index_count` integer NOT NULL,
	`detail_count` integer NOT NULL,
	`missing_detail_count` integer NOT NULL,
	`invariant_violation_count` integer NOT NULL,
	`projection_state` text NOT NULL,
	`activated_at` text NOT NULL,
	`verified_at` text NOT NULL
);
