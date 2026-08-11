CREATE TABLE IF NOT EXISTS `learning_records` (
	`resource` text NOT NULL,
	`record_key` text NOT NULL,
	`sort_epoch` integer NOT NULL,
	`payload_hash` text NOT NULL,
	`payload` text NOT NULL,
	`received_at` text NOT NULL,
	PRIMARY KEY(`resource`, `record_key`)
);
--> statement-breakpoint
CREATE INDEX IF NOT EXISTS `learning_records_resource_time_idx`
	ON `learning_records` (`resource`, `sort_epoch`, `record_key`);
