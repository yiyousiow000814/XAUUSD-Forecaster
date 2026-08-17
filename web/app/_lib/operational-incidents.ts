import {
  normalizeOperationalEvent,
  operationalCodeDefinitions,
  schedulerTaskLabel,
  type OperationalAlert,
  type OperationalCategory,
  type OperationalCodeDefinition,
} from "./operational-health";

export type OperationalIncident = {
  incident_key: string;
  category: OperationalCategory;
  severity: OperationalAlert["severity"];
  state: "ACTIVE" | "RECOVERING";
  action_state: "ACTION_REQUIRED" | "AUTO_RECOVERING" | "MONITORING";
  title_zh: string;
  summary_zh: string;
  root_event: Required<OperationalAlert>;
  related_events: Array<Required<OperationalAlert>>;
  technical_events: Array<Required<OperationalAlert>>;
  reason_projections: Array<{
    source_event_code: string;
    source_scope: string;
    reason_code: string;
  }>;
  affected_scopes: string[];
  summary_metrics: Array<{ label: string; value: string }>;
  blocking: boolean;
  technical_event_count: number;
};

const severityRank = { ERROR: 3, WARNING: 2, INFO: 1 } as const;
const capacitySymptoms = new Set([
  "OPS_AI_PIPELINE_STALLED", "OPS_AI_BACKLOG_OVERDUE",
]);
const rootPriority: Record<string, number> = {
  OPS_AI_ROUTE_CAPACITY_SATURATED: 0,
  OPS_AI_JOB_RETRY_LOOP: 1,
  OPS_AI_PIPELINE_STALLED: 2,
  OPS_AI_BACKLOG_OVERDUE: 3,
  OPS_COMPONENT_UNHEALTHY: 9,
};

function stringEvidence(event: Required<OperationalAlert>, key: string): string | null {
  const value = event.evidence[key];
  return typeof value === "string" && value ? value : null;
}

function reasonCodes(event: Required<OperationalAlert>): string[] {
  const value = event.evidence.reason_codes;
  return Array.isArray(value)
    ? [...new Set(value.filter(item => typeof item === "string"))]
    : [];
}

function actionableFailureCounts(
  event: Required<OperationalAlert>, stage: string,
): Array<{ code: string; count: number }> {
  const allCounts = event.evidence.actionable_failure_counts;
  if (!allCounts || typeof allCounts !== "object" || Array.isArray(allCounts)) return [];
  const stageCounts = (allCounts as Record<string, unknown>)[stage];
  if (!stageCounts || typeof stageCounts !== "object" || Array.isArray(stageCounts)) return [];
  return Object.entries(stageCounts as Record<string, unknown>)
    .map(([code, count]) => ({ code, count: Number(count) }))
    .filter(item => Number.isFinite(item.count) && item.count > 0)
    .sort((left, right) => right.count - left.count || left.code.localeCompare(right.code));
}

function causalDefinition(event: Required<OperationalAlert>) {
  const code = stringEvidence(event, "latest_failure_code")
    ?? stringEvidence(event, "dominant_failure_code")
    ?? stringEvidence(event, "failure_code");
  return code ? operationalCodeDefinitions.get(code) : undefined;
}

function eventFamily(event: Required<OperationalAlert>): string {
  return causalDefinition(event)?.root_cause_family ?? event.root_cause_family;
}

function incidentTitle(event: Required<OperationalAlert>, family: string): string {
  const route = schedulerTaskLabel[event.scope];
  if (family === "MODEL_CAPACITY_LOCAL" && route) return route;
  const cause = causalDefinition(event);
  return cause?.title_zh ?? operationalCodeDefinitions.get(event.code)?.title_zh ?? event.message_zh;
}

function incidentSummary(event: Required<OperationalAlert>, family: string): string {
  if (family === "MODEL_CAPACITY_LOCAL") return "处理速度受到本地模型容量限制。";
  if (family === "MODEL_OUTPUT_CONTRACT") return "模型输出未通过结构与证据契约，相关任务无法完成。";
  if (family === "PROVIDER_PACING") return "任务由服务商调度节奏延后，尚未发送请求。";
  return event.message_zh;
}

