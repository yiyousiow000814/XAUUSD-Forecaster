import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import test from "node:test";

import {
  abandonNewsProjection, activateNewsProjection, advanceNewsReceiptDigest, EMPTY_RECEIPT_DIGEST,
  NEWS_PROJECTION_CONTRACT_VERSION, prepareNewsProjection,
  newsProjectionPayloadHash,
  readNewsProjectionDetails, readNewsProjectionHealth, readNewsProjectionPage,
  stageNewsProjectionBatch, verifyNewsProjection,
} from "../app/api/_shared/news-projection-store.ts";
import { D1TestDatabase } from "./d1-test-database.mjs";

const id = digit => digit.repeat(64);
const hash = value => createHash("sha256").update(value).digest("hex");
const receiptVectors = JSON.parse(readFileSync(
  new URL("../../tests/fixtures/news_projection_receipt_vectors.json", import.meta.url),
  "utf8",
));
const detail = digit => ({
  detail_key: id(digit), detail_hash: id(digit === "1" ? "a" : "b"),
  payload: { headline: `detail ${digit}`, body: "bounded body" },
});
const index = digit => ({
  detail_key: id(digit), category: "美国宏观", cluster_id: `cluster-${digit}`,
  source_published_time: `2026-08-2${digit}T10:00:00Z`,
  collector_first_seen_time: `2026-08-2${digit}T10:01:00Z`,
  annotation_status: "READY", model_visibility: "MODEL_VISIBLE",
  parsed_at: `2026-08-2${digit}T10:02:00Z`,
  mirror_contract: NEWS_PROJECTION_CONTRACT_VERSION,
});
const receipt = async (details, indexes) => {
  let digest = EMPTY_RECEIPT_DIGEST;
  if (details.length) {
    digest = await advanceNewsReceiptDigest(
      digest, "detail", 0, details.length, await newsProjectionPayloadHash(details),
    );
  }
  if (indexes.length) {
    digest = await advanceNewsReceiptDigest(
      digest, "index", 0, indexes.length, await newsProjectionPayloadHash(indexes),
    );
  }
  return digest;
};
const manifest = async (digit, details, indexes, overrides = {}) => ({
  generation_id: id(digit), snapshot_id: id(digit),
  contract_version: NEWS_PROJECTION_CONTRACT_VERSION,
  window_start: "2026-06-25T00:00:00.000Z",
  watermark: "2026-08-24T00:00:00.000Z",
  expected_index_count: indexes.length, expected_detail_count: details.length,
  withdrawal_count: 0, source_digest: hash(`source-${digit}`),
  expected_receipt_digest: await receipt(details, indexes), ...overrides,
});
const database = () => new D1TestDatabase(["0022_news_projection_generation.sql"]);

test("shares canonical receipt vectors with the Python producer", async () => {
  assert.equal(receiptVectors.contract_version, NEWS_PROJECTION_CONTRACT_VERSION);
  for (const vector of receiptVectors.payload_vectors) {
    assert.equal(await newsProjectionPayloadHash(vector.value), vector.expected_hash);
  }
  let digest = await advanceNewsReceiptDigest(
    EMPTY_RECEIPT_DIGEST, "detail", 0,
    receiptVectors.payload_vectors[0].value.length,
    await newsProjectionPayloadHash(receiptVectors.payload_vectors[0].value),
  );
  digest = await advanceNewsReceiptDigest(
    digest, "index", 0,
    Object.keys(receiptVectors.payload_vectors[1].value).length,
    await newsProjectionPayloadHash(receiptVectors.payload_vectors[1].value),
  );
  assert.equal(digest, receiptVectors.expected_receipt_digest);
  assert.equal(await newsProjectionPayloadHash({ value: 0 }), await newsProjectionPayloadHash({ value: 0.0 }));
  assert.equal(await newsProjectionPayloadHash({ value: 0 }), await newsProjectionPayloadHash({ value: -0 }));
  assert.equal(await newsProjectionPayloadHash({ z: 1, a: 2 }), await newsProjectionPayloadHash({ a: 2, z: 1 }));
});

test("stages detail before index and atomically activates one receipt-backed generation", async () => {
  const db = database();
  const details = [detail("1"), detail("2")];
  const indexes = [index("1"), index("2")];
  const source = await manifest("a", details, indexes);
  assert.deepEqual(await prepareNewsProjection(db, source), {
    status: "OK", active: false, generation_id: id("a"),
    next_detail_offset: 0, next_index_offset: 0,
    receipt_digest: EMPTY_RECEIPT_DIGEST,
  });
  await assert.rejects(
    stageNewsProjectionBatch(db, "index", id("a"), 0, indexes),
    error => error.code === "NEWS_PROJECTION_DETAILS_INCOMPLETE",
  );
  const first = await stageNewsProjectionBatch(db, "detail", id("a"), 0, details);
  assert.equal(first.received, 2);
  assert.deepEqual(await stageNewsProjectionBatch(db, "detail", id("a"), 0, details), {
    status: "OK", duplicate: true, received: 2, receipt_digest: first.receipt_digest,
  });
  await assert.rejects(
    stageNewsProjectionBatch(db, "detail", id("a"), 0, [{ ...detail("1"), payload: {} }]),
    error => error.code === "NEWS_PROJECTION_RECEIPT_CONTRADICTION",
  );
  await stageNewsProjectionBatch(db, "index", id("a"), 0, indexes);
  const activated = await activateNewsProjection(db, id("a"));
  assert.equal(activated.index_count, 2);
  assert.equal((await readNewsProjectionHealth(db)).verified_complete, true);
  const page = await readNewsProjectionPage(db, {
    page: 1, pageSize: 1, category: "", reviewState: "COMPLETED",
  });
  assert.equal(page.items.length, 1);
  assert.equal(page.all_total, 2);
  assert.equal(page.totals_scope, "VERIFIED_CURRENT_GENERATION");
  const found = await readNewsProjectionDetails(db, [id("1"), id("f")]);
  assert.deepEqual(found.missing, [id("f")]);
  assert.equal(found.items[id("1")].payload.headline, "detail 1");
  assert.equal((await verifyNewsProjection(db, id("a"))).status, "OK");
});

