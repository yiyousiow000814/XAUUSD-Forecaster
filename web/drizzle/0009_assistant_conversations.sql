-- Keep this trigger-bearing migration LF-only; remote D1 rejects CRLF compound SQL.
CREATE TABLE IF NOT EXISTS `assistant_conversations` (
	`id` text PRIMARY KEY NOT NULL,
	`owner_id` text NOT NULL,
	`initial_idempotency_key` text NOT NULL,
	`title` text NOT NULL,
	`title_source` text NOT NULL CHECK (`title_source` IN ('PROVISIONAL','AI','USER')),
	`title_revision` integer NOT NULL DEFAULT 0,
	`title_request_version` integer NOT NULL DEFAULT 0,
	`pending_title_job_id` text,
	`created_at` text NOT NULL,
	`last_activity_at` text NOT NULL,
	`archived_at` text,
	`summary_version` integer NOT NULL DEFAULT 0,
	`status` text NOT NULL DEFAULT 'ACTIVE' CHECK (`status` IN ('ACTIVE','ARCHIVED')),
	UNIQUE (`owner_id`,`initial_idempotency_key`)
);
--> statement-breakpoint
CREATE INDEX IF NOT EXISTS `assistant_conversations_owner_activity_idx`
	ON `assistant_conversations` (`owner_id`,`status`,`last_activity_at`,`id`);
--> statement-breakpoint
CREATE TABLE IF NOT EXISTS `assistant_messages` (
	`id` text PRIMARY KEY NOT NULL,
	`conversation_id` text NOT NULL REFERENCES `assistant_conversations`(`id`),
	`role` text NOT NULL CHECK (`role` IN ('USER','ASSISTANT')),
	`content` text NOT NULL,
	`created_at` text NOT NULL,
	`provenance_json` text NOT NULL CHECK (json_valid(`provenance_json`)),
	`source_kind` text NOT NULL,
	`source_id` text NOT NULL,
	UNIQUE (`source_kind`,`source_id`,`role`)
);
--> statement-breakpoint
CREATE INDEX IF NOT EXISTS `assistant_messages_conversation_time_idx`
	ON `assistant_messages` (`conversation_id`,`created_at`,`id`);
--> statement-breakpoint
CREATE TABLE IF NOT EXISTS `assistant_title_jobs` (
	`id` text PRIMARY KEY NOT NULL,
	`conversation_id` text NOT NULL REFERENCES `assistant_conversations`(`id`),
	`idempotency_key` text NOT NULL,
	`requested_by` text NOT NULL CHECK (`requested_by` IN ('AUTOMATIC','USER')),
	`input_version` integer NOT NULL,
	`expected_title_revision` integer NOT NULL,
	`first_user_message_id` text NOT NULL REFERENCES `assistant_messages`(`id`),
	`assistant_message_id` text NOT NULL REFERENCES `assistant_messages`(`id`),
	`status` text NOT NULL CHECK (`status` IN ('PENDING','PROCESSING','COMPLETED','FAILED','CANCELLED')),
	`available_at` text NOT NULL,
	`lease_owner` text,
	`lease_token` text,
	`lease_expires_at` text,
	`attempt_count` integer NOT NULL DEFAULT 0,
	`max_attempts` integer NOT NULL DEFAULT 3,
	`prompt_version` text NOT NULL,
	`model_version` text,
	`created_at` text NOT NULL,
	`completed_at` text,
	`generated_title` text,
	`failure_code` text,
	UNIQUE (`conversation_id`,`input_version`),
	UNIQUE (`conversation_id`,`idempotency_key`)
);
--> statement-breakpoint
CREATE INDEX IF NOT EXISTS `assistant_title_jobs_claim_idx`
	ON `assistant_title_jobs` (`status`,`available_at`,`created_at`);
--> statement-breakpoint
CREATE INDEX IF NOT EXISTS `assistant_title_jobs_lease_idx`
	ON `assistant_title_jobs` (`status`,`lease_expires_at`);
--> statement-breakpoint
ALTER TABLE `news_questions` ADD COLUMN `conversation_id` text REFERENCES `assistant_conversations`(`id`);
--> statement-breakpoint
ALTER TABLE `news_questions` ADD COLUMN `user_message_id` text REFERENCES `assistant_messages`(`id`);
--> statement-breakpoint
ALTER TABLE `news_questions` ADD COLUMN `assistant_message_id` text REFERENCES `assistant_messages`(`id`);
--> statement-breakpoint
INSERT OR IGNORE INTO `assistant_conversations` (
	`id`,`owner_id`,`initial_idempotency_key`,`title`,`title_source`,
	`created_at`,`last_activity_at`,`status`
)
SELECT
	'conversation:' || `id`, `owner_id`, `idempotency_key`,
	substr(replace(replace(trim(`question`), char(13), ' '), char(10), ' '), 1, 32),
	'PROVISIONAL', `asked_at`, COALESCE(`answered_at`,`asked_at`), 'ACTIVE'
FROM `news_questions`;
--> statement-breakpoint
INSERT OR IGNORE INTO `assistant_messages` (
	`id`,`conversation_id`,`role`,`content`,`created_at`,`provenance_json`,`source_kind`,`source_id`
)
SELECT
	'message:user:' || `id`, 'conversation:' || `id`, 'USER', `question`, `asked_at`,
	json_object('kind','USER_SUBMISSION','question_id',`id`), 'NEWS_QA', `id`
