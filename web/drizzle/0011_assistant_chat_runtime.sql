-- Keep this trigger-bearing migration LF-only; remote D1 rejects CRLF compound SQL.
CREATE TABLE IF NOT EXISTS `assistant_turn_jobs` (
	`id` text PRIMARY KEY NOT NULL,
	`owner_id` text NOT NULL,
	`conversation_id` text NOT NULL REFERENCES `assistant_conversations`(`id`),
	`user_message_id` text NOT NULL UNIQUE REFERENCES `assistant_messages`(`id`),
	`idempotency_key` text NOT NULL,
	`message_hash` text NOT NULL,
	`status` text NOT NULL CHECK (`status` IN (
		'PENDING','PROCESSING','ANSWERED','FAILED','REJECTED','EXPIRED','CANCELLED'
	)),
	`event_sequence` integer NOT NULL DEFAULT 0 CHECK (`event_sequence` BETWEEN 0 AND 256),
	`available_at` text NOT NULL,
	`expires_at` text NOT NULL,
	`processing_started_at` text,
	`lease_owner` text,
	`lease_token` text,
	`lease_expires_at` text,
	`attempt_count` integer NOT NULL DEFAULT 0 CHECK (`attempt_count` >= 0),
	`max_attempts` integer NOT NULL DEFAULT 3 CHECK (`max_attempts` BETWEEN 1 AND 5),
	`attempt_history_json` text NOT NULL DEFAULT '[]' CHECK (
		json_valid(`attempt_history_json`) AND json_type(`attempt_history_json`)='array'
	),
	`assistant_message_id` text REFERENCES `assistant_messages`(`id`),
	`failure_code` text,
	`cancel_requested` integer NOT NULL DEFAULT 0 CHECK (`cancel_requested` IN (0,1)),
	`created_at` text NOT NULL,
	`completed_at` text,
	UNIQUE (`owner_id`,`idempotency_key`),
	CHECK (
		(`status`='PROCESSING' AND `lease_owner` IS NOT NULL
			AND `lease_token` IS NOT NULL AND `lease_expires_at` IS NOT NULL
			AND `processing_started_at` IS NOT NULL)
		OR (`status`!='PROCESSING' AND `lease_owner` IS NULL
			AND `lease_token` IS NULL AND `lease_expires_at` IS NULL
			AND `processing_started_at` IS NULL)
	),
	CHECK (
		(`status`='ANSWERED' AND `assistant_message_id` IS NOT NULL)
		OR (`status`!='ANSWERED' AND `assistant_message_id` IS NULL)
	),
	CHECK (`attempt_count` <= `max_attempts`),
	CHECK (
		(`status` IN ('ANSWERED','FAILED','REJECTED','EXPIRED','CANCELLED')
			AND `completed_at` IS NOT NULL)
		OR (`status` IN ('PENDING','PROCESSING') AND `completed_at` IS NULL)
	),
	CHECK (
		(`status`='CANCELLED' AND `cancel_requested`=1)
		OR (`status`!='CANCELLED' AND `cancel_requested`=0)
	),
	CHECK (
		(`status` IN ('FAILED','REJECTED','EXPIRED','CANCELLED')
			AND `failure_code` IS NOT NULL)
		OR `status`='PENDING'
		OR (`status` IN ('PROCESSING','ANSWERED') AND `failure_code` IS NULL)
	)
);
--> statement-breakpoint
CREATE INDEX IF NOT EXISTS `assistant_turn_jobs_claim_idx`
	ON `assistant_turn_jobs` (`status`,`available_at`,`created_at`,`id`);
--> statement-breakpoint
CREATE INDEX IF NOT EXISTS `assistant_turn_jobs_lease_idx`
	ON `assistant_turn_jobs` (`status`,`lease_expires_at`);
--> statement-breakpoint
CREATE INDEX IF NOT EXISTS `assistant_turn_jobs_owner_idx`
	ON `assistant_turn_jobs` (`owner_id`,`created_at`,`id`);
