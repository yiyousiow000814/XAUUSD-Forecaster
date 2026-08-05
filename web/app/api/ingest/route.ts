import { env } from "cloudflare:workers";
import { NextResponse } from "next/server";

export async function POST(request: Request) {
  const expected = process.env.INGEST_TOKEN;
  const provided = request.headers.get("authorization")?.replace(/^Bearer\s+/i, "");
  if (!expected || !provided || provided !== expected) {
    return NextResponse.json({ error: "unauthorized" }, { status: 401 });
  }
  const payload = await request.json();
  const serialized = JSON.stringify(payload);
  if (serialized.length > 500_000) {
    return NextResponse.json({ error: "payload too large" }, { status: 413 });
  }
  const binding = env.DB as D1Database | undefined;
  if (!binding) {
    return NextResponse.json({ error: "database unavailable" }, { status: 503 });
  }
  await binding
    .prepare(
      `INSERT INTO dashboard_snapshots (id, payload, received_at)
       VALUES (?, ?, ?)
       ON CONFLICT(id) DO UPDATE SET
         payload=excluded.payload, received_at=excluded.received_at`,
    )
    .bind(1, serialized, new Date().toISOString())
    .run();
  return NextResponse.json({ status: "OK" });
}
