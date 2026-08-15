ALTER TABLE `assistant_conversations` ADD COLUMN `pending_compaction_job_id` text;
--> statement-breakpoint
ALTER TABLE `assistant_conversations` ADD COLUMN `compaction_request_version` integer NOT NULL DEFAULT 0;
--> statement-breakpoint
ALTER TABLE `assistant_title_jobs` ADD COLUMN `attempt_history_json` text NOT NULL DEFAULT '[]'
	CHECK (json_valid(`attempt_history_json`) AND json_type(`attempt_history_json`)='array');
--> statement-breakpoint
CREATE TABLE IF NOT EXISTS `assistant_compaction_jobs` (
	`id` text PRIMARY KEY NOT NULL,
	`conversation_id` text NOT NULL REFERENCES `assistant_conversations`(`id`),
	`input_version` integer NOT NULL,
	`prior_summary_version` integer NOT NULL,
	`output_summary_version` integer NOT NULL,
	`source_message_ids_json` text NOT NULL CHECK (
		json_valid(`source_message_ids_json`) AND json_type(`source_message_ids_json`)='array'
	),
	`source_message_count` integer NOT NULL CHECK (`source_message_count` > 0),
	`first_source_message_id` text NOT NULL REFERENCES `assistant_messages`(`id`),
	`last_source_message_id` text NOT NULL REFERENCES `assistant_messages`(`id`),
	`pinned_snapshot_json` text NOT NULL CHECK (
		json_valid(`pinned_snapshot_json`) AND json_type(`pinned_snapshot_json`)='array'
	),
	`context_profile_id` text NOT NULL,
	`capacity_state` text NOT NULL CHECK (`capacity_state` IN ('GREEN','YELLOW','RED')),
	`estimated_context_tokens` integer NOT NULL CHECK (`estimated_context_tokens` > 0),
	`status` text NOT NULL CHECK (`status` IN ('PENDING','PROCESSING','COMPLETED','FAILED')),
	`available_at` text NOT NULL,
	`lease_owner` text,
	`lease_token` text,
	`lease_expires_at` text,
	`attempt_count` integer NOT NULL DEFAULT 0,
	`max_attempts` integer NOT NULL DEFAULT 3,
	`attempt_history_json` text NOT NULL DEFAULT '[]' CHECK (
		json_valid(`attempt_history_json`) AND json_type(`attempt_history_json`)='array'
	),
	`prompt_version` text NOT NULL,
	`model_version` text,
	`created_at` text NOT NULL,
	`completed_at` text,
	`failure_code` text,
	UNIQUE (`conversation_id`,`input_version`)
);
--> statement-breakpoint
CREATE INDEX IF NOT EXISTS `assistant_compaction_jobs_claim_idx`
	ON `assistant_compaction_jobs` (`status`,`available_at`,`created_at`);
--> statement-breakpoint
CREATE INDEX IF NOT EXISTS `assistant_compaction_jobs_lease_idx`
	ON `assistant_compaction_jobs` (`status`,`lease_expires_at`);
--> statement-breakpoint
CREATE INDEX IF NOT EXISTS `assistant_compaction_jobs_output_idx`
	ON `assistant_compaction_jobs` (`conversation_id`,`output_summary_version`,`status`);
--> statement-breakpoint
CREATE TABLE IF NOT EXISTS `assistant_pinned_entries` (
	`id` text PRIMARY KEY NOT NULL,
	`conversation_id` text NOT NULL REFERENCES `assistant_conversations`(`id`),
	`idempotency_key` text NOT NULL,
	`kind` text NOT NULL CHECK (`kind` IN (
		'CONSTRAINT','UNRESOLVED','DECISION','TASK_SCOPE','EVIDENCE_REF',
		'TOOL_ARTIFACT','IMPORTANT_TIMESTAMP','TOPIC'
	)),
	`content` text NOT NULL CHECK (length(trim(`content`)) > 0),
	`origin_message_ids_json` text NOT NULL CHECK (
		json_valid(`origin_message_ids_json`)
		AND json_type(`origin_message_ids_json`)='array'
		AND json_array_length(`origin_message_ids_json`) > 0
	),
	`evidence_ids_json` text NOT NULL CHECK (
		json_valid(`evidence_ids_json`) AND json_type(`evidence_ids_json`)='array'
	),
	`source_refs_json` text NOT NULL CHECK (
		json_valid(`source_refs_json`) AND json_type(`source_refs_json`)='array'
	),
	`important_timestamps_json` text NOT NULL CHECK (
		json_valid(`important_timestamps_json`) AND json_type(`important_timestamps_json`)='array'
	),
	`tool_refs_json` text NOT NULL CHECK (
		json_valid(`tool_refs_json`) AND json_type(`tool_refs_json`)='array'
	),
	`artifact_refs_json` text NOT NULL CHECK (
		json_valid(`artifact_refs_json`) AND json_type(`artifact_refs_json`)='array'
	),
	`created_by` text NOT NULL CHECK (`created_by` IN ('SYSTEM','COMPACTION')),
	`source_job_id` text REFERENCES `assistant_compaction_jobs`(`id`),
	`created_at` text NOT NULL,
	UNIQUE (`conversation_id`,`idempotency_key`)
);
--> statement-breakpoint
CREATE INDEX IF NOT EXISTS `assistant_pinned_entries_conversation_idx`
	ON `assistant_pinned_entries` (`conversation_id`,`created_at`,`id`);
