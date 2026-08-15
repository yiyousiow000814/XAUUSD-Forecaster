-- Keep this trigger-bearing migration LF-only; remote D1 rejects CRLF compound SQL.
CREATE TRIGGER IF NOT EXISTS `assistant_turn_jobs_lease_within_turn`
BEFORE UPDATE OF `lease_expires_at` ON `assistant_turn_jobs`
WHEN NEW.`lease_expires_at` IS NOT NULL
	AND NEW.`lease_expires_at` > NEW.`expires_at`
BEGIN
	SELECT RAISE(ABORT, 'assistant lease cannot outlive turn');
END;
