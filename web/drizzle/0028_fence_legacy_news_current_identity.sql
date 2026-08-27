-- Keep the rollback-only legacy projection equal to the verified CURRENT
-- generation while the pre-cutover Stable writer is still running.
INSERT INTO `news_details` (`detail_key`, `detail_hash`, `payload`, `received_at`)
SELECT d.`detail_key`, d.`detail_hash`, d.`payload`, d.`received_at`
  FROM `news_projection_details` d
  JOIN `news_projection_state` s
    ON s.`id` = 1
   AND s.`active_generation_id` = d.`generation_id`
  JOIN `news_projection_generations` g
    ON g.`generation_id` = s.`active_generation_id`
 WHERE s.`projection_state` = 'CURRENT'
   AND g.`state` = 'CURRENT'
   AND s.`receipt_digest` = g.`expected_receipt_digest`
   AND g.`receipt_digest` = g.`expected_receipt_digest`
   AND s.`missing_detail_count` = 0
   AND s.`invariant_violation_count` = 0
ON CONFLICT(`detail_key`) DO UPDATE SET
  `detail_hash` = `excluded`.`detail_hash`,
  `payload` = `excluded`.`payload`,
  `received_at` = `excluded`.`received_at`;
--> statement-breakpoint
INSERT INTO `news_index`
  (`detail_key`, `category`, `cluster_id`, `published_time`,
   `collector_first_seen_time`, `parsed`, `model_candidate`,
   `impact_expires_at`, `mirror_contract`, `payload`, `received_at`)
SELECT i.`detail_key`, i.`category`, i.`cluster_id`, i.`published_time`,
       i.`collector_first_seen_time`, i.`parsed`, i.`model_candidate`,
       i.`impact_expires_at`, i.`mirror_contract`, i.`payload`, i.`received_at`
  FROM `news_projection_index` i
  JOIN `news_projection_state` s
    ON s.`id` = 1
   AND s.`active_generation_id` = i.`generation_id`
  JOIN `news_projection_generations` g
    ON g.`generation_id` = s.`active_generation_id`
 WHERE s.`projection_state` = 'CURRENT'
   AND g.`state` = 'CURRENT'
   AND s.`receipt_digest` = g.`expected_receipt_digest`
   AND g.`receipt_digest` = g.`expected_receipt_digest`
   AND s.`missing_detail_count` = 0
   AND s.`invariant_violation_count` = 0
ON CONFLICT(`detail_key`) DO UPDATE SET
  `category` = `excluded`.`category`,
  `cluster_id` = `excluded`.`cluster_id`,
  `published_time` = `excluded`.`published_time`,
  `collector_first_seen_time` = `excluded`.`collector_first_seen_time`,
  `parsed` = `excluded`.`parsed`,
  `model_candidate` = `excluded`.`model_candidate`,
  `impact_expires_at` = `excluded`.`impact_expires_at`,
  `mirror_contract` = `excluded`.`mirror_contract`,
  `payload` = `excluded`.`payload`,
  `received_at` = `excluded`.`received_at`;
--> statement-breakpoint
UPDATE `news_index`
   SET `parsed` = 0,
       `model_candidate` = 0,
       `payload` = json_set(
         json_set(
           json_set(`payload`, '$.annotation_status', 'SUPERSEDED_CONTRACT'),
           '$.model_visibility', 'MODEL_INELIGIBLE'
         ),
         '$.parsed_at', json('null')
       )
 WHERE COALESCE(json_extract(`payload`, '$.annotation_status'), '') <>
         'SUPERSEDED_CONTRACT'
   AND EXISTS (
     SELECT 1 FROM `news_projection_state` s
      WHERE s.`id` = 1 AND s.`projection_state` = 'CURRENT'
   )
   AND NOT EXISTS (
     SELECT 1
       FROM `news_projection_state` s
       JOIN `news_projection_index` i
         ON i.`generation_id` = s.`active_generation_id`
      WHERE s.`id` = 1
        AND s.`projection_state` = 'CURRENT'
        AND i.`detail_key` = `news_index`.`detail_key`
   );
--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `legacy_news_current_index_delete_fence`
BEFORE DELETE ON `news_index`
WHEN EXISTS (
  SELECT 1
    FROM `news_projection_state` s
    JOIN `news_projection_index` i
      ON i.`generation_id` = s.`active_generation_id`
     AND i.`detail_key` = OLD.`detail_key`
   WHERE s.`id` = 1 AND s.`projection_state` = 'CURRENT'
)
BEGIN
  SELECT RAISE(IGNORE);
END;
--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `legacy_news_current_detail_delete_fence`
BEFORE DELETE ON `news_details`
WHEN EXISTS (
  SELECT 1
    FROM `news_projection_state` s
    JOIN `news_projection_index` i
      ON i.`generation_id` = s.`active_generation_id`
     AND i.`detail_key` = OLD.`detail_key`
   WHERE s.`id` = 1 AND s.`projection_state` = 'CURRENT'
)
BEGIN
  SELECT RAISE(IGNORE);
END;
--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `legacy_news_noncurrent_index_insert_fence`
BEFORE INSERT ON `news_index`
WHEN EXISTS (
  SELECT 1 FROM `news_projection_state` s
   WHERE s.`id` = 1 AND s.`projection_state` = 'CURRENT'
)
AND NOT EXISTS (
  SELECT 1
    FROM `news_projection_state` s
    JOIN `news_projection_index` i
      ON i.`generation_id` = s.`active_generation_id`
     AND i.`detail_key` = NEW.`detail_key`
   WHERE s.`id` = 1 AND s.`projection_state` = 'CURRENT'
)
BEGIN
  SELECT RAISE(IGNORE);
END;
--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `legacy_news_current_index_update_fence`
BEFORE UPDATE ON `news_index`
WHEN EXISTS (SELECT 1 FROM `news_projection_state` s
 WHERE s.`id` = 1 AND s.`projection_state` = 'CURRENT')
BEGIN
  SELECT RAISE(IGNORE);
END;
