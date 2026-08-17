-- Hybrid Assistant memory keeps lexical receipts in D1 and vectors in Vectorize.
CREATE TABLE IF NOT EXISTS `assistant_memory_vectors_v1` (
	`entry_id` text PRIMARY KEY REFERENCES `assistant_memory_entries`(`id`),
	`owner_id` text NOT NULL,
	`vector_id` text NOT NULL UNIQUE,
	`embedding_text_version` text NOT NULL,
	`embedding_model` text NOT NULL,
	`embedding_model_digest` text NOT NULL CHECK (
		length(`embedding_model_digest`)=64
		AND `embedding_model_digest` NOT GLOB '*[^0-9a-f]*'
	),
	`embedding_dimensions` integer NOT NULL CHECK (`embedding_dimensions`=1024),
	`vector_mutation_id` text NOT NULL,
	`indexed_at` text NOT NULL
);
--> statement-breakpoint
CREATE INDEX IF NOT EXISTS `assistant_memory_vectors_owner_idx`
	ON `assistant_memory_vectors_v1` (`owner_id`,`vector_id`,`entry_id`);
--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `assistant_memory_vectors_require_entry`
BEFORE INSERT ON `assistant_memory_vectors_v1`
WHEN NOT EXISTS (
	SELECT 1 FROM `assistant_memory_entries` entry
	WHERE entry.`id`=NEW.`entry_id` AND entry.`owner_id`=NEW.`owner_id`
)
BEGIN
	SELECT RAISE(ABORT, 'assistant memory vector requires its owner-scoped entry');
END;
--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `assistant_memory_vectors_immutable_update`
BEFORE UPDATE ON `assistant_memory_vectors_v1`
BEGIN
	SELECT RAISE(ABORT, 'assistant memory vectors are immutable');
END;
--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `assistant_memory_vectors_immutable_delete`
BEFORE DELETE ON `assistant_memory_vectors_v1`
BEGIN
	SELECT RAISE(ABORT, 'assistant memory vectors are immutable');
END;
--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `assistant_memory_hybrid_completion_requires_vector`
BEFORE UPDATE OF `status` ON `assistant_memory_index_jobs`
WHEN NEW.`status`='COMPLETED'
	AND OLD.`index_version`='assistant-memory-hybrid-v2'
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
		'memory-index:assistant-memory-hybrid-v2:' || NEW.`id`,
		conversation.`owner_id`,NEW.`conversation_id`,NEW.`id`,NEW.`created_at`,
		'assistant-memory-hybrid-v2','PENDING',NEW.`created_at`,NEW.`created_at`
	FROM `assistant_conversations` conversation
	WHERE conversation.`id`=NEW.`conversation_id`;
END;
--> statement-breakpoint
INSERT OR IGNORE INTO `assistant_memory_index_jobs` (
	`id`,`owner_id`,`conversation_id`,`source_message_id`,`source_created_at`,
	`index_version`,`status`,`available_at`,`created_at`
)
SELECT
	'memory-index:assistant-memory-hybrid-v2:' || message.`id`,
	conversation.`owner_id`,message.`conversation_id`,message.`id`,message.`created_at`,
	'assistant-memory-hybrid-v2','PENDING',message.`created_at`,message.`created_at`
FROM `assistant_messages` message
JOIN `assistant_conversations` conversation ON conversation.`id`=message.`conversation_id`;
