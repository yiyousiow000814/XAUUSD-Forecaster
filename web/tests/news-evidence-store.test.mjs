import assert from "node:assert/strict";
import test from "node:test";

import {
  activateNewsEvidenceSnapshot,
  cleanupNewsEvidenceSnapshots,
  NEWS_EVIDENCE_CURSOR_STALE,
  prepareNewsEvidenceSnapshot,
  readNewsEvidencePage,
  readPreviewNewsEvidencePage,
  stageNewsEvidenceBatch,
} from "../app/api/_shared/news-evidence-store.ts";
import { D1TestDatabase } from "./d1-test-database.mjs";

const id = digit => digit.repeat(64);
const item = (digit, minute = 0) => ({
  event_key: id(digit),
  source_published_time: `2026-08-19T10:${String(minute).padStart(2, "0")}:00+00:00`,
  collector_first_seen_time: `2026-08-19T10:${String(minute).padStart(2, "0")}:30+00:00`,
  broad_model_eligible: true,
  model_seen: minute % 2 === 0,
  canonical_headline: `evidence ${digit}`,
});

const database = () => new D1TestDatabase([
  "0021_paged_news_evidence.sql",
  "0030_news_evidence_cleanup_budget.sql",
  "0036_incremental_news_evidence.sql",
]);

test("pages immutable Preview evidence with generation-bound cursors", () => {
  const generationA = id("a");
  const rows = Array.from({ length: 25 }, (_, index) => ({
    ...item(((index % 9) + 1).toString(), index),
    event_key: index.toString(16).padStart(64, "0"),
  }));
  const snapshot = {
    snapshot_id: generationA,
    contract_version: "news-evidence-preview-v1",
    activated_at: "2026-08-19T10:00:00+00:00",
    items: rows,
  };
  const first = readPreviewNewsEvidencePage(snapshot, {
    mode: "all", rawCursor: null, page: 1, pageSize: 20,
  });
  assert.equal(first.items.length, 20);
  assert.equal(first.has_more, true);
  const second = readPreviewNewsEvidencePage(snapshot, {
    mode: "all", rawCursor: first.next_cursor, page: 2, pageSize: 20,
  });
  assert.equal(second.items.length, 5);
  assert.equal(second.has_more, false);
  assert.equal(new Set([...first.items, ...second.items].map(row => row.event_key)).size, 25);
  assert.throws(
    () => readPreviewNewsEvidencePage({ ...snapshot, snapshot_id: id("b") }, {
      mode: "all", rawCursor: first.next_cursor, page: 2, pageSize: 20,
    }),
    error => error.code === NEWS_EVIDENCE_CURSOR_STALE,
  );
});

test("stages replay-safe batches and binds every read cursor to one generation", async () => {
  const db = database();
  const generationA = id("a");
  const rowsA = [item("1", 1), item("2", 2)];
  assert.deepEqual(await prepareNewsEvidenceSnapshot(db, generationA, 2), {
    status: "OK", active: false, next_offset: 0,
  });
  await stageNewsEvidenceBatch(db, generationA, 0, [rowsA[0]]);
  assert.deepEqual(
    await stageNewsEvidenceBatch(db, generationA, 0, [rowsA[0]]),
    { status: "OK", received: 1, duplicate: true },
  );
  await assert.rejects(
    stageNewsEvidenceBatch(db, generationA, 0, [item("3", 3)]),
    error => error.code === "NEWS_EVIDENCE_REPLAY_MISMATCH",
  );
  await assert.rejects(
    stageNewsEvidenceBatch(db, generationA, 2, [rowsA[1]]),
    error => error.code === "NEWS_EVIDENCE_BATCH_OVERFLOW"
      || error.code === "NEWS_EVIDENCE_OFFSET_MISMATCH",
  );
  await assert.rejects(
    activateNewsEvidenceSnapshot(db, generationA, 2),
    error => error.code === "NEWS_EVIDENCE_INCOMPLETE",
  );
  await stageNewsEvidenceBatch(db, generationA, 1, [rowsA[1]]);
  await activateNewsEvidenceSnapshot(db, generationA, 2);
  const first = await readNewsEvidencePage(db, {
    mode: "all", rawCursor: null, page: 1, pageSize: 1,
  });
  assert.equal(first.snapshot_id, generationA);
  assert.match(first.next_cursor, new RegExp(`^\\["${generationA}"`));

  const generationB = id("b");
  await prepareNewsEvidenceSnapshot(db, generationB, 1);
  await stageNewsEvidenceBatch(db, generationB, 0, [item("3", 3)]);
  await activateNewsEvidenceSnapshot(db, generationB, 1);
  await assert.rejects(
    readNewsEvidencePage(db, {
      mode: "all", rawCursor: first.next_cursor, page: 2, pageSize: 1,
    }),
    error => error.code === NEWS_EVIDENCE_CURSOR_STALE
      && error.details.active_snapshot_id === generationB,
  );
  const current = await readNewsEvidencePage(db, {
    mode: "all", rawCursor: null, page: 1, pageSize: 20,
  });
  assert.equal(current.snapshot_id, generationB);
  assert.deepEqual(current.items.map(row => row.event_key), [id("3")]);
});

