import { env } from "cloudflare:workers";
import { NextResponse } from "next/server";
import { previewBundle, previewJson } from "../_shared/preview";
import { GET as marketHistory } from "../market-history/route";
import { pagedRecords } from "../learning-history/route";

export const dynamic = "force-dynamic";
const POINT_LIMIT = 1200;
const WINDOWS: Record<string,number> = {"24h":86400,"7d":604800,"30d":2592000};
type Point = {model_identity:string;decision_time:string;chart_ordinal:number;[key:string]:unknown};
type Plan = {sql:string;values:unknown[]};
type Part = {first:number;last:number;size:number};

// Aligned interior blocks plus smaller edge blocks cover every ordinal exactly.
// Only the two outer edges can require individual source points (at most15 each).
function partition(first:number,last:number,maximum:number):Part[] {
  const parts:Part[]=[];
  for(let next=first;next<=last;) {
    let size=maximum;
    while(size>1 && (next%size!==0 || next+size-1>last)) size=size===16?1:size/4;
    const previous=parts.at(-1);
    if(previous?.size===size) previous.last=next+size-1;
    else parts.push({first:next,last:next+size-1,size});
    next+=size;
  }
  return parts;
}
const blockKey=(identity:string,size:number,bucket:number)=>
  `${identity}\0${String(size).padStart(16,"0")}\0${String(bucket).padStart(16,"0")}`;

async function queryPlans(db:D1Database,plans:Plan[]) {
  const statements=[];
  for(let i=0;i<plans.length;i+=4) {
    const chunk=plans.slice(i,i+4);
    statements.push(db.prepare(chunk.map(p=>p.sql).join(" UNION ALL ")).bind(...chunk.flatMap(p=>p.values)));
  }
  if(!statements.length)return [];
  const results=await db.batch<{kind:string;payload:string}>(statements);
  return results.flatMap(result=>result.results ?? []);
}

async function learningPoints(db:D1Database,resource:string,start:number,end:number) {
  // Unary plus removes the TEXT column affinity, preserving JSON-index equality.
  const endpoints=await db.prepare(`SELECT model_identity,
    (SELECT payload FROM learning_records INDEXED BY learning_records_resource_identity_time_idx WHERE resource=?
       AND json_extract(payload,'$.model_identity')=+counts.model_identity
       AND sort_epoch>=? AND sort_epoch<=? ORDER BY sort_epoch,record_key LIMIT 1) first_point,
    (SELECT payload FROM learning_records INDEXED BY learning_records_resource_identity_time_idx WHERE resource=?
       AND json_extract(payload,'$.model_identity')=+counts.model_identity
       AND sort_epoch>=? AND sort_epoch<=? ORDER BY sort_epoch DESC,record_key DESC LIMIT 1) last_point
    FROM learning_record_counts counts WHERE resource=? AND model_identity<>''`)
    .bind(resource,start,end,resource,start,end,resource).all<{model_identity:string;first_point:string|null;last_point:string|null}>();
  const series=new Map<string,{count:number;first:number;last:number}>();
  const plans:Plan[]=[];let expectedBlocks=0;
  for(const row of endpoints.results ?? []) {
    if(!row.first_point || !row.last_point)continue;
    const first=JSON.parse(row.first_point) as Point,last=JSON.parse(row.last_point) as Point;
    if(!Number.isSafeInteger(first.chart_ordinal)||!Number.isSafeInteger(last.chart_ordinal)
      ||first.chart_ordinal>last.chart_ordinal)throw new Error("chart ordinal unavailable");
    const count=last.chart_ordinal-first.chart_ordinal+1;
    series.set(row.model_identity,{count,first:first.chart_ordinal,last:last.chart_ordinal});
    const exact=(limit:number,descending=false):Plan=>({
      sql:`SELECT 'point' kind,payload FROM (SELECT payload FROM learning_records INDEXED BY learning_records_resource_identity_time_idx WHERE resource=?
        AND json_extract(payload,'$.model_identity')=? AND sort_epoch>=? AND sort_epoch<=?
        ORDER BY sort_epoch ${descending?"DESC":"ASC"},record_key ${descending?"DESC":"ASC"} LIMIT ?)`,
      values:[resource,row.model_identity,start,end,limit]});
    if(count<=POINT_LIMIT){plans.push(exact(count));continue;}
    let size=16;
    while(Math.ceil(count/size)>150)size*=4;
    for(const part of partition(first.chart_ordinal,last.chart_ordinal,size)) {
      if(part.size===1) {
        if(part.first!==first.chart_ordinal && part.last!==last.chart_ordinal)throw new Error("invalid chart edge");
        plans.push(exact(part.last-part.first+1,part.first!==first.chart_ordinal));
      } else {
        expectedBlocks+=(part.last-part.first+1)/part.size;
        plans.push({sql:"SELECT 'block' kind,payload FROM learning_records WHERE resource=? AND record_key>=? AND record_key<=?",
          values:[resource.replace("curve-","curve-tile-"),blockKey(row.model_identity,part.size,part.first/part.size),
            blockKey(row.model_identity,part.size,Math.floor(part.last/part.size))]});
      }
    }
    plans.push({sql:`SELECT 'point' kind,payload FROM learning_records INDEXED BY learning_records_chart_anchor_idx
      WHERE resource=? AND json_extract(payload,'$.model_identity')=? AND sort_epoch>=? AND sort_epoch<=?
        AND json_extract(payload,'$.chart_anchor')=1`,values:[resource,row.model_identity,start,end]});
  }
  const rows=await queryPlans(db,plans);
  if(rows.filter(row=>row.kind==="block").length!==expectedBlocks)throw new Error("chart blocks unavailable");
  const points=new Map<string,Point>();
  for(const row of rows) {
    const payload=JSON.parse(row.payload);
    for(const point of (row.kind==="block"?payload.points:[payload]) as Point[]) {
      const bounds=series.get(point.model_identity);
      if(bounds && point.chart_ordinal>=bounds.first && point.chart_ordinal<=bounds.last)
        points.set(`${point.model_identity}\0${point.chart_ordinal}`,point);
    }
  }
  const items=Array.from(series,([identity,meta])=>{
    const selected=Array.from(points.values()).filter(p=>p.model_identity===identity).sort((a,b)=>a.chart_ordinal-b.chart_ordinal);
    if(selected[0]?.chart_ordinal!==meta.first || selected.at(-1)?.chart_ordinal!==meta.last
      ||meta.count<=POINT_LIMIT&&selected.length!==meta.count)throw new Error("incomplete chart interval");
    return {model_identity:identity,cadence:resource.endsWith("30m")?"30m":"5m",points:selected,
      source_point_count:meta.count,chart_point_count:selected.length,chart_downsampled:selected.length<meta.count};
  });
  return {items,sourceCount:Array.from(series.values()).reduce((sum,r)=>sum+r.count,0),pointCount:points.size};
}

