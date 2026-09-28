import assert from "node:assert/strict";
import test from "node:test";
import {loadNewsDetail, readNewsDetail} from "../app/_lib/news-detail-loader.ts";

const originalFetch = globalThis.fetch, originalWindow = globalThis.window;
globalThis.window = {setTimeout, clearTimeout};
test.after(() => {globalThis.fetch = originalFetch; globalThis.window = originalWindow;});
const ref = n => ({source:"wire", source_item_id:`article-${n}`, revision_number:1, cluster_id:`cluster-${n}`, content_hash:String(n).padStart(64,"0")});
const key = n => String(n).padStart(64,"a");
const response = body => new Response(JSON.stringify(body), {headers:{"content-type":"application/json"}});

test("raw batch and current article share detail in both directions without losing explanation", async () => {
  const calls=[];
  globalThis.fetch=async url=>{
    calls.push(url); const query=new URL(url,"https://test").searchParams;
    if(query.has("article")) return response({detail_key:key(2),payload:{summary_zh:"second",impact_reason_zh:"second explanation"}});
    return response({items:{[key(1)]:{payload:{summary_zh:"first",content_hash:ref(1).content_hash}}}});
  };
  const raw={...ref(1),detail_key:key(1),impact_reason_zh:"first explanation"};
  const [a,b]=await Promise.all([loadNewsDetail(raw),loadNewsDetail(raw)]);
  assert.equal(a,b); assert.equal(calls.length,1);
  assert.equal((await loadNewsDetail(ref(1))).payload.impact_reason_zh,"first explanation");
  assert.equal(calls.length,1);
  await loadNewsDetail(ref(2));
  assert.equal((await loadNewsDetail({...ref(2),detail_key:key(2)})).payload.summary_zh,"second");
  assert.equal(calls.length,2);
  assert.equal(readNewsDetail({...ref(1),content_hash:"f".repeat(64)}),null);
  assert.equal(readNewsDetail({...ref(1),revision_number:2}),null);
  const now=Date.now; Date.now=()=>now()+16_000;
  try {assert.equal(readNewsDetail(ref(1)),null); assert.ok(readNewsDetail(raw));}
  finally {Date.now=now;}
});

test("bounded batches isolate missing details and permit successful retry", async () => {
  let active=0, peak=0; const sizes=[]; let missing=true;
  globalThis.fetch=async url=>{
    peak=Math.max(peak,++active);
    const keys=new URL(url,"https://test").searchParams.get("keys").split(","); sizes.push(keys.length);
    await new Promise(resolve=>setTimeout(resolve,2)); active--;
    return response({items:Object.fromEntries(keys.filter(k=>!missing||k!==key(10)).map(k=>[k,{payload:{summary_zh:k}}]))});
  };
  const targets=Array.from({length:29},(_,i)=>({detail_key:key(i+10)}));
  const result=await Promise.allSettled(targets.map(loadNewsDetail));
  assert.equal(result[0].status,"rejected"); assert.ok(result.slice(1).every(r=>r.status==="fulfilled"));
  assert.deepEqual(sizes,[12,12,5]); assert.ok(peak<=2);
  missing=false; assert.equal((await loadNewsDetail(targets[0])).payload.summary_zh,key(10));
  const before=sizes.length; await loadNewsDetail(targets[1]); assert.equal(sizes.length,before);
});
