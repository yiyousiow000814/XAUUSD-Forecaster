import { env } from "cloudflare:workers";
import { NextResponse } from "next/server";
import { isIngestAuthorized } from "../_shared/ingest-auth";
import { readBoundedBody } from "../_shared/dashboard-snapshot";
import { previewBundle, previewJson, rejectPreviewWrite } from "../_shared/preview";

export const dynamic = "force-dynamic";

type LearningRecord = {
  resource: string;
  record_key: string;
  sort_epoch: number;
  payload_hash: string;
  payload: Record<string, unknown>;
};

const MAX_INGEST_BYTES = 350_000;
const MAX_INGEST_ROWS = 1_000;
const MAX_PAGE_ROWS = 500;
const CURVE_OVERVIEW_POINTS = 240;
const VERSION_OVERVIEW_GROUPS = 60;
const ALLOWED_RESOURCES = new Set([
  "model", "version-group", "curve-5m", "curve-30m",
  "execution-point", "execution-result",
]);

function encodeCursor(sortEpoch: number, recordKey: string) {
  return btoa(JSON.stringify([sortEpoch, recordKey]))
    .replaceAll("+", "-").replaceAll("/", "_").replaceAll("=", "");
}

function decodeCursor(value: string | null): [number, string] | null {
  if (!value) return null;
  try {
    const padded = value.replaceAll("-", "+").replaceAll("_", "/")
      + "=".repeat((4 - value.length % 4) % 4);
    const decoded = JSON.parse(atob(padded));
    return Array.isArray(decoded) && Number.isSafeInteger(decoded[0])
      && typeof decoded[1] === "string" ? [decoded[0], decoded[1]] : null;
  } catch {
    return null;
  }
}

async function ensureSchema(binding: D1Database) {
  await binding.batch([
    binding.prepare(`CREATE TABLE IF NOT EXISTS learning_records (
      resource text NOT NULL,record_key text NOT NULL,sort_epoch integer NOT NULL,
      payload_hash text NOT NULL,payload text NOT NULL,received_at text NOT NULL,
      PRIMARY KEY(resource,record_key))`),
    binding.prepare(`CREATE INDEX IF NOT EXISTS learning_records_resource_time_idx
      ON learning_records (resource,sort_epoch,record_key)`),
  ]);
}

function previewRecords() {
  return Array.isArray(previewBundle?.learning_history)
    ? previewBundle.learning_history as LearningRecord[] : [];
}

function responseFromRows(
  rows: Array<{ sort_epoch: number; record_key: string; payload: string | Record<string, unknown> }>,
  total: number,
  limit: number,
) {
  const visible = rows.slice(0, limit);
  const last = visible.at(-1);
  return {
    items: visible.map(row => typeof row.payload === "string" ? JSON.parse(row.payload) : row.payload),
    total,
    next_cursor: rows.length > limit && last
      ? encodeCursor(Number(last.sort_epoch), last.record_key) : null,
    has_more: rows.length > limit,
  };
}

function previewPage(url: URL) {
  const resource = url.searchParams.get("resource") ?? "";
  const identity = url.searchParams.get("identity");
  const limit = Math.min(MAX_PAGE_ROWS, Math.max(1, Number(url.searchParams.get("limit")) || 6));
  const cursor = decodeCursor(url.searchParams.get("cursor"));
  let rows = previewRecords().filter(row => row.resource === resource);
  if (identity) rows = rows.filter(row => row.payload.model_identity === identity);
  rows.sort((a, b) => b.sort_epoch - a.sort_epoch || b.record_key.localeCompare(a.record_key));
  const total = rows.length;
  if (cursor) rows = rows.filter(row => row.sort_epoch < cursor[0]
    || (row.sort_epoch === cursor[0] && row.record_key < cursor[1]));
  return previewJson({ ...responseFromRows(rows, total, limit), preview_limited: true });
}

