import { env } from "cloudflare:workers";
import { NextResponse } from "next/server";
import { isIngestAuthorized } from "../_shared/ingest-auth";
import { previewBundle, previewJson, rejectPreviewWrite } from "../_shared/preview";
import {
  ACTIVE_NEWS_SQL,
  NEWS_REVIEW_STATE_INVARIANT_SQL,
  NEWS_REVIEW_STATES,
  newsReviewStateInvariantHolds,
  newsReviewStateOf,
  parseNewsReviewState,
  type NewsReviewState,
} from "../../_lib/news-review-state";
import { publicNewsRecord } from "../../_lib/public-news-copy";

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
  annotation_status?: unknown;
  [key: string]: unknown;
};

const pageRequest = (request: Request) => {
  const query = new URL(request.url).searchParams;
  const page = Math.max(1, Number.parseInt(query.get("page") ?? "1", 10) || 1);
  const pageSize = Math.min(50, Math.max(1, Number.parseInt(query.get("limit") ?? "12", 10) || 12));
  return {
    page, pageSize,
    category: query.get("category")?.trim() ?? "",
    reviewState: parseNewsReviewState(query.get("review_state")),
  };
};

const REVIEW_STATE_SQL: Record<NewsReviewState, string> = {
  COMPLETED: "json_extract(payload, '$.annotation_status') IN ('READY','NOT_REQUIRED')",
  ISOLATED: "json_extract(payload, '$.annotation_status') IN ('DEAD_LETTER','CONTENT_UNAVAILABLE')",
  PROCESSING: "COALESCE(json_extract(payload, '$.annotation_status'), '') NOT IN ('READY','NOT_REQUIRED','DEAD_LETTER','CONTENT_UNAVAILABLE')",
};
export async function GET(request: Request) {
  const query = new URL(request.url).searchParams;
  const healthCheck = query.get("health_check") === "1";
  const expectedContract = query.get("expected_contract")?.trim() ?? "";
  const { page, pageSize, category, reviewState } = pageRequest(request);
  if (reviewState === null) {
    return NextResponse.json({ error: "invalid review state" }, { status: 400 });
  }
  // D1 is the source of truth even on the first Preview page. Returning the
  // immutable build bundle here freezes the visible total at build time while
  // the bounded archive continues to grow.
  try {
    const binding = env.DB as D1Database | undefined;
    if (!binding && healthCheck) {
      return NextResponse.json({
        status: "ERROR",
        error_code: "NEWS_MIRROR_HEALTH_UNAVAILABLE",
        error: "DatabaseUnavailable",
      }, { status: 503, headers: { "Cache-Control": "no-store, max-age=0" } });
    }
    if (binding) {
      if (healthCheck) {
        const checks = await binding.prepare(
          `SELECT 'NEWS_REVIEW_STATE_INVALID' AS code, count(*) AS count
             FROM news_index
            WHERE ${ACTIVE_NEWS_SQL} AND NOT ${NEWS_REVIEW_STATE_INVARIANT_SQL}
           UNION ALL
           SELECT 'NEWS_DETAIL_MISSING', count(*)
             FROM news_index i
            WHERE ${ACTIVE_NEWS_SQL.replaceAll("payload", "i.payload")}
              AND NOT EXISTS (
                SELECT 1 FROM news_details d WHERE d.detail_key=i.detail_key
              )
           UNION ALL
           SELECT 'NEWS_PARSED_FLAG_MISMATCH', count(*)
             FROM news_index
            WHERE ${ACTIVE_NEWS_SQL}
              AND parsed <> CASE
                WHEN json_extract(payload, '$.parsed_at') IS NOT NULL THEN 1 ELSE 0 END
           UNION ALL
           SELECT 'NEWS_CANDIDATE_FLAG_MISMATCH', count(*)
             FROM news_index
            WHERE ${ACTIVE_NEWS_SQL}
              AND model_candidate <> CASE
                WHEN json_extract(payload, '$.model_visibility')='MODEL_VISIBLE'
                THEN 1 ELSE 0 END
           UNION ALL
           SELECT 'NEWS_DUPLICATE_ACTIVE_CLUSTER', count(*) FROM (
             SELECT cluster_id FROM news_index
              WHERE ${ACTIVE_NEWS_SQL}
              GROUP BY cluster_id HAVING count(*) > 1
           )`,
        ).all<{ code: string; count: number }>();
        const failures = checks.results
          .map(row => ({ code: row.code, count: Number(row.count ?? 0) }))
          .filter(row => row.count > 0);
        if (expectedContract) {
          const contractRow = await binding.prepare(
            `SELECT count(*) AS count FROM news_index
              WHERE ${ACTIVE_NEWS_SQL} AND mirror_contract <> ?`,
          ).bind(expectedContract).first<{ count: number }>();
          const count = Number(contractRow?.count ?? 0);
          if (count > 0) failures.push({ code: "NEWS_MIRROR_CONTRACT_STALE", count });
        }
        const violations = failures.reduce((sum, row) => sum + row.count, 0);
        return NextResponse.json({
          status: violations ? "ERROR" : "OK",
          error_code: violations ? "NEWS_MIRROR_STATE_INVARIANT_VIOLATION" : null,
          violation_count: violations,
          checks: failures.slice(0, 12),
        }, { headers: { "Cache-Control": "no-store, max-age=0" } });
      }
      const conditions = [ACTIVE_NEWS_SQL, REVIEW_STATE_SQL[reviewState]];
      const bindValues: string[] = [];
      if (category) {
        conditions.push("category = ?");
        bindValues.push(category);
      }
      const where = `WHERE ${conditions.join(" AND ")}`;
      const reviewWhere = `WHERE ${ACTIVE_NEWS_SQL} AND ${REVIEW_STATE_SQL[reviewState]}`;
      const offset = (page - 1) * pageSize;
      const now = new Date().toISOString();
      const [rows, totalRow, totalsRow, categoryRows, reviewRows] = await Promise.all([
        binding.prepare(
          `SELECT payload FROM news_index ${where}
           ORDER BY published_time DESC,
                    collector_first_seen_time DESC, detail_key DESC LIMIT ? OFFSET ?`,
        ).bind(...bindValues, pageSize, offset).all<{ payload: string }>(),
        binding.prepare(`SELECT count(*) AS count FROM news_index ${where}`)
          .bind(...bindValues).first<{ count: number }>(),
        binding.prepare(
          `SELECT count(*) AS count,
                  COALESCE(sum(CASE WHEN ${ACTIVE_NEWS_SQL} THEN parsed ELSE 0 END), 0) AS parsed,
                  COALESCE(sum(CASE WHEN ${ACTIVE_NEWS_SQL} AND model_candidate=1
                    AND (impact_expires_at IS NULL OR impact_expires_at='' OR impact_expires_at>?)
                    THEN 1 ELSE 0 END), 0) AS model_candidate
           FROM news_index`,
        ).bind(now).first<{ count: number; parsed: number; model_candidate: number }>(),
        binding.prepare(
          `SELECT category, count(*) AS count FROM news_index ${reviewWhere}
           GROUP BY category`,
        ).all<{ category: string; count: number }>(),
        binding.prepare(
          `SELECT CASE
             WHEN json_extract(payload, '$.annotation_status') IN ('READY','NOT_REQUIRED') THEN 'COMPLETED'
             WHEN json_extract(payload, '$.annotation_status') IN ('DEAD_LETTER','CONTENT_UNAVAILABLE') THEN 'ISOLATED'
             ELSE 'PROCESSING' END AS review_state, count(*) AS count
           FROM news_index WHERE ${ACTIVE_NEWS_SQL} GROUP BY review_state`,
        ).all<{ review_state: NewsReviewState; count: number }>(),
      ]);
      const payload = {
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
          return publicNewsRecord(item) as NewsIndexItem;
        }),
        total: totalRow?.count ?? 0,
        all_total: totalsRow?.count ?? 0,
        readable_total: totalsRow?.count ?? 0,
        parsed_total: totalsRow?.parsed ?? 0,
        model_candidate_total: totalsRow?.model_candidate ?? 0,
        category_counts: Object.fromEntries(
          categoryRows.results.map(row => [row.category, row.count]),
        ),
        review_state: reviewState,
        review_state_counts: Object.fromEntries(
          NEWS_REVIEW_STATES.map(state => [
            state,
            reviewRows.results.find(row => row.review_state === state)?.count ?? 0,
          ]),
        ),
        page,
        page_size: pageSize,
        window_days: 60,
        totals_scope: "D1_ARCHIVE",
      };
      if (previewBundle) return previewJson(payload, 200, "read-only-d1-archive");
      return NextResponse.json(payload, {
        headers: { "Cache-Control": "public, max-age=15, s-maxage=30, stale-while-revalidate=120" },
      });
    }
  } catch (error) {
    if (healthCheck) {
      return NextResponse.json({
        status: "ERROR",
        error_code: "NEWS_MIRROR_HEALTH_UNAVAILABLE",
        error: error instanceof Error ? error.name : "UnknownError",
      }, { status: 503, headers: { "Cache-Control": "no-store, max-age=0" } });
    }
    // Fall through to the relay when D1 is temporarily unavailable.
  }

  // A Preview is allowed to read the shared archive, but it must never replace
  // a failed archive page with the relay's tiny recent-news window. That would
  // turn a transient D1 error into a convincing but false empty result.
  if (previewBundle) {
    return previewJson(
      { error: "新闻档案暂时不可用，请稍后重试" },
      503,
      "current-read-unavailable",
    );
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
      const all = [...(payload.recent_news ?? [])]
        .map(item => publicNewsRecord(item) as NewsIndexItem)
        .sort((left, right) => {
          const leftTime = String(left.source_published_time ?? left.collector_first_seen_time ?? "");
          const rightTime = String(right.source_published_time ?? right.collector_first_seen_time ?? "");
          return rightTime.localeCompare(leftTime);
        });
      const stateItems = all.filter(row => newsReviewStateOf(row) === reviewState);
      const categoryCounts = Object.fromEntries(
        [...new Set(stateItems.map(row => String(row.category ?? "其他")))].map(name => [
          name, stateItems.filter(row => String(row.category ?? "其他") === name).length,
        ]),
      );
      const filtered = category
        ? stateItems.filter(row => row.category === category)
        : stateItems;
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
        review_state: reviewState,
        review_state_counts: Object.fromEntries(
          NEWS_REVIEW_STATES.map(state => [
            state, all.filter(row => newsReviewStateOf(row) === state).length,
          ]),
        ),
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
      reconcile_contract?: unknown; withdraw_detail_keys?: unknown;
      neutralize_operational_state_for_contract?: unknown;
    };
    if (body.reset === true) {
      await binding.prepare("DELETE FROM news_index").run();
      return NextResponse.json({ status: "OK", reset: true });
    }
    if (body.withdraw_detail_keys !== undefined) {
      if (
        !Array.isArray(body.withdraw_detail_keys)
        || body.withdraw_detail_keys.length > 20
        || body.withdraw_detail_keys.some(
          key => typeof key !== "string" || !/^[a-f0-9]{64}$/.test(key),
        )
      ) {
        return NextResponse.json({ error: "invalid news withdrawal batch" }, { status: 400 });
      }
      const keys = body.withdraw_detail_keys as string[];
      const statements = keys.flatMap(key => [
        binding.prepare("DELETE FROM news_index WHERE detail_key = ?").bind(key),
        binding.prepare("DELETE FROM news_details WHERE detail_key = ?").bind(key),
      ]);
      const results = statements.length ? await binding.batch(statements) : [];
      return NextResponse.json({
        status: "OK",
        withdrawn: results.reduce(
          (sum, result) => sum + (result.meta.changes ?? 0), 0,
        ),
      });
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
    if (typeof body.neutralize_operational_state_for_contract === "string") {
      if (!/^[a-z0-9][a-z0-9._-]{0,127}$/.test(body.neutralize_operational_state_for_contract)) {
        return NextResponse.json({ error: "invalid mirror contract" }, { status: 400 });
      }
      const contract = body.neutralize_operational_state_for_contract;
      const results = await binding.batch([
        binding.prepare(
          `UPDATE news_index SET model_candidate=0 WHERE mirror_contract <> ?`,
        ).bind(contract),
        binding.prepare(
          `UPDATE news_index
         SET parsed=0,
             payload=json_set(
               payload,
               '$.parsed_at', NULL,
               '$.model_visibility', 'NOT_YET_PARSED',
               '$.annotation_status', 'SUPERSEDED_CONTRACT',
               '$.annotation_reason_code', 'CONTRACT_HANDOVER_PENDING',
               '$.annotation_reason', '旧语义契约已退出当前视图',
               '$.impact_status', 'SUPERSEDED_CONTRACT'
             )
         WHERE mirror_contract <> ?
           AND NOT ${NEWS_REVIEW_STATE_INVARIANT_SQL}`,
        ).bind(contract),
      ]);
      return NextResponse.json({
        status: "OK",
        candidates_neutralized: results[0]?.meta.changes ?? 0,
        operational_states_neutralized: results[1]?.meta.changes ?? 0,
      });
    }
    if (!Array.isArray(body.items) || body.items.length > 20) {
      return NextResponse.json({ error: "invalid news index batch" }, { status: 400 });
    }
    if (body.items.some(item => !newsReviewStateInvariantHolds(item))) {
      return NextResponse.json({
        error: "news review state invariant violation",
        error_code: "NEWS_MIRROR_STATE_INVARIANT_VIOLATION",
      }, { status: 409 });
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
