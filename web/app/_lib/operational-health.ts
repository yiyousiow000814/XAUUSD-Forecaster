export type OperationalAlert = {
  code: string;
  severity: "ERROR" | "WARNING" | "INFO";
  scope: string;
  message_zh: string;
  blocking: boolean;
  evidence: Record<string, unknown>;
};

export type SchedulerTaskHealth = {
  task_type: string;
  queued: number;
  leased: number;
  backing_off: number;
  dead_letter: number;
  completed_15m: number;
  deferred_15m: number;
  errors_15m: number;
  failure_codes_15m: Array<{ code: string; count: number }>;
  claimable: number;
  scheduled_retry: number;
  earliest_retry_at: string | null;
  oldest_active_at: string | null;
  oldest_age_seconds: number | null;
  max_claim_count: number;
  max_claim_job_ref: string | null;
};

export type OperationalHealth = {
  schema_version: string;
  observed_at: string;
  window_seconds: number;
  status: "HEALTHY" | "WARNING" | "ERROR";
  alerts: OperationalAlert[];
  scheduler: { status: string; tasks: SchedulerTaskHealth[] };
};

export type AssistantQueueHealth = {
  queue: string;
  label: string;
  queued: number;
  processing: number;
  claimable: number;
  scheduled_retry: number;
  oldest_active_at: string | null;
  oldest_age_seconds: number | null;
  max_attempt_count: number;
  completed_15m: number;
  failed_15m: number;
  capacity_deferred: number;
  failure_codes: Array<{ code: string; count: number }>;
};

export type AssistantOperationalHealth = {
  schema_version: string;
  observed_at: string | null;
  status: "HEALTHY" | "WARNING" | "ERROR" | "SNAPSHOT_UNAVAILABLE";
  alerts: OperationalAlert[];
  queues: AssistantQueueHealth[];
  current: boolean;
};

export const schedulerTaskLabel: Record<string, string> = {
  ACTIVE_ANNOTATION: "Gemini 语义复核",
  ACTIVE_IMPACT: "Gemma 事件与影响复核",
  TITLE_TRANSLATION: "中文标题展示",
};
