export const OPERATOR_RETRY_MODES = [
  "KEEP_ORIGINAL", "IMMEDIATE", "DELAY_15_MIN", "DELAY_1_HOUR",
  "IDLE_CAPACITY", "CUSTOM_TIME",
] as const;

export type OperatorRetryMode = typeof OPERATOR_RETRY_MODES[number];
export const OPERATOR_RETRY_BATCH_LIMIT = 100;
export const OPERATOR_RETRY_LEASE_MS = 2 * 60_000;

export class OperatorRetryInputError extends Error {
  readonly code: string;

  constructor(code: string, message: string) {
    super(message);
    this.code = code;
  }
}

// A full 200-row mirror can change at once after an offline period. Drain that
// delta across control cycles instead of spending an unbounded share of the
// account's daily D1 write allowance in one catch-up burst.
export const OPERATOR_RETRY_SYNC_MUTATIONS_PER_INVOCATION = 3;

const retryJobJson = (alias: string) => `json_object(
  'job_id',${alias}.job_id,'task_type',${alias}.task_type,'title',${alias}.title,
  'state',${alias}.state,'priority',${alias}.priority,
  'available_at',${alias}.available_at,'attempt_count',${alias}.attempt_count,
  'last_error',${alias}.last_error,'last_failure_at',${alias}.last_failure_at,
  'lease_expires_at',${alias}.lease_expires_at,'override_mode',${alias}.override_mode,
  'override_requested_at',${alias}.override_requested_at,
  'original_available_at',${alias}.original_available_at)`;

const isoTime = (value: unknown, field: string) => {
  const text = String(value ?? "").trim();
  const epoch = Date.parse(text);
  if (!text || !Number.isFinite(epoch)) {
    throw new OperatorRetryInputError("INVALID_TIME", `${field} 时间无效`);
  }
  return new Date(epoch).toISOString();
};

const digest = async (value: string) => {
  const bytes = new Uint8Array(await crypto.subtle.digest(
    "SHA-256", new TextEncoder().encode(value),
  ));
  return Array.from(bytes, byte => byte.toString(16).padStart(2, "0")).join("");
};

export function parseOperatorRetryMode(value: unknown): OperatorRetryMode {
  const mode = String(value ?? "").trim().toUpperCase();
  if (!OPERATOR_RETRY_MODES.includes(mode as OperatorRetryMode)) {
    throw new OperatorRetryInputError("INVALID_MODE", "重试计划选项无效");
  }
  return mode as OperatorRetryMode;
}

export function parseOperatorRetryReason(value: unknown) {
  const reason = String(value ?? "").trim().replace(/\s+/g, " ");
  if (!reason || reason.length > 500) {
    throw new OperatorRetryInputError("INVALID_REASON", "请填写 1 至 500 字的调整原因");
  }
  return reason;
}

export function parseOperatorRetryCustomTime(
  mode: OperatorRetryMode,
  value: unknown,
  now = new Date(),
) {
  if (mode !== "CUSTOM_TIME") {
    if (value !== undefined && value !== null && value !== "") {
      throw new OperatorRetryInputError("UNEXPECTED_TIME", "此选项不接受指定时间");
    }
    return null;
  }
  const timestamp = isoTime(value, "指定重试");
  const epoch = Date.parse(timestamp);
  if (epoch < now.getTime() - 5 * 60_000) {
    throw new OperatorRetryInputError("CUSTOM_TIME_PAST", "指定时间不能早于当前时间 5 分钟以上");
  }
  if (epoch > now.getTime() + 366 * 24 * 60 * 60_000) {
    throw new OperatorRetryInputError("CUSTOM_TIME_TOO_FAR", "指定时间不能超过一年");
  }
  return timestamp;
}

export function parseOperatorRetryIdempotencyKey(value: string | null) {
  const key = value?.trim() ?? "";
  if (!/^[A-Za-z0-9._:-]{16,128}$/.test(key)) {
    throw new OperatorRetryInputError("INVALID_IDEMPOTENCY_KEY", "缺少有效的 Idempotency-Key");
  }
  return key;
}

type RetryJobRow = Record<string, unknown> & {
  job_id: string; task_type: string; state: string; available_at: string;
};

