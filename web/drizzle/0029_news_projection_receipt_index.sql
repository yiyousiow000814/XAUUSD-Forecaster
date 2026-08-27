ALTER TABLE `news_projection_receipts_v2`
  ADD COLUMN `identity_keys_json` text NOT NULL DEFAULT '[]';
--> statement-breakpoint

ALTER TABLE `news_projection_receipts_v2`
  ADD COLUMN `items_json` text NOT NULL DEFAULT '[]';
--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `legacy_news_v4_current_index_delete_fence`
BEFORE DELETE ON `news_index`
WHEN COALESCE(json_extract(OLD.`payload`, '$.annotation_status'), '') <>
       'SUPERSEDED_CONTRACT'
 AND EXISTS (
   SELECT 1 FROM `news_projection_state` s
    WHERE s.`id` = 1 AND s.`projection_state` = 'CURRENT'
      AND s.`contract_version` = 'news-projection-generation-v4'
 )
BEGIN
  SELECT RAISE(IGNORE);
END;
--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `legacy_news_v4_current_detail_delete_fence`
BEFORE DELETE ON `news_details`
WHEN EXISTS (
  SELECT 1
    FROM `news_projection_state` s
    JOIN `news_index` i ON i.`detail_key` = OLD.`detail_key`
   WHERE s.`id` = 1 AND s.`projection_state` = 'CURRENT'
     AND s.`contract_version` = 'news-projection-generation-v4'
     AND COALESCE(json_extract(i.`payload`, '$.annotation_status'), '') <>
           'SUPERSEDED_CONTRACT'
)
BEGIN
  SELECT RAISE(IGNORE);
END;
