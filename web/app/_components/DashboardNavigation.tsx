"use client";

import { createContext, useContext, type ReactNode } from "react";

export type DashboardRoom = "live" | "audit" | "health" | "admin" | "assistant" | "retry" | "status" | "architecture";
export type AuditViewName = "briefs" | "search" | "news" | "evidence" | "stories" | "decisions" | "league" | "coverage";

export type DashboardLocation = {
  room: DashboardRoom;
  auditView: AuditViewName;
};

export type DashboardGlobalDestinationId = "live" | "audit" | "system" | "admin";

export type DashboardGlobalDestination = {
  id: DashboardGlobalDestinationId;
  label: string;
  authenticatedLabel?: string;
  href: string;
  rooms: readonly DashboardRoom[];
  private?: boolean;
};

export const DASHBOARD_GLOBAL_DESTINATIONS: readonly DashboardGlobalDestination[] = [
  { id: "live", label: "总览", href: "/", rooms: ["live"] },
  { id: "audit", label: "新闻与决策", href: "/audit?view=news", rooms: ["audit"] },
  { id: "system", label: "系统", href: "/health", rooms: ["health"] },
  {
    id: "admin", label: "管理员登录", authenticatedLabel: "管理后台", href: "/admin",
    rooms: ["admin", "assistant", "retry", "status", "architecture"], private: true,
  },
];

export const DASHBOARD_ADMIN_DESTINATIONS = [
  { id: "overview", label: "概览", href: "/admin", room: "admin" },
  { id: "assistant", label: "Assistant", href: "/admin/assistant", room: "assistant" },
  { id: "retry", label: "重试任务", href: "/admin/retry-jobs", room: "retry" },
  { id: "ai-usage", label: "AI 模型用量", href: "/admin/ai-usage", room: "status" },
  { id: "architecture", label: "系统架构", href: "/admin/architecture", room: "architecture" },
] as const;

export function activeDashboardDestination(room: DashboardRoom): DashboardGlobalDestinationId {
  return DASHBOARD_GLOBAL_DESTINATIONS.find(destination => destination.rooms.includes(room))?.id ?? "live";
}

type DashboardNavigationValue = {
  navigate: (href: string, replace?: boolean) => Promise<void>;
  preload: (href: string) => void;
};

const DashboardNavigationContext = createContext<DashboardNavigationValue | null>(null);

export function DashboardNavigationProvider({ children, value }: { children: ReactNode; value: DashboardNavigationValue }) {
  return <DashboardNavigationContext.Provider value={value}>{children}</DashboardNavigationContext.Provider>;
}

export function useDashboardNavigation() {
  return useContext(DashboardNavigationContext);
}