async function readChart(request:Request) {
  const url=new URL(request.url),type=url.searchParams.get("type")||"learning";
  if(type==="market")return marketHistory(request);
  if(!["learning","versions","version-group","execution-point","model"].includes(type))return NextResponse.json({error:"invalid chart"},{status:400});
  const resource=type==="versions"?"exact-version-group":url.searchParams.get("cadence")==="30m"?"exact-curve-30m":"exact-curve-5m";
  const range=url.searchParams.get("range")||"all",page=Number(url.searchParams.get("page")||0);
  if(range!=="all"&&!WINDOWS[range]||!Number.isSafeInteger(page)||page<0)return NextResponse.json({error:"invalid range"},{status:400});
  try {
    const db=env.DB as D1Database|undefined;if(!db)throw new Error("database unavailable");
    const state=await db.prepare("SELECT payload FROM chart_history_state WHERE id=1").first<{payload:string}>();
    if(!state)throw new Error("chart history unavailable");
    const completed=JSON.parse(state.payload);
    if(["version-group","execution-point","model"].includes(type)){
      url.searchParams.set("resource","exact-"+type);const response=await pagedRecords(db,url);
      if(previewBundle)response.headers.set("X-Aurum-Preview","current-read-only-d1");return response;
    }
    if(type==="learning"&&completed.chart_format!=="pyramid-v2")throw new Error("chart blocks pending");
    const bounds=await db.prepare(`SELECT
      (SELECT sort_epoch FROM learning_records WHERE resource=? ORDER BY sort_epoch,record_key LIMIT 1) first,
      (SELECT sort_epoch FROM learning_records WHERE resource=? ORDER BY sort_epoch DESC,record_key DESC LIMIT 1) last`)
      .bind(resource,resource).first<{first:number|null;last:number|null}>();
    if(bounds?.first==null||bounds.last==null)return NextResponse.json({items:[],source_count:0,chart_point_count:0,generated_at:completed.generated_at,history_start:null,history_end:null});
    let end=bounds.last-(WINDOWS[range]||0)*page,start=range==="all"?bounds.first:end-WINDOWS[range];
    if(url.searchParams.has("from"))start=Date.parse(url.searchParams.get("from")!)/1000;
    if(url.searchParams.has("to"))end=Date.parse(url.searchParams.get("to")!)/1000;
    if(!Number.isFinite(start)||!Number.isFinite(end)||start>end)return NextResponse.json({error:"invalid interval"},{status:400});
    let items:unknown[],sourceCount:number,pointCount:number;
    if(type==="versions") {
      const rows=await db.prepare("SELECT payload FROM learning_records WHERE resource=? AND sort_epoch>=? AND sort_epoch<=? ORDER BY sort_epoch,record_key").bind(resource,start,end).all<{payload:string}>();
      items=(rows.results??[]).map(row=>JSON.parse(row.payload));sourceCount=pointCount=items.length;
    } else ({items,sourceCount,pointCount}=await learningPoints(db,resource,start,end));
    const body={items,mode:sourceCount>pointCount?"sampled":"exact",generated_at:completed.generated_at,
      source_count:sourceCount,chart_point_count:pointCount,downsampled:sourceCount>pointCount,
      history_start:new Date(bounds.first*1000).toISOString(),history_end:new Date(bounds.last*1000).toISOString(),
      range_start:new Date(start*1000).toISOString(),range_end:new Date(end*1000).toISOString(),has_earlier:start>bounds.first,has_later:page>0};
    return previewBundle?previewJson(body,200,"current-read-only-d1"):NextResponse.json(body);
  } catch {return NextResponse.json({error:"完整图表历史尚未就绪，请稍后重试"},{status:503});}
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
