import assert from "node:assert/strict";
import test from "node:test";
import {D1TestDatabase} from "./d1-test-database.mjs";
const {default:worker}=await import("../dist/server/index.js");
test("chart range reads real rows, only samples large ranges and refuses an unready baseline",async()=>{
 const db=new D1TestDatabase(["0000_sad_toad.sql","0005_learning_history.sql","0031_bounded_learning_history_reads.sql"]);
 const bindings={DB:db,ASSETS:{fetch:async()=>new Response("asset")}};
 globalThis.__AURUM_TEST_WORKER_ENV=bindings;
 const request=q=>worker.fetch(new Request("https://example.test/api/chart?type=learning&"+q),bindings,{waitUntil(){},passThroughOnException(){}});
 assert.equal((await request("range=all")).status,503);
 db.database.exec("CREATE TABLE chart_history_state(id INTEGER PRIMARY KEY,payload TEXT)");
 db.database.prepare("INSERT INTO chart_history_state VALUES(1,?)").run(JSON.stringify({generated_at:"2026-09-09T00:00:00Z"}));
 const put=db.database.prepare("INSERT INTO learning_records VALUES(?,?,?,?,?,?)");
 const first=Date.parse("2026-08-01T00:00:00Z")/1000;
 for(let i=0;i<4000;i++) {
   const epoch=first+i*900;
   put.run("exact-curve-5m","FULL"+i,epoch,"a".repeat(64),JSON.stringify({model_identity:"FULL",decision_time:new Date(epoch*1000).toISOString(),cumulative_quote_return:i===123?999:Math.sin(i),...(i===120?{model_version:"new"}:{})}),"now");
 }
 const beforeReads=db.database.prepare("SELECT total_changes() n").get().n;
 const response=await request("range=all");
 if(process.env.WORKERS_CI_BRANCH && process.env.WORKERS_CI_BRANCH!=="main")
   assert.equal(response.headers.get("X-Aurum-Preview"),"current-read-only-d1");
 const all=await response.json();
 assert.equal(all.source_count,4000);assert.equal(all.downsampled,true);
 const points=all.items[0].points;
 assert.equal(points[0].decision_time,"2026-08-01T00:00:00.000Z");
 assert.equal(points.at(-1).decision_time,new Date((first+3999*900)*1000).toISOString());
 assert.ok(points.some(p=>p.cumulative_quote_return===999));
 assert.ok(points.some(p=>p.model_version==="new"));
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
 assert.equal(db.database.prepare("SELECT total_changes() n").get().n,beforeReads);
 db.database.close(); delete globalThis.__AURUM_TEST_WORKER_ENV;
});
