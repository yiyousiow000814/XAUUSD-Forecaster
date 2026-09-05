// Isolated SQLite adapter; execute the real Worker store and ACK boundary.
import {
  prepareNewsEvidenceSnapshot, stageNewsEvidenceBatch,
  activateNewsEvidenceSnapshot, cleanupNewsEvidenceSnapshots,
  newsEvidenceWriteAcknowledgement, readNewsEvidencePage,
} from "../../web/app/api/_shared/news-evidence-store.ts";
import { D1TestDatabase } from "../../web/tests/d1-test-database.mjs";
import { createInterface } from "node:readline";
import { newsProjectionPayloadHash } from "../../web/app/api/_shared/news-projection-store.ts";

const db = new D1TestDatabase([
  "0021_paged_news_evidence.sql", "0030_news_evidence_cleanup_budget.sql",
]);
async function execute(encoded) {
    if (encoded?.inspect === true) {
      const active = db.database.prepare("SELECT active_snapshot_id,record_count FROM news_evidence_state WHERE id=1").get();
      const rows = active ? db.database.prepare(
        "SELECT payload FROM news_evidence_records WHERE snapshot_id=? ORDER BY ordinal LIMIT 8193",
      ).all(active.active_snapshot_id) : [];
      if (rows.length > 8192) throw new Error("fixture inspection exceeds bound");
      return { snapshot_id: active?.active_snapshot_id, count: rows.length,
        expected_count: active?.record_count,
        content_digest: await newsProjectionPayloadHash(rows.map(row => JSON.parse(row.payload))) };
    }
    if (typeof encoded !== "string" || encoded.length > 110_000) throw new Error("fixture request exceeds bound");
    const serialized = new TextDecoder("utf-8", { fatal: true }).decode(Buffer.from(encoded, "base64"));
    const request = JSON.parse(serialized);
    let result;
    const snapshot = request.prepare_snapshot ?? request.snapshot_id
      ?? request.activate_snapshot ?? request.cleanup_active_snapshot;
    if (request.prepare_snapshot) {
      result = await prepareNewsEvidenceSnapshot(db, snapshot, request.expected_count);
    } else if (request.items) {
      result = await stageNewsEvidenceBatch(db, snapshot, request.offset, request.items);
    } else if (request.activate_snapshot) {
      result = await activateNewsEvidenceSnapshot(db, snapshot, request.expected_count);
    } else {
      result = await cleanupNewsEvidenceSnapshots(db, snapshot);
    }
    return newsEvidenceWriteAcknowledgement(serialized, snapshot, result);
}
try {
  if (process.argv.includes("--serve")) {
    const lines = createInterface({ input: process.stdin });
    let count = 0;
    for await (const line of lines) {
      if (++count > 1024 || line.length > 120_000) throw new Error("fixture session exceeds bound");
      try {
        process.stdout.write(JSON.stringify({ result: await execute(JSON.parse(line)) }) + "\n");
      } catch (error) {
        process.stdout.write(JSON.stringify({ error: String(error), code: error.code }) + "\n");
      }
    }
  } else {
    let input = "";
    for await (const chunk of process.stdin) {
      input += chunk;
      if (Buffer.byteLength(input) > 400_000) throw new Error("fixture input exceeds bound");
    }
    const results = [];
    for (const encoded of JSON.parse(input)) results.push(await execute(encoded));
    const page = await readNewsEvidencePage(db, { mode: "all", rawCursor: null, page: 1, pageSize: 8 });
    process.stdout.write(JSON.stringify({ results, page, inspection: await execute({ inspect: true }) }));
  }
} finally {
  db.database.close();
}
