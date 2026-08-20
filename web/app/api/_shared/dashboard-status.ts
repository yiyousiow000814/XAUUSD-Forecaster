import { applyFreshness } from "../status/freshness.js";
import { PUBLIC_STATUS_PRIVATE_FIELDS } from "./dashboard-snapshot";

export type DashboardStatusRead = {
  payload: Record<string, unknown>;
  status: number;
  scope: "D1_SNAPSHOT";
};

export async function readDashboardStatus(
  binding: D1Database | undefined,
): Promise<DashboardStatusRead | null> {
  try {
    if (binding) {
      const row = await binding.prepare(
        "SELECT payload FROM dashboard_snapshots WHERE id = ?",
      ).bind(1).first<{ payload: string }>();
      if (row) return {
        payload: applyFreshness(JSON.parse(row.payload)) as Record<string, unknown>,
        status: 200,
        scope: "D1_SNAPSHOT",
      };
    }
  } catch {
    // Callers decide whether their public surface may use a relay fallback.
  }

  return null;
}

export function publicDashboardStatus(payload: Record<string, unknown>) {
  const publicPayload = { ...payload };
  for (const privateField of PUBLIC_STATUS_PRIVATE_FIELDS) {
    delete publicPayload[privateField];
  }
  return publicPayload;
}
