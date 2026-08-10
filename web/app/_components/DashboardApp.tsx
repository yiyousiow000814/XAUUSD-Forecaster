"use client";

import { lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";
import LiveRoomView from "../_views/LiveRoomView";
import { primeDashboardResources } from "../_lib/dashboard-resource";
import {
  DashboardNavigationProvider,
  type AuditViewName,
  type DashboardLocation,
  type DashboardRoom,
} from "./DashboardNavigation";

const loadStatusView = () => import("../_views/StatusView");
const loadHealthView = () => import("../_views/HealthView");
const loadAuditView = () => import("../_views/AuditView");

const StatusView = lazy(loadStatusView);
const HealthView = lazy(loadHealthView);
const AuditView = lazy(loadAuditView);

const AUDIT_VIEWS = new Set<AuditViewName>(["news", "evidence", "stories", "decisions", "league", "coverage"]);

function validAuditView(value: string | null | undefined): AuditViewName {
  return value && AUDIT_VIEWS.has(value as AuditViewName) ? value as AuditViewName : "news";
}

function parseDashboardUrl(url: URL): DashboardLocation | null {
  if (url.pathname === "/status") return { room: "status", auditView: "news" };
  if (url.pathname === "/health") return { room: "health", auditView: "news" };
  if (url.pathname === "/audit") return { room: "audit", auditView: validAuditView(url.searchParams.get("view")) };
  if (url.pathname !== "/") return null;
  const room = url.searchParams.get("room");
  if (room === "status" || room === "health") return { room, auditView: "news" };
  if (room === "audit") return { room, auditView: validAuditView(url.searchParams.get("view")) };
  return { room: "live", auditView: "news" };
}

function canonicalHref(location: DashboardLocation): string {
  if (location.room === "live") return "/";
  if (location.room === "audit") return `/?room=audit&view=${location.auditView}`;
  return `/?room=${location.room}`;
}

function preloadRoom(room: DashboardRoom): Promise<unknown> {
  if (room === "status") return loadStatusView();
  if (room === "health") return loadHealthView();
  if (room === "audit") return loadAuditView();
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

  const preload = useCallback((href: string) => {
    const destination = parseDashboardUrl(new URL(href, window.location.href));
    if (destination) void preloadRoom(destination.room);
  }, []);

  const navigate = useCallback(async (href: string, replace = false) => {
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
    setLocation(destination);
    window.scrollTo({ top: 0, behavior: "instant" });
  }, []);

  useEffect(() => {
    void loadStatusView();
    void loadHealthView();
    const prepareAudit = () => void loadAuditView();
    if ("requestIdleCallback" in window) {
      const idleId = window.requestIdleCallback(prepareAudit, { timeout: 2_000 });
      return () => window.cancelIdleCallback(idleId);
    }
    const timer = window.setTimeout(prepareAudit, 600);
    return () => window.clearTimeout(timer);
  }, []);

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

  return <DashboardNavigationProvider value={navigation}>
    <Suspense fallback={<main className="app-view-loading" aria-label="正在打开页面"><i /></main>}>
      {location.room === "live" && <LiveRoomView />}
      {location.room === "status" && <StatusView />}
      {location.room === "health" && <HealthView />}
      {location.room === "audit" && <AuditView key={location.auditView} />}
    </Suspense>
  </DashboardNavigationProvider>;
}
