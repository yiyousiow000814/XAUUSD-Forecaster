-- Keep this trigger-bearing migration LF-only; remote D1 rejects CRLF compound SQL.
CREATE TABLE IF NOT EXISTS `assistant_memory_index_jobs` (
	`id` text PRIMARY KEY NOT NULL,
	`owner_id` text NOT NULL,
	`conversation_id` text NOT NULL REFERENCES `assistant_conversations`(`id`),
	`source_message_id` text NOT NULL REFERENCES `assistant_messages`(`id`),
	`source_created_at` text NOT NULL,
	`index_version` text NOT NULL,
	`status` text NOT NULL CHECK (`status` IN ('PENDING','PROCESSING','COMPLETED','FAILED')),
	`available_at` text NOT NULL,
	`lease_owner` text,
	`lease_token` text,
	`lease_expires_at` text,
	`attempt_count` integer NOT NULL DEFAULT 0 CHECK (`attempt_count` >= 0),
	`max_attempts` integer NOT NULL DEFAULT 3 CHECK (`max_attempts` BETWEEN 1 AND 5),
	`attempt_history_json` text NOT NULL DEFAULT '[]' CHECK (
		json_valid(`attempt_history_json`) AND json_type(`attempt_history_json`)='array'
	),
	`source_content_sha256` text,
	`term_count` integer CHECK (`term_count` IS NULL OR `term_count` BETWEEN 0 AND 64),
	`failure_code` text,
	`created_at` text NOT NULL,
	`completed_at` text,
	UNIQUE (`source_message_id`,`index_version`),
	CHECK (
		(`status`='PROCESSING' AND `lease_owner` IS NOT NULL
			AND `lease_token` IS NOT NULL AND `lease_expires_at` IS NOT NULL)
		OR (`status`!='PROCESSING' AND `lease_owner` IS NULL
			AND `lease_token` IS NULL AND `lease_expires_at` IS NULL)
	),
	CHECK (`attempt_count` <= `max_attempts`),
	CHECK (
		(`status`='COMPLETED' AND `source_content_sha256` IS NOT NULL
			AND length(`source_content_sha256`)=64
			AND `source_content_sha256` NOT GLOB '*[^0-9a-f]*'
			AND `term_count` IS NOT NULL AND `failure_code` IS NULL
			AND `completed_at` IS NOT NULL)
		OR (`status`='FAILED' AND `source_content_sha256` IS NULL
			AND `term_count` IS NULL AND `failure_code` IS NOT NULL
			AND `completed_at` IS NOT NULL)
		OR (`status` IN ('PENDING','PROCESSING') AND `source_content_sha256` IS NULL
			AND `term_count` IS NULL AND `completed_at` IS NULL)
	)
);
--> statement-breakpoint
CREATE INDEX IF NOT EXISTS `assistant_memory_index_jobs_claim_idx`
	ON `assistant_memory_index_jobs` (`status`,`available_at`,`created_at`,`id`);
--> statement-breakpoint
CREATE INDEX IF NOT EXISTS `assistant_memory_index_jobs_lease_idx`
	ON `assistant_memory_index_jobs` (`status`,`lease_expires_at`);
--> statement-breakpoint
CREATE INDEX IF NOT EXISTS `assistant_memory_index_jobs_owner_cutoff_idx`
	ON `assistant_memory_index_jobs` (
		`owner_id`,`index_version`,`conversation_id`,`source_created_at`,`source_message_id`,`status`
	);
--> statement-breakpoint
CREATE TABLE IF NOT EXISTS `assistant_memory_entries` (
	`id` text PRIMARY KEY NOT NULL,
	`source_job_id` text NOT NULL UNIQUE REFERENCES `assistant_memory_index_jobs`(`id`),
	`owner_id` text NOT NULL,
	`conversation_id` text NOT NULL REFERENCES `assistant_conversations`(`id`),
	`source_message_id` text NOT NULL REFERENCES `assistant_messages`(`id`),
	`source_role` text NOT NULL CHECK (`source_role` IN ('USER','ASSISTANT')),
	`source_created_at` text NOT NULL,
	`source_content_sha256` text NOT NULL CHECK (
		length(`source_content_sha256`)=64
		AND `source_content_sha256` NOT GLOB '*[^0-9a-f]*'
	),
	`index_version` text NOT NULL,
	`term_count` integer NOT NULL CHECK (`term_count` BETWEEN 0 AND 64),
	`indexed_at` text NOT NULL,
	UNIQUE (`source_message_id`,`index_version`)
);
--> statement-breakpoint
CREATE INDEX IF NOT EXISTS `assistant_memory_entries_owner_cutoff_idx`
	ON `assistant_memory_entries` (
		`owner_id`,`index_version`,`conversation_id`,`source_created_at`,`source_message_id`
	);
