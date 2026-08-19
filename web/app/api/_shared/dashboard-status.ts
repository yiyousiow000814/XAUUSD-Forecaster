import { applyFreshness } from "../status/freshness.js";

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
  for (const privateField of [
    "annotation_queue", "gemini_quota", "gemini_31_quota", "gemma_quota",
    "gemini_embedding_quota", "llm_routing",
  ]) delete publicPayload[privateField];
  return publicPayload;
}
