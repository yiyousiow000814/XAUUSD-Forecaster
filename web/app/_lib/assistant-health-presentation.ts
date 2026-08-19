export type AssistantHealthPayload = {
  status: "HEALTHY" | "WARNING" | "ERROR" | "SNAPSHOT_UNAVAILABLE";
  current?: boolean;
  alerts?: Array<{ message_zh?: string; blocking?: boolean }>;
};

export function assistantHealthPresentation(payload: AssistantHealthPayload | null) {
  if (!payload || payload.status === "SNAPSHOT_UNAVAILABLE") return "运行状态暂不可用";
  const suffix = payload.current === false ? "（合成）" : "";
  if (payload.status === "HEALTHY") return `运行正常${suffix}`;
  const alert = payload.alerts?.find(item => item.blocking) ?? payload.alerts?.[0];
  return `${alert?.message_zh ?? (payload.status === "ERROR" ? "运行异常" : "运行警告")}${suffix}`;
}
