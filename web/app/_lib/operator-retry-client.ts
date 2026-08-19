export type OperatorRetryRequest = {
  request_id: string;
  job_id: string;
  mode: string;
  requested_at: string;
  status: "PENDING" | "APPLYING" | "APPLIED" | "CONFLICT" | "REJECTED";
  completed_at: string | null;
  result_json: string | null;
};

export type OperatorRetryJob = {
  job_id: string;
  task_type: string;
  title: string;
  state: string;
  priority: string;
  available_at: string;
  attempt_count: number;
  last_error: string | null;
  last_failure_at: string | null;
  lease_expires_at: string | null;
  override_mode: string | null;
  original_available_at: string;
};

export const operatorRetryPreviewJobs: OperatorRetryJob[] = [{
  job_id: "a".repeat(64), task_type: "ACTIVE_IMPACT", title: "黄金重新成为货币抵押品的市场讨论",
  state: "BACKING_OFF", priority: "NORMAL", available_at: "2026-08-19T06:47:00.000Z",
  attempt_count: 3, last_error: "ConnectionResetError", last_failure_at: "2026-08-19T00:46:00.000Z",
  lease_expires_at: null, override_mode: null, original_available_at: "2026-08-19T06:47:00.000Z",
}, {
  job_id: "b".repeat(64), task_type: "ACTIVE_ANNOTATION", title: "美联储官员就通胀路径发表最新讲话",
  state: "BACKING_OFF", priority: "FAST", available_at: "2026-08-19T04:05:00.000Z",
  attempt_count: 2, last_error: "MODEL_OUTPUT_CONTRACT_FAILED", last_failure_at: "2026-08-19T00:51:00.000Z",
  lease_expires_at: null, override_mode: "DELAY_1_HOUR", original_available_at: "2026-08-19T06:58:00.000Z",
}, {
  job_id: "c".repeat(64), task_type: "TITLE_TRANSLATION", title: "央行储备资产配置讨论",
  state: "QUEUED", priority: "NORMAL", available_at: "2026-08-19T05:15:00.000Z",
  attempt_count: 1, last_error: "UPSTREAM_TEMPORARILY_UNAVAILABLE", last_failure_at: "2026-08-19T01:02:00.000Z",
  lease_expires_at: null, override_mode: null, original_available_at: "2026-08-19T05:15:00.000Z",
}];

export const operatorRetryPreviewRequests: OperatorRetryRequest[] = [{
  request_id: "preview-pending", job_id: "a".repeat(64), mode: "IMMEDIATE",
  requested_at: "2026-08-19T03:05:00.000Z", completed_at: null,
  status: "PENDING", result_json: null,
}, {
  request_id: "preview-applied", job_id: "b".repeat(64), mode: "DELAY_1_HOUR",
  requested_at: "2026-08-19T03:04:00.000Z", completed_at: "2026-08-19T03:04:03.000Z",
  status: "APPLIED", result_json: JSON.stringify({ current: { state: "BACKING_OFF" } }),
}, {
  request_id: "preview-conflict", job_id: "c".repeat(64), mode: "CUSTOM_TIME",
  requested_at: "2026-08-19T03:03:00.000Z", completed_at: "2026-08-19T03:03:02.000Z",
  status: "CONFLICT", result_json: JSON.stringify({ code: "JOB_STATE_CHANGED" }),
}];

const resultCode = (request: OperatorRetryRequest) => {
  try {
    const result = JSON.parse(request.result_json ?? "{}") as { code?: unknown };
    return String(result.code ?? "");
  } catch {
    return "";
  }
};

export const operatorRetryCommandPresentation = (request: OperatorRetryRequest) => {
  if (request.status === "PENDING") return {
    label: "等待 Windows 调度器应用", shortLabel: "等待应用", tone: "pending", terminal: false,
  } as const;
  if (request.status === "APPLYING") return {
    label: "正在由 Windows 调度器应用", shortLabel: "应用中", tone: "applying", terminal: false,
  } as const;
  if (request.status === "APPLIED") return {
    label: "已应用到 Windows 调度器", shortLabel: "已应用", tone: "applied", terminal: true,
  } as const;
  const code = resultCode(request);
  if (code === "JOB_NOT_MUTABLE") return {
    label: "未应用：任务已不可调整", shortLabel: "未应用", tone: "rejected", terminal: true,
  } as const;
  if (code === "JOB_STATE_CHANGED") return {
    label: "未应用：任务状态或计划已变化", shortLabel: "冲突", tone: "conflict", terminal: true,
  } as const;
  return {
    label: request.status === "CONFLICT" ? "未应用：调度状态冲突" : "未应用：命令被拒绝",
    shortLabel: request.status === "CONFLICT" ? "冲突" : "未应用",
    tone: request.status === "CONFLICT" ? "conflict" : "rejected",
    terminal: true,
  } as const;
};

export const latestOperatorRetryRequests = (requests: OperatorRetryRequest[]) => {
  const latest = new Map<string, OperatorRetryRequest>();
  for (const request of requests) {
    if (!latest.has(request.job_id)) latest.set(request.job_id, request);
  }
  return latest;
};

export const shouldPollOperatorRetry = (
  request: OperatorRetryRequest | undefined,
  now = Date.now(),
) => {
  if (!request) return false;
  if (!operatorRetryCommandPresentation(request).terminal) {
    return now - Date.parse(request.requested_at) <= 3 * 60_000;
  }
  return Boolean(
    request.completed_at
    && now - Date.parse(request.completed_at) <= 5_000,
  );
};

export const shouldPollOperatorRetryRequests = (
  requests: OperatorRetryRequest[],
  now = Date.now(),
) => requests.some(request => shouldPollOperatorRetry(request, now));

export const summarizeOperatorRetryQueue = (
  jobs: OperatorRetryJob[],
  requests: OperatorRetryRequest[],
) => {
  const commands = [...latestOperatorRetryRequests(requests).values()];
  return {
    total: jobs.length,
    waiting: jobs.filter(job => job.state === "BACKING_OFF").length,
    overridden: jobs.filter(job => Boolean(job.override_mode)).length,
    applying: commands.filter(request => request.status === "PENDING" || request.status === "APPLYING").length,
    conflict: commands.filter(request => request.status === "CONFLICT").length,
  };
};
