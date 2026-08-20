"use client";

import { lazy, Suspense, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import LiveRoomView from "../_views/LiveRoomView";
import { primeDashboardResources } from "../_lib/dashboard-resource";
import { settleResponsiveScroll } from "../_lib/responsive-scroll";
import type { StatusPayload as HealthStatusPayload } from "../_views/HealthView";
import {
  DashboardNavigationProvider,
  type AuditViewName,
  type DashboardLocation,
  type DashboardRoom,
} from "./DashboardNavigation";
import DashboardShell from "./DashboardShell";

const loadStatusView = () => import("../_views/StatusView");
const loadHealthView = () => import("../_views/HealthView");
const loadRetryView = () => import("../_views/RetryView");
const loadAdminOverviewView = () => import("../_views/AdminOverviewView");
const loadAuditView = () => import("../_views/AuditView");
const loadAssistantView = () => import("../_views/AssistantView");

const StatusView = lazy(loadStatusView);
const HealthView = lazy(loadHealthView);
const RetryView = lazy(loadRetryView);
const AdminOverviewView = lazy(loadAdminOverviewView);
const AuditView = lazy(loadAuditView);
const AssistantView = lazy(loadAssistantView);

const AUDIT_VIEWS = new Set<AuditViewName>(["briefs", "search", "news", "evidence", "stories", "decisions", "league", "coverage"]);

function validAuditView(value: string | null | undefined): AuditViewName {
  if (value === "qa") return "briefs";
  return value && AUDIT_VIEWS.has(value as AuditViewName) ? value as AuditViewName : "news";
}

function parseDashboardUrl(url: URL): DashboardLocation | null {
  if (url.pathname === "/health") return { room: "health", auditView: "news" };
  if (url.pathname === "/admin") return { room: "admin", auditView: "news" };
  if (url.pathname === "/admin/assistant") return { room: "assistant", auditView: "news" };
  if (url.pathname === "/admin/retry-jobs") return { room: "retry", auditView: "news" };
  if (url.pathname === "/admin/ai-usage") return { room: "status", auditView: "news" };
  if (url.pathname === "/audit") return { room: "audit", auditView: validAuditView(url.searchParams.get("view")) };
  if (url.pathname !== "/") return null;
  const room = url.searchParams.get("room");
  if (room === "assistant") return { room: "assistant", auditView: "news" };
  if (room === "retry") return { room: "retry", auditView: "news" };
  if (room === "status") return { room: "status", auditView: "news" };
  if (room === "health") return { room, auditView: "news" };
  if (room === "audit") return { room, auditView: validAuditView(url.searchParams.get("view")) };
  return { room: "live", auditView: "news" };
}

function canonicalHref(location: DashboardLocation): string {
  if (location.room === "live") return "/";
  if (location.room === "audit") return `/audit?view=${location.auditView}`;
  if (location.room === "health") return "/health";
  if (location.room === "admin") return "/admin";
  if (location.room === "assistant") return "/admin/assistant";
  if (location.room === "retry") return "/admin/retry-jobs";
  return "/admin/ai-usage";
}

function preloadRoom(room: DashboardRoom): Promise<unknown> {
  if (room === "status") return loadStatusView();
  if (room === "health") return loadHealthView();
  if (room === "retry") return loadRetryView();
  if (room === "admin") return loadAdminOverviewView();
  if (room === "audit") return loadAuditView();
  if (room === "assistant") return loadAssistantView();
  return Promise.resolve();
}

export default function DashboardApp({
  initialLocation, initialResources = {},
}: {
  initialLocation: DashboardLocation;
  initialResources?: Record<string, unknown>;
}) {
  primeDashboardResources(initialResources);
  const [location, setLocation] = useState(initialLocation);
  const navigationSequence = useRef(0);
  const pendingScrollTop = useRef<number | null>(null);

  const preload = useCallback((href: string) => {
    const destination = parseDashboardUrl(new URL(href, window.location.href));
    if (destination) void preloadRoom(destination.room);
  }, []);

  const navigate = useCallback(async (href: string, replace = false) => {
    const currentScrollTop = window.scrollY;
    const destinationUrl = new URL(href, window.location.href);
    const destination = parseDashboardUrl(destinationUrl);
    if (!destination) {
      if (replace) window.location.replace(destinationUrl.href);
      else window.location.assign(destinationUrl.href);
      return;
    }
    const sequence = ++navigationSequence.current;
    await preloadRoom(destination.room);
    if (sequence !== navigationSequence.current) return;
    const nextHref = canonicalHref(destination);
    if (replace) window.history.replaceState(null, "", nextHref);
    else window.history.pushState(null, "", nextHref);
    pendingScrollTop.current = currentScrollTop;
    setLocation(destination);
  }, []);

  useLayoutEffect(() => {
    const currentUrl = new URL(window.location.href);
    const destination = parseDashboardUrl(currentUrl);
    if (!destination) return;
    if (
      window.location.pathname === "/"
      && ["admin", "assistant", "retry", "status"].includes(destination.room)
    ) {
      window.location.replace(canonicalHref(destination));
      return;
    }
    if (
      currentUrl.pathname === "/audit"
      && currentUrl.searchParams.get("view") !== destination.auditView
    ) {
      window.history.replaceState(null, "", canonicalHref(destination));
    }
    let active = true;
    queueMicrotask(() => {
      if (!active) return;
      setLocation(current =>
        current.room === destination.room && current.auditView === destination.auditView
          ? current : destination,
      );
    });
    return () => { active = false; };
  }, []);

  useLayoutEffect(() => {
    if (pendingScrollTop.current === null) return;
    const cancel = settleResponsiveScroll(options => window.scrollTo(options), () => window.scrollY, pendingScrollTop.current!);
    pendingScrollTop.current = null;
    return cancel;
  }, [location]);

  useEffect(() => {
    void loadHealthView();
    if (["admin", "assistant", "retry", "status"].includes(location.room)) {
      void loadStatusView();
      void loadRetryView();
      void loadAdminOverviewView();
      void loadAssistantView();
      return;
    }
    const prepareAudit = () => void loadAuditView();
    if ("requestIdleCallback" in window) {
      const idleId = window.requestIdleCallback(prepareAudit, { timeout: 2_000 });
      return () => window.cancelIdleCallback(idleId);
    }
    const timer = window.setTimeout(prepareAudit, 600);
    return () => window.clearTimeout(timer);
  }, [location.room]);

  useEffect(() => {
    const handlePopState = () => {
      const destination = parseDashboardUrl(new URL(window.location.href));
      if (!destination) return;
      const sequence = ++navigationSequence.current;
      void preloadRoom(destination.room).then(() => {
        if (sequence === navigationSequence.current) setLocation(destination);
      });
    };
    window.addEventListener("popstate", handlePopState);
    return () => window.removeEventListener("popstate", handlePopState);
  }, []);

  const navigation = useMemo(() => ({ navigate, preload }), [navigate, preload]);
  const initialStatus = initialResources["/api/status"] as HealthStatusPayload | undefined;

  return <DashboardNavigationProvider value={navigation}>
    <DashboardShell location={location}>
      <Suspense fallback={<main className="app-view-loading" aria-label="正在打开页面"><i /></main>}>
        {location.room === "live" && <LiveRoomView />}
        {location.room === "status" && <StatusView />}
        {location.room === "health" && <HealthView initialPayload={initialStatus} />}
        {location.room === "admin" && <AdminOverviewView />}
        {location.room === "retry" && <RetryView />}
        {location.room === "audit" && <AuditView key={location.auditView} initialView={location.auditView} />}
        {location.room === "assistant" && <AssistantView />}
      </Suspense>
    </DashboardShell>
  </DashboardNavigationProvider>;
}
