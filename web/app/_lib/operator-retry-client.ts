export type OperatorRetryRequest = {
  request_id: string;
  job_id: string;
  mode: string;
  requested_at: string;
  status: "PENDING" | "APPLYING" | "APPLIED" | "CONFLICT" | "REJECTED";
  completed_at: string | null;
  result_json: string | null;
};

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
    label: "等待 Windows 调度器应用", tone: "pending", terminal: false,
  } as const;
  if (request.status === "APPLYING") return {
    label: "正在由 Windows 调度器应用", tone: "applying", terminal: false,
  } as const;
  if (request.status === "APPLIED") return {
    label: "已应用到 Windows 调度器", tone: "applied", terminal: true,
  } as const;
  const code = resultCode(request);
  if (code === "JOB_NOT_MUTABLE") return {
    label: "未应用：任务已不可调整", tone: "rejected", terminal: true,
  } as const;
  if (code === "JOB_STATE_CHANGED") return {
    label: "未应用：任务状态或计划已变化", tone: "conflict", terminal: true,
  } as const;
  return {
    label: request.status === "CONFLICT" ? "未应用：调度状态冲突" : "未应用：命令被拒绝",
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
