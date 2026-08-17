-- Roll the hybrid Assistant memory index to a new immutable generation after
-- the v2 jobs were consumed by a pre-v2 worker during deployment. The failed
-- v2 receipts remain untouched; v3 gets distinct jobs and vector receipts.
DROP TRIGGER IF EXISTS `assistant_memory_hybrid_completion_requires_vector`;
--> statement-breakpoint
CREATE TRIGGER `assistant_memory_hybrid_completion_requires_vector`
BEFORE UPDATE OF `status` ON `assistant_memory_index_jobs`
WHEN NEW.`status`='COMPLETED'
	AND OLD.`index_version` IN (
		'assistant-memory-hybrid-v2',
		'assistant-memory-hybrid-v3'
	)
	AND NOT EXISTS (
		SELECT 1 FROM `assistant_memory_entries` entry
		JOIN `assistant_memory_vectors_v1` memory_vector
			ON memory_vector.`entry_id`=entry.`id`
		WHERE entry.`source_job_id`=OLD.`id`
			AND memory_vector.`owner_id`=OLD.`owner_id`
	)
BEGIN
	SELECT RAISE(ABORT, 'completed hybrid memory index requires its vector receipt');
END;
--> statement-breakpoint
DROP TRIGGER IF EXISTS `assistant_messages_schedule_memory_index`;
--> statement-breakpoint
CREATE TRIGGER `assistant_messages_schedule_memory_index`
AFTER INSERT ON `assistant_messages`
BEGIN
	INSERT OR IGNORE INTO `assistant_memory_index_jobs` (
		`id`,`owner_id`,`conversation_id`,`source_message_id`,`source_created_at`,
		`index_version`,`status`,`available_at`,`created_at`
	)
	SELECT
		'memory-index:assistant-memory-hybrid-v3:' || NEW.`id`,
		conversation.`owner_id`,NEW.`conversation_id`,NEW.`id`,NEW.`created_at`,
		'assistant-memory-hybrid-v3','PENDING',NEW.`created_at`,NEW.`created_at`
	FROM `assistant_conversations` conversation
	WHERE conversation.`id`=NEW.`conversation_id`;
END;
--> statement-breakpoint
INSERT OR IGNORE INTO `assistant_memory_index_jobs` (
	`id`,`owner_id`,`conversation_id`,`source_message_id`,`source_created_at`,
	`index_version`,`status`,`available_at`,`created_at`
)
SELECT
	'memory-index:assistant-memory-hybrid-v3:' || message.`id`,
	conversation.`owner_id`,message.`conversation_id`,message.`id`,message.`created_at`,
	'assistant-memory-hybrid-v3','PENDING',message.`created_at`,message.`created_at`
FROM `assistant_messages` message
JOIN `assistant_conversations` conversation ON conversation.`id`=message.`conversation_id`;
