import { env } from "cloudflare:workers";
import { NextResponse } from "next/server";
import { isIngestAuthorized } from "../_shared/ingest-auth";
import { readBoundedBody } from "../_shared/dashboard-snapshot";
import { previewBundle, previewJson, rejectPreviewWrite } from "../_shared/preview";
import { publicNewsRecord } from "../../_lib/public-news-copy";
import {
  authorizeReleaseValidation, isReleaseValidationContext, releaseValidationResponse,
} from "../_shared/release-validation";

export const dynamic = "force-dynamic";

const DETAIL_KEY_PATTERN = /^[a-f0-9]{64}$/;
const DETAIL_BATCH_LIMIT = 12;

type NewsDetailItem = {
  detail_key?: unknown;
  detail_hash?: unknown;
  payload?: unknown;
};

export async function GET(request: Request) {
  const query = new URL(request.url).searchParams;
  const detailKeys = [...new Set(
    (query.get("keys") ?? "").split(",").map(key => key.trim()).filter(Boolean),
  )];
  if (detailKeys.length) {
    if (
      detailKeys.length > DETAIL_BATCH_LIMIT
      || detailKeys.some(key => !DETAIL_KEY_PATTERN.test(key))
    ) {
      return NextResponse.json({ error: "invalid news detail keys" }, { status: 400 });
    }
    const items: Record<string, { detail_hash?: unknown; payload?: unknown }> = {};
    if (previewBundle) {
      for (const key of detailKeys) {
        if (previewBundle.news_details[key]) {
          items[key] = publicNewsRecord(
            previewBundle.news_details[key],
          ) as typeof items[string];
        }
      }
    }
    const missing = detailKeys.filter(key => !items[key]);
    const binding = env.DB as D1Database | undefined;
    if (missing.length && binding) {
      const placeholders = missing.map(() => "?").join(",");
      const rows = await binding.prepare(
        `SELECT detail_key, payload, detail_hash FROM news_details
         WHERE detail_key IN (${placeholders})`,
      ).bind(...missing).all<{ detail_key: string; payload: string; detail_hash: string }>();
      for (const row of rows.results) {
        items[row.detail_key] = publicNewsRecord({
          detail_hash: row.detail_hash,
          payload: JSON.parse(row.payload),
        }) as typeof items[string];
      }
    }
    return NextResponse.json({ items, missing: detailKeys.filter(key => !items[key]) }, {
      headers: { "Cache-Control": "private, max-age=300" },
    });
  }

  const detailKey = query.get("key");
  if (!detailKey || !DETAIL_KEY_PATTERN.test(detailKey)) {
    return NextResponse.json({ error: "invalid news detail key" }, { status: 400 });
  }
  if (previewBundle) {
    const detail = previewBundle.news_details[detailKey];
    if (detail) return previewJson(publicNewsRecord(detail));
    // Only the visible first page is compiled into the Worker. Older details
    // remain readable from D1; Preview writes are still rejected below.
  }
  const binding = env.DB as D1Database | undefined;
  if (!binding) {
    return NextResponse.json({ error: "database unavailable" }, { status: 503 });
  }
  const row = await binding
    .prepare("SELECT payload, detail_hash FROM news_details WHERE detail_key = ?")
    .bind(detailKey)
    .first<{ payload: string; detail_hash: string }>();
  if (!row) {
    return NextResponse.json({ error: "新闻详情仍在同步" }, { status: 404 });
  }
  const payload = publicNewsRecord({
    detail_hash: row.detail_hash, payload: JSON.parse(row.payload),
  });
  if (previewBundle) return previewJson(payload, 200, "read-only-d1-detail");
  return NextResponse.json(payload, {
    headers: { "Cache-Control": "private, max-age=300" },
  });
}

