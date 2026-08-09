CREATE TABLE IF NOT EXISTS `market_candles` (
	`time_epoch` integer PRIMARY KEY NOT NULL,
	`time` text NOT NULL,
	`open_milli` integer NOT NULL,
	`high_milli` integer NOT NULL,
	`low_milli` integer NOT NULL,
	`close_milli` integer NOT NULL,
	`ticks` integer NOT NULL,
	`received_at` text NOT NULL
);
--> statement-breakpoint
CREATE TABLE IF NOT EXISTS `market_decisions` (
	`decision_key` text PRIMARY KEY NOT NULL,
	`decision_epoch` integer NOT NULL,
	`decision_time` text NOT NULL,
	`model_identity` text NOT NULL,
	`payload` text NOT NULL,
	`received_at` text NOT NULL
);
--> statement-breakpoint
CREATE INDEX IF NOT EXISTS `market_decisions_time_idx` ON `market_decisions` (`decision_epoch`);
--> statement-breakpoint
CREATE INDEX IF NOT EXISTS `market_decisions_model_time_idx` ON `market_decisions` (`model_identity`,`decision_epoch`);
