import { env } from "cloudflare:workers";
import { NextResponse } from "next/server";
import { applyFreshness } from "./freshness.js";
import { previewBundle, previewJson } from "../_shared/preview";
import { withPreviewIdentity } from "../_shared/preview-status";

export const dynamic = "force-dynamic";

type DashboardPayload = {
  generated_at?: string;
  system?: { online?: boolean; quote_age_seconds?: number | null };
};

export async function GET() {
  // Public viewers must not depend on a direct connection to the owner's PC.
  // The synchronizer writes the authoritative public snapshot to D1; the
  // local relay is only a last-resort fallback before the first sync.
  try {
    const binding = env.DB as D1Database | undefined;
    if (binding) {
      const row = await binding
        .prepare("SELECT payload FROM dashboard_snapshots WHERE id = ?")
        .bind(1)
        .first<{ payload: string }>();
      if (row) {
        const current = applyFreshness(JSON.parse(row.payload)) as Record<string, unknown>;
        if (previewBundle) {
          return previewJson(
            withPreviewIdentity(current, previewBundle.status),
            200,
            "read-only-d1-snapshot",
          );
        }
        return NextResponse.json({ ...current, observation_scope: "D1_SNAPSHOT" }, {
          headers: { "Cache-Control": "no-store, max-age=0" },
        });
      }
    }
  } catch {
    // Fall through to the relay. A temporary D1 read problem should not stop
    // the public page when the live relay is still reachable.
  }

  if (previewBundle) return previewJson(previewBundle.status);

  const relay = process.env.STATUS_RELAY_URL;
  if (relay) {
    try {
      const response = await fetch(relay, {
        cache: "no-store",
        headers: { Accept: "application/json" },
        signal: AbortSignal.timeout(4_000),
      });
      const payload = (await response.json()) as DashboardPayload;
      return NextResponse.json({ ...applyFreshness(payload), observation_scope: "RELAY" }, {
        status: response.status,
        headers: { "Cache-Control": "no-store, max-age=0" },
      });
    } catch {
      // The final error is intentionally generic: viewers do not need to know
      // the owner's local network layout.
    }
  }

  return NextResponse.json({ error: "等待公开状态快照" }, { status: 503 });
}
