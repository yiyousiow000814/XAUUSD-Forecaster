import { env } from "cloudflare:workers";
import { NextResponse } from "next/server";
import { readBoundedBody } from "../_shared/dashboard-snapshot";
import { isIngestAuthorized } from "../_shared/ingest-auth";
import { previewBundle, previewJson, rejectPreviewWrite } from "../_shared/preview";

export const dynamic = "force-dynamic";

const CONTRACT_VERSION = "news-evidence-paged-v1";
const MAX_WRITE_BYTES = 400_000;
const MAX_WRITE_ITEMS = 20;
const MAX_PAGE_ITEMS = 50;
const SNAPSHOT_ID = /^[a-f0-9]{64}$/;

type EvidenceItem = {
  event_key?: unknown;
  source_published_time?: unknown;
  collector_first_seen_time?: unknown;
  broad_model_eligible?: unknown;
  model_seen?: unknown;
  [key: string]: unknown;
};

type EvidenceMode = "all" | "eligible" | "seen" | "unseen";

function evidenceMode(value: string | null): EvidenceMode | null {
  return value === null || value === "all" ? "all"
    : value === "eligible" || value === "seen" || value === "unseen" ? value
      : null;
}

export async function GET(request: Request) {
  const binding = env.DB as D1Database | undefined;
  if (!binding) {
    const body = { error: "新闻证据档案暂时不可用" };
    return previewBundle ? previewJson(body, 503, "current-read-unavailable")
      : NextResponse.json(body, { status: 503 });
  }
  const query = new URL(request.url).searchParams;
  const mode = evidenceMode(query.get("mode"));
  if (mode === null) {
    return NextResponse.json({ error: "invalid evidence mode" }, { status: 400 });
  }
  const page = Math.max(1, Number.parseInt(query.get("page") ?? "1", 10) || 1);
  const pageSize = Math.min(
    MAX_PAGE_ITEMS,
    Math.max(1, Number.parseInt(query.get("limit") ?? "20", 10) || 20),
  );
  try {
    const state = await binding.prepare(
      "SELECT active_snapshot_id,contract_version,record_count,activated_at "
      + "FROM news_evidence_state WHERE id=1",
    ).first<{
      active_snapshot_id: string; contract_version: string;
      record_count: number; activated_at: string;
    }>();
    if (!state) {
      return NextResponse.json({ error: "等待新闻证据首次同步" }, { status: 503 });
    }
    const conditions = ["snapshot_id=?"];
    const binds: Array<string | number> = [state.active_snapshot_id];
    if (mode === "eligible") conditions.push("broad_model_eligible=1");
    if (mode === "seen") conditions.push("model_seen=1");
    if (mode === "unseen") conditions.push("model_seen=0");
    const rawCursor = query.get("cursor");
    if (rawCursor) {
      let cursor: unknown;
      try {
        cursor = JSON.parse(rawCursor) as unknown;
      } catch {
        return NextResponse.json({ error: "invalid evidence cursor" }, { status: 400 });
      }
      if (
        !Array.isArray(cursor) || cursor.length !== 2
        || cursor.some(value => typeof value !== "string" || !value)
      ) {
        return NextResponse.json({ error: "invalid evidence cursor" }, { status: 400 });
      }
      conditions.push(
        "(sort_time<? OR (sort_time=? AND event_key<?))",
      );
      binds.push(cursor[0], cursor[0], cursor[1]);
    }
    const boundedRows = await binding.prepare(
      `SELECT payload,sort_time,event_key FROM news_evidence_records
       WHERE ${conditions.join(" AND ")}
       ORDER BY sort_time DESC,event_key DESC LIMIT ?`,
    ).bind(...binds, pageSize + 1).all<{
      payload: string; sort_time: string; event_key: string;
    }>();
    const hasMore = boundedRows.results.length > pageSize;
    const rows = boundedRows.results.slice(0, pageSize);
    const last = rows.at(-1);
    const payload = {
      items: rows.map(row => JSON.parse(row.payload) as EvidenceItem),
      page,
      page_size: pageSize,
      mode,
      has_more: hasMore,
      next_cursor: hasMore && last
        ? JSON.stringify([last.sort_time, last.event_key]) : null,
      snapshot_id: state.active_snapshot_id,
      contract_version: state.contract_version,
      activated_at: state.activated_at,
      source_mode: "D1_AUDIT_ARCHIVE",
    };
    return previewBundle ? previewJson(payload, 200, "read-only-d1-archive")
      : NextResponse.json(payload, {
        headers: { "Cache-Control": "public, max-age=15, s-maxage=30, stale-while-revalidate=120" },
      });
  } catch {
    return NextResponse.json({ error: "新闻证据档案暂时不可用" }, { status: 503 });
  }
}

