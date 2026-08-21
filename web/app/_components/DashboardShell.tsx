"use client";

import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import {
  adminAuthStateAfterProbe,
  isTrustedAdminAuthMessage,
  openAdminAuthPopup,
  probeAdminSession,
  subscribeAdminAuthOutcomes,
  type AdminAuthProbeOutcome,
  type AdminAuthState,
} from "../_lib/admin-auth-session";
import {
  clearPrivateDashboardResources,
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
  useDashboardNavigation,
} from "./DashboardNavigation";
import MobileDashboardNav from "./MobileDashboardNav";
import SystemStatePill from "./SystemStatePill";

export const isVersionedCandidateHost = (hostname: string) => (
  /^[0-9a-f]{8}-aurum-signal-room\./i.test(hostname)
);

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
  activeDestination, adminAuthenticated, openAdminLogin,
}: {
  activeDestination: ReturnType<typeof activeDashboardDestination>;
  adminAuthenticated: boolean;
  openAdminLogin: () => void;
}) {
  return <nav className="dashboard-global-nav" aria-label="产品区域">
    {DASHBOARD_GLOBAL_DESTINATIONS.map(destination => {
      const active = destination.id === activeDestination;
      const label = adminAuthenticated && destination.authenticatedLabel
        ? destination.authenticatedLabel : destination.label;
      if (destination.private && !adminAuthenticated) {
        return <button
          aria-current={active ? "page" : undefined}
          className="dashboard-global-link dashboard-admin-login-trigger"
          key={destination.id}
          onClick={openAdminLogin}
          type="button"
        ><span aria-hidden="true" className="dashboard-admin-lock" />{label}</button>;
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
  location, adminAuthenticated, openAdminLogin,
}: { location: DashboardLocation; adminAuthenticated: boolean; openAdminLogin: () => void }) {
  const activeDestination = activeDashboardDestination(location.room);
  return <header className="dashboard-header topbar">
    <DashboardBrand />
    <GlobalNavigation
      activeDestination={activeDestination}
      adminAuthenticated={adminAuthenticated}
      openAdminLogin={openAdminLogin}
    />
    <MobileDashboardNav
      activeDestination={activeDestination}
      adminAuthenticated={adminAuthenticated}
      openAdminLogin={openAdminLogin}
    />
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
  const navigation = useDashboardNavigation();
  const [adminAuthState, setAdminAuthState] = useState<AdminAuthState>("CHECKING");
  const [adminLoginOpen, setAdminLoginOpen] = useState(false);
  const [candidateInspection, setCandidateInspection] = useState(false);
  const dialogRef = useRef<HTMLDialogElement>(null);
  const popupRef = useRef<Window | null>(null);
  const popupCloseTimerRef = useRef<number | null>(null);
  const authCompletionInFlightRef = useRef(false);
  const applyAuthOutcome = useCallback((outcome: AdminAuthProbeOutcome) => {
    if (outcome === "ANONYMOUS" || outcome === "FORBIDDEN") {
      clearPrivateDashboardResources();
    }
    setAdminAuthState(current => adminAuthStateAfterProbe(current, outcome));
  }, []);
  const revalidateAdminSession = useCallback(async () => {
    const outcome = await probeAdminSession();
    applyAuthOutcome(outcome);
    return outcome;
  }, [applyAuthOutcome]);

  useEffect(() => {
    setCandidateInspection(isVersionedCandidateHost(window.location.hostname));
    queueMicrotask(() => void revalidateAdminSession());
    const unsubscribe = subscribeAdminAuthOutcomes(applyAuthOutcome);
    const revalidateOnReturn = () => {
      if (document.visibilityState === "visible") void revalidateAdminSession();
    };
    window.addEventListener("pageshow", revalidateOnReturn);
    document.addEventListener("visibilitychange", revalidateOnReturn);
    return () => {
      unsubscribe();
      window.removeEventListener("pageshow", revalidateOnReturn);
      document.removeEventListener("visibilitychange", revalidateOnReturn);
    };
  }, [applyAuthOutcome, revalidateAdminSession]);

  const clearPopupCloseTimer = useCallback(() => {
    if (popupCloseTimerRef.current !== null) {
      window.clearInterval(popupCloseTimerRef.current);
      popupCloseTimerRef.current = null;
    }
  }, []);

  const completeAdminLogin = useCallback(async () => {
    if (authCompletionInFlightRef.current) return;
    authCompletionInFlightRef.current = true;
    try {
      const outcome = await revalidateAdminSession();
      if (outcome !== "AUTHENTICATED") return;
      clearPopupCloseTimer();
      popupRef.current?.close();
      popupRef.current = null;
      dialogRef.current?.close();
      setAdminLoginOpen(false);
      if (navigation) void navigation.navigate("/admin");
      else window.location.assign("/admin");
    } finally {
      authCompletionInFlightRef.current = false;
    }
  }, [clearPopupCloseTimer, navigation, revalidateAdminSession]);

  useEffect(() => {
    const handleMessage = (event: MessageEvent) => {
      if (!isTrustedAdminAuthMessage(
        event, window.location.origin, popupRef.current,
      )) return;
      void completeAdminLogin();
    };
    window.addEventListener("message", handleMessage);
    return () => {
      window.removeEventListener("message", handleMessage);
      clearPopupCloseTimer();
    };
  }, [clearPopupCloseTimer, completeAdminLogin]);

  const openAdminLogin = () => {
    setAdminLoginOpen(true);
    queueMicrotask(() => dialogRef.current?.showModal());
  };
  const closeAdminLogin = () => {
    dialogRef.current?.close();
    setAdminLoginOpen(false);
  };
  const beginAdminLogin = () => {
    clearPopupCloseTimer();
    popupRef.current = openAdminAuthPopup(
      (url, target, features) => window.open(url, target, features),
      () => window.location.assign("/admin"),
    );
    if (!popupRef.current) return;
    popupRef.current.focus();
    popupCloseTimerRef.current = window.setInterval(() => {
      if (!popupRef.current?.closed) return;
      clearPopupCloseTimer();
      popupRef.current = null;
      void completeAdminLogin();
    }, 500);
  };
  return <div className={`dashboard-shell is-${activeDestination}`}>
    <div className="grain" />
    <div className="dashboard-shell-header">
      <DashboardHeader
        location={location}
        adminAuthenticated={adminAuthState === "AUTHENTICATED"}
        openAdminLogin={openAdminLogin}
      />
      <AdminSectionNavigation location={location} />
    </div>
    {children}
    <dialog
      className="admin-login-dialog"
      onClose={() => setAdminLoginOpen(false)}
      ref={dialogRef}
    >
      {adminLoginOpen ? <article>
        <header><h2>管理员登录</h2></header>
        <div>
          <p>{candidateInspection
            ? "这是未受 Cloudflare Access 保护的 Candidate 检查页面。"
            : "仅系统管理员可访问 Assistant、重试任务和 AI 模型用量。"}</p>
          <span>{candidateInspection
            ? "管理员登录需在正式 Access 边界验收；此页面不会伪造登录通过。"
            : adminAuthState === "FORBIDDEN"
            ? "当前 Google 账号不在管理员允许名单中。"
            : "登录后进入私有管理后台。"}</span>
        </div>
        <footer>
          <button type="button" onClick={closeAdminLogin}>取消</button>
          {candidateInspection ? null : <button
            className="admin-login-primary" type="button" onClick={beginAdminLogin}
          >使用 Google 登录</button>}
        </footer>
      </article> : null}
    </dialog>
  </div>;
}
