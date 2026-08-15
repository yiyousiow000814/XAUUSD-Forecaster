import { env } from "cloudflare:workers";
import { NextResponse } from "next/server";
import { isIngestAuthorized } from "../_shared/ingest-auth";
import { previewBundle, previewJson, rejectPreviewWrite } from "../_shared/preview";

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
        if (previewBundle.news_details[key]) items[key] = previewBundle.news_details[key];
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
        items[row.detail_key] = {
          detail_hash: row.detail_hash,
          payload: JSON.parse(row.payload),
        };
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
    if (detail) return previewJson(detail);
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
  const payload = { detail_hash: row.detail_hash, payload: JSON.parse(row.payload) };
  if (previewBundle) return previewJson(payload, 200, "read-only-d1-detail");
  return NextResponse.json(payload, {
    headers: { "Cache-Control": "private, max-age=300" },
  });
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
  if (!binding) {
    return NextResponse.json({ error: "database unavailable" }, { status: 503 });
  }
  try {
    const body = JSON.parse(serialized) as { items?: NewsDetailItem[]; reset?: unknown };
    if (body.reset === true) {
      await binding.prepare("DELETE FROM news_details").run();
      return NextResponse.json({ status: "OK", reset: true });
    }
    if (!Array.isArray(body.items) || body.items.length > 20) {
      return NextResponse.json({ error: "invalid news detail batch" }, { status: 400 });
    }
    const now = new Date().toISOString();
    const statements = body.items.map((item) => {
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
