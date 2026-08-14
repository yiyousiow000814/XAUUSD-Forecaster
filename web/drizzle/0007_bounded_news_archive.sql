ALTER TABLE `news_index` ADD `cluster_id` text NOT NULL DEFAULT '';
--> statement-breakpoint
ALTER TABLE `news_index` ADD `published_time` text NOT NULL DEFAULT '';
--> statement-breakpoint
ALTER TABLE `news_index` ADD `parsed` integer NOT NULL DEFAULT 0;
--> statement-breakpoint
ALTER TABLE `news_index` ADD `model_candidate` integer NOT NULL DEFAULT 0;
--> statement-breakpoint
ALTER TABLE `news_index` ADD `impact_expires_at` text;
--> statement-breakpoint
ALTER TABLE `news_index` ADD `mirror_contract` text NOT NULL DEFAULT '';
--> statement-breakpoint
UPDATE `news_index`
SET `cluster_id` = COALESCE(json_extract(`payload`, '$.cluster_id'), `detail_key`),
    `published_time` = COALESCE(json_extract(`payload`, '$.source_published_time'),
                                `collector_first_seen_time`),
    `parsed` = CASE WHEN COALESCE(json_extract(`payload`, '$.parsed_at'), '') <> ''
                    THEN 1 ELSE 0 END,
    `model_candidate` = CASE WHEN json_extract(`payload`, '$.model_visibility') = 'MODEL_VISIBLE'
                             THEN 1 ELSE 0 END,
    `impact_expires_at` = json_extract(`payload`, '$.impact_expires_at');
--> statement-breakpoint
CREATE INDEX `news_index_published_idx` ON `news_index` (`published_time`);
--> statement-breakpoint
CREATE INDEX `news_index_cluster_idx` ON `news_index` (`cluster_id`);
--> statement-breakpoint
DROP INDEX `news_index_category_seen_idx`;
--> statement-breakpoint
CREATE INDEX `news_index_category_published_idx`
ON `news_index` (`category`, `published_time`);
