CREATE TABLE `news_index` (
	`detail_key` text PRIMARY KEY NOT NULL,
	`category` text NOT NULL,
	`collector_first_seen_time` text NOT NULL,
	`payload` text NOT NULL,
	`received_at` text NOT NULL
);
