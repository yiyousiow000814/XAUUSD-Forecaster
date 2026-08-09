"use client";

import { createContext, useContext, type ReactNode } from "react";

export type DashboardRoom = "live" | "status" | "health" | "audit";
export type AuditViewName = "news" | "evidence" | "stories" | "decisions" | "league" | "coverage";

export type DashboardLocation = {
  room: DashboardRoom;
  auditView: AuditViewName;
};

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
