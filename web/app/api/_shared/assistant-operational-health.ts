import { normalizeOperationalEvent, type OperationalAlert } from "../../_lib/operational-health";

export type AssistantQueueDefinition = {
  queue: string;
  label: string;
  table: string;
  createdColumn: string;
  completedExpression: string;
  successStatuses: string[];
  failureStatuses: string[];
  slaSeconds: number;
};

type QueueAggregateRow = {
  queued: number | string | null;
  processing: number | string | null;
  claimable: number | string | null;
  scheduled_retry: number | string | null;
  oldest_active_at: string | null;
  max_attempt_count: number | string | null;
  completed_15m: number | string | null;
  failed_15m: number | string | null;
  capacity_deferred: number | string | null;
};

type FailureRow = { failure_code: string | null; total: number | string | null };

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

const definitions: AssistantQueueDefinition[] = [
  { queue: "CHAT_TURN", label: "Assistant 对话", table: "assistant_turn_jobs", createdColumn: "created_at", completedExpression: "completed_at", successStatuses: ["ANSWERED"], failureStatuses: ["FAILED", "REJECTED", "EXPIRED"], slaSeconds: 300 },
  { queue: "NEWS_QUESTION", label: "新闻问答", table: "news_questions", createdColumn: "asked_at", completedExpression: "COALESCE(answered_at,available_at)", successStatuses: ["ANSWERED"], failureStatuses: ["FAILED", "REJECTED", "EXPIRED"], slaSeconds: 300 },
  { queue: "TITLE", label: "会话标题", table: "assistant_title_jobs", createdColumn: "created_at", completedExpression: "completed_at", successStatuses: ["COMPLETED"], failureStatuses: ["FAILED"], slaSeconds: 1800 },
  { queue: "COMPACTION", label: "上下文压缩", table: "assistant_compaction_jobs", createdColumn: "created_at", completedExpression: "completed_at", successStatuses: ["COMPLETED"], failureStatuses: ["FAILED"], slaSeconds: 1800 },
  { queue: "MEMORY_INDEX", label: "历史记忆索引", table: "assistant_memory_index_jobs", createdColumn: "created_at", completedExpression: "completed_at", successStatuses: ["COMPLETED"], failureStatuses: ["FAILED"], slaSeconds: 1800 },
];

const quoted = (values: string[]) => values.map(value => `'${value}'`).join(",");
const count = (value: number | string | null | undefined) => Math.max(0, Number(value ?? 0));

export function summarizeAssistantQueue(
  definition: AssistantQueueDefinition,
  aggregate: QueueAggregateRow,
  failures: FailureRow[],
  now: Date,
): AssistantQueueHealth {
  const oldest = aggregate.oldest_active_at ? Date.parse(aggregate.oldest_active_at) : NaN;
  return {
    queue: definition.queue,
    label: definition.label,
    queued: count(aggregate.queued),
    processing: count(aggregate.processing),
    claimable: count(aggregate.claimable),
    scheduled_retry: count(aggregate.scheduled_retry),
    oldest_active_at: aggregate.oldest_active_at ?? null,
    oldest_age_seconds: Number.isFinite(oldest)
      ? Math.max(0, Math.floor((now.getTime() - oldest) / 1000)) : null,
    max_attempt_count: count(aggregate.max_attempt_count),
    completed_15m: count(aggregate.completed_15m),
    failed_15m: count(aggregate.failed_15m),
    capacity_deferred: count(aggregate.capacity_deferred),
    failure_codes: failures
      .filter(row => row.failure_code)
      .map(row => ({ code: String(row.failure_code), count: count(row.total) }))
      .sort((left, right) => right.count - left.count || left.code.localeCompare(right.code))
      .slice(0, 8),
  };
}

