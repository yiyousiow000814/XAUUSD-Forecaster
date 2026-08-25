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

const database = () => new D1TestDatabase(["0021_paged_news_evidence.sql"]);

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
    "SELECT active_snapshot_id,record_count FROM news_evidence_state WHERE id=1",
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
    "SELECT active_snapshot_id FROM news_evidence_state WHERE id=1",
  ).get().active_snapshot_id, emptyB);
});

test("bounded cleanup retains the active generation", async () => {
  const db = database();
  const oldGeneration = id("a");
  const activeGeneration = id("b");
  await prepareNewsEvidenceSnapshot(db, oldGeneration, 1);
  await stageNewsEvidenceBatch(db, oldGeneration, 0, [item("1", 1)]);
  await activateNewsEvidenceSnapshot(db, oldGeneration, 1);
  await prepareNewsEvidenceSnapshot(db, activeGeneration, 1);
  await stageNewsEvidenceBatch(db, activeGeneration, 0, [item("2", 2)]);
  await activateNewsEvidenceSnapshot(db, activeGeneration, 1);
  db.database.exec(
    "UPDATE news_evidence_records SET received_at='2020-01-01T00:00:00.000Z' "
    + `WHERE snapshot_id='${oldGeneration}'`,
  );
  db.database.exec(
    "UPDATE news_evidence_batches SET updated_at='2020-01-01T00:00:00.000Z' "
    + `WHERE snapshot_id='${oldGeneration}'`,
  );
  const cleanup = await cleanupNewsEvidenceSnapshots(db, activeGeneration);
  assert.deepEqual(cleanup, {
    status: "OK",
    cleanup: "advanced",
    deleted_records: 1,
    deleted_batches: 1,
    deleted_staging: 0,
    cleanup_pending: false,
  });
  assert.equal(db.database.prepare(
    "SELECT count(*) AS count FROM news_evidence_records WHERE snapshot_id=?",
  ).get(activeGeneration).count, 1);
  assert.equal(db.database.prepare(
    "SELECT count(*) AS count FROM news_evidence_records WHERE snapshot_id=?",
  ).get(oldGeneration).count, 0);
});

test("cleanup retains fresh staging and prepare repairs its first receipt gap", async () => {
  const db = database();
  const activeGeneration = id("a");
  const stagingGeneration = id("b");
  const rows = [item("1", 1), item("2", 2), item("3", 3)];
  await prepareNewsEvidenceSnapshot(db, activeGeneration, 1);
  await stageNewsEvidenceBatch(db, activeGeneration, 0, [rows[0]]);
  await activateNewsEvidenceSnapshot(db, activeGeneration, 1);
  await prepareNewsEvidenceSnapshot(db, stagingGeneration, rows.length);
  await stageNewsEvidenceBatch(db, stagingGeneration, 0, [rows[0]]);
  await stageNewsEvidenceBatch(db, stagingGeneration, 1, [rows[1]]);
  await stageNewsEvidenceBatch(db, stagingGeneration, 2, [rows[2]]);
  db.database.exec(
    "UPDATE news_evidence_records SET received_at='2020-01-01T00:00:00.000Z' "
    + `WHERE snapshot_id='${stagingGeneration}'`,
  );
  db.database.exec(
    "UPDATE news_evidence_batches SET updated_at='2020-01-01T00:00:00.000Z' "
    + `WHERE snapshot_id='${stagingGeneration}'`,
  );

  const cleanup = await cleanupNewsEvidenceSnapshots(db, activeGeneration);
  assert.equal(cleanup.deleted_records, 0);
  assert.equal(cleanup.deleted_batches, 0);
  db.database.exec(
    `DELETE FROM news_evidence_records WHERE snapshot_id='${stagingGeneration}' AND ordinal=1`,
  );
  const repaired = await prepareNewsEvidenceSnapshot(db, stagingGeneration, rows.length);
  assert.deepEqual(repaired, {
    status: "OK", active: false, next_offset: 1, repaired_from: 3,
  });
  assert.equal(db.database.prepare(
    "SELECT count(*) AS count FROM news_evidence_records WHERE snapshot_id=?",
  ).get(stagingGeneration).count, 1);
  await stageNewsEvidenceBatch(db, stagingGeneration, 1, rows.slice(1));
  await activateNewsEvidenceSnapshot(db, stagingGeneration, rows.length);
  assert.equal((await readNewsEvidencePage(db, {
    mode: "all", rawCursor: null, page: 1, pageSize: 20,
  })).items.length, rows.length);
});

test("cleanup feedback remains pending until bounded debt is drained", async () => {
  const db = database();
  const oldGeneration = id("c");
  const activeGeneration = id("d");
  const oldItems = Array.from({ length: 205 }, (_, index) => ({
    ...item(((index % 9) + 1).toString(), index % 60),
    event_key: index.toString(16).padStart(64, "0"),
  }));
  await prepareNewsEvidenceSnapshot(db, oldGeneration, oldItems.length);
  await stageNewsEvidenceBatch(db, oldGeneration, 0, oldItems);
  await activateNewsEvidenceSnapshot(db, oldGeneration, oldItems.length);
  await prepareNewsEvidenceSnapshot(db, activeGeneration, 1);
  await stageNewsEvidenceBatch(db, activeGeneration, 0, [item("f", 1)]);
  await activateNewsEvidenceSnapshot(db, activeGeneration, 1);
  db.database.exec(
    "UPDATE news_evidence_records SET received_at='2020-01-01T00:00:00.000Z' "
    + `WHERE snapshot_id='${oldGeneration}'`,
  );
  db.database.exec(
    "UPDATE news_evidence_batches SET updated_at='2020-01-01T00:00:00.000Z' "
    + `WHERE snapshot_id='${oldGeneration}'`,
  );

  const first = await cleanupNewsEvidenceSnapshots(db, activeGeneration);
  assert.equal(first.deleted_records, 200);
  assert.equal(first.cleanup_pending, true);
  const second = await cleanupNewsEvidenceSnapshots(db, activeGeneration);
  assert.equal(second.deleted_records, 5);
  assert.equal(second.cleanup_pending, false);
  assert.equal(db.database.prepare(
    "SELECT count(*) AS count FROM news_evidence_records WHERE snapshot_id=?",
  ).get(activeGeneration).count, 1);
});