--> statement-breakpoint
CREATE TABLE IF NOT EXISTS `assistant_memory_terms` (
	`entry_id` text NOT NULL REFERENCES `assistant_memory_entries`(`id`),
	`owner_id` text NOT NULL,
	`term` text NOT NULL CHECK (length(`term`) BETWEEN 1 AND 64),
	`source_created_at` text NOT NULL,
	PRIMARY KEY (`entry_id`,`term`)
);
--> statement-breakpoint
CREATE INDEX IF NOT EXISTS `assistant_memory_terms_lookup_idx`
	ON `assistant_memory_terms` (`owner_id`,`term`,`source_created_at`,`entry_id`);
--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `assistant_messages_schedule_memory_index`
AFTER INSERT ON `assistant_messages`
BEGIN
	INSERT OR IGNORE INTO `assistant_memory_index_jobs` (
		`id`,`owner_id`,`conversation_id`,`source_message_id`,`source_created_at`,
		`index_version`,`status`,`available_at`,`created_at`
	)
	SELECT
		'memory-index:assistant-memory-lexical-v1:' || NEW.`id`,
		conversation.`owner_id`,NEW.`conversation_id`,NEW.`id`,NEW.`created_at`,
		'assistant-memory-lexical-v1','PENDING',NEW.`created_at`,NEW.`created_at`
	FROM `assistant_conversations` conversation
	WHERE conversation.`id`=NEW.`conversation_id`;
END;
--> statement-breakpoint
INSERT OR IGNORE INTO `assistant_memory_index_jobs` (
	`id`,`owner_id`,`conversation_id`,`source_message_id`,`source_created_at`,
	`index_version`,`status`,`available_at`,`created_at`
)
SELECT
	'memory-index:assistant-memory-lexical-v1:' || message.`id`,
	conversation.`owner_id`,message.`conversation_id`,message.`id`,message.`created_at`,
	'assistant-memory-lexical-v1','PENDING',message.`created_at`,message.`created_at`
FROM `assistant_messages` message
JOIN `assistant_conversations` conversation ON conversation.`id`=message.`conversation_id`;
--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `assistant_memory_index_jobs_require_source`
BEFORE INSERT ON `assistant_memory_index_jobs`
WHEN NOT EXISTS (
	SELECT 1
	FROM `assistant_messages` message
	JOIN `assistant_conversations` conversation
		ON conversation.`id`=message.`conversation_id`
	WHERE message.`id`=NEW.`source_message_id`
		AND message.`conversation_id`=NEW.`conversation_id`
		AND message.`created_at`=NEW.`source_created_at`
		AND conversation.`owner_id`=NEW.`owner_id`
)
BEGIN
	SELECT RAISE(ABORT, 'memory index job requires one owner-scoped canonical message');
END;
--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `assistant_memory_index_job_inputs_immutable`
BEFORE UPDATE OF `owner_id`,`conversation_id`,`source_message_id`,`source_created_at`,
	`index_version`,`max_attempts`,`created_at` ON `assistant_memory_index_jobs`
BEGIN
	SELECT RAISE(ABORT, 'memory index job inputs are immutable');
END;
--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `assistant_memory_index_job_status_transition`
BEFORE UPDATE OF `status` ON `assistant_memory_index_jobs`
WHEN NEW.`status` IS NOT OLD.`status` AND NOT (
	(OLD.`status`='PENDING' AND NEW.`status` IN ('PROCESSING','FAILED'))
	OR (OLD.`status`='PROCESSING' AND NEW.`status` IN ('PENDING','COMPLETED','FAILED'))
)
BEGIN
	SELECT RAISE(ABORT, 'memory index job status transition is invalid');
END;
--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `assistant_memory_index_attempt_history_append_only`
BEFORE UPDATE OF `attempt_history_json` ON `assistant_memory_index_jobs`
WHEN json_array_length(NEW.`attempt_history_json`) < json_array_length(OLD.`attempt_history_json`)
	OR EXISTS (
		SELECT 1 FROM json_each(OLD.`attempt_history_json`) receipt
		WHERE json(json_extract(
			NEW.`attempt_history_json`, '$[' || receipt.`key` || ']'
		)) IS NOT json(receipt.`value`)
	)