test("activates first-ever and replacement empty generations idempotently", async () => {
  const firstDb = database();
  const emptyA = id("a");
  await prepareNewsEvidenceSnapshot(firstDb, emptyA, 0);
  const activated = await activateNewsEvidenceSnapshot(firstDb, emptyA, 0);
  assert.equal(activated.count, 0);
  const replay = await activateNewsEvidenceSnapshot(firstDb, emptyA, 0);
  assert.equal(replay.unchanged, true);
  const emptyPage = await readNewsEvidencePage(firstDb, {
    mode: "all", rawCursor: null, page: 1, pageSize: 20,
  });
  assert.deepEqual(emptyPage.items, []);
  assert.equal(emptyPage.snapshot_id, emptyA);

  const replacementDb = database();
  const full = id("c");
  const hundred = Array.from({ length: 100 }, (_, index) => ({
    ...item((index % 9 + 1).toString(), index % 60),
    event_key: index.toString(16).padStart(64, "0"),
  }));
  await prepareNewsEvidenceSnapshot(replacementDb, full, hundred.length);
  for (let offset = 0; offset < hundred.length; offset += 20) {
    await stageNewsEvidenceBatch(
      replacementDb, full, offset, hundred.slice(offset, offset + 20),
    );
  }
  await activateNewsEvidenceSnapshot(replacementDb, full, hundred.length);
  const emptyB = id("d");
  await prepareNewsEvidenceSnapshot(replacementDb, emptyB, 0);
  await activateNewsEvidenceSnapshot(replacementDb, emptyB, 0);
  const state = replacementDb.database.prepare(
    "SELECT active_snapshot_id,record_count FROM news_evidence_publication WHERE id=1",
  ).get();
  assert.deepEqual({ ...state }, { active_snapshot_id: emptyB, record_count: 0 });

  const interrupted = id("e");
  await prepareNewsEvidenceSnapshot(replacementDb, interrupted, 2);
  await stageNewsEvidenceBatch(replacementDb, interrupted, 0, [item("7", 7)]);
  await assert.rejects(
    activateNewsEvidenceSnapshot(replacementDb, interrupted, 0),
    error => error.code === "NEWS_EVIDENCE_INCOMPLETE",
  );
  assert.equal(replacementDb.database.prepare(
    "SELECT active_snapshot_id FROM news_evidence_publication WHERE id=1",
  ).get().active_snapshot_id, emptyB);
});

test("bounded receipt cleanup preserves current data and fresh transfers", async () => {
  const db = database();
  for (const generation of [id("a"), id("b")]) {
    await prepareNewsEvidenceSnapshot(db, generation, 25);
    for (let i=0;i<25;i++) await stageNewsEvidenceBatch(db,generation,i,[{
      ...item("1",i),event_key:i.toString(16).padStart(64,"0"),
    }]);
    await activateNewsEvidenceSnapshot(db,generation,25);
  }
  db.database.exec("UPDATE news_evidence_receipts SET updated_at='2020-01-01'");
  const first=await cleanupNewsEvidenceSnapshots(db,id("b"));
  assert.equal(first.deleted_records,0);
  assert.equal(first.deleted_batches,20);
  assert.equal(first.cleanup_pending,true);
  const second=await cleanupNewsEvidenceSnapshots(db,id("b"));
  assert.equal(second.deleted_batches,5);
  assert.equal(second.cleanup_pending,false);
  assert.equal((await readNewsEvidencePage(db,{mode:"all",rawCursor:null,page:1,pageSize:50})).items.length,25);
  await prepareNewsEvidenceSnapshot(db,id("c"),1);
  await stageNewsEvidenceBatch(db,id("c"),0,[item("f")]);
  db.database.exec("UPDATE news_evidence_receipts SET updated_at='2020-01-01'");
  assert.equal((await cleanupNewsEvidenceSnapshots(db,id("b"))).deleted_batches,0);
});