--> statement-breakpoint
CREATE TABLE IF NOT EXISTS `assistant_turn_events` (
	`id` text PRIMARY KEY NOT NULL,
	`turn_id` text NOT NULL REFERENCES `assistant_turn_jobs`(`id`),
	`protocol` text NOT NULL CHECK (`protocol`='assistant.event.v1'),
	`sequence` integer NOT NULL CHECK (`sequence` BETWEEN 1 AND 256),
	`type` text NOT NULL CHECK (`type` IN (
		'conversation.started','reasoning.started','tool.started','tool.completed',
		'tool.failed','retrieval.started','retrieval.completed','answer.started',
		'answer.delta','content.block','answer.completed','conversation.completed',
		'error','cancelled'
	)),
	`message_id` text REFERENCES `assistant_messages`(`id`),
	`occurred_at` text NOT NULL,
	`payload_json` text NOT NULL CHECK (
		json_valid(`payload_json`) AND json_type(`payload_json`)='object'
		AND length(CAST(`payload_json` AS BLOB)) <= 16384
	),
	`idempotency_key` text NOT NULL,
	UNIQUE (`turn_id`,`sequence`),
	UNIQUE (`turn_id`,`idempotency_key`),
	CHECK (
		(`type`='answer.completed' AND `message_id` IS NOT NULL)
		OR (`type`!='answer.completed' AND `message_id` IS NULL)
	)
);
--> statement-breakpoint
CREATE INDEX IF NOT EXISTS `assistant_turn_events_replay_idx`
	ON `assistant_turn_events` (`turn_id`,`sequence`);
--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `assistant_turn_jobs_require_owner_message`
BEFORE INSERT ON `assistant_turn_jobs`
WHEN NOT EXISTS (
	SELECT 1 FROM `assistant_conversations` conversation
	WHERE conversation.`id`=NEW.`conversation_id`
		AND conversation.`owner_id`=NEW.`owner_id`
		AND conversation.`status`='ACTIVE'
)
	OR NOT EXISTS (
		SELECT 1 FROM `assistant_messages` message
		WHERE message.`id`=NEW.`user_message_id`
			AND message.`conversation_id`=NEW.`conversation_id`
			AND message.`role`='USER'
	)
BEGIN
	SELECT RAISE(ABORT, 'assistant turn requires one owner-scoped user message');
END;
--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `assistant_turn_jobs_inputs_immutable`
BEFORE UPDATE OF `owner_id`,`conversation_id`,`user_message_id`,`idempotency_key`,
	`message_hash`,`max_attempts`,`created_at` ON `assistant_turn_jobs`
BEGIN
	SELECT RAISE(ABORT, 'assistant turn inputs are immutable');
END;
--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `assistant_turn_jobs_terminal_immutable`
BEFORE UPDATE ON `assistant_turn_jobs`
WHEN OLD.`status` IN ('ANSWERED','FAILED','REJECTED','EXPIRED','CANCELLED')
BEGIN
	SELECT RAISE(ABORT, 'assistant terminal turn is immutable');
END;
--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `assistant_turn_jobs_status_transition`
BEFORE UPDATE OF `status` ON `assistant_turn_jobs`
WHEN NEW.`status` IS NOT OLD.`status` AND NOT (
	(OLD.`status`='PENDING'
		AND NEW.`status` IN ('PROCESSING','FAILED','REJECTED','EXPIRED','CANCELLED'))
	OR (OLD.`status`='PROCESSING'
		AND NEW.`status` IN ('PENDING','ANSWERED','FAILED','EXPIRED','CANCELLED'))
)
BEGIN
	SELECT RAISE(ABORT, 'assistant turn status transition is invalid');
