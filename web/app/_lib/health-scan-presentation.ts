import type { OperationalIncident } from "./operational-incidents";

export type HealthTone = "healthy" | "warning" | "error" | "neutral";

export type ScanState = {
  tone: HealthTone;
  symbol: "✓" | "⚠" | "✕" | "—";
  label: string;
  attention: boolean;
};

const componentStates: Record<string, ScanState> = {
  OK: { tone: "healthy", symbol: "✓", label: "正常", attention: false },
  MARKET_CLOSED: { tone: "neutral", symbol: "—", label: "市场休市", attention: false },
  WARNING: { tone: "warning", symbol: "⚠", label: "警告", attention: true },
  STALE: { tone: "warning", symbol: "⚠", label: "需要关注", attention: true },
  ERROR: { tone: "error", symbol: "✕", label: "错误", attention: true },
  UNAVAILABLE: { tone: "neutral", symbol: "—", label: "状态不可用", attention: true },
  UNKNOWN: { tone: "neutral", symbol: "—", label: "状态未知", attention: true },
};

const sourceStates: Record<string, ScanState> = {
  HEALTHY: { tone: "healthy", symbol: "✓", label: "正常", attention: false },
  DEGRADED: { tone: "warning", symbol: "⚠", label: "需要关注", attention: true },
  STALE: { tone: "warning", symbol: "⚠", label: "内容过期", attention: true },
  FALLBACK_ACTIVE: { tone: "warning", symbol: "⚠", label: "后备源接管中", attention: true },
  WARMING_UP: { tone: "warning", symbol: "⚠", label: "等待首次正式发布", attention: true },
  ERROR: { tone: "error", symbol: "✕", label: "错误", attention: true },
};

const unknownState: ScanState = {
  tone: "neutral", symbol: "—", label: "状态未知", attention: true,
};

export function componentScanState(status: string): ScanState {
  return componentStates[status] ?? unknownState;
}

export function sourceScanState(health: string): ScanState {
  return sourceStates[health] ?? unknownState;
}

export function sortAttentionFirst<T>(items: T[], state: (item: T) => ScanState): T[] {
  return [...items].sort((left, right) => Number(state(right).attention) - Number(state(left).attention));
}

export function componentAggregate(statuses: string[]): string {
  const states = statuses.map(componentScanState);
  const healthy = states.filter(state => state.tone === "healthy").length;
  const warning = states.filter(state => state.tone === "warning").length;
  const error = states.filter(state => state.tone === "error").length;
  const neutral = states.filter(state => state.tone === "neutral").length;
  return [
    `${healthy} 正常`, `${warning} 警告`, `${error} 错误`,
    ...(neutral ? [`${neutral} 其他状态`] : []),
  ].join(" · ");
}

export function sourceAggregate(healthStates: string[]): string {
  const count = (health: string) => healthStates.filter(value => value === health).length;
  const warning = count("DEGRADED") + count("STALE") + count("FALLBACK_ACTIVE");
  const unknown = healthStates.filter(health => !sourceStates[health]).length;
  return [
    `${count("HEALTHY")} 正常`,
    ...(count("WARMING_UP") ? [`${count("WARMING_UP")} 等待发布`] : []),
    ...(warning ? [`${warning} 需关注`] : []),
    ...(count("ERROR") ? [`${count("ERROR")} 错误`] : []),
    ...(unknown ? [`${unknown} 状态未知`] : []),
  ].join(" · ");
}

export function primaryOperatorAction(incidents: OperationalIncident[]): OperationalIncident["action_state"] | null {
  if (incidents.some(incident => incident.action_state === "ACTION_REQUIRED")) return "ACTION_REQUIRED";
  if (incidents.some(incident => incident.action_state === "AUTO_RECOVERING")) return "AUTO_RECOVERING";
  if (incidents.some(incident => incident.action_state === "MONITORING")) return "MONITORING";
  return null;
}
