import { env } from "cloudflare:workers";
import { NextResponse } from "next/server";
import { isIngestAuthorized } from "../_shared/ingest-auth";
import { previewBundle, previewJson, rejectPreviewWrite } from "../_shared/preview";

export const dynamic = "force-dynamic";

type Candle = {
  time: string; open: number; high: number; low: number; close: number;
  ticks?: number; source_candles?: number;
};
type Decision = {
  source_decision_id: string; decision_time: string; model_identity: string;
  [key: string]: unknown;
};
type MarketSnapshot = {
  candles?: Candle[]; overview_candles?: Candle[]; decisions?: Decision[];
  training_markers?: Array<Record<string, unknown>>;
  history_start?: string | null; history_end?: string | null;
  source_candle_count?: number; prediction_history_start?: Record<string, string>;
};
type HistoryMeta = { count: number; start_epoch: number; end_epoch: number };

const MAX_INGEST_BYTES = 400_000;
const MAX_BATCH_STATEMENTS = 50;
const OVERVIEW_POINTS = 480;
const OVERVIEW_DECISIONS = 480;
const RANGE_SECONDS: Record<string, number> = {
  "3": 3 * 3_600, "6": 6 * 3_600, "12": 12 * 3_600,
  "24": 24 * 3_600, "168": 7 * 86_400,
};

const asEpoch = (value: string | null) => {
  if (!value) return null;
  const epoch = Math.floor(Date.parse(value) / 1_000);
  return Number.isFinite(epoch) ? epoch : null;
};

const compactCandle = (row: {
  time: string; open_milli: number; high_milli: number; low_milli: number;
  close_milli: number; ticks: number; source_candles?: number;
}): Candle => ({
  time: row.time,
  open: row.open_milli / 1_000,
  high: row.high_milli / 1_000,
  low: row.low_milli / 1_000,
  close: row.close_milli / 1_000,
  ticks: row.ticks,
  ...(row.source_candles ? { source_candles: row.source_candles } : {}),
});

function downsample(rows: Candle[], limit = OVERVIEW_POINTS): Candle[] {
  if (rows.length <= limit) return rows;
  const size = Math.ceil(rows.length / limit);
  const result: Candle[] = [];
  for (let start = 0; start < rows.length; start += size) {
    const group = rows.slice(start, start + size);
    result.push({
      time: group[0].time, open: group[0].open,
      high: Math.max(...group.map(row => row.high)),
      low: Math.min(...group.map(row => row.low)),
      close: group.at(-1)!.close,
      ticks: group.reduce((total, row) => total + (row.ticks ?? 0), 0),
      source_candles: group.length,
    });
  }
  return result;
}

function sampleDecisions(rows: Decision[], limit = OVERVIEW_DECISIONS): Decision[] {
  if (rows.length <= limit) return rows;
  const stride = Math.ceil(rows.length / limit);
  return rows.filter((_, index) => index % stride === 0).slice(0, limit);
}

