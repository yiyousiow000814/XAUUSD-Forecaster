import { env } from "cloudflare:workers";
import { NextResponse } from "next/server";
import { isIngestAuthorized } from "../_shared/ingest-auth";
import { previewBundle, previewJson, rejectPreviewWrite } from "../_shared/preview";
import { writeDashboardSnapshot } from "../_shared/dashboard-snapshot";

export const dynamic = "force-dynamic";

export async function GET() {
  // The fixed-size first page lives in the snapshot table. Older generations
  // and curve points are fetched from /api/learning-history only on demand.
  try {
    const binding = env.DB as D1Database | undefined;
    if (binding) {
      const row = await binding
        .prepare("SELECT payload FROM dashboard_snapshots WHERE id = ?")
        .bind(3)
        .first<{ payload: string }>();
      if (row) {
        // POST validates the snapshot before storing it. Returning the validated
        // JSON bytes directly avoids parsing and serializing a growing history on
        // every poll, which can exceed the Worker CPU limit.
        return new Response(row.payload, {
          headers: {
            "Content-Type": "application/json; charset=utf-8",
            "Cache-Control": "private, max-age=15",
            ...(previewBundle ? { "X-Aurum-Preview": "read-only-d1-snapshot" } : {}),
          },
        });
      }
    }
  } catch {
    // A read failure may use the immutable Preview summary or compact relay.
  }

  if (previewBundle?.learning_summary) {
    return previewJson(previewBundle.learning_summary);
  }

  // The relay carries the small live-status heartbeat.  It deliberately keeps
  // only active model versions, so it is a fallback—not the authority for the
  // append-only learning records stored in D1.
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
        generated_at: payload.generated_at,
        learning_curves: payload.learning_curves ?? {},
        execution_learning: payload.execution_learning ?? {},
      }, { status: response.status });
    } catch {
      return NextResponse.json({ error: "学习历史与本机后备服务均不可用" }, { status: 503 });
    }
  }

  return NextResponse.json({ error: "等待学习数据首次同步" }, { status: 503 });
}

export async function POST(request: Request) {
  const previewRejection = rejectPreviewWrite();
  if (previewRejection) return previewRejection;
  if (!await isIngestAuthorized(request)) {
    return NextResponse.json({ error: "unauthorized" }, { status: 401 });
  }
  const binding = env.DB as D1Database | undefined;
  if (!binding) return NextResponse.json({ error: "database unavailable" }, { status: 503 });
  const writeResult = await writeDashboardSnapshot(request, binding, 3);
  if (writeResult === "too_large") {
    return NextResponse.json({ error: "payload too large" }, { status: 413 });
  }
  if (writeResult === "invalid") {
    return NextResponse.json({ error: "invalid learning payload" }, { status: 400 });
  }
  return NextResponse.json({ status: "OK" });
}
