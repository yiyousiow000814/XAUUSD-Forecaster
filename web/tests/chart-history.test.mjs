import assert from "node:assert/strict";
import test from "node:test";
import {D1TestDatabase} from "./d1-test-database.mjs";
const {default:worker}=await import("../dist/server/index.js");
test("chart range reads real rows, only samples large ranges and refuses an unready baseline",async()=>{
 const db=new D1TestDatabase(["0000_sad_toad.sql","0005_learning_history.sql","0031_bounded_learning_history_reads.sql","0035_chart_anchor_reads.sql"]);
 const bindings={DB:db,ASSETS:{fetch:async()=>new Response("asset")}};
 globalThis.__AURUM_TEST_WORKER_ENV=bindings;
 const request=q=>worker.fetch(new Request("https://example.test/api/chart?type=learning&"+q),bindings,{waitUntil(){},passThroughOnException(){}});
 assert.equal((await request("range=all")).status,503);
 db.database.exec("CREATE TABLE chart_history_state(id INTEGER PRIMARY KEY,payload TEXT)");
 db.database.prepare("INSERT INTO chart_history_state VALUES(1,?)").run(JSON.stringify({generated_at:"2026-09-09T00:00:00Z",chart_format:"pyramid-v1"}));
 const put=db.database.prepare("INSERT INTO learning_records VALUES(?,?,?,?,?,?)");
 const first=Date.parse("2026-08-01T00:00:00Z")/1000;
 const originals=[];
 for(let i=0;i<4000;i++) {
   const epoch=first+i*900;
   const point={model_identity:"FULL",decision_time:new Date(epoch*1000).toISOString(),chart_ordinal:i,
     chart_anchor:[120,129,130].includes(i),cumulative_quote_return:i===123?999:Math.sin(i),
     ...(i===120?{model_version:"new"}:{}),...(i===130?{source_gap_before:true}:{})};
   originals.push(point);
   put.run("exact-curve-5m","FULL"+i,epoch,"a".repeat(64),JSON.stringify(point),"now");
 }
 for(let size=16;;size*=4) {
   for(let offset=0;offset<originals.length;offset+=size) {
     const chunk=originals.slice(offset,offset+size);
     const low=chunk.reduce((a,b)=>a.cumulative_quote_return<b.cumulative_quote_return?a:b);
     const high=chunk.reduce((a,b)=>a.cumulative_quote_return>b.cumulative_quote_return?a:b);
     const points=[...new Map([chunk[0],low,high,chunk.at(-1)].map(p=>[p.chart_ordinal,p])).values()];
     put.run("exact-curve-tile-5m",`FULL\0${String(size).padStart(16,"0")}\0${String(offset/size).padStart(16,"0")}`,
       first+offset*900,"a".repeat(64),JSON.stringify({model_identity:"FULL",block_size:size,first_ordinal:offset,source_point_count:chunk.length,points}),"now");
   }
   if(size>=originals.length)break;
 }
 const queryPlans=[];let returnedRows=0;
 const prepare=db.prepare.bind(db);
 db.prepare=sql=>{
   const statement=prepare(sql),bind=statement.bind.bind(statement);
   statement.bind=(...values)=>{
     const bound=bind(...values),execute=bound.execute.bind(bound);
     bound.execute=()=>{queryPlans.push(...db.database.prepare("EXPLAIN QUERY PLAN "+sql).all(...values));const result=execute();returnedRows+=result.results.length;return result;};
     return bound;
   };return statement;
 };
 const beforeReads=db.database.prepare("SELECT total_changes() n").get().n;
 const response=await request("range=all");
 if(process.env.WORKERS_CI_BRANCH && process.env.WORKERS_CI_BRANCH!=="main")
   assert.equal(response.headers.get("X-Aurum-Preview"),"current-read-only-d1");
 const all=await response.json();
 assert.ok(returnedRows<500);
 assert.ok(queryPlans.every(row=>!/^SCAN (learning_records|selected)/.test(row.detail)));
 assert.equal(all.source_count,4000);assert.equal(all.downsampled,true);
 const points=all.items[0].points;
 assert.equal(points[0].decision_time,"2026-08-01T00:00:00.000Z");
 assert.equal(points.at(-1).decision_time,new Date((first+3999*900)*1000).toISOString());
 assert.ok(points.some(p=>p.cumulative_quote_return===999));
 assert.ok(points.some(p=>p.model_version==="new"));
 assert.ok(points.some(p=>p.source_gap_before===true));
 assert.ok(points.some(p=>p.chart_ordinal===129));
 for(const [range,count] of [["24h",97],["7d",673],["30d",2881]]) {
   const body=await (await request("range="+range)).json();
   assert.equal(body.source_count,count);
   if(count<=1200){assert.equal(body.chart_point_count,count);assert.equal(body.downsampled,false);}
 }
 const narrow=await (await request("from=2026-08-01T00:00:00Z&to=2026-08-01T01:00:00Z")).json();
 assert.equal(narrow.source_count,5);assert.equal(narrow.chart_point_count,5);
 assert.equal((await request("from=bad")).status,400);
 assert.equal((await request("range=bad")).status,400);
 const invalidCursor=await worker.fetch(new Request("https://example.test/api/chart?type=model&cursor=bad"),bindings,{waitUntil(){},passThroughOnException(){}});
 assert.equal(invalidCursor.status,400);
 for(const begin of [0,1,15,63,111])for(const length of [1200,1201,2001,3200]) {
   const finish=Math.min(3999,begin+length-1);
   const from=new Date((first+begin*900)*1000).toISOString(),to=new Date((first+finish*900)*1000).toISOString();
   const body=await (await request(`from=${from}&to=${to}`)).json();
   const selected=body.items[0].points,original=originals.slice(begin,finish+1);
   assert.equal(body.source_count,original.length);
   assert.equal(selected[0].chart_ordinal,begin);assert.equal(selected.at(-1).chart_ordinal,finish);
   assert.equal(Math.min(...selected.map(p=>p.cumulative_quote_return)),Math.min(...original.map(p=>p.cumulative_quote_return)));
   assert.equal(Math.max(...selected.map(p=>p.cumulative_quote_return)),Math.max(...original.map(p=>p.cumulative_quote_return)));
   if(original.length<=1200)assert.deepEqual(selected,original);
 }
 assert.equal(db.database.prepare("SELECT total_changes() n").get().n,beforeReads);
 const missingKey=`FULL\0${String(64).padStart(16,"0")}\0${String(0).padStart(16,"0")}`;
 const missing=db.database.prepare("SELECT * FROM learning_records WHERE resource='exact-curve-tile-5m' AND record_key=?").get(missingKey);
 db.database.prepare("DELETE FROM learning_records WHERE resource=? AND record_key=?").run(missing.resource,missingKey);
 assert.equal((await request("range=all")).status,503);
 put.run(missing.resource,missingKey,missing.sort_epoch,missing.payload_hash,missing.payload,missing.received_at);
 assert.equal((await request("range=all")).status,200);
 db.database.close(); delete globalThis.__AURUM_TEST_WORKER_ENV;
});
