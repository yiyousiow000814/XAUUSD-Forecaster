import { shouldPollDashboardResource } from "./dashboard-refresh-policy";
import { isLiveBroadcastHealthy } from "./live-broadcast";

export type DashboardRefreshCleanup = () => void;
export type DashboardResourceMode = "current" | "build-snapshot";

export const DASHBOARD_REFRESH_INTERVALS = {
  live: 15_000,
  status: 60_000,
  news: 30_000,
  learning: 300_000,
  deployment: 120_000,
} as const;

const POLL_LEASE_PREFIX = "aurum-dashboard-poll";
let statusBaseline: Promise<void> | null = null;

export function ensureStatusBaseline(
  refresh: () => void | Promise<unknown>,
): Promise<void> {
  if (!statusBaseline) {
    statusBaseline = Promise.resolve().then(refresh).then(() => undefined);
  }
  return statusBaseline;
}

export function statusPollingSuppressed(
  coordinationKey: string,
  liveHealthy = isLiveBroadcastHealthy(),
): boolean {
  return coordinationKey === "status" && liveHealthy;
}

function mayPoll(
  coordinationKey: string,
  intervalMs: number,
  lastLocalPollAt: number,
  now: number,
): boolean {
  let lastSharedPollAt = 0;
  try {
    const key = `${POLL_LEASE_PREFIX}:${coordinationKey}`;
    const stored = Number(window.localStorage.getItem(key) ?? 0);
    if (Number.isFinite(stored)) lastSharedPollAt = stored;
    if (!shouldPollDashboardResource({
      visible: document.visibilityState === "visible",
      automated: navigator.webdriver,
      now,
      lastSharedPollAt,
      lastLocalPollAt,
      intervalMs,
    })) return false;
    window.localStorage.setItem(key, String(now));
  } catch {
    // Storage can be unavailable in privacy modes. Visibility gating still
    // protects the request budget in that case.
    return shouldPollDashboardResource({
      visible: document.visibilityState === "visible",
      automated: navigator.webdriver,
      now,
      lastSharedPollAt: 0,
      lastLocalPollAt,
      intervalMs,
    });
  }
  return true;
}

/** Prefer the singleton live stream; only current read-only resources may poll. */
export function scheduleDashboardRefresh(
  initialRefresh: () => void,
  pollRefresh: () => void,
  intervalMs: number,
  resourceMode: DashboardResourceMode,
  coordinationKey = "status",
): DashboardRefreshCleanup {
  const statusResource = coordinationKey === "status";
  const initial = window.setTimeout(
    () => {
      if (statusResource) void ensureStatusBaseline(initialRefresh);
      else initialRefresh();
    },
    0,
  );
  let lastLocalPollAt = Date.now();
  const pollWhenEligible = () => {
    if (statusPollingSuppressed(coordinationKey)) return;
    const now = Date.now();
    if (mayPoll(coordinationKey, intervalMs, lastLocalPollAt, now)) {
      lastLocalPollAt = now;
      pollRefresh();
    }
  };
  const mayRefresh = resourceMode === "current";
  const interval = mayRefresh
    ? window.setInterval(pollWhenEligible, intervalMs)
    : null;
  const resume = () => {
    if (document.visibilityState === "visible") pollWhenEligible();
  };
  if (mayRefresh) document.addEventListener("visibilitychange", resume);
  return () => {
    window.clearTimeout(initial);
    if (interval !== null) window.clearInterval(interval);
    document.removeEventListener("visibilitychange", resume);
  };
}
