CREATE TABLE IF NOT EXISTS `market_history_overview` (
	`overview_key` text PRIMARY KEY NOT NULL,
	`payload` text NOT NULL,
	`received_at` text NOT NULL
);
--> statement-breakpoint
CREATE TABLE IF NOT EXISTS `market_decision_overviews` (
	`overview_key` text PRIMARY KEY NOT NULL,
	`model_identity` text NOT NULL,
	`frequency` text NOT NULL,
	`payload` text NOT NULL,
	`received_at` text NOT NULL
);