BEGIN
	SELECT RAISE(ABORT, 'memory index attempt history is append-only');
END;
--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `assistant_memory_entry_requires_active_job`
BEFORE INSERT ON `assistant_memory_entries`
WHEN NOT EXISTS (
	SELECT 1
	FROM `assistant_memory_index_jobs` job
	JOIN `assistant_messages` message ON message.`id`=job.`source_message_id`
	WHERE job.`id`=NEW.`source_job_id`
		AND job.`status`='PROCESSING'
		AND job.`owner_id`=NEW.`owner_id`
		AND job.`conversation_id`=NEW.`conversation_id`
		AND job.`source_message_id`=NEW.`source_message_id`
		AND job.`source_created_at`=NEW.`source_created_at`
		AND job.`index_version`=NEW.`index_version`
		AND message.`role`=NEW.`source_role`
		AND message.`conversation_id`=NEW.`conversation_id`
	)
BEGIN
	SELECT RAISE(ABORT, 'memory entry requires one active canonical index job');
END;
--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `assistant_memory_term_requires_entry_owner`
BEFORE INSERT ON `assistant_memory_terms`
WHEN NOT EXISTS (
	SELECT 1 FROM `assistant_memory_entries` entry
	WHERE entry.`id`=NEW.`entry_id`
		AND entry.`owner_id`=NEW.`owner_id`
		AND entry.`source_created_at`=NEW.`source_created_at`
)
BEGIN
	SELECT RAISE(ABORT, 'memory term requires its owner-scoped entry');
END;
--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `assistant_memory_index_completion_requires_entry`
BEFORE UPDATE OF `status` ON `assistant_memory_index_jobs`
WHEN NEW.`status`='COMPLETED' AND OLD.`status`!='COMPLETED' AND (
	NOT EXISTS (
		SELECT 1 FROM `assistant_memory_entries` entry
		WHERE entry.`source_job_id`=OLD.`id`
			AND entry.`owner_id`=OLD.`owner_id`
			AND entry.`conversation_id`=OLD.`conversation_id`
			AND entry.`source_message_id`=OLD.`source_message_id`
			AND entry.`source_created_at`=OLD.`source_created_at`
			AND entry.`index_version`=OLD.`index_version`
			AND entry.`source_content_sha256`=NEW.`source_content_sha256`
			AND entry.`term_count`=NEW.`term_count`
	)
	OR NEW.`term_count` != (
		SELECT count(*) FROM `assistant_memory_terms` term
		JOIN `assistant_memory_entries` entry ON entry.`id`=term.`entry_id`
		WHERE entry.`source_job_id`=OLD.`id`
	)
)
BEGIN
	SELECT RAISE(ABORT, 'completed memory index requires its exact derived entry');
END;
--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `assistant_memory_index_jobs_terminal_immutable`
BEFORE UPDATE ON `assistant_memory_index_jobs`
WHEN OLD.`status` IN ('COMPLETED','FAILED')
BEGIN
	SELECT RAISE(ABORT, 'terminal memory index jobs are immutable');
END;
--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `assistant_memory_index_jobs_immutable_delete`
BEFORE DELETE ON `assistant_memory_index_jobs`
BEGIN
	SELECT RAISE(ABORT, 'memory index job receipts are immutable');
END;
--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `assistant_memory_entries_immutable_update`
BEFORE UPDATE ON `assistant_memory_entries`
BEGIN
	SELECT RAISE(ABORT, 'assistant memory entries are immutable');
END;
--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `assistant_memory_entries_immutable_delete`
BEFORE DELETE ON `assistant_memory_entries`
BEGIN
	SELECT RAISE(ABORT, 'assistant memory entries are immutable');
END;
--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `assistant_memory_terms_immutable_update`
BEFORE UPDATE ON `assistant_memory_terms`
BEGIN
	SELECT RAISE(ABORT, 'assistant memory terms are immutable');
END;
--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `assistant_memory_terms_immutable_delete`
BEFORE DELETE ON `assistant_memory_terms`
BEGIN
	SELECT RAISE(ABORT, 'assistant memory terms are immutable');
END;
