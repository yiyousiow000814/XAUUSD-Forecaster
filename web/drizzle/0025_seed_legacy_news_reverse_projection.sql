-- Restore the legacy News read projection from the verified CURRENT generation.
-- This is a one-time reverse-compatibility handover: the authoritative source
-- remains forecasting SQLite and the generation tables remain Candidate's
-- normal read owner. Details are copied before index rows become discoverable.
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
   AND s.`index_count` = s.`detail_count`
   AND s.`missing_detail_count` = 0
   AND s.`invariant_violation_count` = 0
   AND s.`receipt_digest` = g.`expected_receipt_digest`
   AND g.`receipt_digest` = g.`expected_receipt_digest`
   AND g.`staged_index_count` = s.`index_count`
   AND g.`staged_detail_count` = s.`detail_count`
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
   AND s.`index_count` = s.`detail_count`
   AND s.`missing_detail_count` = 0
   AND s.`invariant_violation_count` = 0
   AND s.`receipt_digest` = g.`expected_receipt_digest`
   AND g.`receipt_digest` = g.`expected_receipt_digest`
   AND g.`staged_index_count` = s.`index_count`
   AND g.`staged_detail_count` = s.`detail_count`
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
