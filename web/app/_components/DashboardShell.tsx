"use client";

import { useEffect, useState, type ReactNode } from "react";
import { loadDashboardResource, readDashboardResource, subscribeDashboardResource } from "../_lib/dashboard-resource";
import DashboardLink from "./DashboardLink";
import {
  activeDashboardDestination,
  DASHBOARD_GLOBAL_DESTINATIONS,
  DASHBOARD_SYSTEM_DESTINATIONS,
  type DashboardLocation,
} from "./DashboardNavigation";
import MobileDashboardNav from "./MobileDashboardNav";
import SystemStatePill from "./SystemStatePill";

type ShellStatusPayload = {
  system?: {
    online?: boolean;
    market_session?: "OPEN" | "CLOSED" | "WEEKLY_CLOSED" | "DATA_UNAVAILABLE";
  };
  operational_health?: { status?: "HEALTHY" | "WARNING" | "ERROR" };
};

function DashboardBrand() {
  return <DashboardLink ariaLabel="打开实时室" className="dashboard-brand brand brand-button" href="/" replace>
    <span className="brand-mark">AU</span>
    <span>
      <strong>Aurum Signal Room</strong>
      <small>XAUUSD · Forward-only intelligence</small>
    </span>
  </DashboardLink>;
}

function GlobalNavigation({ activeDestination }: { activeDestination: ReturnType<typeof activeDashboardDestination> }) {
  return <nav className="dashboard-global-nav" aria-label="产品区域">
    {DASHBOARD_GLOBAL_DESTINATIONS.map(destination => {
      const active = destination.id === activeDestination;
      return <DashboardLink
        ariaCurrent={active ? "page" : undefined}
        className={`dashboard-global-link${active ? " is-active" : ""}`}
        href={destination.href}
        key={destination.id}
        replace={destination.id === "live"}
      >
        {destination.label}
      </DashboardLink>;
    })}
  </nav>;
}

function GlobalSystemState() {
  const [payload, setPayload] = useState<ShellStatusPayload | null>(
    () => readDashboardResource<ShellStatusPayload>("/api/status"),
  );
  const [error, setError] = useState(false);

  useEffect(() => subscribeDashboardResource("/api/status", () => {
    setPayload(readDashboardResource<ShellStatusPayload>("/api/status"));
    setError(false);
  }), []);

  useEffect(() => {
    if (payload) return;
    let active = true;
    void loadDashboardResource<ShellStatusPayload>("/api/status").then(next => {
      if (active) setPayload(next);
    }).catch(() => {
      if (active) setError(true);
    });
    return () => { active = false; };
  }, [payload]);

  return <div className="dashboard-global-state" aria-label="全局系统状态">
    <SystemStatePill
      loading={payload === null && !error}
      error={error}
      hasSnapshot={payload !== null}
      online={Boolean(payload?.system?.online)}
      marketSession={payload?.system?.market_session}
      operationalStatus={payload?.operational_health?.status}
    />
  </div>;
}

function DashboardHeader({ location }: { location: DashboardLocation }) {
  const activeDestination = activeDashboardDestination(location.room);
  return <header className="dashboard-header topbar">
    <DashboardBrand />
    <GlobalNavigation activeDestination={activeDestination} />
    <MobileDashboardNav activeDestination={activeDestination} />
    <GlobalSystemState />
  </header>;
}

function SystemSectionNavigation({ location }: { location: DashboardLocation }) {
  if (activeDashboardDestination(location.room) !== "system") return null;
  return <nav className="dashboard-section-nav" aria-label="系统区域">
    {DASHBOARD_SYSTEM_DESTINATIONS.map(destination => {
      const active = destination.room === location.room;
      return <DashboardLink
        ariaCurrent={active ? "page" : undefined}
        className={active ? "is-active" : undefined}
        href={destination.href}
        key={destination.id}
      >
        {destination.label}
      </DashboardLink>;
    })}
  </nav>;
}

export default function DashboardShell({ children, location }: { children: ReactNode; location: DashboardLocation }) {
  const activeDestination = activeDashboardDestination(location.room);
  return <div className={`dashboard-shell is-${activeDestination}`}>
    <div className="grain" />
    <div className="dashboard-shell-header">
      <DashboardHeader location={location} />
      <SystemSectionNavigation location={location} />
    </div>
    {children}
  </div>;
}