function previewHistory(request: Request) {
  const source = previewBundle!.market_chart as MarketSnapshot;
  const url = new URL(request.url);
  const range = url.searchParams.get("range") ?? "24";
  const identity = url.searchParams.get("identity") ?? "BROAD_FULL";
  const frequency = url.searchParams.get("frequency") === "5m" ? "5m" : "30m";
  const detail = source.candles ?? [];
  if (range === "all") {
    const candles = source.overview_candles?.length
      ? source.overview_candles : downsample(detail);
    const allDecisions = (source.decisions ?? []).filter(row => {
      const minute = new Date(row.decision_time).getUTCMinutes();
      return row.model_identity === identity
        && (frequency === "5m" || minute % 30 === 0);
    });
    return previewJson({
      ...source, candles, overview_candles: [],
      decisions: sampleDecisions(allDecisions),
      source_decision_count: allDecisions.length,
      decision_downsampled: allDecisions.length > OVERVIEW_DECISIONS,
      mode: "overview",
      page: { has_earlier: false, has_later: false }, preview_limited: true,
    });
  }
  const seconds = RANGE_SECONDS[range] ?? RANGE_SECONDS["24"];
  const suppliedEnd = asEpoch(url.searchParams.get("before"));
  const end = suppliedEnd ?? (detail.length ? Math.floor(Date.parse(detail.at(-1)!.time) / 1_000) + 300 : 0);
  const start = end - seconds;
  const candles = detail.filter(row => {
    const epoch = Date.parse(row.time) / 1_000;
    return epoch >= start && epoch < end;
  });
  const decisions = (source.decisions ?? []).filter(row => {
    const epoch = Date.parse(row.decision_time) / 1_000;
    const minute = new Date(row.decision_time).getUTCMinutes();
    return row.model_identity === identity && epoch >= start && epoch < end
      && (frequency === "5m" || minute % 30 === 0);
  });
  return previewJson({
    ...source, candles, overview_candles: [], decisions, mode: "detail",
    page: {
      start: candles[0]?.time ?? new Date(start * 1_000).toISOString(),
      end: candles.at(-1)?.time ?? new Date(end * 1_000).toISOString(),
      has_earlier: Boolean(detail.length && start > Date.parse(detail[0].time) / 1_000),
      has_later: Boolean(detail.length && end <= Date.parse(detail.at(-1)!.time) / 1_000),
    },
    preview_limited: true,
  });
}

async function historyMeta(binding: D1Database): Promise<HistoryMeta | null> {
  return binding.prepare(
    `SELECT count(*) count, min(time_epoch) start_epoch, max(time_epoch) end_epoch
     FROM market_candles`,
  ).first<HistoryMeta>();
}

async function ensureMarketSchema(binding: D1Database) {
  await binding.batch([
    binding.prepare(`CREATE TABLE IF NOT EXISTS market_candles (
      time_epoch integer PRIMARY KEY NOT NULL,time text NOT NULL,
      open_milli integer NOT NULL,high_milli integer NOT NULL,low_milli integer NOT NULL,
      close_milli integer NOT NULL,ticks integer NOT NULL,received_at text NOT NULL)`),
    binding.prepare(`CREATE TABLE IF NOT EXISTS market_decisions (
      decision_key text PRIMARY KEY NOT NULL,decision_epoch integer NOT NULL,
      decision_time text NOT NULL,model_identity text NOT NULL,payload text NOT NULL,
      received_at text NOT NULL)`),
    binding.prepare(`CREATE INDEX IF NOT EXISTS market_decisions_time_idx
      ON market_decisions (decision_epoch)`),
    binding.prepare(`CREATE INDEX IF NOT EXISTS market_decisions_model_time_idx
      ON market_decisions (model_identity,decision_epoch)`),
  ]);
}

async function overview(binding: D1Database, meta: HistoryMeta) {
  const span = Math.max(300, meta.end_epoch - meta.start_epoch + 300);
  const bucket = Math.max(300, Math.ceil(span / OVERVIEW_POINTS / 300) * 300);
  const rows = await binding.prepare(
    `WITH bucketed AS (
       SELECT *, CAST((time_epoch - ?) / ? AS INTEGER) bucket,
              row_number() OVER (
                PARTITION BY CAST((time_epoch - ?) / ? AS INTEGER)
                ORDER BY time_epoch
              ) first_rank,
              row_number() OVER (
                PARTITION BY CAST((time_epoch - ?) / ? AS INTEGER)
                ORDER BY time_epoch DESC
              ) last_rank
       FROM market_candles
     )
     SELECT min(time) time,
            max(CASE WHEN first_rank=1 THEN open_milli END) open_milli,
            max(high_milli) high_milli, min(low_milli) low_milli,
            max(CASE WHEN last_rank=1 THEN close_milli END) close_milli,
            sum(ticks) ticks, count(*) source_candles
     FROM bucketed GROUP BY bucket ORDER BY bucket`,
  ).bind(meta.start_epoch, bucket, meta.start_epoch, bucket, meta.start_epoch, bucket)
    .all<{
      time: string; open_milli: number; high_milli: number; low_milli: number;
      close_milli: number; ticks: number; source_candles: number;
    }>();
  return rows.results.map(compactCandle);
}

