import { env } from "cloudflare:workers";
import { NextResponse } from "next/server";
import { applyFreshness } from "./freshness.js";

export const dynamic = "force-dynamic";

type DashboardPayload = {
  generated_at?: string;
  system?: { online?: boolean; quote_age_seconds?: number | null };
};

export async function GET() {
  const relay = process.env.STATUS_RELAY_URL;
  if (relay) {
    try {
      const response = await fetch(relay, {
        cache: "no-store",
        headers: { Accept: "application/json" },
        signal: AbortSignal.timeout(4_000),
      });
      const payload = (await response.json()) as DashboardPayload;
      return NextResponse.json(applyFreshness(payload), {
        status: response.status,
        headers: { "Cache-Control": "no-store, max-age=0" },
      });
    } catch {
      return NextResponse.json({ error: "本机数据服务未运行" }, { status: 503 });
    }
  }

  try {
    const binding = env.DB as D1Database | undefined;
    if (!binding) throw new Error("Dashboard database is unavailable");
    const row = await binding
      .prepare("SELECT payload FROM dashboard_snapshots WHERE id = ?")
      .bind(1)
      .first<{ payload: string }>();
    if (!row) {
      return NextResponse.json({ error: "等待本机首次同步" }, { status: 503 });
    }
    return NextResponse.json(applyFreshness(JSON.parse(row.payload)), {
      headers: { "Cache-Control": "no-store, max-age=0" },
    });
  } catch (reason) {
    return NextResponse.json(
      { error: reason instanceof Error ? reason.message : "无法读取状态" },
      { status: 503 },
    );
  }
}
