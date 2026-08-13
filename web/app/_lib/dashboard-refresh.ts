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

function mayPoll(coordinationKey: string, intervalMs: number): boolean {
  if (document.visibilityState !== "visible" || navigator.webdriver) return false;
  try {
    const key = `${POLL_LEASE_PREFIX}:${coordinationKey}`;
    const now = Date.now();
    const lastPoll = Number(window.localStorage.getItem(key) ?? 0);
    if (Number.isFinite(lastPoll) && now - lastPoll < intervalMs * 0.8) return false;
    window.localStorage.setItem(key, String(now));
  } catch {
    // Storage can be unavailable in privacy modes. Visibility gating still
    // protects the request budget in that case.
  }
  return true;
}

/** Run once immediately; only current read-only resources may poll. */
export function scheduleDashboardRefresh(
  initialRefresh: () => void,
  pollRefresh: () => void,
  intervalMs: number,
  resourceMode: DashboardResourceMode,
  coordinationKey = "status",
): DashboardRefreshCleanup {
  const initial = window.setTimeout(initialRefresh, 0);
  const pollWhenEligible = () => {
    if (mayPoll(coordinationKey, intervalMs)) pollRefresh();
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
