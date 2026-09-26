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

test("unchanged dashboard snapshots cause zero logical row mutation", async () => {
  const db = new D1TestDatabase(["0000_sad_toad.sql"]);
  const bytes = new TextEncoder().encode(JSON.stringify({ generated_at: "fixed" }));
  await writeDashboardSnapshotBytes(bytes, db, 3);
  const before = db.database.prepare("SELECT total_changes() total").get().total;
  await writeDashboardSnapshotBytes(bytes, db, 3);
  const after = db.database.prepare("SELECT total_changes() total").get().total;
  assert.equal(after - before, 0);
});