function recoveryState(events: Array<Required<OperationalAlert>>) {
  const blocking = events.some(event => event.blocking);
  if (blocking) return { state: "ACTIVE" as const, action: "ACTION_REQUIRED" as const };
  const scheduled = events.some(event => (
    event.evidence.claimable === false && Boolean(event.evidence.next_retry_at)
  ));
  const progressing = events.some(event => (
    Number(event.evidence.completed_15m ?? 0) > 0
  ));
  const automatic = scheduled || progressing || events.every(event => event.recovery_policy === "AUTO");
  if (automatic) return { state: "RECOVERING" as const, action: "AUTO_RECOVERING" as const };
  const operator = events.some(event => event.recovery_policy === "OPERATOR" && event.severity === "ERROR");
  return operator
    ? { state: "ACTIVE" as const, action: "ACTION_REQUIRED" as const }
    : { state: "ACTIVE" as const, action: "MONITORING" as const };
}

function metrics(events: Array<Required<OperationalAlert>>) {
  const definitions: Array<[string, string, (value: number) => string]> = [
    ["active_jobs", "待处理", value => String(value)],
    ["oldest_age_seconds", "最老等待", value => `${Math.floor(value / 60)} 分钟`],
    ["completed_15m", "15 分钟完成", value => String(value)],
    ["capacity_deferred_15m", "容量延后", value => String(value)],
    ["failed_15m", "15 分钟失败", value => String(value)],
  ];
  const result: Array<{ label: string; value: string }> = [];
  for (const [key, label, format] of definitions) {
    const values = events.map(event => Number(event.evidence[key])).filter(Number.isFinite);
    if (values.length) result.push({ label, value: format(Math.max(...values)) });
  }
  return result.slice(0, 4);
}

function buildIncident(root: Required<OperationalAlert>, related: Array<Required<OperationalAlert>>): OperationalIncident {
  const events = [root, ...related];
  const family = eventFamily(root);
  const recovery = recoveryState(events);
  const severity = events.reduce((highest, event) => (
    severityRank[event.severity] > severityRank[highest] ? event.severity : highest
  ), "INFO" as OperationalAlert["severity"]);
  return {
    incident_key: `${family}:${root.scope}:${root.code}`,
    category: causalDefinition(root)?.category ?? root.category,
    severity,
    state: recovery.state,
    action_state: recovery.action,
    title_zh: incidentTitle(root, family),
    summary_zh: incidentSummary(root, family),
    root_event: root,
    related_events: related,
    technical_events: [],
    reason_projections: [],
    affected_scopes: [...new Set(related.map(event => event.scope).filter(scope => scope !== root.scope))].sort(),
    summary_metrics: metrics(events),
    blocking: events.some(event => event.blocking),
    technical_event_count: events.length,
  };
}

function stageForPendingReason(reason: string): string | null {
  if (reason === "ACTIONABLE_NEWS_IMPACT_PENDING") return "ACTIVE_IMPACT";
  if (reason === "ACTIONABLE_NEWS_SEMANTICS_PENDING") return "ACTIVE_ANNOTATION";
  return null;
}

function selectPendingCause(
  event: Required<OperationalAlert>, reason: string, incidents: OperationalIncident[],
): OperationalIncident | null {
  const stage = stageForPendingReason(reason);
  if (!stage) return null;
  const candidates = incidents.filter(incident => incident.root_event.scope === stage);
  if (candidates.length === 0) return null;
  const counts = actionableFailureCounts(event, stage);
  if (counts.length) {
    const highest = counts[0].count;
    const matchingFamilies = new Set(
      counts.filter(item => item.count === highest)
        .map(item => operationalCodeDefinitions.get(item.code)?.root_cause_family)
        .filter((family): family is string => Boolean(family)),
    );
    const matches = candidates.filter(
      incident => matchingFamilies.has(eventFamily(incident.root_event)),
    );
    if (matches.length === 1) return matches[0];
    return null;
  }
  return candidates.length === 1 ? candidates[0] : null;
}

function addReasonProjection(
  incident: OperationalIncident,
  event: Required<OperationalAlert>,
  reason: string,
) {
  incident.reason_projections.push({
    source_event_code: event.code,
    source_scope: event.scope,
    reason_code: reason,
  });
  incident.affected_scopes = [
    ...new Set([...incident.affected_scopes, event.scope]),
  ].sort();
}