FROM `news_questions`;
--> statement-breakpoint
INSERT OR IGNORE INTO `assistant_messages` (
	`id`,`conversation_id`,`role`,`content`,`created_at`,`provenance_json`,`source_kind`,`source_id`
)
SELECT
	'message:assistant:' || `id`, 'conversation:' || `id`, 'ASSISTANT', `answer`, `answered_at`,
	json_object(
		'kind','NEWS_QA','question_id',`id`,'answer_status',`answer_status`,
		'evidence_ids',json(CASE WHEN json_valid(`evidence_json`) THEN `evidence_json` ELSE '[]' END),
		'retrieval',json(CASE WHEN json_valid(`retrieval_json`) THEN `retrieval_json` ELSE 'null' END),
		'model_version',`model_version`,'prompt_version',`prompt_version`
	), 'NEWS_QA', `id`
FROM `news_questions`
WHERE `status`='ANSWERED' AND `answer` IS NOT NULL AND `answered_at` IS NOT NULL;
--> statement-breakpoint
UPDATE `news_questions` SET
	`conversation_id`='conversation:' || `id`,
	`user_message_id`='message:user:' || `id`,
	`assistant_message_id`=CASE
		WHEN `status`='ANSWERED' AND `answer` IS NOT NULL AND `answered_at` IS NOT NULL
			THEN 'message:assistant:' || `id`
		ELSE NULL
	END;
--> statement-breakpoint
CREATE INDEX IF NOT EXISTS `news_questions_conversation_idx`
	ON `news_questions` (`conversation_id`);
--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `news_questions_require_conversation_insert`
BEFORE INSERT ON `news_questions`
WHEN NEW.`conversation_id` IS NULL OR NEW.`user_message_id` IS NULL
	OR NOT EXISTS (
		SELECT 1 FROM `assistant_conversations`
		WHERE `id`=NEW.`conversation_id` AND `owner_id`=NEW.`owner_id`
	)
	OR NOT EXISTS (
		SELECT 1 FROM `assistant_messages`
		WHERE `id`=NEW.`user_message_id`
			AND `conversation_id`=NEW.`conversation_id` AND `role`='USER'
	)
	OR (NEW.`assistant_message_id` IS NOT NULL AND NOT EXISTS (
		SELECT 1 FROM `assistant_messages`
		WHERE `id`=NEW.`assistant_message_id`
			AND `conversation_id`=NEW.`conversation_id` AND `role`='ASSISTANT'
	))
BEGIN
	SELECT RAISE(ABORT, 'news question requires canonical conversation messages');
END;
--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `news_questions_require_conversation_update`
BEFORE UPDATE ON `news_questions`
WHEN NEW.`conversation_id` IS NULL OR NEW.`user_message_id` IS NULL
	OR NOT EXISTS (
		SELECT 1 FROM `assistant_conversations`
		WHERE `id`=NEW.`conversation_id` AND `owner_id`=NEW.`owner_id`
	)
	OR NOT EXISTS (
		SELECT 1 FROM `assistant_messages`
		WHERE `id`=NEW.`user_message_id`
			AND `conversation_id`=NEW.`conversation_id` AND `role`='USER'
	)
	OR (NEW.`assistant_message_id` IS NOT NULL AND NOT EXISTS (
		SELECT 1 FROM `assistant_messages`
		WHERE `id`=NEW.`assistant_message_id`
			AND `conversation_id`=NEW.`conversation_id` AND `role`='ASSISTANT'
	))
BEGIN
	SELECT RAISE(ABORT, 'news question requires canonical conversation messages');
END;
--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `news_questions_conversation_links_immutable`
BEFORE UPDATE OF `conversation_id`,`user_message_id`,`assistant_message_id` ON `news_questions`
WHEN OLD.`conversation_id` IS NOT NEW.`conversation_id`
	OR OLD.`user_message_id` IS NOT NEW.`user_message_id`
	OR (OLD.`assistant_message_id` IS NOT NULL
		AND OLD.`assistant_message_id` IS NOT NEW.`assistant_message_id`)
BEGIN
	SELECT RAISE(ABORT, 'canonical conversation links are immutable');
END;
--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `news_questions_prompt_version_immutable`
BEFORE UPDATE OF `prompt_version` ON `news_questions`
WHEN OLD.`prompt_version` IS NOT NEW.`prompt_version`
BEGIN
	SELECT RAISE(ABORT, 'news question prompt version is immutable');
END;
--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `assistant_title_jobs_require_frozen_messages`
BEFORE INSERT ON `assistant_title_jobs`
WHEN NOT EXISTS (
		SELECT 1 FROM `assistant_messages`
		WHERE `id`=NEW.`first_user_message_id`
			AND `conversation_id`=NEW.`conversation_id` AND `role`='USER'
	)
	OR NOT EXISTS (
		SELECT 1 FROM `assistant_messages`
		WHERE `id`=NEW.`assistant_message_id`
			AND `conversation_id`=NEW.`conversation_id` AND `role`='ASSISTANT'
	)
BEGIN
	SELECT RAISE(ABORT, 'title job requires frozen conversation messages');
END;
--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `assistant_title_job_inputs_immutable`
BEFORE UPDATE OF `conversation_id`,`idempotency_key`,`requested_by`,`input_version`,
	`expected_title_revision`,`first_user_message_id`,`assistant_message_id`,
	`max_attempts`,`prompt_version`,`created_at` ON `assistant_title_jobs`
BEGIN
	SELECT RAISE(ABORT, 'title job inputs are immutable');
END;
--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `assistant_messages_immutable_update`
BEFORE UPDATE ON `assistant_messages`
BEGIN
	SELECT RAISE(ABORT, 'canonical assistant messages are immutable');
END;
--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `assistant_messages_immutable_delete`
BEFORE DELETE ON `assistant_messages`
BEGIN
	SELECT RAISE(ABORT, 'canonical assistant messages are immutable');
END;
--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `assistant_conversations_archive_only`
BEFORE DELETE ON `assistant_conversations`
BEGIN
	SELECT RAISE(ABORT, 'assistant conversations must be archived, not deleted');
END;
