export type DashboardRefreshCleanup = () => void;

/** Run once immediately, then poll only when the payload is live. */
export function scheduleDashboardRefresh(
  initialRefresh: () => void,
  pollRefresh: () => void,
  intervalMs: number,
  immutablePreview: boolean,
): DashboardRefreshCleanup {
  const initial = window.setTimeout(initialRefresh, 0);
  const interval = immutablePreview
    ? null
    : window.setInterval(pollRefresh, intervalMs);
  return () => {
    window.clearTimeout(initial);
    if (interval !== null) window.clearInterval(interval);
  };
}

export function isImmutablePreview(payload: unknown): boolean {
  if (!payload || typeof payload !== "object") return false;
  const preview = (payload as { preview?: unknown }).preview;
  return Boolean(
    preview && typeof preview === "object"
    && (preview as { is_preview?: unknown }).is_preview === true,
  );
}
