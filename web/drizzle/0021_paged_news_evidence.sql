CREATE TABLE IF NOT EXISTS `news_evidence_records` (
	`snapshot_id` text NOT NULL,
	`event_key` text NOT NULL,
	`ordinal` integer NOT NULL,
	`sort_time` text NOT NULL,
	`broad_model_eligible` integer NOT NULL,
	`model_seen` integer NOT NULL,
	`payload` text NOT NULL,
	`received_at` text NOT NULL,
	PRIMARY KEY(`snapshot_id`, `event_key`)
);
--> statement-breakpoint
CREATE INDEX IF NOT EXISTS `news_evidence_snapshot_time_idx`
	ON `news_evidence_records` (`snapshot_id`, `sort_time`, `event_key`);
--> statement-breakpoint
CREATE INDEX IF NOT EXISTS `news_evidence_snapshot_eligible_idx`
	ON `news_evidence_records`
	(`snapshot_id`, `broad_model_eligible`, `sort_time`, `event_key`);
--> statement-breakpoint
CREATE INDEX IF NOT EXISTS `news_evidence_snapshot_seen_idx`
	ON `news_evidence_records`
	(`snapshot_id`, `model_seen`, `sort_time`, `event_key`);
--> statement-breakpoint
CREATE UNIQUE INDEX IF NOT EXISTS `news_evidence_snapshot_ordinal_idx`
	ON `news_evidence_records` (`snapshot_id`, `ordinal`);
--> statement-breakpoint
CREATE TABLE IF NOT EXISTS `news_evidence_state` (
	`id` integer PRIMARY KEY NOT NULL,
	`active_snapshot_id` text NOT NULL,
	`contract_version` text NOT NULL,
	`record_count` integer NOT NULL,
	`activated_at` text NOT NULL
);
--> statement-breakpoint
CREATE TABLE IF NOT EXISTS `news_evidence_staging` (
	`snapshot_id` text PRIMARY KEY NOT NULL,
	`next_offset` integer NOT NULL,
	`expected_count` integer NOT NULL,
	`updated_at` text NOT NULL
);
--> statement-breakpoint
CREATE TABLE IF NOT EXISTS `news_evidence_batches` (
	`snapshot_id` text NOT NULL,
	`batch_offset` integer NOT NULL,
	`item_count` integer NOT NULL,
	`payload_hash` text NOT NULL,
	`updated_at` text NOT NULL,
	PRIMARY KEY(`snapshot_id`, `batch_offset`)
);
