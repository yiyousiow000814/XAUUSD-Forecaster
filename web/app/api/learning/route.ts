import { env } from "cloudflare:workers";
import { NextResponse } from "next/server";
import { isIngestAuthorized } from "../_shared/ingest-auth";
import { previewBundle, previewJson, rejectPreviewWrite } from "../_shared/preview";

export const dynamic = "force-dynamic";

export async function GET() {
  if (previewBundle) return previewJson(previewBundle.learning);
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
        },
      });
    }
  }

  // The relay carries the small live-status heartbeat.  It deliberately keeps
  // only active model versions, so it is a fallback—not the authority for the
  // append-only learning history stored in D1.
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
  const serialized = await request.text();
  if (new TextEncoder().encode(serialized).byteLength > 800_000) {
    return NextResponse.json({ error: "payload too large" }, { status: 413 });
  }
  const binding = env.DB as D1Database | undefined;
  if (!binding) return NextResponse.json({ error: "database unavailable" }, { status: 503 });
  try {
    JSON.parse(serialized);
    await binding.prepare(
      `INSERT INTO dashboard_snapshots (id, payload, received_at)
       VALUES (?, ?, ?)
       ON CONFLICT(id) DO UPDATE SET
         payload=excluded.payload, received_at=excluded.received_at`,
    ).bind(3, serialized, new Date().toISOString()).run();
    return NextResponse.json({ status: "OK" });
  } catch {
    return NextResponse.json({ error: "invalid learning payload" }, { status: 400 });
  }
}
