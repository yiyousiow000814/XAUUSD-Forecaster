import { env } from "cloudflare:workers";
import { NextResponse } from "next/server";
import { previewBundle, previewJson } from "../_shared/preview";
import { withPreviewIdentity } from "../_shared/preview-status";
import { publicDashboardStatus, readDashboardStatus } from "../_shared/dashboard-status";

export const dynamic = "force-dynamic";

export async function GET() {
  // Public viewers must not depend on a direct connection to the owner's PC.
  // The synchronizer writes the authoritative public snapshot to D1; the
  // local relay is only a last-resort fallback before the first sync.
  const current = await readDashboardStatus(
    env.DB as D1Database | undefined,
    process.env.STATUS_RELAY_URL,
  );
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

  return NextResponse.json({ error: "等待公开状态快照" }, { status: 503 });
}
