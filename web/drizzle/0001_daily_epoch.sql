CREATE TABLE `news_details` (
	`detail_key` text PRIMARY KEY NOT NULL,
	`detail_hash` text NOT NULL,
	`payload` text NOT NULL,
	`received_at` text NOT NULL
);