async function pagedRecords(binding: D1Database, url: URL) {
  const resource = url.searchParams.get("resource") ?? "";
  const identity = url.searchParams.get("identity") ?? "";
  const limit = Math.min(MAX_PAGE_ROWS, Math.max(1, Number(url.searchParams.get("limit")) || 6));
  const cursor = decodeCursor(url.searchParams.get("cursor"));
  const identityClause = identity ? "AND json_extract(payload,'$.model_identity')=?" : "";
  const cursorClause = cursor
    ? "AND (sort_epoch<? OR (sort_epoch=? AND record_key<?))" : "";
  const values: unknown[] = [resource];
  if (identity) values.push(identity);
  if (cursor) values.push(cursor[0], cursor[0], cursor[1]);
  const rows = await binding.prepare(
    `SELECT sort_epoch,record_key,payload FROM learning_records
     WHERE resource=? ${identityClause} ${cursorClause}
     ORDER BY sort_epoch DESC,record_key DESC LIMIT ?`,
  ).bind(...values, limit + 1).all<{ sort_epoch: number; record_key: string; payload: string }>();
  const countValues: unknown[] = [resource];
  if (identity) countValues.push(identity);
  const count = await binding.prepare(
    `SELECT count(*) count FROM learning_records WHERE resource=? ${identityClause}`,
  ).bind(...countValues).first<{ count: number }>();
  return responseFromRows(rows.results, Number(count?.count ?? 0), limit);
}

async function curveOverview(binding: D1Database, url: URL) {
  const cadence = url.searchParams.get("cadence") === "30m" ? "30m" : "5m";
  const resource = `curve-${cadence}`;
  const result = await binding.prepare(
    `WITH ranked AS (
       SELECT payload,sort_epoch,record_key,
              row_number() OVER (
                PARTITION BY json_extract(payload,'$.model_identity')
                ORDER BY sort_epoch,record_key
              ) sequence,
              count(*) OVER (
                PARTITION BY json_extract(payload,'$.model_identity')
              ) total
       FROM learning_records WHERE resource=?
     )
     SELECT sort_epoch,record_key,payload FROM ranked
     WHERE total<=? OR (sequence-1) % MAX(1,(total+?-1)/?)=0
     ORDER BY sort_epoch,record_key`,
  ).bind(resource, CURVE_OVERVIEW_POINTS, CURVE_OVERVIEW_POINTS, CURVE_OVERVIEW_POINTS)
    .all<{ sort_epoch: number; record_key: string; payload: string }>();
  return {
    items: result.results.map(row => JSON.parse(row.payload)),
    mode: "overview",
    point_limit_per_identity: CURVE_OVERVIEW_POINTS,
  };
}

async function versionOverview(binding: D1Database) {
  const result = await binding.prepare(
    `WITH ranked AS (
       SELECT payload,sort_epoch,record_key,
              row_number() OVER (
                PARTITION BY json_extract(payload,'$.model_identity')
                ORDER BY sort_epoch,record_key
              ) sequence,
              count(*) OVER (
                PARTITION BY json_extract(payload,'$.model_identity')
              ) total
       FROM learning_records WHERE resource='version-group'
     )
     SELECT payload FROM ranked
     WHERE total<=? OR (sequence-1) % MAX(1,(total+?-1)/?)=0
     ORDER BY sort_epoch,record_key`,
  ).bind(VERSION_OVERVIEW_GROUPS, VERSION_OVERVIEW_GROUPS, VERSION_OVERVIEW_GROUPS)
    .all<{ payload: string }>();
  return { items: result.results.map(row => JSON.parse(row.payload)), mode: "overview" };
}

