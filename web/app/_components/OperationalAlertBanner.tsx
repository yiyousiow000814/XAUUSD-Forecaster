"use client";

import { useCallback, useEffect, useState } from "react";
import DashboardLink from "./DashboardLink";
import { loadDashboardResource, readDashboardResource } from "../_lib/dashboard-resource";
import { DASHBOARD_REFRESH_INTERVALS, scheduleDashboardRefresh } from "../_lib/dashboard-refresh";
import type { AssistantOperationalHealth, OperationalAlert, OperationalHealth } from "../_lib/operational-health";

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
  const unavailableAlert: OperationalAlert = {
    code: "OPS_ASSISTANT_HEALTH_UNAVAILABLE",
    severity: "ERROR",
    scope: "ASSISTANT_D1",
    message_zh: "Assistant 云端运行状态无法读取。",
    blocking: true,
    evidence: {},
  };
  const alerts = [
    ...(health?.alerts ?? []),
    ...(assistantUnavailable ? [unavailableAlert] : assistant?.current ? assistant.alerts : []),
  ].sort((left, right) => (
    (left.severity === "ERROR" ? 0 : 1) - (right.severity === "ERROR" ? 0 : 1)
  ));
  if (payload?.preview_status_summary || alerts.length === 0) {
    return null;
  }
  const status = alerts.some(alert => alert.severity === "ERROR") ? "error" : "warning";
  const first = alerts[0];
  return <aside className={`operational-alert-banner is-${status}`} role="alert">
    <div>
      <b>{status === "error" ? "后台运行异常" : "后台运行提醒"}</b>
      <code>{first.code}</code>
      <span>{first.message_zh}{alerts.length > 1 ? ` · 另有 ${alerts.length - 1} 项` : ""}</span>
    </div>
    <DashboardLink href="/health#operational-alerts">查看证据与处理进度 →</DashboardLink>
  </aside>;
}