export async function listOperatorRetryJobs(binding: D1Database, limit = 200) {
  const bounded = Math.max(1, Math.min(500, Math.trunc(limit)));
  const [jobs, requests] = await Promise.all([
    binding.prepare(
      `SELECT * FROM operator_retry_jobs
       ORDER BY CASE state WHEN 'BACKING_OFF' THEN 0 WHEN 'QUEUED' THEN 1 ELSE 2 END,
                available_at,job_id LIMIT ?`,
    ).bind(bounded).all(),
    binding.prepare(
      `SELECT request_id,job_id,operator_id,mode,reason,requested_at,
              requested_available_at,expected_available_at,status,completed_at,result_json
       FROM operator_retry_requests ORDER BY requested_at DESC LIMIT 100`,
    ).all(),
  ]);
  return { items: jobs.results, requests: requests.results };
}

export async function createOperatorRetryRequests(
  binding: D1Database,
  input: {
    operatorId: string;
    idempotencyKey: string;
    jobIds: string[];
    mode: OperatorRetryMode;
    reason: string;
    requestedAvailableAt: string | null;
    now?: Date;
  },
) {
  const jobIds = [...new Set(input.jobIds.map(value => value.trim()).filter(Boolean))];
  if (!jobIds.length || jobIds.length > OPERATOR_RETRY_BATCH_LIMIT) {
    throw new OperatorRetryInputError("INVALID_BATCH", "请选择 1 至 100 个任务");
  }
  if (jobIds.some(id => !/^[a-f0-9]{64}$/i.test(id))) {
    throw new OperatorRetryInputError("INVALID_JOB_ID", "任务编号无效");
  }
  const requestedAt = (input.now ?? new Date()).toISOString();
  const results: Array<Record<string, unknown>> = [];
  for (const jobId of jobIds) {
    const requestId = await digest(`${input.operatorId}\n${input.idempotencyKey}\n${jobId}`);
    const job = await binding.prepare(
      "SELECT * FROM operator_retry_jobs WHERE job_id=?",
    ).bind(jobId).first<RetryJobRow>();
    if (!job) {
      results.push({ job_id: jobId, status: "REJECTED", code: "JOB_NOT_FOUND" });
      continue;
    }
    if (!new Set(["QUEUED", "BACKING_OFF"]).has(job.state)) {
      const resultJson = JSON.stringify({ code: "JOB_NOT_MUTABLE", current: job });
      const inserted = await binding.prepare(
        `INSERT INTO operator_retry_requests (
         request_id,idempotency_key,job_id,task_type,operator_id,mode,reason,
         requested_at,requested_available_at,expected_state,expected_available_at,
         status,completed_at,result_json)
         VALUES (?,?,?,?,?,?,?,?,?,?,?,'REJECTED',?,?)
         ON CONFLICT(operator_id,idempotency_key,job_id) DO NOTHING RETURNING *`,
      ).bind(
        requestId, input.idempotencyKey, jobId, job.task_type, input.operatorId,
        input.mode, input.reason, requestedAt, input.requestedAvailableAt,
        job.state, job.available_at, requestedAt, resultJson,
      ).first<Record<string, unknown>>();
      const row = inserted ?? await binding.prepare(
        `SELECT * FROM operator_retry_requests
         WHERE operator_id=? AND idempotency_key=? AND job_id=?`,
      ).bind(input.operatorId, input.idempotencyKey, jobId).first<Record<string, unknown>>();
      if (
        !inserted && row
        && (
          row.mode !== input.mode
          || row.reason !== input.reason
          || (row.requested_available_at ?? null) !== input.requestedAvailableAt
        )
      ) {
        results.push({ job_id: jobId, status: "CONFLICT", code: "IDEMPOTENCY_CONFLICT" });
        continue;
      }
      if (inserted) {
        await binding.batch([
          binding.prepare(
            `INSERT INTO operator_retry_request_events
             (event_id,request_id,event_type,recorded_at,payload_json)
             VALUES (?,?, 'REQUESTED',?,?)`,
          ).bind(
            crypto.randomUUID(), requestId, requestedAt,
            JSON.stringify({ mode: input.mode, expected_state: job.state, expected_available_at: job.available_at }),
          ),
          binding.prepare(
            `INSERT INTO operator_retry_request_events
             (event_id,request_id,event_type,recorded_at,payload_json)
             VALUES (?,?, 'REJECTED',?,?)`,
          ).bind(crypto.randomUUID(), requestId, requestedAt, resultJson),
        ]);
      }
      results.push({ ...row, code: "JOB_NOT_MUTABLE", current: job, duplicate: !inserted });
      continue;
    }
    const inserted = await binding.prepare(
      `INSERT INTO operator_retry_requests (
       request_id,idempotency_key,job_id,task_type,operator_id,mode,reason,
       requested_at,requested_available_at,expected_state,expected_available_at,status)
       VALUES (?,?,?,?,?,?,?,?,?,?,?,'PENDING')
       ON CONFLICT(operator_id,idempotency_key,job_id) DO NOTHING RETURNING *`,
    ).bind(
      requestId, input.idempotencyKey, jobId, job.task_type, input.operatorId,
      input.mode, input.reason, requestedAt, input.requestedAvailableAt,
      job.state, job.available_at,
    ).first<Record<string, unknown>>();
    const row = inserted ?? await binding.prepare(
      `SELECT * FROM operator_retry_requests
       WHERE operator_id=? AND idempotency_key=? AND job_id=?`,
    ).bind(input.operatorId, input.idempotencyKey, jobId).first<Record<string, unknown>>();
    if (
      !inserted && row
      && (
        row.mode !== input.mode
        || row.reason !== input.reason
        || (row.requested_available_at ?? null) !== input.requestedAvailableAt
      )
    ) {
      results.push({ job_id: jobId, status: "CONFLICT", code: "IDEMPOTENCY_CONFLICT" });
      continue;
    }
    if (inserted) {
      await binding.prepare(
        `INSERT INTO operator_retry_request_events
         (event_id,request_id,event_type,recorded_at,payload_json)
         VALUES (?,?, 'REQUESTED',?,?)`,
      ).bind(
        crypto.randomUUID(), requestId, requestedAt,
        JSON.stringify({ mode: input.mode, expected_state: job.state, expected_available_at: job.available_at }),
      ).run();
    }
    results.push({ ...row, duplicate: !inserted });
  }
  return results;
}

