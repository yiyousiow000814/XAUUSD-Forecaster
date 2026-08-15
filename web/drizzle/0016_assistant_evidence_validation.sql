ALTER TABLE `news_questions` ADD COLUMN `evidence_validation_json` text;
--> statement-breakpoint
UPDATE `news_questions`
SET `status` = 'FAILED',
	`failure_code` = 'PROMPT_VERSION_SUPERSEDED',
	`processing_started_at` = NULL,
	`lease_owner` = NULL,
	`lease_token` = NULL,
	`lease_expires_at` = NULL,
	`attempt_history_json` = json_insert(
		CASE WHEN json_valid(`attempt_history_json`) THEN `attempt_history_json` ELSE '[]' END,
		'$[#]',json_object(
			'event','PROMPT_VERSION_SUPERSEDED',
			'at',strftime('%Y-%m-%dT%H:%M:%fZ','now'),
			'attempt',`attempt_count`
		)
	)
WHERE `status` IN ('PENDING','PROCESSING')
	AND `prompt_version` != 'news-qa-v3';
--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `news_questions_evidence_validation_required_update`
BEFORE UPDATE ON `news_questions`
FOR EACH ROW
WHEN NEW.`status` = 'ANSWERED'
	AND NEW.`prompt_version` = 'news-qa-v3'
	AND (
		NEW.`evidence_validation_json` IS NULL
		OR json_valid(NEW.`evidence_validation_json`) != 1
		OR CASE WHEN json_valid(NEW.`evidence_validation_json`) = 1 THEN (
			COALESCE(json_extract(NEW.`evidence_validation_json`, '$.protocol'), '') != 'assistant.evidence.v1'
			OR COALESCE(json_extract(NEW.`evidence_validation_json`, '$.validator_version'), '') != 'assistant-evidence-validator-v1'
			OR COALESCE(json_extract(NEW.`evidence_validation_json`, '$.mode'), '') NOT IN ('CITATION_COVERAGE','INSUFFICIENT_EVIDENCE')
			OR COALESCE(json_extract(NEW.`evidence_validation_json`, '$.entailment_status'), '') != 'NOT_VERIFIED'
		) ELSE 0 END
	)
BEGIN
	SELECT RAISE(ABORT, 'current news answer requires evidence validation');
END;
--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `news_questions_evidence_validation_required_insert`
BEFORE INSERT ON `news_questions`
FOR EACH ROW
WHEN NEW.`status` = 'ANSWERED'
	AND NEW.`prompt_version` = 'news-qa-v3'
	AND (
		NEW.`evidence_validation_json` IS NULL
		OR json_valid(NEW.`evidence_validation_json`) != 1
		OR CASE WHEN json_valid(NEW.`evidence_validation_json`) = 1 THEN (
			COALESCE(json_extract(NEW.`evidence_validation_json`, '$.protocol'), '') != 'assistant.evidence.v1'
			OR COALESCE(json_extract(NEW.`evidence_validation_json`, '$.validator_version'), '') != 'assistant-evidence-validator-v1'
			OR COALESCE(json_extract(NEW.`evidence_validation_json`, '$.mode'), '') NOT IN ('CITATION_COVERAGE','INSUFFICIENT_EVIDENCE')
			OR COALESCE(json_extract(NEW.`evidence_validation_json`, '$.entailment_status'), '') != 'NOT_VERIFIED'
		) ELSE 0 END
	)
BEGIN
	SELECT RAISE(ABORT, 'current news answer requires evidence validation');
END;
--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `news_questions_evidence_validation_immutable`
BEFORE UPDATE OF `evidence_validation_json` ON `news_questions`
FOR EACH ROW
WHEN NEW.`evidence_validation_json` IS NOT OLD.`evidence_validation_json`
	AND (OLD.`evidence_validation_json` IS NOT NULL OR OLD.`status` = 'ANSWERED')
BEGIN
	SELECT RAISE(ABORT, 'news evidence validation is immutable');
END;