export async function assistantOperationalHealth(
  binding: D1Database,
  now = new Date(),
) {
  const timestamp = now.toISOString();
  const cutoff = new Date(now.getTime() - 15 * 60_000).toISOString();
  const queues: AssistantQueueHealth[] = [];
  for (const definition of definitions) {
    const successes = quoted(definition.successStatuses);
    const failures = quoted(definition.failureStatuses);
    const aggregate = await binding.prepare(
      `SELECT
         COALESCE(sum(CASE WHEN status='PENDING' THEN 1 ELSE 0 END),0) queued,
         COALESCE(sum(CASE WHEN status='PROCESSING' THEN 1 ELSE 0 END),0) processing,
         COALESCE(sum(CASE WHEN status='PROCESSING' OR (status='PENDING' AND available_at<=?) THEN 1 ELSE 0 END),0) claimable,
         COALESCE(sum(CASE WHEN status='PENDING' AND available_at>? THEN 1 ELSE 0 END),0) scheduled_retry,
         min(CASE WHEN status='PROCESSING' OR (status='PENDING' AND available_at<=?)
                  THEN CASE WHEN available_at>${definition.createdColumn} THEN available_at ELSE ${definition.createdColumn} END END) oldest_active_at,
         COALESCE(max(CASE WHEN status='PROCESSING' OR (status='PENDING' AND available_at<=?) THEN attempt_count ELSE 0 END),0) max_attempt_count,
         COALESCE(sum(CASE WHEN status IN (${successes}) AND ${definition.completedExpression}>=? THEN 1 ELSE 0 END),0) completed_15m,
         COALESCE(sum(CASE WHEN status IN (${failures}) AND ${definition.completedExpression}>=? THEN 1 ELSE 0 END),0) failed_15m,
         COALESCE(sum(CASE WHEN status='PENDING' AND failure_code IN ('NO_MODEL_CAPACITY','CAPACITY_DEFERRED') THEN 1 ELSE 0 END),0) capacity_deferred
       FROM ${definition.table}`,
    ).bind(timestamp, timestamp, timestamp, timestamp, cutoff, cutoff).first<QueueAggregateRow>();
    const failureResult = await binding.prepare(
      `SELECT failure_code,count(*) total FROM ${definition.table}
       WHERE status IN (${failures}) AND failure_code IS NOT NULL
         AND ${definition.completedExpression}>=?
       GROUP BY failure_code ORDER BY total DESC,failure_code LIMIT 8`,
    ).bind(cutoff).all<FailureRow>();
    queues.push(summarizeAssistantQueue(
      definition,
      aggregate ?? {
        queued: 0, processing: 0, claimable: 0, scheduled_retry: 0,
        oldest_active_at: null, max_attempt_count: 0, completed_15m: 0,
        failed_15m: 0, capacity_deferred: 0,
      },
      failureResult.results ?? [],
      now,
    ));
  }

  const alerts: OperationalAlert[] = [];
  for (const queue of queues) {
    const definition = definitions.find(item => item.queue === queue.queue)!;
    const evidence = {
      claimable: queue.claimable,
      scheduled_retry: queue.scheduled_retry,
      completed_15m: queue.completed_15m,
      failed_15m: queue.failed_15m,
      oldest_age_seconds: queue.oldest_age_seconds,
    };
    if (queue.max_attempt_count >= 3 && queue.claimable > 0) alerts.push({
      code: "OPS_ASSISTANT_JOB_RETRY_LOOP", severity: "ERROR", scope: queue.queue,
      message_zh: `${queue.label}有任务已尝试 ${queue.max_attempt_count} 次。`, blocking: true,
      evidence: { ...evidence, max_attempt_count: queue.max_attempt_count },
    });
    if (queue.claimable > 0 && (queue.oldest_age_seconds ?? 0) >= definition.slaSeconds) alerts.push({
      code: queue.completed_15m === 0 ? "OPS_ASSISTANT_PIPELINE_STALLED" : "OPS_ASSISTANT_BACKLOG_OVERDUE",
      severity: queue.completed_15m === 0 ? "ERROR" : "WARNING", scope: queue.queue,
      message_zh: queue.completed_15m === 0
        ? `${queue.label}有 ${queue.claimable} 条可处理任务，但最近15分钟没有完成。`
        : `${queue.label}最旧可处理任务已等待 ${Math.floor((queue.oldest_age_seconds ?? 0) / 60)} 分钟。`,
      blocking: queue.completed_15m === 0, evidence,
    });
    if (queue.failed_15m > 0) alerts.push({
      code: "OPS_ASSISTANT_NEW_TERMINAL_FAILURE", severity: "WARNING", scope: queue.queue,
      message_zh: `${queue.label}最近15分钟新增 ${queue.failed_15m} 个终止失败。`, blocking: false,
      evidence: { ...evidence, failure_codes: queue.failure_codes },
    });
  }
  const normalizedAlerts = alerts.map(normalizeOperationalEvent);
  normalizedAlerts.sort((left, right) => {
    const order = { ERROR: 0, WARNING: 1, INFO: 2 } as Record<string, number>;
    return order[String(left.severity)] - order[String(right.severity)]
      || String(left.code).localeCompare(String(right.code));
  });
  return {
    schema_version: "assistant-operational-health.v1",
    observed_at: timestamp,
    status: normalizedAlerts.some(alert => alert.severity === "ERROR")
      ? "ERROR" : normalizedAlerts.length ? "WARNING" : "HEALTHY",
    alerts: normalizedAlerts,
    queues,
  };
}
