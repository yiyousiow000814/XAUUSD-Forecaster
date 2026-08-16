"use client";

import { useCallback, useEffect, useState } from "react";
import DashboardLink from "./DashboardLink";
import { loadDashboardResource, readDashboardResource } from "../_lib/dashboard-resource";
import { DASHBOARD_REFRESH_INTERVALS, scheduleDashboardRefresh } from "../_lib/dashboard-refresh";
import type { OperationalHealth } from "../_lib/operational-health";

type AlertPayload = {
  preview_status_summary?: boolean;
  operational_health?: OperationalHealth;
};

export default function OperationalAlertBanner() {
  const cached = readDashboardResource<AlertPayload>("/api/status");
  const [payload, setPayload] = useState<AlertPayload | null>(() => cached);
  const refresh = useCallback(async (force = false) => {
    try {
      setPayload(await loadDashboardResource<AlertPayload>("/api/status", { force }));
    } catch {
      // The page-level current-data state remains the authority for transport
      // errors. Never replace it with an alert inferred from missing data.
    }
  }, []);

  useEffect(() => scheduleDashboardRefresh(
    () => void refresh(Boolean(payload?.preview_status_summary)),
    () => void refresh(true),
    DASHBOARD_REFRESH_INTERVALS.status,
    "current",
    "status",
  ), [payload?.preview_status_summary, refresh]);

  const health = payload?.operational_health;
  if (!health || payload?.preview_status_summary || health.alerts.length === 0) {
    return null;
  }
  const first = health.alerts[0];
  return <aside className={`operational-alert-banner is-${health.status.toLowerCase()}`} role="alert">
    <div>
      <b>{health.status === "ERROR" ? "后台运行异常" : "后台运行提醒"}</b>
      <code>{first.code}</code>
      <span>{first.message_zh}{health.alerts.length > 1 ? ` · 另有 ${health.alerts.length - 1} 项` : ""}</span>
    </div>
    <DashboardLink href="/health#operational-alerts">查看证据与处理进度 →</DashboardLink>
  </aside>;
}
