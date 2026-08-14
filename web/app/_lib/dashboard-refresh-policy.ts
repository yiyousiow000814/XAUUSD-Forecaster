export const SHARED_POLL_LEASE_RATIO = 0.8;
export const LOCAL_POLL_MAX_INTERVALS = 2;

export function shouldPollDashboardResource({
  visible,
  automated,
  now,
  lastSharedPollAt,
  lastLocalPollAt,
  intervalMs,
}: {
  visible: boolean;
  automated: boolean;
  now: number;
  lastSharedPollAt: number;
  lastLocalPollAt: number;
  intervalMs: number;
}): boolean {
  if (!visible || automated) return false;

  const localDataIsTooOld = now - lastLocalPollAt >= intervalMs * LOCAL_POLL_MAX_INTERVALS;
  if (localDataIsTooOld) return true;

  return now - lastSharedPollAt >= intervalMs * SHARED_POLL_LEASE_RATIO;
}
