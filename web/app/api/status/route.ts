import { env } from "cloudflare:workers";
import { NextResponse } from "next/server";
import { previewBundle, previewJson } from "../_shared/preview";
import { withPreviewIdentity } from "../_shared/preview-status";
import { publicDashboardStatus, readDashboardStatus } from "../_shared/dashboard-status";
import { applyFreshness } from "./freshness.js";

export const dynamic = "force-dynamic";

export async function GET() {
  // Public viewers must not depend on a direct connection to the owner's PC.
  // The synchronizer writes the authoritative public snapshot to D1; the
  // local relay is only a last-resort fallback before the first sync.
  const current = await readDashboardStatus(env.DB as D1Database | undefined);
  if (current) {
    const payload = previewBundle
      ? withPreviewIdentity(current.payload, previewBundle.status)
      : current.payload;
    const publicPayload = publicDashboardStatus(payload);
    if (previewBundle) return previewJson(publicPayload, 200, "read-only-public-status");
    return NextResponse.json({ ...publicPayload, observation_scope: current.scope }, {
      status: current.status,
      headers: { "Cache-Control": "no-store, max-age=0" },
    });
  }

  if (previewBundle) return previewJson(publicDashboardStatus(previewBundle.status));

  const relay = process.env.STATUS_RELAY_URL;
  if (relay) {
    try {
      const response = await fetch(relay, {
        cache: "no-store",
        headers: { Accept: "application/json" },
        signal: AbortSignal.timeout(4_000),
      });
      const payload = await response.json() as Record<string, unknown>;
      return NextResponse.json({
        ...publicDashboardStatus(applyFreshness(payload)), observation_scope: "RELAY",
      }, {
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