--> statement-breakpoint
CREATE TABLE IF NOT EXISTS `assistant_summaries` (
	`id` text PRIMARY KEY NOT NULL,
	`conversation_id` text NOT NULL REFERENCES `assistant_conversations`(`id`),
	`version` integer NOT NULL CHECK (`version` > 0),
	`prior_summary_id` text REFERENCES `assistant_summaries`(`id`),
	`source_job_id` text NOT NULL UNIQUE REFERENCES `assistant_compaction_jobs`(`id`),
	`first_source_message_id` text NOT NULL REFERENCES `assistant_messages`(`id`),
	`covered_through_message_id` text NOT NULL REFERENCES `assistant_messages`(`id`),
	`covered_through_created_at` text NOT NULL,
	`source_message_count` integer NOT NULL CHECK (`source_message_count` > 0),
	`content` text NOT NULL CHECK (length(trim(`content`)) > 0),
	`anchors_json` text NOT NULL CHECK (
		json_valid(`anchors_json`) AND json_type(`anchors_json`)='object'
	),
	`estimated_tokens` integer NOT NULL CHECK (`estimated_tokens` > 0),
	`context_profile_id` text NOT NULL,
	`prompt_version` text NOT NULL,
	`model_version` text NOT NULL,
	`created_at` text NOT NULL,
	UNIQUE (`conversation_id`,`version`)
);
--> statement-breakpoint
CREATE INDEX IF NOT EXISTS `assistant_summaries_conversation_idx`
	ON `assistant_summaries` (`conversation_id`,`version`);
--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `assistant_compaction_jobs_require_frozen_messages`
BEFORE INSERT ON `assistant_compaction_jobs`
WHEN json_array_length(NEW.`source_message_ids_json`) != NEW.`source_message_count`
	OR json_extract(NEW.`source_message_ids_json`,'$[0]') IS NOT NEW.`first_source_message_id`
	OR json_extract(
		NEW.`source_message_ids_json`,
		'$[' || (NEW.`source_message_count` - 1) || ']'
	) IS NOT NEW.`last_source_message_id`
	OR EXISTS (
		SELECT 1 FROM json_each(NEW.`source_message_ids_json`) source
		WHERE source.`type` != 'text'
			OR NOT EXISTS (
				SELECT 1 FROM `assistant_messages` message
				WHERE message.`id`=source.`value`
					AND message.`conversation_id`=NEW.`conversation_id`
			)
	)
BEGIN
	SELECT RAISE(ABORT, 'compaction job requires frozen conversation messages');
END;
--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `assistant_compaction_job_inputs_immutable`
BEFORE UPDATE OF `conversation_id`,`input_version`,`prior_summary_version`,
	`output_summary_version`,`source_message_ids_json`,`source_message_count`,
	`first_source_message_id`,`last_source_message_id`,`pinned_snapshot_json`,
	`context_profile_id`,`capacity_state`,`estimated_context_tokens`,
	`max_attempts`,`prompt_version`,`created_at` ON `assistant_compaction_jobs`
BEGIN
	SELECT RAISE(ABORT, 'compaction job inputs are immutable');
END;
--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `assistant_title_attempt_history_append_only`
BEFORE UPDATE OF `attempt_history_json` ON `assistant_title_jobs`
WHEN json_array_length(NEW.`attempt_history_json`) < json_array_length(OLD.`attempt_history_json`)
	OR EXISTS (
		SELECT 1 FROM json_each(OLD.`attempt_history_json`) receipt
		WHERE json(json_extract(
			NEW.`attempt_history_json`, '$[' || receipt.`key` || ']'
		)) IS NOT json(receipt.`value`)
	)
BEGIN
	SELECT RAISE(ABORT, 'title attempt history is append-only');