/** Deterministic, conservative presentation projection over authoritative events. */
export function correlateOperationalEvents(input: OperationalAlert[]): OperationalIncident[] {
  const events = input.map(normalizeOperationalEvent).sort((left, right) => (
    (rootPriority[left.code] ?? 5) - (rootPriority[right.code] ?? 5)
    || left.scope.localeCompare(right.scope)
    || left.code.localeCompare(right.code)
  ));
  const consumed = new Set<number>();
  const incidents: OperationalIncident[] = [];

  const capacityRoots = events
    .map((event, index) => ({ event, index }))
    .filter(({ event }) => eventFamily(event) === "MODEL_CAPACITY_LOCAL" && event.scope !== "daily_news_brief");
  for (const { event: root, index } of capacityRoots) {
    if (consumed.has(index)) continue;
    consumed.add(index);
    const related: Array<Required<OperationalAlert>> = [];
    events.forEach((candidate, candidateIndex) => {
      if (consumed.has(candidateIndex)) return;
      const sameCapacity = eventFamily(candidate) === "MODEL_CAPACITY_LOCAL";
      const queueSymptom = candidate.scope === root.scope && capacitySymptoms.has(candidate.code);
      const briefSymptom = candidate.code === "OPS_DAILY_BRIEF_DEFERRED" && sameCapacity;
      if (sameCapacity && (candidate.scope === root.scope || briefSymptom) || queueSymptom) {
        consumed.add(candidateIndex);
        related.push(candidate);
      }
    });
    incidents.push(buildIncident(root, related));
  }

  events.forEach((event, index) => {
    if (consumed.has(index) || event.code === "OPS_COMPONENT_UNHEALTHY") return;
    consumed.add(index);
    const family = eventFamily(event);
    const related: Array<Required<OperationalAlert>> = [];
    events.forEach((candidate, candidateIndex) => {
      if (consumed.has(candidateIndex) || candidate.code === "OPS_COMPONENT_UNHEALTHY") return;
      if (eventFamily(candidate) === family && candidate.scope === event.scope) {
        consumed.add(candidateIndex);
        related.push(candidate);
      }
    });
    incidents.push(buildIncident(event, related));
  });

  events.forEach((event, index) => {
    if (event.code !== "OPS_COMPONENT_UNHEALTHY" || event.scope !== "news_semantic_pipeline") return;
    const reasons = reasonCodes(event);
    const explained = new Map<string, OperationalIncident>();
    for (const reason of reasons) {
      const incident = selectPendingCause(event, reason, incidents);
      if (incident) {
        addReasonProjection(incident, event, reason);
        explained.set(reason, incident);
      }
    }
    if (explained.size === 0) return;
    const unexplained = reasons.filter(reason => !explained.has(reason));
    if (unexplained.length) {
      const standalone = buildIncident(event, []);
      standalone.reason_projections = unexplained.map(reason => ({
        source_event_code: event.code,
        source_scope: event.scope,
        reason_code: reason,
      }));
      const definitions = unexplained
        .map(reason => operationalCodeDefinitions.get(reason))
        .filter((definition): definition is OperationalCodeDefinition => Boolean(definition));
      if (definitions.length) {
        standalone.category = definitions[0].category;
        standalone.incident_key = `${definitions[0].root_cause_family}:${event.scope}:${unexplained.join("+")}`;
        standalone.title_zh = definitions.map(item => item.title_zh).join("；");
        standalone.summary_zh = event.message_zh;
      }
      incidents.push(standalone);
    } else {
      const owner = [...new Set(explained.values())]
        .sort((left, right) => left.incident_key.localeCompare(right.incident_key))[0];
      owner.technical_events.push(event);
      owner.technical_event_count = 1 + owner.related_events.length + owner.technical_events.length;
    }
    consumed.add(index);
  });

  events.forEach((event, index) => {
    if (consumed.has(index)) return;
    consumed.add(index);
    const family = eventFamily(event);
    const related: Array<Required<OperationalAlert>> = [];
    events.forEach((candidate, candidateIndex) => {
      if (consumed.has(candidateIndex)) return;
      if (eventFamily(candidate) === family && candidate.scope === event.scope
          && candidate.code !== "OPS_COMPONENT_UNHEALTHY") {
        consumed.add(candidateIndex);
        related.push(candidate);
      }
    });
    incidents.push(buildIncident(event, related));
  });

  return incidents.sort((left, right) => (
    Number(right.blocking) - Number(left.blocking)
    || severityRank[right.severity] - severityRank[left.severity]
    || left.incident_key.localeCompare(right.incident_key)
  ));
}

export const globalOperationalIncidents = (incidents: OperationalIncident[]) => incidents.filter(
  incident => incident.blocking || incident.severity === "ERROR",
);

export const affectedOperationalScopeCount = (incidents: OperationalIncident[]) => new Set(
  incidents.flatMap(incident => incident.affected_scopes),
).size;