test("prepare repairs the first missing receipt and can resume after restart",async()=>{
  const db=database(); const g=id("c");
  await prepareNewsEvidenceSnapshot(db,g,3);
  for(let i=0;i<3;i++) await stageNewsEvidenceBatch(db,g,i,[item(String(i+1),i)]);
  db.database.prepare("DELETE FROM news_evidence_receipts WHERE snapshot_id=? AND batch_offset=1").run(g);
  assert.deepEqual(await prepareNewsEvidenceSnapshot(db,g,3),{
    status:"OK",active:false,next_offset:1,repaired_from:3,
  });
  await stageNewsEvidenceBatch(db,g,1,[item("2",1),item("3",2)]);
  await activateNewsEvidenceSnapshot(db,g,3);
  assert.equal((await readNewsEvidencePage(db,{mode:"all",rawCursor:null,page:1,pageSize:20})).items.length,3);
});

test("500-row replacement writes only changed current records and counts receipt overhead",async()=>{
  const db=database();
  db.database.exec(`CREATE TABLE mutation_audit(kind TEXT);
    CREATE TRIGGER evidence_insert AFTER INSERT ON news_evidence_current BEGIN INSERT INTO mutation_audit VALUES ('insert'); END;
    CREATE TRIGGER evidence_update AFTER UPDATE ON news_evidence_current BEGIN INSERT INTO mutation_audit VALUES ('update'); END;
    CREATE TRIGGER evidence_delete AFTER DELETE ON news_evidence_current BEGIN INSERT INTO mutation_audit VALUES ('delete'); END;`);
  const rows=Array.from({length:500},(_,i)=>({...item("1",i%60),event_key:i.toString(16).padStart(64,"0")}));
  const publish=async(g,items)=>{
    await prepareNewsEvidenceSnapshot(db,g,items.length);
    for(let i=0;i<items.length;i+=8) await stageNewsEvidenceBatch(db,g,i,items.slice(i,i+8));
    await activateNewsEvidenceSnapshot(db,g,items.length);
  };
  await publish(id("a"),rows);
  db.database.exec('DELETE FROM mutation_audit');
  rows[0]={...rows[0],canonical_headline:"changed"};
  const before=db.database.prepare('SELECT total_changes() AS n').get().n;
  await publish(id("b"),rows);
  const total=db.database.prepare('SELECT total_changes() AS n').get().n-before;
  assert.deepEqual(db.database.prepare('SELECT kind FROM mutation_audit').all().map(r=>r.kind),['update']);
  // Actual table writes include 63 receipts and 63 offset updates, not only the changed news.
  assert.ok(total>=128 && total<140, `logical writes including audit trigger: ${total}`);
  db.database.exec('DELETE FROM mutation_audit');
  await publish(id("c"),rows);
  assert.equal(db.database.prepare('SELECT count(*) AS n FROM mutation_audit').get().n,0);
  const beforeReplay=db.database.prepare('SELECT total_changes() AS n').get().n;
  await prepareNewsEvidenceSnapshot(db,id("c"),rows.length);
  await activateNewsEvidenceSnapshot(db,id("c"),rows.length);
  assert.equal(db.database.prepare('SELECT total_changes() AS n').get().n,beforeReplay);
  db.database.exec('DELETE FROM mutation_audit');
  await publish(id("d"),rows.slice(1));
  assert.deepEqual(db.database.prepare('SELECT kind FROM mutation_audit').all().map(r=>r.kind),['delete']);
});

