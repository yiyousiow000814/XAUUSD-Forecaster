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

test("learning history keeps exact counts while its page lookup uses the identity index", () => {
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
