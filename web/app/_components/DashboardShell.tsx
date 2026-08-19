"use client";

import { useEffect, useRef, useState, type ReactNode } from "react";
import {
  loadDashboardResource,
  readDashboardResourceState,
  subscribeDashboardResource,
} from "../_lib/dashboard-resource";
import DashboardLink from "./DashboardLink";
import {
  activeDashboardDestination,
  DASHBOARD_ADMIN_DESTINATIONS,
  DASHBOARD_GLOBAL_DESTINATIONS,
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
  return <DashboardLink ariaLabel="打开总览" className="dashboard-brand brand brand-button" href="/" replace>
    <span className="brand-mark">AU</span>
    <span>
      <strong>Aurum Signal Room</strong>
      <small>XAUUSD · Forward-only intelligence</small>
    </span>
  </DashboardLink>;
}

function GlobalNavigation({
  activeDestination, openAdminLogin,
}: {
  activeDestination: ReturnType<typeof activeDashboardDestination>;
  openAdminLogin: () => void;
}) {
  return <nav className="dashboard-global-nav" aria-label="产品区域">
    {DASHBOARD_GLOBAL_DESTINATIONS.map(destination => {
      const active = destination.id === activeDestination;
      const label = active && destination.authenticatedLabel
        ? destination.authenticatedLabel : destination.label;
      if (destination.private && !active) {
        return <button
          className="dashboard-global-link dashboard-admin-login-trigger"
          key={destination.id}
          onClick={openAdminLogin}
          type="button"
        >{label}</button>;
      }
      return <DashboardLink
        ariaCurrent={active ? "page" : undefined}
        className={`dashboard-global-link${active ? " is-active" : ""}`}
        href={destination.href}
        key={destination.id}
        replace={destination.id === "live"}
      >
        {label}
      </DashboardLink>;
    })}
  </nav>;
}

function GlobalSystemState() {
  const [resource, setResource] = useState(
    () => readDashboardResourceState<ShellStatusPayload>("/api/status"),
  );

  useEffect(() => {
    let active = true;
    const update = () => {
      if (active) {
        setResource(readDashboardResourceState<ShellStatusPayload>("/api/status"));
      }
    };
    const unsubscribe = subscribeDashboardResource("/api/status", update);
    const current = readDashboardResourceState<ShellStatusPayload>("/api/status");
    queueMicrotask(update);
    if (!current.hasSnapshot && !current.loading) {
      void loadDashboardResource<ShellStatusPayload>("/api/status").catch(() => undefined);
    }
    return () => {
      active = false;
      unsubscribe();
    };
  }, []);

  const payload = resource.data;

  return <div className="dashboard-global-state" aria-label="全局系统状态">
    <SystemStatePill
      loading={resource.loading}
      error={resource.error !== null}
      hasSnapshot={resource.hasSnapshot}
      online={Boolean(payload?.system?.online)}
      marketSession={payload?.system?.market_session}
      operationalStatus={payload?.operational_health?.status}
    />
  </div>;
}

function DashboardHeader({
  location, openAdminLogin,
}: { location: DashboardLocation; openAdminLogin: () => void }) {
  const activeDestination = activeDashboardDestination(location.room);
  return <header className="dashboard-header topbar">
    <DashboardBrand />
    <GlobalNavigation activeDestination={activeDestination} openAdminLogin={openAdminLogin} />
    <MobileDashboardNav activeDestination={activeDestination} openAdminLogin={openAdminLogin} />
    <GlobalSystemState />
  </header>;
}

function AdminSectionNavigation({ location }: { location: DashboardLocation }) {
  if (activeDashboardDestination(location.room) !== "admin") return null;
  return <nav className="dashboard-section-nav admin-section-nav" aria-label="管理后台区域">
    {DASHBOARD_ADMIN_DESTINATIONS.map(destination => {
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
  const [adminLoginOpen, setAdminLoginOpen] = useState(false);
  const dialogRef = useRef<HTMLDialogElement>(null);
  const openAdminLogin = () => {
    setAdminLoginOpen(true);
    queueMicrotask(() => dialogRef.current?.showModal());
  };
  const closeAdminLogin = () => {
    dialogRef.current?.close();
    setAdminLoginOpen(false);
  };
  return <div className={`dashboard-shell is-${activeDestination}`}>
    <div className="grain" />
    <div className="dashboard-shell-header">
      <DashboardHeader location={location} openAdminLogin={openAdminLogin} />
      <AdminSectionNavigation location={location} />
    </div>
    {children}
    <dialog
      className="admin-login-dialog"
      onClose={() => setAdminLoginOpen(false)}
      ref={dialogRef}
    >
      {adminLoginOpen ? <article>
        <header><p>PRIVATE ADMIN WORKSPACE</p><h2>管理员登录</h2></header>
        <div>
          <p>此区域仅供系统管理员使用。</p>
          <span>登录后可以访问</span>
          <ul><li>Assistant</li><li>重试任务</li><li>AI 模型用量</li><li>其他私有运维工具</li></ul>
        </div>
        <footer>
          <button type="button" onClick={closeAdminLogin}>取消</button>
          <a href="/admin">使用 Google 登录</a>
        </footer>
      </article> : null}
    </dialog>
  </div>;
}
