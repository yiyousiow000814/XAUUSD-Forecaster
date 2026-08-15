-- Keep this trigger-bearing migration LF-only; remote D1 rejects CRLF compound SQL.
ALTER TABLE `assistant_messages` ADD COLUMN `content_protocol` text;
--> statement-breakpoint
ALTER TABLE `assistant_messages` ADD COLUMN `content_document_json` text;
--> statement-breakpoint
ALTER TABLE `assistant_messages` ADD COLUMN `content_document_sha256` text;
--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `assistant_messages_structured_content_contract`
BEFORE INSERT ON `assistant_messages`
WHEN NEW.`source_kind` IN ('ASSISTANT_CHAT','NEWS_QA') AND (
	(NEW.`role`='USER' AND (
		NEW.`content_protocol` IS NOT NULL
		OR NEW.`content_document_json` IS NOT NULL
		OR NEW.`content_document_sha256` IS NOT NULL
	))
	OR (NEW.`role`='ASSISTANT' AND (
		NEW.`content_protocol` IS NOT 'assistant.content.v1'
		OR NEW.`content_document_json` IS NULL
		OR NOT json_valid(NEW.`content_document_json`)
		OR json_extract(NEW.`content_document_json`,'$.protocol') IS NOT NEW.`content_protocol`
		OR json_extract(NEW.`content_document_json`,'$.document_sha256') IS NOT NEW.`content_document_sha256`
		OR length(NEW.`content_document_sha256`) != 64
		OR NEW.`content_document_sha256` GLOB '*[^0-9a-f]*'
	))
)
BEGIN
	SELECT RAISE(ABORT, 'runtime assistant message content contract is invalid');
END;
