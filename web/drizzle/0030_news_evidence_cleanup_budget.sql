CREATE TABLE `news_evidence_cleanup_budget` (
  `id` integer PRIMARY KEY NOT NULL,
  `budget_day` text NOT NULL,
  `reserved_rows_written` integer NOT NULL,
  `updated_at` text NOT NULL,
  CHECK (`id` = 1),
  CHECK (`reserved_rows_written` >= 0)
);
