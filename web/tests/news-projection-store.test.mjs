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
  detail_key: id(digit), category: "增长/经济", cluster_id: `cluster-${digit}`,
  source_published_time: `2026-08-2${digit}T10:00:00Z`,
  collector_first_seen_time: `2026-08-2${digit}T10:01:00Z`,
  annotation_status: "READY", model_visibility: "MODEL_VISIBLE",
  parsed_at: `2026-08-2${digit}T10:02:00Z`,
  impact_expires_at: `2026-09-2${digit}T10:00:00.000000+00:00`,
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
const database = () => new D1TestDatabase([
  "0001_daily_epoch.sql", "0002_hard_bishop.sql",
  "0003_news_index_lookup.sql", "0007_bounded_news_archive.sql",
  "0022_news_projection_generation.sql", "0027_materialize_news_projection_counts.sql",
  "0028_fence_legacy_news_current_identity.sql",
]);

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

test("every receipt progress field uses the bounded generation-kind index", async () => {
  const db = database();
  const details = [detail("1")];
  const source = await manifest("a", details, []);
  await prepareNewsProjection(db, source);

  let progressSql = "";
  const originalPrepare = db.prepare.bind(db);
  db.prepare = sql => {
    if (sql.includes("next_detail_offset") && sql.includes("updated_at")) {
      progressSql = sql;
    }
    return originalPrepare(sql);
  };
  await stageNewsProjectionBatch(db, "detail", id("a"), 0, details);
  db.prepare = originalPrepare;

  assert.ok(progressSql, "receipt progress query was exercised");
  const bindings = Array.from(
    { length: (progressSql.match(/\?/g) ?? []).length }, () => id("a"),
  );
  const plan = db.database.prepare(`EXPLAIN QUERY PLAN ${progressSql}`).all(...bindings);
  const receiptReads = plan
    .map(row => String(row.detail))
    .filter(detailText => detailText.includes("news_projection_receipts_v2"));
  assert.ok(receiptReads.length >= 8, "all receipt progress fields are planned");
  assert.ok(receiptReads.every(detailText => (
    detailText.includes("generation_id=? AND batch_kind=?")
  )), receiptReads.join("\n"));
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
  assert.equal(db.database.prepare("SELECT count(*) total FROM news_details").get().total, 2);
  assert.equal(db.database.prepare("SELECT count(*) total FROM news_index").get().total, 2);
  assert.equal((await readNewsProjectionHealth(db)).verified_complete, true);
  let pagePrepareCount = 0;
  const originalPrepare = db.prepare.bind(db);
  db.prepare = sql => {
    pagePrepareCount += 1;
    return originalPrepare(sql);
  };
  const page = await readNewsProjectionPage(db, {
    page: 1, pageSize: 2, category: "", reviewState: "COMPLETED",
  });
  db.prepare = originalPrepare;
  assert.equal(pagePrepareCount, 2, "state plus one bounded page projection query");
  assert.equal(page.items.length, 2);
  assert.deepEqual(page.items.map(item => item.detail_key), [id("2"), id("1")]);
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

test("staging and abandonment cannot change the active legacy reverse projection", async () => {
  const db = database();
  const currentDetails = [detail("1")];
  const currentIndexes = [index("1")];
  await prepareNewsProjection(db, await manifest("a", currentDetails, currentIndexes));
  await stageNewsProjectionBatch(db, "detail", id("a"), 0, currentDetails);
  await stageNewsProjectionBatch(db, "index", id("a"), 0, currentIndexes);
  await activateNewsProjection(db, id("a"));

  const stagingDetails = [detail("2")];
  const stagingIndexes = [index("2")];
  await prepareNewsProjection(db, await manifest("b", stagingDetails, stagingIndexes));
  await stageNewsProjectionBatch(db, "detail", id("b"), 0, stagingDetails);
  await stageNewsProjectionBatch(db, "index", id("b"), 0, stagingIndexes);
  assert.deepEqual(
    db.database.prepare("SELECT detail_key FROM news_index ORDER BY detail_key").all()
      .map(row => row.detail_key),
    [id("1")],
    "STAGING membership is not visible through the reverse-stable projection",
  );

  await abandonNewsProjection(db, id("b"));
  assert.equal(
    db.database.prepare("SELECT count(*) total FROM news_details WHERE detail_key=?")
      .get(id("2")).total,
    0,
    "abandonment removes an unreferenced derived detail",
  );
  assert.equal((await readNewsProjectionHealth(db)).active_generation_id, id("a"));
});

test("materializes every generation's review totals and keeps page reads bounded", async () => {
  const db = database();
  const details = ["1", "2", "3", "4"].map(detail);
  const indexes = [
    { ...index("1"), category: "利率/Fed", impact_expires_at: "2099-01-01T00:00:00.000000+00:00" },
    { ...index("2"), category: "利率/Fed", annotation_status: "NOT_REQUIRED",
      model_visibility: "MODEL_INELIGIBLE", parsed_at: null },
    { ...index("3"), category: "油价/能源", annotation_status: "QUEUED",
      model_visibility: "NOT_YET_PARSED", parsed_at: null },
    { ...index("4"), category: "油价/能源", annotation_status: "DEAD_LETTER",
      model_visibility: "DEAD_LETTER", parsed_at: null },
  ];
  await prepareNewsProjection(db, await manifest("e", details, indexes));
  await stageNewsProjectionBatch(db, "detail", id("e"), 0, details);
  await stageNewsProjectionBatch(db, "index", id("e"), 0, indexes);
  await activateNewsProjection(db, id("e"));

  const completed = await readNewsProjectionPage(db, {
    page: 1, pageSize: 20, category: "", reviewState: "COMPLETED",
  });
  assert.equal(completed.total, 2);
  assert.equal(completed.parsed_total, 1);
  assert.equal(completed.model_candidate_total, 1);
  assert.deepEqual(completed.category_counts, { "利率/Fed": 2 });
  assert.deepEqual(completed.review_state_counts, {
    COMPLETED: 2, PROCESSING: 1, ISOLATED: 1,
  });
  const processing = await readNewsProjectionPage(db, {
    page: 1, pageSize: 20, category: "油价/能源", reviewState: "PROCESSING",
  });
  assert.equal(processing.total, 1);
  assert.equal(processing.items[0].detail_key, id("3"));
  assert.equal(db.database.prepare(
    "SELECT count(*) total FROM news_projection_counts WHERE generation_id=?",
  ).get(id("e")).total, 7);
});

test("rejects unbounded categories and non-canonical active expiries at the batch boundary", async () => {
  for (const [generationDigit, badIndex] of [
    ["f", { ...index("1"), category: "unbounded-category" }],
    ["9", { ...index("1"), impact_expires_at: "2099-01-01T00:00:00Z" }],
  ]) {
    const db = database();
    const details = [detail("1")];
    const indexes = [badIndex];
    await prepareNewsProjection(db, await manifest(generationDigit, details, indexes));
    await stageNewsProjectionBatch(db, "detail", id(generationDigit), 0, details);
    await assert.rejects(
      stageNewsProjectionBatch(db, "index", id(generationDigit), 0, indexes),
      error => error.code === "NEWS_PROJECTION_BATCH_INVALID",
    );
  }
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
  assert.equal(
    db.database.prepare("SELECT count(*) total FROM news_index WHERE detail_key=?")
      .get(id("1")).total,
    0,
    "obsolete superseded reverse rows are deleted after their generation is cleaned",
  );
  assert.equal(
    db.database.prepare("SELECT count(*) total FROM news_details WHERE detail_key=?")
      .get(id("1")).total,
    0,
    "obsolete unreferenced derived bodies are deleted with the superseded generation",
  );
});

test("keeps the rollback legacy identity set equal across replacement activations", async () => {
  const db = database();
  const firstDetails = [detail("1"), detail("2")];
  const firstIndexes = [index("1"), index("2")];
  await prepareNewsProjection(db, await manifest("a", firstDetails, firstIndexes));
  await stageNewsProjectionBatch(db, "detail", id("a"), 0, firstDetails);
  await stageNewsProjectionBatch(db, "index", id("a"), 0, firstIndexes);
  await activateNewsProjection(db, id("a"));

  const replacementDetails = [detail("2")];
  const replacementIndexes = [index("2")];
  await prepareNewsProjection(
    db, await manifest("b", replacementDetails, replacementIndexes),
  );
  const changesBeforeReplay = db.database.prepare("SELECT total_changes() total").get().total;
  await stageNewsProjectionBatch(db, "detail", id("b"), 0, replacementDetails);
  const changesAfterDetails = db.database.prepare("SELECT total_changes() total").get().total;
  await stageNewsProjectionBatch(db, "index", id("b"), 0, replacementIndexes);
  const changesAfterIndex = db.database.prepare("SELECT total_changes() total").get().total;
  assert.equal(changesAfterDetails - changesBeforeReplay, 1,
    "unchanged global detail replay writes only its append-only batch receipt");
  assert.equal(changesAfterIndex - changesAfterDetails, 2,
    "unchanged legacy index replay writes only its generation index row and batch receipt");
  assert.equal(
    db.database.prepare(
      "SELECT count(*) total FROM news_index WHERE json_extract(payload,'$.annotation_status')<>'SUPERSEDED_CONTRACT'",
    ).get().total,
    2,
    "staging remains a bounded legacy upsert and does not supersede the active set",
  );
  await activateNewsProjection(db, id("b"));

  const fields = `detail_key,category,cluster_id,published_time,
    collector_first_seen_time,parsed,model_candidate,impact_expires_at,
    mirror_contract,payload,received_at`;
  const legacyCurrent = db.database.prepare(
    `SELECT ${fields} FROM news_index
      WHERE json_extract(payload,'$.annotation_status')<>'SUPERSEDED_CONTRACT'
      ORDER BY detail_key`,
  ).all();
  const projectionCurrent = db.database.prepare(
    `SELECT ${fields} FROM news_projection_index
      WHERE generation_id=? ORDER BY detail_key`,
  ).all(id("b"));
  assert.deepEqual(legacyCurrent, projectionCurrent);
  const superseded = JSON.parse(db.database.prepare(
    "SELECT payload FROM news_index WHERE detail_key=?",
  ).get(id("1")).payload);
  assert.equal(superseded.annotation_status, "SUPERSEDED_CONTRACT");
  assert.equal(superseded.model_visibility, "MODEL_INELIGIBLE");
  assert.equal(superseded.parsed_at, null);
});

test("fences the active reverse projection from every legacy writer mutation family", async () => {
  const db = database();
  const details = [detail("1")];
  const indexes = [index("1")];
  await prepareNewsProjection(db, await manifest("a", details, indexes));
  await stageNewsProjectionBatch(db, "detail", id("a"), 0, details);
  await stageNewsProjectionBatch(db, "index", id("a"), 0, indexes);
  await activateNewsProjection(db, id("a"));

  const current = db.database.prepare(
    "SELECT * FROM news_index WHERE detail_key=?",
  ).get(id("1"));
  const currentDetail = db.database.prepare(
    "SELECT * FROM news_details WHERE detail_key=?",
  ).get(id("1"));
  db.database.prepare(
    "DELETE FROM news_index WHERE cluster_id=? AND detail_key<>?",
  ).run(current.cluster_id, id("9"));
  db.database.prepare("DELETE FROM news_index WHERE detail_key=?").run(id("1"));
  db.database.prepare("DELETE FROM news_details WHERE detail_key=?").run(id("1"));
  assert.deepEqual(
    db.database.prepare("SELECT * FROM news_index WHERE detail_key=?").get(id("1")),
    current,
    "cluster replacement, reset, prune, and withdrawal cannot delete CURRENT",
  );
  assert.deepEqual(
    db.database.prepare("SELECT * FROM news_details WHERE detail_key=?").get(id("1")),
    currentDetail,
    "withdrawal cannot orphan a CURRENT detail",
  );

  db.database.prepare(
    `UPDATE news_index SET category='Other',parsed=0,model_candidate=0,
       payload=json_set(payload,'$.annotation_status','QUEUED') WHERE detail_key=?`,
  ).run(id("1"));
  assert.deepEqual(
    db.database.prepare("SELECT * FROM news_index WHERE detail_key=?").get(id("1")),
    current,
    "an old upsert cannot rewrite CURRENT fields or payload",
  );

  const replacement = { ...index("2"), cluster_id: current.cluster_id };
  const changesBefore = db.database.prepare("SELECT total_changes() total").get().total;
  db.database.prepare(
    "INSERT INTO news_details (detail_key,detail_hash,payload,received_at) VALUES (?,?,?,?)",
  ).run(id("2"), id("b"), JSON.stringify(detail("2").payload), "2026-08-27T00:00:00Z");
  db.database.prepare(
    `INSERT INTO news_index
       (detail_key,category,cluster_id,published_time,collector_first_seen_time,
        parsed,model_candidate,impact_expires_at,mirror_contract,payload,received_at)
     VALUES (?,?,?,?,?,?,?,?,?,?,?)`,
  ).run(
    replacement.detail_key, replacement.category, replacement.cluster_id,
    replacement.source_published_time, replacement.collector_first_seen_time,
    1, 1, replacement.impact_expires_at, replacement.mirror_contract,
    JSON.stringify(replacement), "2026-08-27T00:00:00Z",
  );
  const changesAfter = db.database.prepare("SELECT total_changes() total").get().total;
  assert.equal(changesAfter - changesBefore, 1,
    "changed legacy replay retains only its derived detail evidence");
  assert.equal(db.database.prepare(
    "SELECT count(*) total FROM news_index WHERE detail_key=?",
  ).get(id("2")).total, 0);
  assert.equal(db.database.prepare(
    "SELECT count(*) total FROM news_details WHERE detail_key=?",
  ).get(id("2")).total, 1);
  assert.deepEqual(
    db.database.prepare(
      `SELECT detail_key FROM news_index
        WHERE json_extract(payload,'$.annotation_status')<>'SUPERSEDED_CONTRACT'
        ORDER BY detail_key`,
    ).all().map(row => row.detail_key),
    [id("1")],
  );
});

test("activation requires the detail and index identity chains to match", async () => {
  const db = database();
  const priorDetails = [detail("2")];
  const priorIndexes = [index("2")];
  await prepareNewsProjection(db, await manifest("a", priorDetails, priorIndexes));
  await stageNewsProjectionBatch(db, "detail", id("a"), 0, priorDetails);
  await stageNewsProjectionBatch(db, "index", id("a"), 0, priorIndexes);
  await activateNewsProjection(db, id("a"));

  const wrongDetails = [detail("1")];
  const wrongIndexes = [index("2")];
  await prepareNewsProjection(db, await manifest("b", wrongDetails, wrongIndexes));
  await stageNewsProjectionBatch(db, "detail", id("b"), 0, wrongDetails);
  await stageNewsProjectionBatch(db, "index", id("b"), 0, wrongIndexes);
  await assert.rejects(
    activateNewsProjection(db, id("b")),
    error => error.code === "NEWS_PROJECTION_INCOMPLETE"
      && error.details.missing_detail_count === 0
      && error.details.identity_match === false,
  );
  assert.equal((await readNewsProjectionHealth(db)).active_generation_id, id("a"));
});

test("fails closed when a later generation contradicts content-addressed detail evidence", async () => {
  const db = database();
  const originalDetails = [detail("1")];
  const originalIndexes = [index("1")];
  await prepareNewsProjection(db, await manifest("a", originalDetails, originalIndexes));
  await stageNewsProjectionBatch(db, "detail", id("a"), 0, originalDetails);
  await stageNewsProjectionBatch(db, "index", id("a"), 0, originalIndexes);
  await activateNewsProjection(db, id("a"));

  const contradiction = [{
    ...detail("1"), detail_hash: id("f"), payload: { headline: "changed evidence" },
  }];
  await prepareNewsProjection(
    db, await manifest("b", contradiction, originalIndexes),
  );
  const changesBefore = db.database.prepare("SELECT total_changes() total").get().total;
  await assert.rejects(
    stageNewsProjectionBatch(db, "detail", id("b"), 0, contradiction),
    error => error.code === "NEWS_PROJECTION_DETAIL_CONTRADICTION",
  );
  const changesAfter = db.database.prepare("SELECT total_changes() total").get().total;
  assert.equal(changesAfter, changesBefore, "contradiction cannot mutate evidence or progress");
  assert.equal(
    db.database.prepare("SELECT detail_hash FROM news_details WHERE detail_key=?").get(id("1")).detail_hash,
    detail("1").detail_hash,
  );
});
