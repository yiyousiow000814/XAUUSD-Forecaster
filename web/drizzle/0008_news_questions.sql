CREATE TABLE IF NOT EXISTS `news_questions` (
	`id` text PRIMARY KEY NOT NULL,
	`owner_id` text NOT NULL,
	`idempotency_key` text NOT NULL,
	`question_hash` text NOT NULL,
	`question` text NOT NULL,
	`retrieval_query` text NOT NULL,
	`status` text NOT NULL CHECK (`status` IN ('PENDING','PROCESSING','ANSWERED','FAILED','REJECTED','EXPIRED')),
	`asked_at` text NOT NULL,
	`available_at` text NOT NULL,
	`expires_at` text NOT NULL,
	`processing_started_at` text,
	`lease_owner` text,
	`lease_token` text,
	`lease_expires_at` text,
	`attempt_count` integer NOT NULL DEFAULT 0,
	`max_attempts` integer NOT NULL DEFAULT 3,
	`attempt_history_json` text NOT NULL DEFAULT '[]',
	`failure_code` text,
	`answer` text,
	`answer_status` text CHECK (`answer_status` IS NULL OR `answer_status` IN ('ANSWERED','INSUFFICIENT_EVIDENCE')),
	`evidence_json` text,
	`retrieval_json` text,
	`answered_at` text,
	`model_version` text,
	`prompt_version` text NOT NULL,
	UNIQUE (`owner_id`,`idempotency_key`),
	UNIQUE (`owner_id`,`question_hash`)
);
--> statement-breakpoint
CREATE INDEX IF NOT EXISTS `news_questions_claim_idx` ON `news_questions` (`status`,`available_at`,`asked_at`);
--> statement-breakpoint
CREATE INDEX IF NOT EXISTS `news_questions_owner_time_idx` ON `news_questions` (`owner_id`,`asked_at`);
--> statement-breakpoint
CREATE INDEX IF NOT EXISTS `news_questions_lease_idx` ON `news_questions` (`status`,`lease_expires_at`);
