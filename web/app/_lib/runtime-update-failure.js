const FAILURE_LABELS = {
  ROLLED_BACK: "新版运行验证失败，已自动恢复上一版。",
  ROLLBACK_FAILED: "新版运行验证失败，自动恢复也失败，请检查本机服务。",
  SWITCH_FAILED: "新版切换失败，当前版本继续运行。",
};

export function runtimeUpdateFailurePresentation(failure) {
  if (!failure) return null;
  return {
    label: FAILURE_LABELS[failure.status]
      ?? "新版预检失败，当前版本继续运行。",
    message: failure.message,
    failedAt: failure.failed_at,
  };
}
