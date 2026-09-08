const FAILURE_LABELS = {
  UPDATE_FAILED: "main 更新失败，请检查当前运行状态。",
  FAILED: "main 服务启动失败，请检查本机服务。",
  STATUS_UNAVAILABLE: "无法读取当前 main 运行状态。",
};

export function runtimeUpdateFailurePresentation(failure) {
  if (!failure) return null;
  return {
    label: FAILURE_LABELS[failure.status]
      ?? "当前运行状态异常，请检查本机服务。",
    failedAt: failure.failed_at,
  };
}