END;
--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `assistant_turn_events_require_admitted_turn`
BEFORE INSERT ON `assistant_turn_events`
WHEN NOT EXISTS (
	SELECT 1 FROM `assistant_turn_jobs` turn
	WHERE turn.`id`=NEW.`turn_id`
)
BEGIN
	SELECT RAISE(ABORT, 'assistant event requires admitted turn');
END;
--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `assistant_turn_events_require_next_sequence`
BEFORE INSERT ON `assistant_turn_events`
WHEN EXISTS (
	SELECT 1 FROM `assistant_turn_jobs` turn
	WHERE turn.`id`=NEW.`turn_id`
)
	AND NOT EXISTS (
		SELECT 1 FROM `assistant_turn_jobs` turn
		WHERE turn.`id`=NEW.`turn_id`
			AND NEW.`sequence`=turn.`event_sequence` + 1
	)
BEGIN
	SELECT RAISE(ABORT, 'assistant event sequence must be contiguous');
END;
--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `assistant_turn_events_require_active_turn`
BEFORE INSERT ON `assistant_turn_events`
WHEN EXISTS (
	SELECT 1 FROM `assistant_turn_jobs` turn
	WHERE turn.`id`=NEW.`turn_id`
)
	AND NOT EXISTS (
		SELECT 1 FROM `assistant_turn_jobs` turn
		WHERE turn.`id`=NEW.`turn_id` AND turn.`status` IN ('PENDING','PROCESSING')
	)
BEGIN
	SELECT RAISE(ABORT, 'assistant event requires active turn');
END;
--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `assistant_turn_events_require_canonical_message`
BEFORE INSERT ON `assistant_turn_events`
WHEN NEW.`message_id` IS NOT NULL AND NOT EXISTS (
	SELECT 1
	FROM `assistant_messages` message
	JOIN `assistant_turn_jobs` turn ON turn.`id`=NEW.`turn_id`
	WHERE message.`id`=NEW.`message_id`
		AND message.`conversation_id`=turn.`conversation_id`
		AND message.`role`='ASSISTANT'
)
BEGIN
	SELECT RAISE(ABORT, 'assistant completion event requires canonical message');
END;
--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `assistant_turn_event_sequence_advances_one`
BEFORE UPDATE OF `event_sequence` ON `assistant_turn_jobs`
WHEN NEW.`event_sequence` IS NOT OLD.`event_sequence`
	AND NOT (
		NEW.`event_sequence`=OLD.`event_sequence` + 1
		AND EXISTS (
			SELECT 1 FROM `assistant_turn_events` event
			WHERE event.`turn_id`=OLD.`id`
				AND event.`sequence`=NEW.`event_sequence`
		)
	)
BEGIN
	SELECT RAISE(ABORT, 'assistant event sequence must advance one');
END;
--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `assistant_turn_attempt_history_append_only`
BEFORE UPDATE OF `attempt_history_json` ON `assistant_turn_jobs`
WHEN json_array_length(NEW.`attempt_history_json`) < json_array_length(OLD.`attempt_history_json`)
	OR EXISTS (
		SELECT 1 FROM json_each(OLD.`attempt_history_json`) receipt
		WHERE json(json_extract(
			NEW.`attempt_history_json`, '$[' || receipt.`key` || ']'
		)) IS NOT json(receipt.`value`)
	)
BEGIN
	SELECT RAISE(ABORT, 'assistant turn attempt history is append-only');
END;
--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `assistant_turn_events_immutable_update`
BEFORE UPDATE ON `assistant_turn_events`
BEGIN
	SELECT RAISE(ABORT, 'assistant turn events are immutable');
END;
--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `assistant_turn_events_immutable_delete`
BEFORE DELETE ON `assistant_turn_events`
BEGIN
	SELECT RAISE(ABORT, 'assistant turn events are immutable');
END;
--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `assistant_turn_jobs_immutable_delete`
BEFORE DELETE ON `assistant_turn_jobs`
BEGIN
	SELECT RAISE(ABORT, 'assistant turn jobs are immutable');
END;