export async function syncOperatorRetryJobs(
  binding: D1Database,
  items: Array<Record<string, unknown>>,
  now = new Date(),
) {
  if (items.length > 500) throw new OperatorRetryInputError("INVALID_SYNC", "retry mirror is too large");
  const boundedText = (value: unknown, field: string, limit: number) => {
    const text = String(value ?? "").trim();
    if (!text || text.length > limit) {
      throw new OperatorRetryInputError("INVALID_SYNC", `${field} is invalid`);
    }
    return text;
  };
  const nullableText = (value: unknown, field: string, limit: number) => {
    if (value === undefined || value === null || value === "") return null;
    return boundedText(value, field, limit);
  };
  const nullableTime = (value: unknown, field: string) => (
    value === undefined || value === null || value === "" ? null : isoTime(value, field)
  );
  const normalized = items.map(item => {
    const jobId = String(item.job_id ?? "").trim().toLowerCase();
    const attemptCount = Number(item.attempt_count);
    if (!/^[a-f0-9]{64}$/.test(jobId) || !Number.isSafeInteger(attemptCount) || attemptCount < 0) {
      throw new OperatorRetryInputError("INVALID_SYNC", "retry mirror job identity is invalid");
    }
    const overrideMode = nullableText(item.override_mode, "override_mode", 32);
    if (overrideMode !== null && !OPERATOR_RETRY_MODES.includes(overrideMode as OperatorRetryMode)) {
      throw new OperatorRetryInputError("INVALID_SYNC", "override_mode is invalid");
    }
    return {
      job_id: jobId,
      task_type: boundedText(item.task_type, "task_type", 100),
      title: boundedText(item.title, "title", 500),
      state: boundedText(item.state, "state", 32),
      priority: boundedText(item.priority, "priority", 32),
      available_at: isoTime(item.available_at, "available_at"),
      attempt_count: attemptCount,
      last_error: nullableText(item.last_error, "last_error", 4_000),
      last_failure_at: nullableTime(item.last_failure_at, "last_failure_at"),
      lease_expires_at: nullableTime(item.lease_expires_at, "lease_expires_at"),
      override_mode: overrideMode,
      override_requested_at: nullableTime(item.override_requested_at, "override_requested_at"),
      original_available_at: isoTime(item.original_available_at, "original_available_at"),
    };
  }).sort((left, right) => left.job_id.localeCompare(right.job_id));
  if (new Set(normalized.map(item => item.job_id)).size !== normalized.length) {
    throw new OperatorRetryInputError("INVALID_SYNC", "retry mirror contains duplicate jobs");
  }
  const payload = JSON.stringify(normalized);
  const payloadDigest = await digest(payload);
  const previous = await binding.prepare(
    "SELECT payload_digest,item_count,synced_at FROM operator_retry_sync_state WHERE id=1",
  ).first<{ payload_digest: string; item_count: number; synced_at: string }>();
  if (previous?.payload_digest === payloadDigest && Number(previous.item_count) === normalized.length) {
    return {
      count: normalized.length, accepted: normalized.length, written: 0, deleted: 0,
      unchanged: true, complete: true, synced_at: previous.synced_at,
    };
  }
  const syncedAt = now.toISOString();
  const generation = crypto.randomUUID();
  const incomingSql = `SELECT value row,json_extract(value,'$.job_id') job_id
    FROM json_each(?)`;
  const changedSql = `SELECT incoming.row FROM incoming
    LEFT JOIN operator_retry_jobs current ON current.job_id=incoming.job_id
    WHERE current.job_id IS NULL OR ${retryJobJson("current")} IS NOT json(incoming.row)
    ORDER BY incoming.job_id
    LIMIT ${OPERATOR_RETRY_SYNC_MUTATIONS_PER_INVOCATION}`;
  const results = await binding.batch([
    binding.prepare(
    `WITH incoming AS (${incomingSql}), changed AS (${changedSql})
     INSERT INTO operator_retry_jobs (
     job_id,task_type,title,state,priority,available_at,attempt_count,last_error,
     last_failure_at,lease_expires_at,override_mode,override_requested_at,
     original_available_at,synced_at,sync_generation)
     SELECT json_extract(row,'$.job_id'),json_extract(row,'$.task_type'),
       json_extract(row,'$.title'),json_extract(row,'$.state'),
       json_extract(row,'$.priority'),json_extract(row,'$.available_at'),
       json_extract(row,'$.attempt_count'),json_extract(row,'$.last_error'),
       json_extract(row,'$.last_failure_at'),json_extract(row,'$.lease_expires_at'),
       json_extract(row,'$.override_mode'),json_extract(row,'$.override_requested_at'),
       json_extract(row,'$.original_available_at'),?,?
     FROM changed WHERE true
     ON CONFLICT(job_id) DO UPDATE SET
       task_type=excluded.task_type,title=excluded.title,state=excluded.state,
       priority=excluded.priority,available_at=excluded.available_at,
       attempt_count=excluded.attempt_count,last_error=excluded.last_error,
       last_failure_at=excluded.last_failure_at,lease_expires_at=excluded.lease_expires_at,
       override_mode=excluded.override_mode,override_requested_at=excluded.override_requested_at,
       original_available_at=excluded.original_available_at,synced_at=excluded.synced_at,
       sync_generation=excluded.sync_generation
     WHERE operator_retry_jobs.task_type IS NOT excluded.task_type
        OR operator_retry_jobs.title IS NOT excluded.title
        OR operator_retry_jobs.state IS NOT excluded.state
        OR operator_retry_jobs.priority IS NOT excluded.priority
        OR operator_retry_jobs.available_at IS NOT excluded.available_at
        OR operator_retry_jobs.attempt_count IS NOT excluded.attempt_count
        OR operator_retry_jobs.last_error IS NOT excluded.last_error
        OR operator_retry_jobs.last_failure_at IS NOT excluded.last_failure_at
        OR operator_retry_jobs.lease_expires_at IS NOT excluded.lease_expires_at
        OR operator_retry_jobs.override_mode IS NOT excluded.override_mode
        OR operator_retry_jobs.override_requested_at IS NOT excluded.override_requested_at
        OR operator_retry_jobs.original_available_at IS NOT excluded.original_available_at`,
    ).bind(payload, syncedAt, generation),
    binding.prepare(
    `WITH incoming AS (${incomingSql}), changed AS (${changedSql})
     DELETE FROM operator_retry_jobs
     WHERE NOT EXISTS (SELECT 1 FROM changed)
       AND job_id IN (
         SELECT current.job_id FROM operator_retry_jobs current
         WHERE NOT EXISTS (
           SELECT 1 FROM incoming WHERE incoming.job_id=current.job_id
         )
         ORDER BY current.job_id
         LIMIT ${OPERATOR_RETRY_SYNC_MUTATIONS_PER_INVOCATION}
       )`,
    ).bind(payload),
    binding.prepare(
      `INSERT INTO operator_retry_sync_state (id,payload_digest,item_count,synced_at)
       SELECT 1,?,?,? WHERE
         NOT EXISTS (
           SELECT 1 FROM json_each(?) incoming
           LEFT JOIN operator_retry_jobs current
             ON current.job_id=json_extract(incoming.value,'$.job_id')
           WHERE current.job_id IS NULL
              OR ${retryJobJson("current")} IS NOT json(incoming.value)
         )
         AND NOT EXISTS (
           SELECT 1 FROM operator_retry_jobs current
           WHERE NOT EXISTS (
             SELECT 1 FROM json_each(?) incoming
             WHERE json_extract(incoming.value,'$.job_id')=current.job_id
           )
         )
       ON CONFLICT(id) DO UPDATE SET
         payload_digest=excluded.payload_digest,item_count=excluded.item_count,
         synced_at=excluded.synced_at`,
    ).bind(payloadDigest, normalized.length, syncedAt, payload, payload),
  ]);
  return {
    count: normalized.length, accepted: normalized.length,
    written: Number(results[0].meta?.changes ?? 0),
    deleted: Number(results[1].meta?.changes ?? 0),
    unchanged: false,
    complete: Number(results[2].meta?.changes ?? 0) > 0,
    synced_at: Number(results[2].meta?.changes ?? 0) > 0 ? syncedAt : null,
  };
}

