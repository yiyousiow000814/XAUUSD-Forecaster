CREATE TABLE IF NOT EXISTS `news_questions` (
	`id` text PRIMARY KEY NOT NULL,
	`question_hash` text NOT NULL UNIQUE,
	`question` text NOT NULL,
	`status` text NOT NULL,
	`asked_at` text NOT NULL,
	`answer` text,
	`evidence_json` text,
	`answered_at` text,
	`model_version` text
);
--> statement-breakpoint
CREATE INDEX IF NOT EXISTS `news_questions_status_time_idx` ON `news_questions` (`status`,`asked_at`);
