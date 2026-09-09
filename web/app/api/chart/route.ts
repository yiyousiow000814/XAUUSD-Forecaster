import { env } from "cloudflare:workers";
import { NextResponse } from "next/server";
import { previewBundle, previewJson } from "../_shared/preview";
import { GET as marketHistory } from "../market-history/route";
import { pagedRecords } from "../learning-history/route";

export const dynamic = "force-dynamic";
const POINT_LIMIT = 1200;
const WINDOWS: Record<string, number> = { "24h":86400, "7d":604800, "30d":2592000 };
type Row = { resource: string; sort_epoch: number; payload: Record<string, unknown> };

async function readChart(request: Request) {
  const url = new URL(request.url);
  const type = url.searchParams.get("type") || "learning";
  if (type === "market") return marketHistory(request);
  if (!["learning","versions","version-group","execution-point","model"].includes(type)) return NextResponse.json({error:"invalid chart"},{status:400});
  const resource = type === "versions" ? "version-group"
    : url.searchParams.get("cadence") === "30m" ? "curve-30m" : "curve-5m";
  const range = url.searchParams.get("range") || "all";
  const page = Number(url.searchParams.get("page") || 0);
  if ((range !== "all" && !WINDOWS[range]) || !Number.isSafeInteger(page) || page<0)
    return NextResponse.json({error:"invalid range"},{status:400});
  try {
    let rows: Row[]; let historyStart: number; let historyEnd: number;
    let generatedAt: string; let sourceCount: number; let start: number; let end: number;
    let seriesCounts: Record<string, number> = {};
    {
      const db=env.DB as D1Database | undefined;
      if (!db) throw new Error("database unavailable");
      const state=await db.prepare("SELECT payload FROM chart_history_state WHERE id=1").first<{payload:string}>();
      if (!state) throw new Error("完整图表历史正在同步");
      generatedAt=JSON.parse(state.payload).generated_at;
      if (["version-group","execution-point","model"].includes(type)) {
        url.searchParams.set("resource", "exact-" + type);
        const response = await pagedRecords(db, url);
        if (previewBundle) response.headers.set("X-Aurum-Preview", "current-read-only-d1");
        return response;
      }
      const bounds=await db.prepare(`SELECT
        (SELECT sort_epoch FROM learning_records WHERE resource=? ORDER BY sort_epoch,record_key LIMIT 1) first,
        (SELECT sort_epoch FROM learning_records WHERE resource=? ORDER BY sort_epoch DESC,record_key DESC LIMIT 1) last`)
        .bind("exact-"+resource,"exact-"+resource).first<{first:number|null;last:number|null}>();
      if (bounds?.first==null || bounds.last==null) return NextResponse.json({items:[],source_count:0,chart_point_count:0,generated_at:generatedAt,history_start:null,history_end:null});
      historyStart=bounds.first;historyEnd=bounds.last;
      end=historyEnd-(WINDOWS[range] || 0)*page;
      start=range==="all"?historyStart:end-WINDOWS[range];
      const from=url.searchParams.get("from"),to=url.searchParams.get("to");
      if (from) start=Date.parse(from)/1000;
      if (to) end=Date.parse(to)/1000;
      if (!Number.isFinite(start)||!Number.isFinite(end)||start>end) return NextResponse.json({error:"invalid interval"},{status:400});
      // One SQL snapshot owns selection, counts and sampling. Only oversized
      // series enter the window calculations; small ranges return every row.
      const result=await db.prepare(`WITH selected AS MATERIALIZED (
        SELECT sort_epoch,record_key,payload,
          json_extract(payload,'$.model_identity') identity,
          COALESCE(json_extract(payload,'$.cumulative_quote_return'),0) value
        FROM learning_records WHERE resource=? AND sort_epoch>=? AND sort_epoch<=?
      ), stats AS MATERIALIZED (
        SELECT identity,count(*) series_count FROM selected GROUP BY identity
      ), numbered AS (
        SELECT selected.*,stats.series_count,
          row_number() OVER (PARTITION BY identity ORDER BY sort_epoch,record_key) n
        FROM selected JOIN stats USING(identity) WHERE series_count>?
      ), buckets AS (
        SELECT *,CAST((n-1)*150/MAX(series_count,1) AS INTEGER) bucket FROM numbered
      ), extremes AS (
        SELECT *,row_number() OVER (PARTITION BY identity,bucket ORDER BY value,sort_epoch) lo,
          row_number() OVER (PARTITION BY identity,bucket ORDER BY value DESC,sort_epoch) hi FROM buckets
      ), visible AS (
        SELECT payload,sort_epoch,record_key FROM selected JOIN stats USING(identity)
        WHERE series_count<=?
        UNION ALL
        SELECT payload,sort_epoch,record_key FROM extremes WHERE n=1 OR n=series_count
          OR lo=1 OR hi=1 OR json_extract(payload,'$.model_version') IS NOT NULL
          OR json_extract(payload,'$.source_gap_before')=1
      ) SELECT (SELECT COALESCE(sum(series_count),0) FROM stats) source_count,
        (SELECT json_group_object(identity,series_count) FROM stats) series_counts,
        COALESCE(json_group_array(json(payload)),'[]') items
        FROM (SELECT payload FROM visible ORDER BY sort_epoch,record_key)`)
        .bind("exact-"+resource,start,end,POINT_LIMIT,POINT_LIMIT)
        .first<{source_count:number;series_counts:string;items:string}>();
      seriesCounts=JSON.parse(result?.series_counts || "{}");
      sourceCount=Number(result?.source_count || 0);
      rows=JSON.parse(result?.items || "[]").map((payload:Record<string,unknown>)=>({payload}));
    }
    const points=rows.map(row=>row.payload);
    const items=type==="versions"?points:Array.from(new Set(points.map(p=>p.model_identity))).map(identity=>{
      const subset=points.filter(p=>p.model_identity===identity);
      return {model_identity:identity,cadence:resource==="curve-30m"?"30m":"5m",points:subset,
        source_point_count:seriesCounts[String(identity)] ?? subset.length,
        chart_point_count:subset.length,chart_downsampled:(seriesCounts[String(identity)] ?? subset.length)>subset.length};
    });
    const body={items,mode:sourceCount>points.length?"sampled":"exact",generated_at:generatedAt,
      source_count:sourceCount,chart_point_count:points.length,downsampled:sourceCount>points.length,
      history_start:new Date(historyStart*1000).toISOString(),history_end:new Date(historyEnd*1000).toISOString(),
      range_start:new Date(start*1000).toISOString(),range_end:new Date(end*1000).toISOString(),
      has_earlier:start>historyStart,has_later:page>0};
    return previewBundle?previewJson(body, 200, "current-read-only-d1"):NextResponse.json(body);
  } catch {
    return NextResponse.json({error:"完整图表历史尚未就绪，请稍后重试"},{status:503});
  }
}

export async function GET(request: Request) {
  if (previewBundle) return readChart(request);
  const cache=(globalThis.caches as CacheStorage & {default?:Cache} | undefined)?.default;
  const key=new Request(request.url,{method:"GET"});
  const existing=cache ? await cache.match(key) : null;
  if (existing) return existing;
  const response=await readChart(request);
  if (response.ok && cache) {
    response.headers.set("Cache-Control","public, max-age=30");
    await cache.put(key,response.clone());
  }
  return response;
}
