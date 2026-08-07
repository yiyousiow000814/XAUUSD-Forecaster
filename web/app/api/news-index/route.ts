import { env } from "cloudflare:workers";
import { NextResponse } from "next/server";

export const dynamic = "force-dynamic";

type NewsIndexItem = {
  detail_key?: unknown;
  category?: unknown;
  collector_first_seen_time?: unknown;
  [key: string]: unknown;
};

const pageRequest = (request: Request) => {
  const query = new URL(request.url).searchParams;
  const page = Math.max(1, Number.parseInt(query.get("page") ?? "1", 10) || 1);
  const pageSize = Math.min(50, Math.max(1, Number.parseInt(query.get("limit") ?? "12", 10) || 12));
  return { page, pageSize, category: query.get("category")?.trim() ?? "" };
};

export async function GET(request: Request) {
  const { page, pageSize, category } = pageRequest(request);
  const relay = process.env.STATUS_RELAY_URL;
  if (relay) {
    try {
      const response = await fetch(relay, {
        cache: "no-store",
        headers: { Accept: "application/json" },
        signal: AbortSignal.timeout(4_000),
      });
      const payload = await response.json() as { recent_news?: NewsIndexItem[] };
      const all = payload.recent_news ?? [];
      const categoryCounts = Object.fromEntries(
        [...new Set(all.map(row => String(row.category ?? "其他")))].map(name => [
          name, all.filter(row => String(row.category ?? "其他") === name).length,
        ]),
      );
      const filtered = category ? all.filter(row => row.category === category) : all;
      const offset = (page - 1) * pageSize;
      return NextResponse.json({
        items: filtered.slice(offset, offset + pageSize),
        total: filtered.length,
        all_total: all.length,
        category_counts: categoryCounts,
        page,
        page_size: pageSize,
      }, { status: response.status });
    } catch {
      return NextResponse.json({ error: "本机新闻索引服务未运行" }, { status: 503 });
    }
  }

  const binding = env.DB as D1Database | undefined;
  if (!binding) return NextResponse.json({ error: "database unavailable" }, { status: 503 });
  const where = category ? "WHERE category = ?" : "";
  const bindValues = category ? [category] : [];
  const offset = (page - 1) * pageSize;
  const [rows, totalRow, allTotalRow, categoryRows] = await Promise.all([
    binding.prepare(
      `SELECT payload FROM news_index ${where}
       ORDER BY collector_first_seen_time DESC, detail_key DESC LIMIT ? OFFSET ?`,
    ).bind(...bindValues, pageSize, offset).all<{ payload: string }>(),
    binding.prepare(`SELECT count(*) AS count FROM news_index ${where}`)
      .bind(...bindValues).first<{ count: number }>(),
    binding.prepare("SELECT count(*) AS count FROM news_index").first<{ count: number }>(),
    binding.prepare(
      "SELECT category, count(*) AS count FROM news_index GROUP BY category",
    ).all<{ category: string; count: number }>(),
  ]);
  return NextResponse.json({
    items: rows.results.map(row => JSON.parse(row.payload)),
    total: totalRow?.count ?? 0,
    all_total: allTotalRow?.count ?? 0,
    category_counts: Object.fromEntries(
      categoryRows.results.map(row => [row.category, row.count]),
    ),
    page,
    page_size: pageSize,
  }, { headers: { "Cache-Control": "private, max-age=15" } });
}

export async function POST(request: Request) {
  const expected = process.env.INGEST_TOKEN;
  const provided = request.headers.get("authorization")?.replace(/^Bearer\s+/i, "");
  if (!expected || !provided || provided !== expected) {
    return NextResponse.json({ error: "unauthorized" }, { status: 401 });
  }
  const serialized = await request.text();
  if (new TextEncoder().encode(serialized).byteLength > 450_000) {
    return NextResponse.json({ error: "payload too large" }, { status: 413 });
  }
  const body = JSON.parse(serialized) as { items?: NewsIndexItem[] };
  if (!Array.isArray(body.items) || body.items.length > 200) {
    return NextResponse.json({ error: "invalid news index batch" }, { status: 400 });
  }
  const binding = env.DB as D1Database | undefined;
  if (!binding) return NextResponse.json({ error: "database unavailable" }, { status: 503 });
  try {
    const now = new Date().toISOString();
    const statements = body.items.map(item => {
      if (
        typeof item.detail_key !== "string"
        || !/^[a-f0-9]{64}$/.test(item.detail_key)
        || typeof item.category !== "string"
        || typeof item.collector_first_seen_time !== "string"
      ) throw new Error("invalid news index item");
      return binding.prepare(
        `INSERT INTO news_index
          (detail_key, category, collector_first_seen_time, payload, received_at)
         VALUES (?, ?, ?, ?, ?)
         ON CONFLICT(detail_key) DO UPDATE SET
           category=excluded.category,
           collector_first_seen_time=excluded.collector_first_seen_time,
           payload=excluded.payload,
           received_at=excluded.received_at`,
      ).bind(
        item.detail_key, item.category, item.collector_first_seen_time,
        JSON.stringify(item), now,
      );
    });
    if (statements.length) await binding.batch(statements);
    return NextResponse.json({ status: "OK", received: statements.length });
  } catch (reason) {
    return NextResponse.json(
      { error: reason instanceof Error ? reason.message : "invalid news index item" },
      { status: 400 },
    );
  }
}
