"use client";

import { useCallback, useEffect, useState } from "react";
import DashboardLink from "./DashboardLink";
import { loadDashboardResource, readDashboardResource } from "../_lib/dashboard-resource";
import { DASHBOARD_REFRESH_INTERVALS, scheduleDashboardRefresh } from "../_lib/dashboard-refresh";
import { correlateOperationalEvents, globalOperationalIncidents } from "../_lib/operational-incidents";
import { normalizeOperationalEvent, type AssistantOperationalHealth, type OperationalAlert, type OperationalHealth } from "../_lib/operational-health";

type AlertPayload = {
  preview_status_summary?: boolean;
  operational_health?: OperationalHealth;
};

export default function OperationalAlertBanner() {
  const cached = readDashboardResource<AlertPayload>("/api/status");
  const cachedAssistant = readDashboardResource<AssistantOperationalHealth>("/api/assistant-health");
  const [payload, setPayload] = useState<AlertPayload | null>(() => cached);
  const [assistant, setAssistant] = useState<AssistantOperationalHealth | null>(() => cachedAssistant);
  const [assistantUnavailable, setAssistantUnavailable] = useState(false);
  const [expanded, setExpanded] = useState(false);
  const refresh = useCallback(async (force = false) => {
    try {
      setPayload(await loadDashboardResource<AlertPayload>("/api/status", { force }));
    } catch {
      // The page-level current-data state remains the authority for transport
      // errors. Never replace it with an alert inferred from missing data.
    }
  }, []);
  const refreshAssistant = useCallback(async (force = false) => {
    try {
      setAssistant(await loadDashboardResource<AssistantOperationalHealth>("/api/assistant-health", { force }));
      setAssistantUnavailable(false);
    } catch {
      setAssistantUnavailable(true);
    }
  }, []);

  useEffect(() => scheduleDashboardRefresh(
    () => void refresh(Boolean(payload?.preview_status_summary)),
    () => void refresh(true),
    DASHBOARD_REFRESH_INTERVALS.status,
    "current",
    "status",
  ), [payload?.preview_status_summary, refresh]);
  useEffect(() => scheduleDashboardRefresh(
    () => void refreshAssistant(false),
    () => void refreshAssistant(true),
    DASHBOARD_REFRESH_INTERVALS.status,
    "current",
    "assistant-health",
  ), [refreshAssistant]);

  const health = payload?.operational_health;
  const unavailableAlert: OperationalAlert = normalizeOperationalEvent({
    code: "OPS_ASSISTANT_HEALTH_UNAVAILABLE",
    severity: "ERROR",
    scope: "ASSISTANT_D1",
    message_zh: "Assistant 云端运行状态无法读取。",
    blocking: true,
    evidence: {},
  });
  const incidents = globalOperationalIncidents(correlateOperationalEvents([
    ...(health?.alerts ?? []),
    ...(assistantUnavailable ? [unavailableAlert] : assistant?.current ? assistant.alerts : []),
  ]));
  if (payload?.preview_status_summary || incidents.length === 0) {
    return null;
  }
  const status = incidents.some(incident => incident.severity === "ERROR") ? "error" : "warning";
  const first = incidents[0];
  const title = status === "error" ? "后台运行异常" : "后台运行提醒";
  return <aside className={`operational-alert-banner is-${status}${expanded ? " is-expanded" : ""}`} role="alert">
    <div className="operational-alert-heading">
      <b>{title}</b>
      <span>{incidents.length} 个问题</span>
    </div>
    <button
      type="button"
      className="operational-alert-toggle"
      aria-expanded={expanded}
      aria-controls="operational-alert-details"
      onClick={() => setExpanded(value => !value)}
    >
      <b>{title}</b>
      <span>{incidents.length} 个问题</span>
      <i aria-hidden="true">{expanded ? "−" : "+"}</i>
    </button>
    <div className="operational-alert-detail" id="operational-alert-details">
      <span>{first.title_zh}</span>
      <span>{first.summary_zh}{incidents.length > 1 ? ` · 另有 ${incidents.length - 1} 个问题` : ""}</span>
    </div>
    <DashboardLink href="/health#operational-alerts">查看证据与处理进度 →</DashboardLink>
  </aside>;
}
