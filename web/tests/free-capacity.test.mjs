import assert from "node:assert/strict";
import test from "node:test";

import { D1TestDatabase } from "./d1-test-database.mjs";
import {
  writeDashboardSnapshotBytes,
} from "../app/api/_shared/dashboard-snapshot.ts";

const migrations = [
  "0000_sad_toad.sql",
  "0005_learning_history.sql",
  "0031_bounded_learning_history_reads.sql",
];

test("learning history keeps exact counts while its page lookup uses the identity index", async () => {
  const db = new D1TestDatabase(migrations);
  const insert = db.database.prepare(
    `INSERT INTO learning_records
       (resource,record_key,sort_epoch,payload_hash,payload,received_at)
     VALUES (?,?,?,?,?,?)`,
  );
  for (let index = 0; index < 2_000; index += 1) {
    const identity = index % 2 ? "FULL" : "MARKET_ONLY";
    insert.run(
      "curve-5m", `${identity}\u0000${index}`, index, "a".repeat(64),
      JSON.stringify({ model_identity: identity, value: index }),
      "2026-09-03T00:00:00Z",
    );
  }
  assert.deepEqual(db.database.prepare(
    `SELECT model_identity,record_count FROM learning_record_counts
     WHERE resource='curve-5m' ORDER BY model_identity`,
  ).all().map(row => ({ ...row })), [
    { model_identity: "", record_count: 2_000 },
    { model_identity: "FULL", record_count: 1_000 },
    { model_identity: "MARKET_ONLY", record_count: 1_000 },
  ]);
  const plan = db.database.prepare(
    `EXPLAIN QUERY PLAN SELECT sort_epoch,record_key,payload FROM learning_records
     WHERE resource=? AND json_extract(payload,'$.model_identity')=?
     ORDER BY sort_epoch DESC,record_key DESC LIMIT ?`,
  ).all("curve-5m", "FULL", 7).map(row => row.detail).join("\n");
  assert.match(plan, /learning_records_resource_identity_time_idx/);
  assert.doesNotMatch(plan, /SCAN learning_records(?:\s|$)/);

  if (!process.env.WORKERS_CI_BRANCH || process.env.WORKERS_CI_BRANCH === "main") {
    const previousEnv = globalThis.__AURUM_TEST_WORKER_ENV;
    const bindings = { DB: db, ASSETS: { fetch: async () => new Response("asset") } };
    globalThis.__AURUM_TEST_WORKER_ENV = bindings;
    const prepare = db.prepare.bind(db);
    let checked = 0;
    db.prepare = sql => {
      const statement = prepare(sql);
      if (!sql.includes("page_source AS")) return statement;
      return { bind(...values) {
        const details = db.database.prepare(`EXPLAIN QUERY PLAN ${sql}`)
          .all(...values).map(row => row.detail);
        assert.ok(details.some(detail => /SEARCH lr .*sort_epoch/.test(detail)),
          `deep page must seek its time boundary: ${details.join("; ")}`);
        checked += 1;
        return statement.bind(...values);
      } };
    };
    try {
      const { default: worker } = await import("../dist/server/index.js");
      for (const position of [1501, 501, 51]) {
        const cursor = btoa(JSON.stringify([
          position, `FULL\u0000${position}`, 1999, "FULL\u00001999",
        ]));
        const response = await worker.fetch(new Request(
          `https://example.test/api/learning-history?resource=curve-5m&identity=FULL&limit=7&cursor=${encodeURIComponent(cursor)}`,
        ), bindings, { waitUntil() {}, passThroughOnException() {} });
        assert.equal(response.status, 200);
        const page = await response.json();
        assert.deepEqual(page.items.map(row => row.value),
          Array.from({ length: 7 }, (_, index) => position - 2 * (index + 1)));
        assert.equal(page.total, 1000);
        assert.equal(page.has_more, true);
      }
      assert.equal(checked, 3);
      insert.run("curve-5m", "FULL\u0000!tie", 501, "a".repeat(64),
        JSON.stringify({ model_identity: "FULL", value: "same-time" }),
        "2026-09-03T00:00:00Z");
      const tiedCursor = btoa(JSON.stringify([501, "FULL\u0000501", 1999, "FULL\u00001999"]));
      const tiedResponse = await worker.fetch(new Request(
        `https://example.test/api/learning-history?resource=curve-5m&identity=FULL&limit=2&cursor=${encodeURIComponent(tiedCursor)}`,
      ), bindings, { waitUntil() {}, passThroughOnException() {} });
      assert.equal(tiedResponse.status, 200);
      assert.deepEqual((await tiedResponse.json()).items.map(row => row.value), ["same-time", 499]);
      db.database.prepare("DELETE FROM learning_records WHERE resource=? AND record_key=?")
        .run("curve-5m", "FULL\u0000!tie");
      // Exercise the real page serializer at byte and empty boundaries.
      for (let i = 0; i < 3; i++) insert.run(
        "curve-5m", `LARGE-${i}`, 3000+i, "a".repeat(64),
        JSON.stringify({ model_identity: "LARGE", value: i, text: "x".repeat(210_000) }),
        "2026-09-03T00:00:00Z",
      );
      const largeResponse = await worker.fetch(new Request(
        "https://example.test/api/learning-history?resource=curve-5m&identity=LARGE&limit=500",
      ), bindings, { waitUntil() {}, passThroughOnException() {} });
      const large = await largeResponse.json();
      assert.deepEqual(large.items.map(row => row.value), [2]);
      assert.equal(large.has_more, true);
      assert.ok(large.next_cursor);
      const nextResponse = await worker.fetch(new Request(
        `https://example.test/api/learning-history?resource=curve-5m&identity=LARGE&limit=500&cursor=${encodeURIComponent(large.next_cursor)}`,
      ), bindings, { waitUntil() {}, passThroughOnException() {} });
      assert.deepEqual((await nextResponse.json()).items.map(row => row.value), [1]);
      const emptyResponse = await worker.fetch(new Request(
        "https://example.test/api/learning-history?resource=curve-5m&identity=MISSING&limit=6",
      ), bindings, { waitUntil() {}, passThroughOnException() {} });
      const empty = await emptyResponse.json();
      assert.deepEqual(empty.items, []);
      assert.equal(empty.has_more, false);
      assert.equal(empty.next_cursor, null);
    } finally {
      db.prepare = prepare;
      globalThis.__AURUM_TEST_WORKER_ENV = previousEnv;
    }
  }

  db.database.prepare(
    `UPDATE learning_records SET payload=? WHERE resource='curve-5m' AND record_key=?`,
  ).run(JSON.stringify({ model_identity: "MARKET_ONLY", value: 1 }), "FULL\u00001");
  assert.equal(db.database.prepare(
    `SELECT record_count FROM learning_record_counts
     WHERE resource='curve-5m' AND model_identity='FULL'`,
  ).get().record_count, 999);
  assert.equal(db.database.prepare(
    `SELECT record_count FROM learning_record_counts
     WHERE resource='curve-5m' AND model_identity='MARKET_ONLY'`,
  ).get().record_count, 1_001);
});

test("unchanged dashboard snapshots cause zero logical row mutation", async () => {
  const db = new D1TestDatabase(["0000_sad_toad.sql"]);
  const bytes = new TextEncoder().encode(JSON.stringify({ generated_at: "fixed" }));
  await writeDashboardSnapshotBytes(bytes, db, 3);
  const before = db.database.prepare("SELECT total_changes() total").get().total;
  await writeDashboardSnapshotBytes(bytes, db, 3);
  const after = db.database.prepare("SELECT total_changes() total").get().total;
  assert.equal(after - before, 0);
});
