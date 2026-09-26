import { env } from "cloudflare:workers";
import { NextResponse } from "next/server";
import { readBoundedBody } from "../_shared/dashboard-snapshot";
import { isIngestAuthorized } from "../_shared/ingest-auth";
import { previewBundle, previewJson, rejectPreviewWrite } from "../_shared/preview";
import {
  authorizeReleaseValidation, isReleaseValidationContext, releaseValidationResponse,
  validateJsonWithD1,
} from "../_shared/release-validation";

export const dynamic = "force-dynamic";

type Candle = {
  time: string; open: number; high: number; low: number; close: number;
  ticks?: number; source_candles?: number;
};
type MarketSnapshot = {
  candles?: Candle[]; overview_candles?: Candle[];
  history_start?: string | null; history_end?: string | null;
  source_candle_count?: number;
};
type MaterializedOverview = {
  candles: Candle[]; source_candle_count: number;
  history_start: string | null; history_end: string | null;
};

const MAX_INGEST_BYTES = 400_000;
const MAX_BATCH_STATEMENTS = 50;
const OVERVIEW_POINTS = 480;
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


function previewHistory(request: Request) {
  const source = previewBundle!.market_chart as MarketSnapshot;
  const url = new URL(request.url);
  const range = url.searchParams.get("range") ?? "24";
  const detail = source.candles ?? [];
  if (range === "all") {
    const candles = source.overview_candles?.length
      ? source.overview_candles : downsample(detail);
    return previewJson({
      ...source, candles, overview_candles: [],
      mode: "overview",
      page: { has_earlier: false, has_later: false }, preview_limited: true,
    });
  }
  const seconds = RANGE_SECONDS[range] ?? RANGE_SECONDS["24"];
  const suppliedEnd = asEpoch(url.searchParams.get("before"));
  let end = suppliedEnd ?? (detail.length ? Math.floor(Date.parse(detail.at(-1)!.time) / 1_000) + 300 : 0);
  if (suppliedEnd && !detail.some(row => {
    const epoch = Date.parse(row.time) / 1_000;
    return epoch >= suppliedEnd - seconds && epoch < suppliedEnd;
  })) {
    const previous = detail.findLast(row => Date.parse(row.time) / 1_000 < suppliedEnd);
    if (previous) end = Math.floor(Date.parse(previous.time) / 1_000) + 300;
  }
  const start = end - seconds;
  const candles = detail.filter(row => {
    const epoch = Date.parse(row.time) / 1_000;
    return epoch >= start && epoch < end;
  });
  return previewJson({
    ...source, candles, overview_candles: [], mode: "detail",
    page: {
      start: candles[0]?.time ?? new Date(start * 1_000).toISOString(),
      end: candles.at(-1)?.time ?? new Date(end * 1_000).toISOString(),
      has_earlier: Boolean(detail.length && start > Date.parse(detail[0].time) / 1_000),
      has_later: Boolean(detail.length && end <= Date.parse(detail.at(-1)!.time) / 1_000),
    },
    preview_limited: true,
  });
}

async function previousCandleEnd(binding: D1Database, before: number) {
  const previous = await binding.prepare(
    `SELECT time_epoch FROM market_candles
     WHERE time_epoch<? ORDER BY time_epoch DESC LIMIT 1`,
  ).bind(before).first<{ time_epoch: number }>();
  return previous ? Number(previous.time_epoch) + 300 : before;
}

async function materializedMarketOverview(binding: D1Database) {
  const row = await binding.prepare(
    `SELECT payload FROM market_history_overview WHERE overview_key='all'`,
  ).first<{ payload: string }>();
  if (!row) throw new Error("market overview not materialized");
  const payload = JSON.parse(row.payload) as MaterializedOverview;
  if (!Array.isArray(payload.candles) || payload.candles.length > OVERVIEW_POINTS) {
    throw new Error("invalid materialized market overview");
  }
  return payload;
}