async function overviewDecisions(
  binding: D1Database, identity: string, frequency: "5m" | "30m",
) {
  const frequencyClause = frequency === "30m" ? "AND decision_epoch % 1800 = 0" : "";
  const countRow = await binding.prepare(
    `SELECT count(*) count FROM market_decisions
     WHERE model_identity=? ${frequencyClause}`,
  ).bind(identity).first<{ count: number }>();
  const count = Number(countRow?.count ?? 0);
  const stride = Math.max(1, Math.ceil(count / OVERVIEW_DECISIONS));
  const result = await binding.prepare(
    `WITH ordered AS (
       SELECT payload,row_number() OVER (ORDER BY decision_epoch,decision_key) sequence
       FROM market_decisions WHERE model_identity=? ${frequencyClause}
     )
     SELECT payload FROM ordered WHERE (sequence-1) % ? = 0
     ORDER BY sequence LIMIT ?`,
  ).bind(identity, stride, OVERVIEW_DECISIONS).all<{ payload: string }>();
  return {
    decisions: result.results.map(row => JSON.parse(row.payload)),
    sourceDecisionCount: count,
  };
}

export async function GET(request: Request) {
  if (previewBundle) return previewHistory(request);
  const binding = env.DB as D1Database | undefined;
  if (!binding) return NextResponse.json({ error: "database unavailable" }, { status: 503 });
  const url = new URL(request.url);
  const range = url.searchParams.get("range") ?? "24";
  const identity = url.searchParams.get("identity") ?? "BROAD_FULL";
  const frequency = url.searchParams.get("frequency") === "5m" ? "5m" : "30m";
  if (!(range in RANGE_SECONDS) && range !== "all") {
    return NextResponse.json({ error: "invalid range" }, { status: 400 });
  }
  try {
    const meta = await historyMeta(binding);
    if (!meta?.count) return NextResponse.json({ error: "等待历史行情同步" }, { status: 503 });
    const historyStart = new Date(meta.start_epoch * 1_000).toISOString();
    const historyEnd = new Date(meta.end_epoch * 1_000).toISOString();
    if (range === "all") {
      const decisionOverview = await overviewDecisions(binding, identity, frequency);
      return NextResponse.json({
        candles: await overview(binding, meta),
        decisions: decisionOverview.decisions,
        source_decision_count: decisionOverview.sourceDecisionCount,
        decision_downsampled: decisionOverview.sourceDecisionCount > OVERVIEW_DECISIONS,
        training_markers: [],
        mode: "overview", history_start: historyStart, history_end: historyEnd,
        source_candle_count: meta.count, overview_downsampled: meta.count > OVERVIEW_POINTS,
        page: { has_earlier: false, has_later: false },
      }, { headers: { "Cache-Control": "no-store, max-age=0" } });
    }
    const seconds = RANGE_SECONDS[range];
    const requestedEnd = asEpoch(url.searchParams.get("before"));
    const end = Math.min(requestedEnd ?? meta.end_epoch + 300, meta.end_epoch + 300);
    const start = end - seconds;
    const candlesResult = await binding.prepare(
      `SELECT time,open_milli,high_milli,low_milli,close_milli,ticks
       FROM market_candles WHERE time_epoch>=? AND time_epoch<? ORDER BY time_epoch`,
    ).bind(start, end).all<{
      time: string; open_milli: number; high_milli: number; low_milli: number;
      close_milli: number; ticks: number;
    }>();
    const decisionSql = `SELECT payload FROM market_decisions
      WHERE model_identity=? AND decision_epoch>=? AND decision_epoch<?
      ${frequency === "30m" ? "AND decision_epoch % 1800 = 0" : ""}
      ORDER BY decision_epoch,decision_key`;
    const decisionsResult = await binding.prepare(decisionSql)
      .bind(identity, start, end).all<{ payload: string }>();
    const candles = candlesResult.results.map(compactCandle);
    return NextResponse.json({
      candles,
      decisions: decisionsResult.results.map(row => JSON.parse(row.payload)),
      training_markers: [], mode: "detail",
      history_start: historyStart, history_end: historyEnd,
      source_candle_count: meta.count,
      page: {
        start: candles[0]?.time ?? new Date(start * 1_000).toISOString(),
        end: candles.at(-1)?.time ?? new Date(end * 1_000).toISOString(),
        has_earlier: start > meta.start_epoch,
        has_later: end < meta.end_epoch + 300,
      },
    }, { headers: { "Cache-Control": "no-store, max-age=0" } });
  } catch {
    return NextResponse.json({ error: "历史行情读取失败" }, { status: 500 });
  }
}

