import { env } from "cloudflare:workers";
import { NextResponse } from "next/server";

export const dynamic = "force-dynamic";

export async function GET() {
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
      return NextResponse.json({ error: "本机图表数据服务未运行" }, { status: 503 });
    }
  }

  const binding = env.DB as D1Database | undefined;
  if (!binding) return NextResponse.json({ error: "database unavailable" }, { status: 503 });
  const row = await binding
    .prepare("SELECT payload FROM dashboard_snapshots WHERE id = ?")
    .bind(2)
    .first<{ payload: string }>();
  if (!row) return NextResponse.json({ error: "等待图表首次同步" }, { status: 503 });
  return NextResponse.json(JSON.parse(row.payload), {
    headers: { "Cache-Control": "private, max-age=15" },
  });
}

export async function POST(request: Request) {
  const expected = process.env.INGEST_TOKEN;
  const provided = request.headers.get("authorization")?.replace(/^Bearer\s+/i, "");
  if (!expected || !provided || provided !== expected) {
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
