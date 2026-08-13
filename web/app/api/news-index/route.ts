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
  cluster_id?: unknown;
  parsed_at?: unknown;
  model_visibility?: unknown;
  impact_expires_at?: unknown;
  mirror_contract?: unknown;
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
  // D1 is the source of truth even on the first Preview page. Returning the
  // immutable build bundle here freezes the visible total at build time while
  // the bounded archive continues to grow.
  try {
    const binding = env.DB as D1Database | undefined;
    if (binding) {
      const where = category ? "WHERE category = ?" : "";
      const bindValues = category ? [category] : [];
      const offset = (page - 1) * pageSize;
      const now = new Date().toISOString();
      const [rows, totalRow, totalsRow, categoryRows] = await Promise.all([
        binding.prepare(
          `SELECT payload FROM news_index ${where}
           ORDER BY published_time DESC,
                    collector_first_seen_time DESC, detail_key DESC LIMIT ? OFFSET ?`,
        ).bind(...bindValues, pageSize, offset).all<{ payload: string }>(),
        binding.prepare(`SELECT count(*) AS count FROM news_index ${where}`)
          .bind(...bindValues).first<{ count: number }>(),
        binding.prepare(
          `SELECT count(*) AS count, COALESCE(sum(parsed), 0) AS parsed,
                  COALESCE(sum(CASE WHEN model_candidate=1
                    AND (impact_expires_at IS NULL OR impact_expires_at='' OR impact_expires_at>?)
                    THEN 1 ELSE 0 END), 0) AS model_candidate
           FROM news_index`,
        ).bind(now).first<{ count: number; parsed: number; model_candidate: number }>(),
        binding.prepare(
          `SELECT category, count(*) AS count FROM news_index GROUP BY category`,
        ).all<{ category: string; count: number }>(),
      ]);
      return NextResponse.json({
        items: rows.results.map(row => {
          const item = JSON.parse(row.payload) as NewsIndexItem;
          if (
            item.model_visibility === "MODEL_VISIBLE"
            && typeof item.impact_expires_at === "string"
            && item.impact_expires_at <= now
          ) {
            item.model_visibility = "IMPACT_EXPIRED";
            item.impact_status = "EXPIRED";
          }
          return item;
        }),
        total: totalRow?.count ?? 0,
        all_total: totalsRow?.count ?? 0,
        readable_total: totalsRow?.count ?? 0,
        parsed_total: totalsRow?.parsed ?? 0,
        model_candidate_total: totalsRow?.model_candidate ?? 0,
        category_counts: Object.fromEntries(
          categoryRows.results.map(row => [row.category, row.count]),
        ),
        page,
        page_size: pageSize,
        window_days: 60,
        totals_scope: "D1_ARCHIVE",
      }, { headers: { "Cache-Control": "public, max-age=15, s-maxage=30, stale-while-revalidate=120" } });
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
        totals_scope: "RECENT_WINDOW",
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
    const body = JSON.parse(serialized) as {
      items?: NewsIndexItem[]; reset?: unknown; prune_before?: unknown;
      reconcile_contract?: unknown;
    };
    if (body.reset === true) {
      await binding.prepare("DELETE FROM news_index").run();
      return NextResponse.json({ status: "OK", reset: true });
    }
    if (typeof body.prune_before === "string" || typeof body.reconcile_contract === "string") {
      const statements: D1PreparedStatement[] = [];
      if (typeof body.prune_before === "string") {
        if (Number.isNaN(Date.parse(body.prune_before))) {
          return NextResponse.json({ error: "invalid prune cutoff" }, { status: 400 });
        }
        statements.push(
          binding.prepare("DELETE FROM news_index WHERE published_time < ?").bind(body.prune_before),
        );
      }
      if (typeof body.reconcile_contract === "string") {
        statements.push(
          binding.prepare("DELETE FROM news_index WHERE mirror_contract <> ?")
            .bind(body.reconcile_contract),
        );
      }
      statements.push(binding.prepare(
        "DELETE FROM news_details WHERE NOT EXISTS (SELECT 1 FROM news_index WHERE news_index.detail_key = news_details.detail_key)",
      ));
      const results = await binding.batch(statements);
      return NextResponse.json({
        status: "OK", removed: results.reduce((sum, result) => sum + (result.meta.changes ?? 0), 0),
      });
    }
    if (!Array.isArray(body.items) || body.items.length > 20) {
      return NextResponse.json({ error: "invalid news index batch" }, { status: 400 });
    }
    const now = new Date().toISOString();
    const statements = body.items.flatMap(item => {
      if (
        typeof item.detail_key !== "string"
        || !/^[a-f0-9]{64}$/.test(item.detail_key)
        || typeof item.category !== "string"
        || typeof item.collector_first_seen_time !== "string"
        || typeof item.cluster_id !== "string"
        || typeof item.mirror_contract !== "string"
      ) throw new Error("invalid news index item");
      const publishedTime = typeof item.source_published_time === "string"
        ? item.source_published_time : item.collector_first_seen_time;
      return [binding.prepare(
        "DELETE FROM news_index WHERE cluster_id = ? AND detail_key <> ?",
      ).bind(item.cluster_id, item.detail_key), binding.prepare(
        `INSERT INTO news_index
          (detail_key, category, cluster_id, published_time, collector_first_seen_time,
           parsed, model_candidate, impact_expires_at, mirror_contract, payload, received_at)
         VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
         ON CONFLICT(detail_key) DO UPDATE SET
           category=excluded.category,
           cluster_id=excluded.cluster_id,
           published_time=excluded.published_time,
           collector_first_seen_time=excluded.collector_first_seen_time,
           parsed=excluded.parsed,
           model_candidate=excluded.model_candidate,
           impact_expires_at=excluded.impact_expires_at,
           mirror_contract=excluded.mirror_contract,
           payload=excluded.payload,
           received_at=excluded.received_at`,
      ).bind(
        item.detail_key, item.category, item.cluster_id, publishedTime,
        item.collector_first_seen_time, typeof item.parsed_at === "string" ? 1 : 0,
        item.model_visibility === "MODEL_VISIBLE" ? 1 : 0,
        typeof item.impact_expires_at === "string" ? item.impact_expires_at : null,
        item.mirror_contract, JSON.stringify(item), now,
      )];
    });
    if (statements.length) await binding.batch(statements);
    return NextResponse.json({ status: "OK", received: body.items.length });
  } catch (reason) {
    return NextResponse.json(
      { error: reason instanceof Error ? reason.message : "invalid news index item" },
      { status: 400 },
    );
  }
}
