import { env } from "cloudflare:workers";
import { NextResponse } from "next/server";
import { previewBundle, previewJson } from "../_shared/preview";

export const dynamic = "force-dynamic";

const requestValues = (request: Request) => {
  const params = new URL(request.url).searchParams;
  const query = (params.get("q") ?? "").trim().replace(/\s+/g, " ").slice(0, 80);
  const page = Math.max(1, Number.parseInt(params.get("page") ?? "1", 10) || 1);
  const pageSize = Math.min(20, Math.max(1, Number.parseInt(params.get("limit") ?? "10", 10) || 10));
  return { query, page, pageSize };
};

const matches = (row: Record<string, unknown>, query: string) => {
  const haystack = [row.headline, row.source, row.emerging_topic_zh, row.impact_reason_zh]
    .map(value => String(value ?? "").toLocaleLowerCase("zh-CN")).join("\n");
  return query.toLocaleLowerCase("zh-CN").split(" ").every(token => haystack.includes(token));
};

export async function GET(request: Request) {
  const { query, page, pageSize } = requestValues(request);
  if (!query) return NextResponse.json({ items: [], total: 0, page, page_size: pageSize, query });
  if (previewBundle) {
    const filtered = (previewBundle.news_index.items ?? []).filter(row => matches(row, query));
    const offset = (page - 1) * pageSize;
    return previewJson({ items: filtered.slice(offset, offset + pageSize), total: filtered.length, page, page_size: pageSize, query });
  }
  const binding = env.DB as D1Database | undefined;
  if (!binding) return NextResponse.json({ error: "新闻搜索暂不可用" }, { status: 503 });
  try {
    const tokens = query.split(" ").slice(0, 6);
    const searchable = `lower(COALESCE(json_extract(payload,'$.headline'),'') || ' ' ||
      COALESCE(json_extract(payload,'$.source'),'') || ' ' ||
      COALESCE(json_extract(payload,'$.emerging_topic_zh'),'') || ' ' ||
      COALESCE(json_extract(payload,'$.impact_reason_zh'),''))`;
    const clauses = tokens.map(() => `${searchable} LIKE ? ESCAPE '\\'`);
    const values = tokens.map(token => `%${token.toLocaleLowerCase("zh-CN").replace(/[\\%_]/g, "\\$&")}%`);
    const where = clauses.join(" AND ");
    const offset = (page - 1) * pageSize;
    const [rows, count] = await Promise.all([
      binding.prepare(`SELECT payload FROM news_index WHERE ${where}
        ORDER BY collector_first_seen_time DESC,detail_key DESC LIMIT ? OFFSET ?`)
        .bind(...values, pageSize, offset).all<{ payload: string }>(),
      binding.prepare(`SELECT count(*) count FROM news_index WHERE ${where}`)
        .bind(...values).first<{ count: number }>(),
    ]);
    return NextResponse.json({
      items: rows.results.map(row => JSON.parse(row.payload)), total: count?.count ?? 0,
      page, page_size: pageSize, query,
    }, { headers: { "Cache-Control": "no-store, max-age=0" } });
  } catch {
    return NextResponse.json({ error: "新闻搜索暂不可用" }, { status: 503 });
  }
}