export async function GET(request: Request) {
  if (previewBundle) return previewHistory(request);
  const binding = env.DB as D1Database | undefined;
  if (!binding) return NextResponse.json({ error: "database unavailable" }, { status: 503 });
  const url = new URL(request.url);
  const range = url.searchParams.get("range") ?? "24";
  if (!(range in RANGE_SECONDS) && range !== "all") {
    return NextResponse.json({ error: "invalid range" }, { status: 400 });
  }
  try {
    const marketOverview = await materializedMarketOverview(binding);
    const startEpoch = asEpoch(marketOverview.history_start);
    const endEpoch = asEpoch(marketOverview.history_end);
    if (startEpoch === null || endEpoch === null || !marketOverview.source_candle_count) {
      return NextResponse.json({ error: "等待历史行情同步" }, { status: 503 });
    }
    const historyStart = new Date(startEpoch * 1_000).toISOString();
    const historyEnd = new Date(endEpoch * 1_000).toISOString();
    if (range === "all") {
      return NextResponse.json({
        candles: marketOverview.candles,
        mode: "overview", history_start: marketOverview.history_start ?? historyStart,
        history_end: marketOverview.history_end ?? historyEnd,
        source_candle_count: marketOverview.source_candle_count,
        overview_downsampled: marketOverview.source_candle_count > marketOverview.candles.length,
        page: { has_earlier: false, has_later: false },
      }, { headers: { "Cache-Control": "no-store, max-age=0" } });
    }
    const seconds = RANGE_SECONDS[range];
    const requestedEnd = asEpoch(url.searchParams.get("before"));
    let end = Math.min(requestedEnd ?? endEpoch + 300, endEpoch + 300);
    let start = end - seconds;
    let candlesResult = await binding.prepare(
      `SELECT time,open_milli,high_milli,low_milli,close_milli,ticks
       FROM market_candles WHERE time_epoch>=? AND time_epoch<? ORDER BY time_epoch`,
    ).bind(start, end).all<{
      time: string; open_milli: number; high_milli: number; low_milli: number;
      close_milli: number; ticks: number;
    }>();
    // A fixed wall-clock step can land wholly inside a weekend closure. Skip
    // that empty interval and return the nearest earlier trading window.
    if (requestedEnd && candlesResult.results.length === 0) {
      end = await previousCandleEnd(binding, requestedEnd);
      start = end - seconds;
      candlesResult = await binding.prepare(
        `SELECT time,open_milli,high_milli,low_milli,close_milli,ticks
         FROM market_candles WHERE time_epoch>=? AND time_epoch<? ORDER BY time_epoch`,
      ).bind(start, end).all<{
        time: string; open_milli: number; high_milli: number; low_milli: number;
        close_milli: number; ticks: number;
      }>();
    }
    const candles = candlesResult.results.map(compactCandle);
    return NextResponse.json({
      candles,
      mode: "detail",
      history_start: historyStart, history_end: historyEnd,
      source_candle_count: marketOverview.source_candle_count,
      page: {
        start: candles[0]?.time ?? new Date(start * 1_000).toISOString(),
        end: candles.at(-1)?.time ?? new Date(end * 1_000).toISOString(),
        has_earlier: start > startEpoch,
        has_later: end < endEpoch + 300,
      },
    }, { headers: { "Cache-Control": "no-store, max-age=0" } });
  } catch {
    return NextResponse.json({ error: "历史行情读取失败" }, { status: 500 });
  }
}

export async function POST(request: Request) {
  const previewRejection = rejectPreviewWrite();
  if (previewRejection) return previewRejection;
  const validation = await authorizeReleaseValidation(
    request, "market-history-write", isIngestAuthorized,
  );
  if (validation instanceof Response) return validation;
  const bounded = await readBoundedBody(request, MAX_INGEST_BYTES);
  if (bounded.status === "too_large") {
    return NextResponse.json({ error: "payload too large" }, { status: 413 });
  }
  const serialized = bounded.serialized;
  const binding = env.DB as D1Database | undefined;
  if (!binding) return NextResponse.json({ error: "database unavailable" }, { status: 503 });
  try {
    const body = JSON.parse(serialized) as {
      candles?: Candle[]; overview?: MaterializedOverview;
    };
    const candles = Array.isArray(body.candles) ? body.candles : [];
    if (candles.length > 500) throw new Error("batch too large");
    const receivedAt = new Date().toISOString();
    const statements: D1PreparedStatement[] = [];
    if (body.overview) {
      const overview = body.overview;
      if (!Array.isArray(overview.candles) || overview.candles.length > OVERVIEW_POINTS
          || !Number.isSafeInteger(overview.source_candle_count)
          || overview.source_candle_count < overview.candles.length
          || overview.candles.some(row => !row.time
            || ![row.open, row.high, row.low, row.close].every(Number.isFinite))) {
        throw new Error("invalid overview");
      }
      statements.push(binding.prepare(
        `INSERT INTO market_history_overview (overview_key,payload,received_at)
         VALUES ('all',?,?) ON CONFLICT(overview_key) DO UPDATE SET
           payload=excluded.payload,received_at=excluded.received_at
         WHERE market_history_overview.payload IS NOT excluded.payload`,
      ).bind(JSON.stringify(overview), receivedAt));
    }
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
           ticks=excluded.ticks,received_at=excluded.received_at
         WHERE market_candles.time IS NOT excluded.time
            OR market_candles.open_milli IS NOT excluded.open_milli
            OR market_candles.high_milli IS NOT excluded.high_milli
            OR market_candles.low_milli IS NOT excluded.low_milli
            OR market_candles.close_milli IS NOT excluded.close_milli
            OR market_candles.ticks IS NOT excluded.ticks`,
      ).bind(epoch, row.time, Math.round(row.open * 1_000), Math.round(row.high * 1_000),
        Math.round(row.low * 1_000), Math.round(row.close * 1_000), row.ticks ?? 0, receivedAt));
    }
    if (isReleaseValidationContext(validation)) {
      if (!await validateJsonWithD1(binding, serialized)) {
        throw new Error("invalid JSON");
      }
      return releaseValidationResponse(validation, {
        body: "bounded-read", json: "parsed+d1-json1",
        transformed: { candles: candles.length,
          overview: Boolean(body.overview),
          prepared_statements: statements.length },
        mutation_boundary: "schema-and-history-batch",
      });
    }
    let written = 0;
    for (let start = 0; start < statements.length; start += MAX_BATCH_STATEMENTS) {
      const results = await binding.batch(statements.slice(start, start + MAX_BATCH_STATEMENTS));
      written += results.reduce((total, result) => total + Number(result.meta?.changes ?? 0), 0);
    }
    return NextResponse.json({
      status: "OK", candles: candles.length,
      overview: Boolean(body.overview),
      accepted: statements.length, written,
    });
  } catch {
    return NextResponse.json({ error: "invalid market history payload" }, { status: 400 });
  }
}
