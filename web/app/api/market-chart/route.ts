import { env } from "cloudflare:workers";
import { NextResponse } from "next/server";
import { isIngestAuthorized } from "../_shared/ingest-auth";
import { previewBundle, previewJson, rejectPreviewWrite } from "../_shared/preview";
import { writeDashboardSnapshot } from "../_shared/dashboard-snapshot";

export const dynamic = "force-dynamic";

export async function GET() {
  if (previewBundle) return previewJson(previewBundle.market_chart);
  try {
    const binding = env.DB as D1Database | undefined;
    if (binding) {
      const row = await binding
        .prepare("SELECT payload FROM dashboard_snapshots WHERE id = ?")
        .bind(2)
        .first<{ payload: string }>();
      if (row) {
        // POST validates the snapshot before storing it. Returning those bytes
        // directly avoids parsing and serializing a payload that can approach
        // the Worker request-size budget.
        return new Response(row.payload, {
          headers: {
            "Content-Type": "application/json; charset=utf-8",
            "Cache-Control": "private, max-age=15",
          },
        });
      }
    }
  } catch {
    // Fall through to the relay when D1 is temporarily unavailable.
  }

  const relay = process.env.STATUS_RELAY_URL;
  if (relay) {
    try {
      const response = await fetch(relay, {
        cache: "no-store",
        headers: { Accept: "application/json" },
        signal: AbortSignal.timeout(4_000),
      });
      const payload = await response.json() as { market_chart?: unknown };
      return NextResponse.json(payload.market_chart ?? {}, {
        status: response.status,
        headers: { "Cache-Control": "no-store, max-age=0" },
      });
    } catch {
      // Return a single public-facing error below.
    }
  }

  return NextResponse.json({ error: "等待公开图表快照" }, { status: 503 });
}

export async function POST(request: Request) {
  const previewRejection = rejectPreviewWrite();
  if (previewRejection) return previewRejection;
  if (!await isIngestAuthorized(request)) {
    return NextResponse.json({ error: "unauthorized" }, { status: 401 });
  }
  const binding = env.DB as D1Database | undefined;
  if (!binding) return NextResponse.json({ error: "database unavailable" }, { status: 503 });
  const writeResult = await writeDashboardSnapshot(request, binding, 2);
  if (writeResult === "too_large") {
    return NextResponse.json({ error: "payload too large" }, { status: 413 });
  }
  if (writeResult === "invalid") {
    return NextResponse.json({ error: "invalid market chart payload" }, { status: 400 });
  }
  return NextResponse.json({ status: "OK" });
}
