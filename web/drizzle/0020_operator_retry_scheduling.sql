CREATE TABLE IF NOT EXISTS `operator_retry_jobs` (
  `job_id` text PRIMARY KEY NOT NULL,
  `task_type` text NOT NULL,
  `title` text NOT NULL,
  `state` text NOT NULL,
  `priority` text NOT NULL,
  `available_at` text NOT NULL,
  `attempt_count` integer NOT NULL,
  `last_error` text,
  `last_failure_at` text,
  `lease_expires_at` text,
  `override_mode` text,
  `override_requested_at` text,
  `original_available_at` text NOT NULL,
  `synced_at` text NOT NULL,
  `sync_generation` text NOT NULL
);
--> statement-breakpoint
CREATE INDEX IF NOT EXISTS `operator_retry_jobs_schedule_idx`
ON `operator_retry_jobs` (`state`,`available_at`,`synced_at`);
--> statement-breakpoint
CREATE TABLE IF NOT EXISTS `operator_retry_requests` (
  `request_id` text PRIMARY KEY NOT NULL,
  `idempotency_key` text NOT NULL,
  `job_id` text NOT NULL,
  `task_type` text NOT NULL,
  `operator_id` text NOT NULL,
  `mode` text NOT NULL CHECK (`mode` IN (
    'KEEP_ORIGINAL','IMMEDIATE','DELAY_15_MIN','DELAY_1_HOUR',
    'IDLE_CAPACITY','CUSTOM_TIME')),
  `reason` text NOT NULL,
  `requested_at` text NOT NULL,
  `requested_available_at` text,
  `expected_state` text NOT NULL,
  `expected_available_at` text NOT NULL,
  `status` text NOT NULL CHECK (`status` IN (
    'PENDING','APPLYING','APPLIED','CONFLICT','REJECTED')),
  `lease_owner` text,
  `lease_token` text,
  `lease_expires_at` text,
  `completed_at` text,
  `result_json` text,
  UNIQUE (`operator_id`,`idempotency_key`,`job_id`)
);
--> statement-breakpoint
CREATE INDEX IF NOT EXISTS `operator_retry_requests_claim_idx`
ON `operator_retry_requests` (`status`,`requested_at`);
--> statement-breakpoint
CREATE TABLE IF NOT EXISTS `operator_retry_request_events` (
  `event_id` text PRIMARY KEY NOT NULL,
  `request_id` text NOT NULL,
  `event_type` text NOT NULL CHECK (`event_type` IN (
    'REQUESTED','APPLIED','CONFLICT','REJECTED')),
  `recorded_at` text NOT NULL,
  `payload_json` text NOT NULL,
  FOREIGN KEY (`request_id`) REFERENCES `operator_retry_requests` (`request_id`)
);
--> statement-breakpoint
CREATE INDEX IF NOT EXISTS `operator_retry_request_events_lookup_idx`
ON `operator_retry_request_events` (`request_id`,`recorded_at`);
--> statement-breakpoint
CREATE UNIQUE INDEX IF NOT EXISTS `operator_retry_request_events_type_idx`
ON `operator_retry_request_events` (`request_id`,`event_type`);
