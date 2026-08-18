"use client";

import { createContext, useContext, type ReactNode } from "react";

export type DashboardRoom = "live" | "assistant" | "status" | "health" | "audit";
export type AuditViewName = "news" | "evidence" | "stories" | "decisions" | "league" | "coverage";

export type DashboardLocation = {
  room: DashboardRoom;
  auditView: AuditViewName;
};

export type DashboardGlobalDestinationId = "live" | "assistant" | "audit" | "system";

export type DashboardGlobalDestination = {
  id: DashboardGlobalDestinationId;
  label: string;
  href: string;
  rooms: readonly DashboardRoom[];
};

export const DASHBOARD_GLOBAL_DESTINATIONS: readonly DashboardGlobalDestination[] = [
  { id: "live", label: "总览", href: "/", rooms: ["live"] },
  { id: "audit", label: "新闻与决策", href: "/audit?view=news", rooms: ["audit"] },
  { id: "assistant", label: "Assistant", href: "/assistant", rooms: ["assistant"] },
  { id: "system", label: "系统", href: "/health", rooms: ["health", "status"] },
];

export const DASHBOARD_SYSTEM_DESTINATIONS = [
  { id: "health", label: "系统健康", href: "/health", room: "health" },
  { id: "status", label: "AI 模型用量", href: "/status", room: "status" },
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
