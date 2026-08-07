CREATE INDEX `news_index_seen_idx` ON `news_index` (`collector_first_seen_time`);
--> statement-breakpoint
CREATE INDEX `news_index_category_seen_idx` ON `news_index` (`category`,`collector_first_seen_time`);