export async function GET(request: Request) {
  const url = new URL(request.url);
  const resource = url.searchParams.get("resource") ?? "";
  if (resource === "version-overview") {
    if (previewBundle) {
      const source = previewRecords().filter(row => row.resource === "version-group");
      const identities = [...new Set(source.map(row => String(row.payload.model_identity ?? "")))];
      return previewJson({
        items: identities.flatMap(identity => source
          .filter(row => row.payload.model_identity === identity)
          .sort((a, b) => a.sort_epoch - b.sort_epoch)
          .slice(-VERSION_OVERVIEW_GROUPS)
          .map(row => row.payload)),
        mode: "overview", preview_limited: true,
      });
    }
    const binding = env.DB as D1Database | undefined;
    if (!binding) return NextResponse.json({ error: "database unavailable" }, { status: 503 });
    try {
      return NextResponse.json(await versionOverview(binding), {
        headers: { "Cache-Control": "private, max-age=60" },
      });
    } catch {
      return NextResponse.json({ error: "训练组总览读取失败" }, { status: 500 });
    }
  }
  if (resource === "curve-overview") {
    if (previewBundle) {
      const cadence = url.searchParams.get("cadence") === "30m" ? "curve-30m" : "curve-5m";
      const source = previewRecords().filter(row => row.resource === cadence);
      const identities = [...new Set(source.map(row => String(row.payload.model_identity ?? "")))];
      const sampled = identities.flatMap(identity => {
        const rows = source.filter(row => row.payload.model_identity === identity)
          .sort((a, b) => a.sort_epoch - b.sort_epoch);
        if (rows.length <= CURVE_OVERVIEW_POINTS) return rows;
        const stride = Math.ceil(rows.length / CURVE_OVERVIEW_POINTS);
        return rows.filter((_, index) => index % stride === 0).slice(0, CURVE_OVERVIEW_POINTS);
      });
      return previewJson({
        items: sampled.map(row => row.payload),
        mode: "overview", preview_limited: true,
      });
    }
    const binding = env.DB as D1Database | undefined;
    if (!binding) return NextResponse.json({ error: "database unavailable" }, { status: 503 });
    try {
      return NextResponse.json(await curveOverview(binding, url), {
        headers: { "Cache-Control": "private, max-age=60" },
      });
    } catch {
      return NextResponse.json({ error: "学习曲线历史读取失败" }, { status: 500 });
    }
  }
  if (!ALLOWED_RESOURCES.has(resource)) {
    return NextResponse.json({ error: "invalid resource" }, { status: 400 });
  }
  if (previewBundle) return previewPage(url);
  const binding = env.DB as D1Database | undefined;
  if (!binding) return NextResponse.json({ error: "database unavailable" }, { status: 503 });
  try {
    return NextResponse.json(await pagedRecords(binding, url), {
      headers: { "Cache-Control": "private, max-age=60" },
    });
  } catch {
    return NextResponse.json({ error: "学习历史读取失败" }, { status: 500 });
  }
}

export async function POST(request: Request) {
  const previewRejection = rejectPreviewWrite();
  if (previewRejection) return previewRejection;
  if (!await isIngestAuthorized(request)) {
    return NextResponse.json({ error: "unauthorized" }, { status: 401 });
  }
  const body = await readBoundedBody(request, MAX_INGEST_BYTES);
  if (body.status === "too_large") {
    return NextResponse.json({ error: "payload too large" }, { status: 413 });
  }
  const binding = env.DB as D1Database | undefined;
  if (!binding) return NextResponse.json({ error: "database unavailable" }, { status: 503 });
  try {
    await ensureSchema(binding);
    // Keep the growing payload off the Worker's JavaScript JSON parser. D1
    // validates every row and performs one set-based idempotent upsert.
    const result = await binding.prepare(
      `WITH root(doc) AS (
         SELECT ? WHERE json_valid(?) AND json_type(?,'$.records')='array'
       ), batch(row) AS (
         SELECT value FROM root,json_each(json_extract(doc,'$.records'))
       ), validation AS (
         SELECT count(*) total,
                sum(CASE WHEN
                  json_type(row)='object'
                  AND json_extract(row,'$.resource') IN
                    ('model','version-group','curve-5m','curve-30m',
                     'execution-point','execution-result')
                  AND length(json_extract(row,'$.record_key'))>0
                  AND json_type(row,'$.sort_epoch')='integer'
                  AND json_extract(row,'$.sort_epoch')>=0
                  AND length(json_extract(row,'$.payload_hash'))=64
                  AND json_extract(row,'$.payload_hash') NOT GLOB '*[^0-9a-f]*'
                  AND json_type(row,'$.payload')='object'
                THEN 1 ELSE 0 END) valid
         FROM batch
       )
       INSERT INTO learning_records
         (resource,record_key,sort_epoch,payload_hash,payload,received_at)
       SELECT json_extract(row,'$.resource'),json_extract(row,'$.record_key'),
              json_extract(row,'$.sort_epoch'),json_extract(row,'$.payload_hash'),
              json_extract(row,'$.payload'),?
       FROM batch,validation
       WHERE total BETWEEN 1 AND ? AND valid=total
       ON CONFLICT(resource,record_key) DO UPDATE SET
         sort_epoch=excluded.sort_epoch,payload_hash=excluded.payload_hash,
         payload=excluded.payload,received_at=excluded.received_at`,
    ).bind(body.serialized, body.serialized, body.serialized,
      new Date().toISOString(), MAX_INGEST_ROWS).run();
    const records = Number(result.meta.changes ?? 0);
    if (!records) throw new Error("empty or invalid batch");
    return NextResponse.json({ status: "OK", records });
  } catch {
    return NextResponse.json({ error: "invalid learning history payload" }, { status: 400 });
  }
}
