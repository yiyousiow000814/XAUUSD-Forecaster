type SourceHealthErrorInput = {
  recovery_mode: string | null;
  fallback_label: string | null;
  fallback_health: string | null;
  last_error_type: string | null;
};

export function sourceHealthErrorPresentation(
  item: SourceHealthErrorInput,
  healthy: boolean,
): { heading: string; fallback: string | null } {
  const heading = item.recovery_mode === "RATE_LIMITED" && item.fallback_label
    ? `GDELT 限流 · ${item.fallback_label} 自动接管`
    : item.last_error_type
      ? `${healthy ? "历史异常 · 已恢复" : "当前异常"} · ${item.last_error_type}`
      : "无已记录异常";
  return {
    heading,
    fallback: item.fallback_label
      ? `后备链路：${item.fallback_label} · ${item.fallback_health}`
      : null,
  };
}
