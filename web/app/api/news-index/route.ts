import { env } from "cloudflare:workers";
import { NextResponse } from "next/server";
import { isIngestAuthorized } from "../_shared/ingest-auth";
import { previewBundle, previewJson, rejectPreviewWrite } from "../_shared/preview";

export const dynamic = "force-dynamic";

type NewsIndexItem = {
  detail_key?: unknown;
  category?: unknown;
  collector_first_seen_time?: unknown;
  source_published_time?: unknown;
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
  // The first unfiltered page is the branch-aware immutable snapshot. Other
  // pages and filters are read-only D1 queries; returning an empty sliced
  // bundle here made Preview look as if the rest of the archive did not exist.
  const inlinePreviewItems = previewBundle?.news_index.items ?? [];
  if (previewBundle && page === 1 && !category && pageSize <= inlinePreviewItems.length) {
    return previewJson({
      ...previewBundle.news_index,
      items: inlinePreviewItems.slice(0, pageSize),
      total: Number(previewBundle.news_index.total ?? inlinePreviewItems.length),
      page,
      page_size: pageSize,
    });
  }
  try {
    const binding = env.DB as D1Database | undefined;
    if (binding) {
      const readableEvidence = `
        json_extract(payload, '$.content_status') IN ('FULL_TEXT', 'SOURCE_CONTENT')
        AND COALESCE(json_extract(payload, '$.model_visibility'), 'COLLECT_ONLY') <> 'COLLECT_ONLY'`;
      const parsedEvidence = `${readableEvidence}
        AND COALESCE(json_extract(payload, '$.parsed_at'), '') <> ''`;
      const modelCandidateEvidence = `${parsedEvidence}
        AND json_extract(payload, '$.model_visibility') = 'MODEL_VISIBLE'`;
      const where = category
        ? `WHERE ${readableEvidence} AND category = ?`
        : `WHERE ${readableEvidence}`;
      const bindValues = category ? [category] : [];
      const offset = (page - 1) * pageSize;
      const [rows, totalRow, allTotalRow, parsedTotalRow, modelCandidateTotalRow, categoryRows] = await Promise.all([
        binding.prepare(
          `SELECT payload FROM news_index ${where}
           ORDER BY COALESCE(json_extract(payload, '$.source_published_time'),
                             collector_first_seen_time) DESC,
                    collector_first_seen_time DESC, detail_key DESC LIMIT ? OFFSET ?`,
        ).bind(...bindValues, pageSize, offset).all<{ payload: string }>(),
        binding.prepare(`SELECT count(*) AS count FROM news_index ${where}`)
          .bind(...bindValues).first<{ count: number }>(),
        binding.prepare(`SELECT count(*) AS count FROM news_index WHERE ${readableEvidence}`)
          .first<{ count: number }>(),
        binding.prepare(`SELECT count(*) AS count FROM news_index WHERE ${parsedEvidence}`)
          .first<{ count: number }>(),
        binding.prepare(`SELECT count(*) AS count FROM news_index WHERE ${modelCandidateEvidence}`)
          .first<{ count: number }>(),
        binding.prepare(
          `SELECT category, count(*) AS count FROM news_index
           WHERE ${readableEvidence} GROUP BY category`,
        ).all<{ category: string; count: number }>(),
      ]);
      return NextResponse.json({
        items: rows.results.map(row => JSON.parse(row.payload)),
        total: totalRow?.count ?? 0,
        all_total: allTotalRow?.count ?? 0,
        readable_total: allTotalRow?.count ?? 0,
        parsed_total: parsedTotalRow?.count ?? 0,
        model_candidate_total: modelCandidateTotalRow?.count ?? 0,
        category_counts: Object.fromEntries(
          categoryRows.results.map(row => [row.category, row.count]),
        ),
        page,
        page_size: pageSize,
      }, { headers: { "Cache-Control": "no-store, max-age=0" } });
    }
  } catch {
    // Fall through to the relay when D1 is temporarily unavailable.
  }

  // A Preview is allowed to read the shared archive, but it must never replace
  // a failed archive page with the relay's tiny recent-news window. That would
  // turn a transient D1 error into a convincing but false empty result.
  if (previewBundle) {
    return previewJson({ error: "新闻档案暂时不可用，请稍后重试" }, 503);
  }

  const relay = process.env.STATUS_RELAY_URL;
  if (relay) {
    try {
      const response = await fetch(relay, {
        cache: "no-store",
        headers: { Accept: "application/json" },
        signal: AbortSignal.timeout(4_000),
      });
      const payload = await response.json() as { recent_news?: NewsIndexItem[] };
      const all = [...(payload.recent_news ?? [])].sort((left, right) => {
        const leftTime = String(left.source_published_time ?? left.collector_first_seen_time ?? "");
        const rightTime = String(right.source_published_time ?? right.collector_first_seen_time ?? "");
        return rightTime.localeCompare(leftTime);
      });
      const categoryCounts = Object.fromEntries(
        [...new Set(all.map(row => String(row.category ?? "其他")))].map(name => [
          name, all.filter(row => String(row.category ?? "其他") === name).length,
        ]),
      );
      const filtered = category ? all.filter(row => row.category === category) : all;
      const parsedTotal = all.filter(row => typeof row.parsed_at === "string" && row.parsed_at.length > 0).length;
      const modelCandidateTotal = all.filter(row => row.model_visibility === "MODEL_VISIBLE").length;
      const offset = (page - 1) * pageSize;
      return NextResponse.json({
        items: filtered.slice(offset, offset + pageSize),
        total: filtered.length,
        all_total: all.length,
        readable_total: all.length,
        parsed_total: parsedTotal,
        model_candidate_total: modelCandidateTotal,
        category_counts: categoryCounts,
        page,
        page_size: pageSize,
      }, { status: response.status, headers: { "Cache-Control": "no-store, max-age=0" } });
    } catch {
      // Return a single public-facing error below.
    }
  }

  return NextResponse.json({ error: "等待公开新闻索引" }, { status: 503 });
}

export async function POST(request: Request) {
  const previewRejection = rejectPreviewWrite();
  if (previewRejection) return previewRejection;
  if (!await isIngestAuthorized(request)) {
    return NextResponse.json({ error: "unauthorized" }, { status: 401 });
  }
  const serialized = await request.text();
  if (new TextEncoder().encode(serialized).byteLength > 450_000) {
    return NextResponse.json({ error: "payload too large" }, { status: 413 });
  }
  const binding = env.DB as D1Database | undefined;
  if (!binding) return NextResponse.json({ error: "database unavailable" }, { status: 503 });
  try {
    const body = JSON.parse(serialized) as { items?: NewsIndexItem[]; reset?: unknown };
    if (body.reset === true) {
      await binding.prepare("DELETE FROM news_index").run();
      return NextResponse.json({ status: "OK", reset: true });
    }
    if (!Array.isArray(body.items) || body.items.length > 200) {
      return NextResponse.json({ error: "invalid news index batch" }, { status: 400 });
    }
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
