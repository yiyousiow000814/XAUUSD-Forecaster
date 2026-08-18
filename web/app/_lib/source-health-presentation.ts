type SourceHealthErrorInput = {
  recovery_mode: string | null;
  fallback_label: string | null;
  fallback_health: string | null;
  last_error_type: string | null;
};

export function sourceHealthErrorPresentation(
  item: SourceHealthErrorInput,
  healthy: boolean,
): { heading: string; recovery: string | null; fallback: string | null } {
  const heading = item.recovery_mode === "RATE_LIMITED" && item.fallback_label
    ? `GDELT 限流 · ${item.fallback_label} 自动接管`
    : healthy && item.last_error_type
      ? "历史异常已恢复"
      : item.recovery_mode === "OPERATOR_ACTION_REQUIRED"
        ? "来源配置需要人工处理"
        : item.recovery_mode === "PARTIAL_RECOVERY"
          ? "来源只完成部分轮询"
          : item.last_error_type === "TimeoutError"
            ? "Provider 响应超时"
            : item.last_error_type === "HTTPError"
              ? "Provider 暂时不可用"
              : item.last_error_type === "ConnectionResetError"
                ? "Provider 连接中断"
                : item.last_error_type
                  ? "来源暂时不可用"
                  : "无已记录异常";
  const recovery = item.recovery_mode === "OPERATOR_ACTION_REQUIRED"
    ? "需要人工处理"
    : item.recovery_mode === "AUTO_RECOVERING" || item.recovery_mode === "PARTIAL_RECOVERY"
      ? "正在自动重试"
      : item.recovery_mode === "RATE_LIMITED"
        ? item.fallback_label ? "后备来源正在接管" : "等待限流窗口结束"
        : null;
  return {
    heading,
    recovery,
    fallback: item.fallback_label
      ? `后备链路：${item.fallback_label} · ${item.fallback_health}`
      : null,
  };
}
