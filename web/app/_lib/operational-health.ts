import operationalCodeRegistry from "../../../xauusd_forecaster/operational_codes.json" with { type: "json" };

export type OperationalCategory = "CAPACITY" | "PROVIDER" | "BACKLOG" | "RETRY" | "MODEL_OUTPUT" | "DATA" | "SEMANTIC" | "SYNC" | "RUNTIME" | "DEPLOYMENT" | "CONFIGURATION" | "DEPENDENCY" | "SECURITY";
export type OperationalRole = "ROOT" | "SYMPTOM" | "STATE";
export type RecoveryPolicy = "AUTO" | "CONDITIONAL" | "OPERATOR";

export type OperationalCodeDefinition = {
  code: string;
  kind: "ALERT" | "FAILURE_REASON" | "HEALTH_REASON";
  category: OperationalCategory;
  root_cause_family: string;
  default_role: OperationalRole;
  recovery_policy: RecoveryPolicy;
  title_zh: string;
  description: string;
  allowed_severities?: Array<OperationalAlert["severity"]>;
};

export type OperationalAlert = {
  code: string;
  severity: "ERROR" | "WARNING" | "INFO";
  scope: string;
  message_zh: string;
  blocking: boolean;
  evidence: Record<string, unknown>;
  category?: OperationalCategory;
  root_cause_family?: string;
  role?: OperationalRole;
  recovery_policy?: RecoveryPolicy;
};

export const OPERATIONAL_CODE_REGISTRY_VERSION = operationalCodeRegistry.schema_version;
export const operationalCodeDefinitions = new Map(
  (operationalCodeRegistry.codes as OperationalCodeDefinition[]).map(item => [item.code, item]),
);

export function normalizeOperationalEvent(event: OperationalAlert): Required<OperationalAlert> {
  const definition = operationalCodeDefinitions.get(event.code);
  return {
    ...event,
    evidence: definition ? event.evidence : {
      ...event.evidence,
      taxonomy_error: `UNREGISTERED_OPERATIONAL_CODE:${event.code}`,
    },
    category: event.category ?? definition?.category ?? "RUNTIME",
    root_cause_family: event.root_cause_family ?? definition?.root_cause_family ?? "UNREGISTERED_OPERATIONAL_CODE",
    role: event.role ?? definition?.default_role ?? "ROOT",
    recovery_policy: event.recovery_policy ?? definition?.recovery_policy ?? "OPERATOR",
  };
}

export type SchedulerTaskHealth = {
  task_type: string;
  queued: number;
  leased: number;
  backing_off: number;
  dead_letter: number;
  completed_15m: number;
  deferred_15m: number;
  provider_dispatch_deferred_15m?: number;
  errors_15m: number;
  failure_codes_15m: Array<{ code: string; count: number }>;
  capacity_dimensions_15m?: Array<{ dimension: string; count: number }>;
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

/** Keep the global shell quiet unless an operator-facing problem is blocking. */
export const globalOperationalAlerts = (alerts: OperationalAlert[]) => alerts.filter(
  alert => alert.blocking || alert.severity === "ERROR",
);

export const schedulerTaskLabel: Record<string, string> = {
  ACTIVE_ANNOTATION: "Gemini 语义复核",
  ACTIVE_IMPACT: "Gemma 事件与影响复核",
  TITLE_TRANSLATION: "中文标题展示",
};