END;
--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `assistant_compaction_attempt_history_append_only`
BEFORE UPDATE OF `attempt_history_json` ON `assistant_compaction_jobs`
WHEN json_array_length(NEW.`attempt_history_json`) < json_array_length(OLD.`attempt_history_json`)
	OR EXISTS (
		SELECT 1 FROM json_each(OLD.`attempt_history_json`) receipt
		WHERE json(json_extract(
			NEW.`attempt_history_json`, '$[' || receipt.`key` || ']'
		)) IS NOT json(receipt.`value`)
	)
BEGIN
	SELECT RAISE(ABORT, 'compaction attempt history is append-only');
END;
--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `assistant_compaction_jobs_require_sequential_version`
BEFORE INSERT ON `assistant_compaction_jobs`
WHEN NEW.`output_summary_version` != NEW.`prior_summary_version` + 1
	OR NOT EXISTS (
		SELECT 1 FROM `assistant_conversations` conversation
		WHERE conversation.`id`=NEW.`conversation_id`
			AND conversation.`summary_version`=NEW.`prior_summary_version`
			AND conversation.`compaction_request_version`=NEW.`input_version`
			AND conversation.`pending_compaction_job_id`=NEW.`id`
	)
BEGIN
	SELECT RAISE(ABORT, 'compaction job must freeze the next summary version');
END;
--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `assistant_compaction_jobs_immutable_delete`
BEFORE DELETE ON `assistant_compaction_jobs`
BEGIN
	SELECT RAISE(ABORT, 'compaction job receipts are immutable');
END;
--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `assistant_pinned_entries_require_origins`
BEFORE INSERT ON `assistant_pinned_entries`
WHEN EXISTS (
	SELECT 1 FROM json_each(NEW.`origin_message_ids_json`) origin
	WHERE origin.`type` != 'text'
		OR NOT EXISTS (
			SELECT 1 FROM `assistant_messages` message
			WHERE message.`id`=origin.`value`
				AND message.`conversation_id`=NEW.`conversation_id`
		)
)
	OR (NEW.`created_by`='COMPACTION' AND NOT EXISTS (
		SELECT 1 FROM `assistant_compaction_jobs` job
		WHERE job.`id`=NEW.`source_job_id`
			AND job.`conversation_id`=NEW.`conversation_id`
			AND job.`status`='COMPLETED'
	))
BEGIN
	SELECT RAISE(ABORT, 'pinned state requires canonical origins');
END;
--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `assistant_pinned_entries_immutable_update`
BEFORE UPDATE ON `assistant_pinned_entries`
BEGIN
	SELECT RAISE(ABORT, 'assistant pinned state is immutable');
END;
--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `assistant_pinned_entries_immutable_delete`
BEFORE DELETE ON `assistant_pinned_entries`
BEGIN
	SELECT RAISE(ABORT, 'assistant pinned state is immutable');
END;
--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `assistant_summaries_require_sequential_job`
BEFORE INSERT ON `assistant_summaries`
WHEN NOT EXISTS (
	SELECT 1
	FROM `assistant_compaction_jobs` job
	JOIN `assistant_conversations` conversation
		ON conversation.`id`=job.`conversation_id`
	WHERE job.`id`=NEW.`source_job_id`
		AND job.`conversation_id`=NEW.`conversation_id`
		AND job.`status`='COMPLETED'
		AND job.`prior_summary_version`=conversation.`summary_version`
		AND job.`output_summary_version`=NEW.`version`
		AND conversation.`pending_compaction_job_id`=job.`id`
		AND NEW.`version`=conversation.`summary_version` + 1
)
BEGIN
	SELECT RAISE(ABORT, 'summary requires one completed sequential compaction job');
END;
--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `assistant_summaries_immutable_update`
BEFORE UPDATE ON `assistant_summaries`
BEGIN
	SELECT RAISE(ABORT, 'assistant summaries are immutable');
END;
--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `assistant_summaries_immutable_delete`
BEFORE DELETE ON `assistant_summaries`
BEGIN
	SELECT RAISE(ABORT, 'assistant summaries are immutable');
END;
--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `assistant_conversation_summary_advances_sequentially`
BEFORE UPDATE OF `summary_version` ON `assistant_conversations`
WHEN NEW.`summary_version` IS NOT OLD.`summary_version`
	AND NOT (
		NEW.`summary_version`=OLD.`summary_version` + 1
		AND EXISTS (
			SELECT 1 FROM `assistant_summaries` summary
			WHERE summary.`conversation_id`=OLD.`id`
				AND summary.`version`=NEW.`summary_version`
				AND summary.`source_job_id`=OLD.`pending_compaction_job_id`
		)
	)
BEGIN
	SELECT RAISE(ABORT, 'assistant summary version must advance sequentially');
END;