export async function claimOperatorRetryRequest(
  binding: D1Database,
  workerId: string,
  now = new Date(),
) {
  const timestamp = now.toISOString();
  const leaseToken = crypto.randomUUID();
  const leaseExpiresAt = new Date(now.getTime() + OPERATOR_RETRY_LEASE_MS).toISOString();
  return binding.prepare(
    `UPDATE operator_retry_requests SET status='APPLYING',lease_owner=?,lease_token=?,lease_expires_at=?
     WHERE request_id=(
       SELECT request_id FROM operator_retry_requests
       WHERE status='PENDING' OR (status='APPLYING' AND lease_expires_at<=?)
       ORDER BY requested_at,request_id LIMIT 1)
     RETURNING *`,
  ).bind(workerId, leaseToken, leaseExpiresAt, timestamp).first<Record<string, unknown>>();
}

export async function finishOperatorRetryRequest(
  binding: D1Database,
  input: Record<string, unknown>,
  now = new Date(),
) {
  const status = String(input.status ?? "").toUpperCase();
  if (!new Set(["APPLIED", "CONFLICT", "REJECTED"]).has(status)) {
    throw new OperatorRetryInputError("INVALID_RESULT", "retry result is invalid");
  }
  const requestId = String(input.request_id ?? "");
  const leaseToken = String(input.lease_token ?? "");
  const timestamp = now.toISOString();
  const resultJson = JSON.stringify(input.result ?? {});
  const result = await binding.batch([
    binding.prepare(
      `UPDATE operator_retry_requests SET status=?,completed_at=?,result_json=?,
       lease_owner=NULL,lease_token=NULL,lease_expires_at=NULL
       WHERE request_id=? AND status='APPLYING' AND lease_token=? RETURNING *`,
    ).bind(status, timestamp, resultJson, requestId, leaseToken),
    binding.prepare(
      `INSERT OR IGNORE INTO operator_retry_request_events
       (event_id,request_id,event_type,recorded_at,payload_json)
       SELECT ?,?,?,?,? WHERE EXISTS (
         SELECT 1 FROM operator_retry_requests WHERE request_id=? AND status=?)`,
    ).bind(crypto.randomUUID(), requestId, status, timestamp, resultJson, requestId, status),
  ]);
  return result[0].results?.[0] ?? null;
}