export async function POST(request: Request) {
  const previewRejection = rejectPreviewWrite();
  if (previewRejection) return previewRejection;
  if (!await isIngestAuthorized(request)) {
    return NextResponse.json({ error: "unauthorized" }, { status: 401 });
  }
  const binding = env.DB as D1Database | undefined;
  if (!binding) return NextResponse.json({ error: "database unavailable" }, { status: 503 });
  const bounded = await readBoundedBody(request, MAX_WRITE_BYTES);
  if (bounded.status === "too_large") {
    return NextResponse.json({ error: "payload too large" }, { status: 413 });
  }
  try {
    const body = JSON.parse(bounded.serialized) as {
      contract_version?: unknown; snapshot_id?: unknown; items?: unknown;
      offset?: unknown; activate_snapshot?: unknown; expected_count?: unknown;
      cleanup_active_snapshot?: unknown;
    };
    if (body.contract_version !== CONTRACT_VERSION) {
      return NextResponse.json({ error: "invalid evidence contract" }, { status: 400 });
    }
    if (typeof body.cleanup_active_snapshot === "string") {
      const active = await binding.prepare(
        "SELECT active_snapshot_id FROM news_evidence_state WHERE id=1",
      ).first<{ active_snapshot_id: string }>();
      if (
        !SNAPSHOT_ID.test(body.cleanup_active_snapshot)
        || active?.active_snapshot_id !== body.cleanup_active_snapshot
      ) {
        return NextResponse.json({ error: "invalid evidence cleanup" }, { status: 409 });
      }
      await binding.batch([
        binding.prepare(
          `DELETE FROM news_evidence_records WHERE rowid IN (
             SELECT rowid FROM news_evidence_records
             WHERE snapshot_id<>? LIMIT 200
           )`,
        ).bind(body.cleanup_active_snapshot),
        binding.prepare(
          `DELETE FROM news_evidence_staging WHERE snapshot_id IN (
             SELECT snapshot_id FROM news_evidence_staging
             WHERE snapshot_id<>? LIMIT 20
           )`,
        ).bind(body.cleanup_active_snapshot),
      ]);
      return NextResponse.json({ status: "OK", cleanup: "advanced" });
    }
    if (typeof body.activate_snapshot === "string") {
      if (
        !SNAPSHOT_ID.test(body.activate_snapshot)
        || !Number.isSafeInteger(body.expected_count)
        || Number(body.expected_count) < 0
      ) {
        return NextResponse.json({ error: "invalid evidence activation" }, { status: 400 });
      }
      const active = await binding.prepare(
        "SELECT active_snapshot_id,record_count FROM news_evidence_state WHERE id=1",
      ).first<{ active_snapshot_id: string; record_count: number }>();
      if (
        active?.active_snapshot_id === body.activate_snapshot
        && Number(active.record_count) === Number(body.expected_count)
      ) {
        return NextResponse.json({
          status: "OK", activated: body.activate_snapshot,
          count: Number(body.expected_count), unchanged: true,
        });
      }
      const staging = await binding.prepare(
        "SELECT next_offset FROM news_evidence_staging WHERE snapshot_id=?",
      ).bind(body.activate_snapshot).first<{ next_offset: number }>();
      const count = Number(staging?.next_offset ?? -1);
      if (count !== Number(body.expected_count)) {
        return NextResponse.json({
          error: "incomplete evidence snapshot", expected: body.expected_count, received: count,
        }, { status: 409 });
      }
      const now = new Date().toISOString();
      await binding.batch([
        binding.prepare(
          `INSERT INTO news_evidence_state
             (id,active_snapshot_id,contract_version,record_count,activated_at)
           VALUES (1,?,?,?,?)
           ON CONFLICT(id) DO UPDATE SET
             active_snapshot_id=excluded.active_snapshot_id,
             contract_version=excluded.contract_version,
             record_count=excluded.record_count,
             activated_at=excluded.activated_at`,
        ).bind(body.activate_snapshot, CONTRACT_VERSION, count, now),
        binding.prepare(
          "DELETE FROM news_evidence_staging WHERE snapshot_id=?",
        ).bind(body.activate_snapshot),
      ]);
      return NextResponse.json({ status: "OK", activated: body.activate_snapshot, count });
    }
    if (
      typeof body.snapshot_id !== "string" || !SNAPSHOT_ID.test(body.snapshot_id)
      || !Number.isSafeInteger(body.offset) || Number(body.offset) < 0
      || !Array.isArray(body.items) || body.items.length < 1
      || body.items.length > MAX_WRITE_ITEMS
    ) {
      return NextResponse.json({ error: "invalid evidence batch" }, { status: 400 });
    }
    const now = new Date().toISOString();
    await binding.prepare(
      `INSERT INTO news_evidence_staging (snapshot_id,next_offset,updated_at)
       VALUES (?,0,?) ON CONFLICT(snapshot_id) DO NOTHING`,
    ).bind(body.snapshot_id, now).run();
    const staging = await binding.prepare(
      "SELECT next_offset FROM news_evidence_staging WHERE snapshot_id=?",
    ).bind(body.snapshot_id).first<{ next_offset: number }>();
    const offset = Number(body.offset);
    const nextOffset = Number(staging?.next_offset ?? -1);
    if (offset < nextOffset && offset + body.items.length <= nextOffset) {
      return NextResponse.json({
        status: "OK", received: body.items.length, duplicate: true,
      });
    }
    if (offset !== nextOffset) {
      return NextResponse.json({
        error: "evidence batch offset mismatch", expected: nextOffset, received: offset,
      }, { status: 409 });
    }
    const statements = (body.items as EvidenceItem[]).map(item => {
      if (
        typeof item.event_key !== "string" || !SNAPSHOT_ID.test(item.event_key)
        || typeof item.collector_first_seen_time !== "string"
        || typeof item.broad_model_eligible !== "boolean"
        || typeof item.model_seen !== "boolean"
      ) throw new Error("invalid evidence item");
      const sortTime = typeof item.source_published_time === "string"
        ? item.source_published_time : item.collector_first_seen_time;
      return binding.prepare(
        `INSERT INTO news_evidence_records
           (snapshot_id,event_key,sort_time,broad_model_eligible,model_seen,payload,received_at)
         VALUES (?,?,?,?,?,?,?)`,
      ).bind(
        body.snapshot_id, item.event_key, sortTime,
        item.broad_model_eligible ? 1 : 0, item.model_seen ? 1 : 0,
        JSON.stringify(item), now,
      );
    });
    statements.push(binding.prepare(
      `UPDATE news_evidence_staging SET next_offset=?,updated_at=?
       WHERE snapshot_id=? AND next_offset=?`,
    ).bind(offset + statements.length, now, body.snapshot_id, offset));
    await binding.batch(statements);
    return NextResponse.json({ status: "OK", received: body.items.length });
  } catch (reason) {
    return NextResponse.json({
      error: reason instanceof Error ? reason.message : "invalid evidence payload",
    }, { status: 400 });
  }
}