export async function POST(request: Request) {
  const previewRejection = rejectPreviewWrite();
  if (previewRejection) return previewRejection;
  if (!await isIngestAuthorized(request)) {
    return NextResponse.json({ error: "unauthorized" }, { status: 401 });
  }
  const serialized = await request.text();
  if (new TextEncoder().encode(serialized).byteLength > MAX_INGEST_BYTES) {
    return NextResponse.json({ error: "payload too large" }, { status: 413 });
  }
  const binding = env.DB as D1Database | undefined;
  if (!binding) return NextResponse.json({ error: "database unavailable" }, { status: 503 });
  try {
    const body = JSON.parse(serialized) as { candles?: Candle[]; decisions?: Decision[] };
    const candles = Array.isArray(body.candles) ? body.candles : [];
    const decisions = Array.isArray(body.decisions) ? body.decisions : [];
    if (candles.length > 500 || decisions.length > 2_500) throw new Error("batch too large");
    const receivedAt = new Date().toISOString();
    await ensureMarketSchema(binding);
    const statements: D1PreparedStatement[] = [];
    for (const row of candles) {
      const epoch = asEpoch(row.time);
      if (epoch === null || ![row.open, row.high, row.low, row.close].every(Number.isFinite)) {
        throw new Error("invalid candle");
      }
      statements.push(binding.prepare(
        `INSERT INTO market_candles
           (time_epoch,time,open_milli,high_milli,low_milli,close_milli,ticks,received_at)
         VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(time_epoch) DO UPDATE SET
           time=excluded.time,open_milli=excluded.open_milli,high_milli=excluded.high_milli,
           low_milli=excluded.low_milli,close_milli=excluded.close_milli,
           ticks=excluded.ticks,received_at=excluded.received_at`,
      ).bind(epoch, row.time, Math.round(row.open * 1_000), Math.round(row.high * 1_000),
        Math.round(row.low * 1_000), Math.round(row.close * 1_000), row.ticks ?? 0, receivedAt));
    }
    for (const row of decisions) {
      const epoch = asEpoch(row.decision_time);
      if (epoch === null || !row.source_decision_id || !row.model_identity) {
        throw new Error("invalid decision");
      }
      const key = `${row.source_decision_id}\u0000${row.model_identity}`;
      statements.push(binding.prepare(
        `INSERT INTO market_decisions
           (decision_key,decision_epoch,decision_time,model_identity,payload,received_at)
         VALUES (?,?,?,?,?,?) ON CONFLICT(decision_key) DO UPDATE SET
           decision_epoch=excluded.decision_epoch,decision_time=excluded.decision_time,
           model_identity=excluded.model_identity,payload=excluded.payload,
           received_at=excluded.received_at`,
      ).bind(key, epoch, row.decision_time, row.model_identity, JSON.stringify(row), receivedAt));
    }
    for (let start = 0; start < statements.length; start += MAX_BATCH_STATEMENTS) {
      await binding.batch(statements.slice(start, start + MAX_BATCH_STATEMENTS));
    }
    return NextResponse.json({ status: "OK", candles: candles.length, decisions: decisions.length });
  } catch {
    return NextResponse.json({ error: "invalid market history payload" }, { status: 400 });
  }
}