test("staged changes stay invisible and a competing publication requires rebase",async()=>{
  const db=database();
  await prepareNewsEvidenceSnapshot(db,id("a"),1);await stageNewsEvidenceBatch(db,id("a"),0,[item("1")]);await activateNewsEvidenceSnapshot(db,id("a"),1);
  await prepareNewsEvidenceSnapshot(db,id("b"),1);await stageNewsEvidenceBatch(db,id("b"),0,[item("2")]);
  assert.equal((await readNewsEvidencePage(db,{mode:"all",rawCursor:null,page:1,pageSize:20})).items[0].event_key,id("1"));
  await prepareNewsEvidenceSnapshot(db,id("c"),1);await stageNewsEvidenceBatch(db,id("c"),0,[item("3")]);await activateNewsEvidenceSnapshot(db,id("c"),1);
  await assert.rejects(activateNewsEvidenceSnapshot(db,id("b"),1),e=>e.code==='NEWS_EVIDENCE_INCOMPLETE');
  assert.equal((await prepareNewsEvidenceSnapshot(db,id("b"),1)).next_offset,0);
  await stageNewsEvidenceBatch(db,id("b"),0,[item("2")]);await activateNewsEvidenceSnapshot(db,id("b"),1);
});

test("duplicate membership cannot activate and keeps previous publication intact",async()=>{
  const db=database();await prepareNewsEvidenceSnapshot(db,id("a"),0);await activateNewsEvidenceSnapshot(db,id("a"),0);
  await prepareNewsEvidenceSnapshot(db,id("b"),2);await stageNewsEvidenceBatch(db,id("b"),0,[item("1"),item("1")]);
  await assert.rejects(activateNewsEvidenceSnapshot(db,id("b"),2),e=>e.code==='NEWS_EVIDENCE_INCOMPLETE');
  assert.equal((await readNewsEvidencePage(db,{mode:"all",rawCursor:null,page:1,pageSize:20})).snapshot_id,id("a"));
});

test("migration retains the last complete legacy publication without rewriting its audit rows",async()=>{
  const db=new D1TestDatabase(["0021_paged_news_evidence.sql"]);
  const value=item("1");
  db.database.prepare("INSERT INTO news_evidence_records VALUES (?,?,?,?,?,?,?,?)")
    .run(id("a"),value.event_key,0,value.source_published_time,1,1,JSON.stringify(value),"2026-09-09");
  db.database.prepare("INSERT INTO news_evidence_state VALUES (1,?,?,1,?)")
    .run(id("a"),"news-evidence-paged-v2","2026-09-09");
  db.applyMigration("0036_incremental_news_evidence.sql");
  assert.deepEqual((await readNewsEvidencePage(db,{mode:"all",rawCursor:null,page:1,pageSize:20})).items,[value]);
  assert.equal((await prepareNewsEvidenceSnapshot(db,id("a"),1)).active,true);
  assert.equal(db.database.prepare("SELECT count(*) AS n FROM news_evidence_records").get().n,1);
});

test("reader detects publication changing between its metadata and page SQL",async()=>{
  const db=database();
  await prepareNewsEvidenceSnapshot(db,id("a"),0);await activateNewsEvidenceSnapshot(db,id("a"),0);
  const original=db.prepare.bind(db);let changed=false;
  db.prepare=sql=>{
    if(sql.includes("WITH page AS")&&!changed){
      changed=true;db.database.prepare("UPDATE news_evidence_publication SET active_snapshot_id=?").run(id("b"));
    }
    return original(sql);
  };
  await assert.rejects(readNewsEvidencePage(db,{mode:"all",rawCursor:null,page:1,pageSize:20}),
    e=>e.code===NEWS_EVIDENCE_CURSOR_STALE);
});

test("activation rolls back changed rows when staging disappears at the transaction boundary",async()=>{
  const db=database();
  await prepareNewsEvidenceSnapshot(db,id("a"),1);await stageNewsEvidenceBatch(db,id("a"),0,[item("1")]);
  await activateNewsEvidenceSnapshot(db,id("a"),1);
  await prepareNewsEvidenceSnapshot(db,id("b"),1);await stageNewsEvidenceBatch(db,id("b"),0,[item("2")]);
  const batch=db.batch.bind(db);
  db.batch=statements=>{
    db.database.prepare("DELETE FROM news_evidence_transfers WHERE snapshot_id=?").run(id("b"));
    return batch(statements);
  };
  await assert.rejects(activateNewsEvidenceSnapshot(db,id("b"),1));
  assert.deepEqual((await readNewsEvidencePage(db,{mode:"all",rawCursor:null,page:1,pageSize:20})).items,[item("1")]);
});