export async function POST(request: Request) {
  const previewRejection = rejectPreviewWrite();
  if (previewRejection) return previewRejection;
  const validation = await authorizeReleaseValidation(
    request, "news-content-write", isIngestAuthorized,
  );
  if (validation instanceof Response) return validation;
  const bounded = await readBoundedBody(request, 450_000);
  if (bounded.status === "too_large") {
    return NextResponse.json({ error: "payload too large" }, { status: 413 });
  }
  const binding = env.DB as D1Database | undefined;
  if (!binding) {
    return NextResponse.json({ error: "database unavailable" }, { status: 503 });
  }
  try {
    if (isReleaseValidationContext(validation)) {
      const checked = await binding.prepare(
        `WITH root(doc) AS (
           SELECT ? WHERE json_valid(?)
         ), batch(row) AS (
           SELECT value FROM root,json_each(json_extract(doc,'$.items'))
         )
         SELECT
            CASE
              WHEN json_type(doc,'$.reset')='true' THEN 'reset'
             WHEN json_type(doc,'$.items')='array' THEN 'items'
             ELSE 'invalid'
           END kind,
           (SELECT count(*) FROM batch) total,
           (SELECT count(*) FROM batch WHERE
             json_type(row)='object'
             AND length(json_extract(row,'$.detail_key'))=64
             AND json_extract(row,'$.detail_key') NOT GLOB '*[^0-9a-f]*'
             AND length(json_extract(row,'$.detail_hash'))=64
             AND json_extract(row,'$.detail_hash') NOT GLOB '*[^0-9a-f]*'
              AND json_type(row,'$.payload') IN ('object','array')) valid
         FROM root`,
      ).bind(bounded.serialized, bounded.serialized).first<{
        kind: string; total: number; valid: number;
      }>();
      const total = Number(checked?.total ?? 0);
      const valid = Number(checked?.valid ?? 0);
      if (!checked || (checked.kind !== "reset"
          && (checked.kind !== "items" || total > 20 || valid !== total))) {
        return NextResponse.json({ error: "invalid news detail batch" }, { status: 400 });
      }
      return releaseValidationResponse(validation, {
        body: "bounded-read", json: "d1-json1+json-each",
        transformed: checked.kind === "reset"
          ? { reset: true }
          : { items: total, prepared_statements: total },
        mutation_boundary: checked.kind === "reset"
          ? "news-content-reset" : "news-content-upsert-batch",
      });
    }
    const body = JSON.parse(bounded.serialized) as {
      items?: NewsDetailItem[]; reset?: unknown
    };
    if (body.reset === true) {
      await binding.prepare("DELETE FROM news_details").run();
      return NextResponse.json({ status: "OK", reset: true });
    }
    if (!Array.isArray(body.items) || body.items.length > 20) {
      return NextResponse.json({ error: "invalid news detail batch" }, { status: 400 });
    }
    for (const item of body.items) {
      if (
        typeof item.detail_key !== "string"
        || !/^[a-f0-9]{64}$/.test(item.detail_key)
        || typeof item.detail_hash !== "string"
        || !/^[a-f0-9]{64}$/.test(item.detail_hash)
        || !item.payload
        || typeof item.payload !== "object"
      ) {
        throw new Error("invalid news detail item");
      }
    }
    const now = new Date().toISOString();
    const statements = body.items.map((item) => {
      return binding.prepare(
        `INSERT INTO news_details (detail_key, detail_hash, payload, received_at)
         VALUES (?, ?, ?, ?)
         ON CONFLICT(detail_key) DO UPDATE SET
           detail_hash=excluded.detail_hash,
           payload=excluded.payload,
           received_at=excluded.received_at`,
      ).bind(item.detail_key, item.detail_hash, JSON.stringify(item.payload), now);
    });
    if (statements.length) await binding.batch(statements);
    return NextResponse.json({ status: "OK", received: statements.length });
  } catch (reason) {
    return NextResponse.json(
      { error: reason instanceof Error ? reason.message : "invalid news detail item" },
      { status: 400 },
    );
  }
}
