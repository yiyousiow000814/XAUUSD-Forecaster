-- Keep Assistant recovery reads bounded as immutable turn history grows.
CREATE INDEX IF NOT EXISTS `assistant_turn_jobs_conversation_status_idx`
	ON `assistant_turn_jobs` (`conversation_id`,`status`,`created_at`,`id`);
