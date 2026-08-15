-- Converge databases that imported the pre-fix 0011 trigger definitions.
DROP TRIGGER IF EXISTS `assistant_turn_events_require_admitted_turn`;
--> statement-breakpoint
DROP TRIGGER IF EXISTS `assistant_turn_events_require_next_sequence`;
--> statement-breakpoint
DROP TRIGGER IF EXISTS `assistant_turn_events_require_active_turn`;
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