test("partial replacement never displaces the last verified current generation", async () => {
  const db = database();
  const detailsA = [detail("1")];
  const indexesA = [index("1")];
  await prepareNewsProjection(db, await manifest("a", detailsA, indexesA));
  await stageNewsProjectionBatch(db, "detail", id("a"), 0, detailsA);
  await stageNewsProjectionBatch(db, "index", id("a"), 0, indexesA);
  await activateNewsProjection(db, id("a"));
  const detailsB = [detail("2")];
  const indexesB = [index("2")];
  await prepareNewsProjection(db, await manifest("b", detailsB, indexesB));
  await stageNewsProjectionBatch(db, "detail", id("b"), 0, detailsB);
  const health = await readNewsProjectionHealth(db);
  assert.equal(health.active_generation_id, id("a"));
  assert.equal(health.projection_state, "REPLAYING");
  const page = await readNewsProjectionPage(db, {
    page: 1, pageSize: 10, category: "", reviewState: "COMPLETED",
  });
  assert.equal(page.generation_id, id("a"));
  assert.equal(page.projection_state, "REPLAYING");
  await assert.rejects(
    readNewsProjectionPage(db, {
      page: 2, pageSize: 10, category: "", reviewState: "COMPLETED",
      expectedGenerationId: id("b"),
    }),
    error => error.code === "NEWS_PROJECTION_GENERATION_CHANGED",
  );
  await assert.rejects(
    activateNewsProjection(db, id("b")),
    error => error.code === "NEWS_PROJECTION_INCOMPLETE",
  );
  assert.equal((await readNewsProjectionHealth(db)).active_generation_id, id("a"));
  assert.equal((await abandonNewsProjection(db, id("b"))).abandoned, id("b"));
  await assert.rejects(
    abandonNewsProjection(db, id("a")),
    error => error.code === "NEWS_PROJECTION_ABANDON_CURRENT_REJECTED",
  );
});

test("activation rejects missing details and receipt contradictions", async () => {
  const db = database();
  const details = [detail("1")];
  const indexes = [index("2")];
  await prepareNewsProjection(db, await manifest("c", details, indexes));
  await stageNewsProjectionBatch(db, "detail", id("c"), 0, details);
  await stageNewsProjectionBatch(db, "index", id("c"), 0, indexes);
  await assert.rejects(
    activateNewsProjection(db, id("c")),
    error => error.code === "NEWS_PROJECTION_INCOMPLETE"
      && error.details.missing_detail_count === 1,
  );
  const db2 = database();
  const matching = [detail("1")];
  const rows = [index("1")];
  await prepareNewsProjection(db2, await manifest("d", matching, rows, {
    expected_receipt_digest: id("f"),
  }));
  await stageNewsProjectionBatch(db2, "detail", id("d"), 0, matching);
  await stageNewsProjectionBatch(db2, "index", id("d"), 0, rows);
  await assert.rejects(
    activateNewsProjection(db2, id("d")),
    error => error.code === "NEWS_PROJECTION_INCOMPLETE"
      && error.details.receipt_match === false,
  );
});

test("retains only current plus one staging while preparing a third generation", async () => {
  const db = database();
  for (const [generationDigit, itemDigit] of [["a", "1"], ["b", "2"]]) {
    const details = [detail(itemDigit)];
    const indexes = [index(itemDigit)];
    await prepareNewsProjection(db, await manifest(generationDigit, details, indexes));
    await stageNewsProjectionBatch(db, "detail", id(generationDigit), 0, details);
    await stageNewsProjectionBatch(db, "index", id(generationDigit), 0, indexes);
    await activateNewsProjection(db, id(generationDigit));
  }
  await prepareNewsProjection(db, await manifest("c", [detail("3")], [index("3")]));
  const generations = db.database.prepare(
    "SELECT generation_id,state FROM news_projection_generations ORDER BY generation_id",
  ).all();
  assert.equal(generations.filter(row => row.state !== "STAGING").length, 1);
  assert.equal(generations.filter(row => row.state === "STAGING").length, 1);
  assert.equal(generations.some(row => row.generation_id === id("a")), false);
  assert.equal(generations.some(row => row.generation_id === id("b")), true);
});
