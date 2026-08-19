import { operationalCodeDefinitions, schedulerTaskLabel, type OperationalAlert } from "./operational-health";
import type { OperationalIncident } from "./operational-incidents";

export const operationalIncidentActionLabels: Record<OperationalIncident["action_state"], string> = {
  ACTION_REQUIRED: "需要人工处理",
  AUTO_RECOVERING: "自动重试中",
  MONITORING: "持续观察",
};

const componentLabels: Record<string, string> = {
  quote_bridge: "XAUUSD 报价桥",
  system_clock: "cTrader 报价时间 / 本机接收时间",
  decision_collector: "5 分钟决策收集器",
  outcome_settler: "30 分钟结果结算器",
  news_collector: "新闻收集器",
  gemini_annotator: "Gemini 新闻分析器",
  news_semantic_pipeline: "新闻语义决策门槛",
  sites_synchronizer: "网页同步器",
  sqlite_backup: "SQLite 备份",
  integrity_check: "数据库完整性检查",
  daily_news_brief: "每日新闻简报",
};

export function operationalScopeLabel(scope: string): string {
  return componentLabels[scope] ?? schedulerTaskLabel[scope] ?? scope;
}

function diagnosticDuration(value: unknown): string | null {
  const seconds = Number(value);
  if (!Number.isFinite(seconds)) return null;
  const whole = Math.max(0, Math.round(seconds));
  if (whole < 60) return `${whole} 秒`;
  const minutes = Math.floor(whole / 60);
  if (minutes < 60) return `${minutes} 分 ${whole % 60} 秒`;
  return `${Math.floor(minutes / 60)} 小时 ${minutes % 60} 分`;
}

export function operationalEventDiagnostic(
  event: Required<OperationalAlert>, incidentReasonCodes?: string[],
) {
  const reasonCodes = incidentReasonCodes === undefined
    ? Array.isArray(event.evidence.reason_codes)
      ? [...new Set(event.evidence.reason_codes.filter(reason => typeof reason === "string"))]
      : []
    : [...new Set(incidentReasonCodes)];
  const duration = diagnosticDuration(event.evidence.age_seconds);
  const status = typeof event.evidence.status === "string" ? event.evidence.status : event.severity;
  return {
    status: `${status}${duration ? ` · 已持续 ${duration}` : ""}`,
    component: operationalScopeLabel(event.scope),
    reasons: reasonCodes
      .map(reason => operationalCodeDefinitions.get(reason)?.title_zh)
      .filter((title): title is string => Boolean(title)),
  };
}

export function operationalIncidentNextRetryAt(incident: OperationalIncident): string | null {
  const retryTimes = [incident.root_event, ...incident.related_events, ...incident.technical_events]
    .map(event => event.evidence.next_retry_at)
    .filter((value): value is string => typeof value === "string" && value.length > 0)
    .map(value => ({ value, timestamp: Date.parse(value) }))
    .filter(item => Number.isFinite(item.timestamp))
    .sort((left, right) => left.timestamp - right.timestamp);
  return retryTimes[0]?.value ?? null;
}

export function operationalIncidentsNextRetryAt(
  incidents: OperationalIncident[], action: OperationalIncident["action_state"] | null,
): string | null {
  if (!action) return null;
  const retryTimes = incidents
    .filter(incident => incident.action_state === action)
    .map(operationalIncidentNextRetryAt)
    .filter((value): value is string => value !== null)
    .map(value => ({ value, timestamp: Date.parse(value) }))
    .sort((left, right) => left.timestamp - right.timestamp);
  return retryTimes[0]?.value ?? null;
}

export function operationalSummaryDetails(
  affectedScopeCount: number,
  action: OperationalIncident["action_state"] | null,
  retryAt: string | null,
): string[] {
  return [
    affectedScopeCount ? `${affectedScopeCount} 个子系统受影响` : null,
    action === "AUTO_RECOVERING" && retryAt ? `下次尝试 ${retryAt}` : null,
  ].filter((detail): detail is string => detail !== null);
}
