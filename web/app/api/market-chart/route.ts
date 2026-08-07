import { env } from "cloudflare:workers";
import { NextResponse } from "next/server";
import { isIngestAuthorized } from "../_shared/ingest-auth";

export const dynamic = "force-dynamic";

export async function GET() {
  try {
    const binding = env.DB as D1Database | undefined;
    if (binding) {
      const row = await binding
        .prepare("SELECT payload FROM dashboard_snapshots WHERE id = ?")
        .bind(2)
        .first<{ payload: string }>();
      if (row) {
        return NextResponse.json(JSON.parse(row.payload), {
          headers: { "Cache-Control": "no-store, max-age=0" },
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
    ).bind(2, serialized, new Date().toISOString()).run();
    return NextResponse.json({ status: "OK" });
  } catch {
    return NextResponse.json({ error: "invalid market chart payload" }, { status: 400 });
  }
}
