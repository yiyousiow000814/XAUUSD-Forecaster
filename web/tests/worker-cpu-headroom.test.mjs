import assert from "node:assert/strict";
import { mkdtempSync, readdirSync, readFileSync, rmSync, statSync } from "node:fs";
import { tmpdir } from "node:os";
import { fileURLToPath } from "node:url";
import { join } from "node:path";
import { spawnSync } from "node:child_process";
import test from "node:test";
import { D1TestDatabase } from "./d1-test-database.mjs";

const migrations = readdirSync(new URL("../drizzle", import.meta.url))
  .filter(name => name.endsWith(".sql"))
  .sort();
const database = new D1TestDatabase(migrations);
const token = "production-shaped-ingest-token";
const isPreviewBuild = Boolean(
  process.env.WORKERS_CI_BRANCH && process.env.WORKERS_CI_BRANCH !== "main",
);
const runtimeEnv = {
  DB: database,
  INGEST_TOKEN: token,
  CF_VERSION_METADATA: { id: "test-worker-version" },
  ASSETS: { fetch: async () => new Response("asset") },
  IMAGES: {},
  ASSISTANT_MEMORY_VECTOR: {},
};
globalThis.__AURUM_TEST_WORKER_ENV = runtimeEnv;

const { default: worker } = await import("../dist/server/index.js");
const context = {
  waitUntil() {},
  passThroughOnException() {},
};

function insertSnapshot(id, payload, receivedAt = new Date().toISOString()) {
  database.database.prepare(
    `INSERT INTO dashboard_snapshots(id,payload,received_at) VALUES(?,?,?)
     ON CONFLICT(id) DO UPDATE SET payload=excluded.payload,received_at=excluded.received_at`,
  ).run(id, payload, receivedAt);
}

test("seeds missing bounded audit metrics exactly once during storage handover", () => {
  const beforeSeed = migrations.filter(name => name < "0024_seed_bounded_audit_news_metrics.sql");
  const handover = new D1TestDatabase(beforeSeed);
  const legacyMetrics = {
    schema_version: "news-metrics-v1",
    articles: { received: 7_711, stored_revisions: 7_714 },
    events: { independent: 3_494 },
  };
  const receivedAt = "2026-08-25T21:44:33.029Z";
  handover.database.prepare(
    "INSERT INTO dashboard_snapshots(id,payload,received_at) VALUES(4,?,?)",
  ).run(JSON.stringify({ generated_at: receivedAt, news_metrics: legacyMetrics }), receivedAt);
  handover.database.prepare(
    "INSERT INTO dashboard_snapshots(id,payload,received_at) VALUES(9,?,?)",
  ).run(JSON.stringify({ generated_at: "older", storyline_summary: { total: 12 } }), "older");

  handover.applyMigration("0024_seed_bounded_audit_news_metrics.sql");
  handover.applyMigration("0024_seed_bounded_audit_news_metrics.sql");
  const seeded = handover.row(9, "dashboard_snapshots");
  const payload = JSON.parse(seeded.payload);
  assert.deepEqual(payload.news_metrics, legacyMetrics);
  assert.deepEqual(payload.storyline_summary, { total: 12 });
  assert.equal(seeded.received_at, "older");

  const absent = new D1TestDatabase(beforeSeed);
  absent.database.prepare(
    "INSERT INTO dashboard_snapshots(id,payload,received_at) VALUES(4,?,?)",
  ).run(JSON.stringify({ generated_at: receivedAt, news_metrics: legacyMetrics }), receivedAt);
  absent.applyMigration("0024_seed_bounded_audit_news_metrics.sql");
  const inserted = absent.row(9, "dashboard_snapshots");
  assert.deepEqual(JSON.parse(inserted.payload).news_metrics, legacyMetrics);
  assert.equal(inserted.received_at, receivedAt);
});

test("rebuilds the legacy rollback News projection from verified CURRENT", () => {
  const beforeSeed = migrations.filter(
    name => name < "0025_seed_legacy_news_reverse_projection.sql",
  );
  const handover = new D1TestDatabase(beforeSeed);
  const generationId = "c".repeat(64);
  const snapshotId = "d".repeat(64);
  const sourceDigest = "e".repeat(64);
  const receiptDigest = "f".repeat(64);
  const detailKey = "a".repeat(64);
  const detailHash = "b".repeat(64);
  const obsoleteDetailKey = "9".repeat(64);
  const receivedAt = "2026-08-26T00:00:00.000Z";
  const indexPayload = JSON.stringify({
    detail_key: detailKey,
    category: "央行购金",
    cluster_id: "cluster-1",
    collector_first_seen_time: receivedAt,
    source_published_time: receivedAt,
    parsed_at: receivedAt,
    annotation_status: "READY",
    model_visibility: "MODEL_VISIBLE",
    mirror_contract: "news-projection-generation-v3",
  });
  const detailPayload = JSON.stringify({
    headline: "黄金与美元",
    nullable: null,
    score: 1.25,
  });
  handover.database.prepare(
    `INSERT INTO news_projection_generations
      (generation_id,snapshot_id,state,contract_version,window_start,watermark,
       expected_index_count,expected_detail_count,withdrawal_count,source_digest,
       expected_receipt_digest,receipt_digest,next_detail_offset,next_index_offset,
       staged_detail_count,staged_index_count,missing_detail_count,
       invariant_violation_count,created_at,updated_at,activated_at)
     VALUES (?,?, 'CURRENT','news-projection-generation-v3',?,?,1,1,0,?,?,?,1,1,1,1,0,0,?,?,?)`,
  ).run(
    generationId, snapshotId, receivedAt, receivedAt, sourceDigest,
    receiptDigest, receiptDigest, receivedAt, receivedAt, receivedAt,
  );
  handover.database.prepare(
    `INSERT INTO news_projection_state
      (id,active_generation_id,snapshot_id,contract_version,source_digest,
       receipt_digest,index_count,detail_count,missing_detail_count,
       invariant_violation_count,projection_state,activated_at,verified_at)
     VALUES (1,?,?,?,?,?,1,1,0,0,'CURRENT',?,?)`,
  ).run(
    generationId, snapshotId, "news-projection-generation-v3", sourceDigest,
    receiptDigest, receivedAt, receivedAt,
  );
  handover.database.prepare(
    `INSERT INTO news_projection_details
      (generation_id,detail_key,detail_hash,payload,received_at)
     VALUES (?,?,?,?,?)`,
  ).run(generationId, detailKey, detailHash, detailPayload, receivedAt);
  handover.database.prepare(
    `INSERT INTO news_projection_index
      (generation_id,detail_key,ordinal,category,cluster_id,published_time,
       collector_first_seen_time,parsed,model_candidate,impact_expires_at,
       mirror_contract,payload_hash,payload,received_at)
     VALUES (?,?,0,'央行购金','cluster-1',?,?,1,1,NULL,
       'news-projection-generation-v3',?,?,?)`,
  ).run(
    generationId, detailKey, receivedAt, receivedAt, "1".repeat(64),
    indexPayload, receivedAt,
  );
  handover.database.prepare(
    `INSERT INTO news_index
      (detail_key,category,cluster_id,published_time,collector_first_seen_time,
       parsed,model_candidate,impact_expires_at,mirror_contract,payload,received_at)
     VALUES (?,'旧分类','cluster-1',?,?,0,0,NULL,'legacy','{}',?)`,
  ).run(detailKey, receivedAt, receivedAt, receivedAt);
  handover.database.prepare(
    `INSERT INTO news_details(detail_key,detail_hash,payload,received_at)
     VALUES (?,?,'{"headline":"保留的旧证据"}',?)`,
  ).run(obsoleteDetailKey, "8".repeat(64), receivedAt);
  handover.database.prepare(
    `INSERT INTO news_index
      (detail_key,category,cluster_id,published_time,collector_first_seen_time,
       parsed,model_candidate,impact_expires_at,mirror_contract,payload,received_at)
     VALUES (?,'旧分类','obsolete-cluster',?,?,1,1,NULL,'legacy',?,?)`,
  ).run(
    obsoleteDetailKey, receivedAt, receivedAt,
    JSON.stringify({
      annotation_status: "READY",
      model_visibility: "MODEL_VISIBLE",
      parsed_at: receivedAt,
    }),
    receivedAt,
  );

  handover.applyMigration("0025_seed_legacy_news_reverse_projection.sql");
  handover.applyMigration("0025_seed_legacy_news_reverse_projection.sql");
  handover.database.prepare(
    "UPDATE news_projection_generations SET expected_receipt_digest=?",
  ).run("7".repeat(64));
  handover.applyMigration("0026_reconcile_legacy_news_current_identity.sql");
  assert.equal(JSON.parse(handover.database.prepare(
    "SELECT payload FROM news_index WHERE detail_key=?",
  ).get(obsoleteDetailKey).payload).annotation_status, "READY");
  handover.database.prepare(
    "UPDATE news_projection_generations SET expected_receipt_digest=?",
  ).run(receiptDigest);
  handover.applyMigration("0026_reconcile_legacy_news_current_identity.sql");
  handover.applyMigration("0026_reconcile_legacy_news_current_identity.sql");
  handover.applyMigration("0027_materialize_news_projection_counts.sql");
  handover.applyMigration("0027_materialize_news_projection_counts.sql");

  const legacyDetail = handover.database.prepare(
    "SELECT * FROM news_details WHERE detail_key=?",
  ).get(detailKey);
  const legacyIndex = handover.database.prepare(
    "SELECT * FROM news_index WHERE detail_key=?",
  ).get(detailKey);
  assert.equal(legacyDetail.detail_hash, detailHash);
  assert.equal(legacyDetail.payload, detailPayload);
  assert.equal(legacyIndex.payload, indexPayload);
  assert.equal(legacyIndex.category, "央行购金");
  assert.deepEqual(handover.database.prepare(
    `SELECT review_state,category,item_count,parsed_count
       FROM news_projection_counts WHERE generation_id=?
       ORDER BY review_state,category`,
  ).all(generationId).map(row => ({ ...row })), [
    { review_state: "ALL", category: "", item_count: 1, parsed_count: 1 },
    { review_state: "COMPLETED", category: "", item_count: 1, parsed_count: 1 },
    { review_state: "COMPLETED", category: "央行购金", item_count: 1, parsed_count: 1 },
  ]);
  const obsoleteIndex = handover.database.prepare(
    "SELECT * FROM news_index WHERE detail_key=?",
  ).get(obsoleteDetailKey);
  const obsoletePayload = JSON.parse(obsoleteIndex.payload);
  assert.equal(obsoleteIndex.parsed, 0);
  assert.equal(obsoleteIndex.model_candidate, 0);
  assert.equal(obsoletePayload.annotation_status, "SUPERSEDED_CONTRACT");
  assert.equal(obsoletePayload.model_visibility, "MODEL_INELIGIBLE");
  assert.equal(obsoletePayload.parsed_at, null);
  assert.ok(handover.database.prepare(
    "SELECT 1 FROM news_details WHERE detail_key=?",
  ).get(obsoleteDetailKey));
  assert.deepEqual(
    handover.database.prepare(
      `SELECT detail_key FROM news_index
        WHERE COALESCE(json_extract(payload,'$.annotation_status'),'')
          <> 'SUPERSEDED_CONTRACT'
        ORDER BY detail_key`,
    ).all().map(row => row.detail_key),
    [detailKey],
  );
});

function jsonOfBytes(targetBytes, fields = {}) {
  const shell = JSON.stringify({ ...fields, padding: "" });
  assert.ok(shell.length <= targetBytes);
  return JSON.stringify({
    ...fields,
    padding: "x".repeat(targetBytes - shell.length),
  });
}

async function invoke(path, init = {}, env = runtimeEnv) {
  return worker.fetch(new Request(`https://example.test${path}`, init), env, context);
}

test("serves canonical public shells and favicon as static assets before Worker execution", () => {
  const config = readFileSync(new URL("../wrangler.jsonc", import.meta.url), "utf8");
  const redirects = readFileSync(new URL("../dist/client/_redirects", import.meta.url), "utf8");
  const staticPages = [
    ["index.html", "最近90分钟"],
    ["health.html", "系统健康状态"],
    ["audit.html", "证据台页面"],
  ];
  for (const [file, identity] of staticPages) {
    const url = new URL(`../dist/client/${file}`, import.meta.url);
    const html = readFileSync(url, "utf8");
    assert.ok(Buffer.byteLength(html) > 10_000, file);
    assert.match(html, new RegExp(identity), file);
  }
  assert.ok(statSync(new URL("../dist/client/favicon.svg", import.meta.url)).size > 0);
  assert.match(redirects, /^\/favicon\.ico \/favicon\.svg 301/m);
  assert.match(config, /"run_worker_first": \[[\s\S]*"\/api\/\*", "\/admin\/api\/\*", "\/_vinext\/image"/);
  assert.doesNotMatch(config, /run_worker_first[^\]]*favicon/);
  assert.doesNotMatch(config, /run_worker_first[^\]]*"\/(?:health|audit)"/);
});

test("benchmarks diagnostic logging without claiming local proof of platform CPU safety", () => {
  const benchmark = readFileSync(
    new URL("../build/benchmark-worker-cpu.mjs", import.meta.url), "utf8",
  );
  assert.match(benchmark, /logging_disabled: loggingDisabled/);
  assert.match(benchmark, /logging_enabled: loggingEnabled/);
  assert.match(benchmark, /logging_delta: loggingDelta/);
  assert.match(benchmark, /diagnostic_log_bytes_written/);
  assert.doesNotMatch(benchmark, /worker_limit_ms/);
  assert.match(benchmark, /cannot prove Cloudflare CPU safety/);
});

test("keeps legacy redirects on the minimal Worker path", async () => {
  for (const [path, destination] of [
    ["/status", "/admin/ai-usage"],
    ["/assistant", "/admin/assistant"],
    ["/retry-jobs", "/admin/retry-jobs"],
  ]) {
    const response = await invoke(path, { redirect: "manual" });
    assert.equal(response.status, 307, path);
    assert.equal(new URL(response.headers.get("location")).pathname + new URL(response.headers.get("location")).search, destination);
    assert.equal(response.headers.get("x-aurum-resource"), "legacy-redirect");
    assert.equal(response.headers.get("x-aurum-d1-operations"), "0");
  }
});

test("replays the production read route family through bounded API modules", async () => {
  if (isPreviewBuild) return;
  insertSnapshot(1, jsonOfBytes(250_000, {
    generated_at: new Date().toISOString(),
    system: { online: true, quote_age_seconds: 1 },
    annotation_queue: { private: true },
    gemini_quota: { private: true },
  }));
  insertSnapshot(2, jsonOfBytes(390_000, { candles: [] }));
  insertSnapshot(3, jsonOfBytes(208_000, { models: [] }));
  insertSnapshot(9, jsonOfBytes(16_000, { news_metrics: {} }));
  insertSnapshot(6, jsonOfBytes(58_000, { recent_decisions: [] }));
  insertSnapshot(7, jsonOfBytes(24_000, { daily_news_briefs: [] }));
  insertSnapshot(8, jsonOfBytes(80_000, { storylines: [] }));
  database.database.prepare(
    "INSERT OR REPLACE INTO market_history_overview(overview_key,payload,received_at) VALUES('all',?,?)",
  ).run(JSON.stringify({
    candles: [], source_candle_count: 1,
    history_start: "2026-08-20T00:00:00Z",
    history_end: "2026-08-20T01:00:00Z",
  }), new Date().toISOString());
  database.database.prepare(
    `INSERT OR REPLACE INTO news_evidence_state
     (id,active_snapshot_id,contract_version,record_count,activated_at)
     VALUES(1,?,?,0,?)`,
  ).run("e".repeat(64), "news-evidence-paged-v2", new Date().toISOString());

  const routes = [
    ["/api/status", 200],
    ["/api/audit", 200],
    ["/api/audit-briefs", 200],
    ["/api/audit-stories", 200],
    ["/api/audit-decisions", 200],
    ["/api/learning", 200],
    ["/api/learning-history?resource=model&limit=6", 200],
    ["/api/market-chart", 200],
    ["/api/market-history?range=24&identity=BROAD_FULL&frequency=30m", 200],
    ["/api/news-evidence?mode=all&page=1&limit=20", 200],
    [`/api/news-content?key=${"a".repeat(64)}`, 503],
    ["/api/operator-retry-worker?worker_id=worker-test", 200],
    ["/api/ingest", 200],
  ];
  for (const [path, expectedStatus] of routes) {
    const headers = path.startsWith("/api/operator-retry-worker")
      ? { Authorization: `Bearer ${token}` } : {};
    const response = await invoke(path, { headers });
    assert.equal(response.status, expectedStatus, path);
    assert.match(response.headers.get("x-aurum-git-sha") ?? "", /^[0-9a-f]{40}$/);
    assert.equal(response.headers.get("x-aurum-worker-version"), "test-worker-version");
    assert.equal(response.headers.get("x-aurum-route"), new URL(`https://x${path}`).pathname);
    assert.ok(response.headers.get("server-timing")?.startsWith("aurum;dur="), path);
    await response.arrayBuffer();
  }
  const status = await (await invoke("/api/status")).json();
  assert.equal(status.annotation_queue, undefined);
  assert.equal(status.gemini_quota, undefined);
  assert.equal(status.observation_scope, "D1_SNAPSHOT");
});

test("selects the freshest valid status snapshot and strips private legacy fields", async () => {
  if (isPreviewBuild) return;
  insertSnapshot(5, JSON.stringify({
    generated_at: "2026-08-20T00:00:00Z",
    system: { online: true, quote_age_seconds: 1 },
    source: "older-public",
  }), "2026-08-20T00:00:01Z");
  insertSnapshot(1, JSON.stringify({
    generated_at: new Date().toISOString(),
    system: { online: true, quote_age_seconds: 1 },
    source: "newer-private",
    annotation_queue: { secret: true },
  }), "2026-08-20T00:00:02Z");
  let payload = await (await invoke("/api/status")).json();
  assert.equal(payload.source, "newer-private");
  assert.equal(payload.annotation_queue, undefined);

  insertSnapshot(1, "{invalid", "2026-08-20T00:00:03Z");
  payload = await (await invoke("/api/status")).json();
  assert.equal(payload.source, "older-public");
});

test("backfills the bounded Live ledger from the authoritative transition snapshot", async () => {
  if (isPreviewBuild) return;
  const decisions = Array.from({ length: 20 }, (_, index) => ({
    decision_id: `legacy-${index}`,
    features: { unused: index },
    predictions: Array.from({ length: 12 }, (_, prediction) => ({ prediction })),
  }));
  insertSnapshot(4, JSON.stringify({
    generated_at: "2026-08-20T00:00:00Z",
    recent_decisions: decisions,
  }), "2026-08-20T00:00:01Z");
  insertSnapshot(1, JSON.stringify({
    generated_at: "2026-08-20T00:00:01Z",
    system: { online: false, quote_age_seconds: 1 },
  }), "2026-08-20T00:00:02Z");
  insertSnapshot(5, JSON.stringify({
    generated_at: "2026-08-20T00:00:03Z",
    system: { online: false, quote_age_seconds: 1 },
  }), "2026-08-20T00:00:04Z");

  const payload = await (await invoke("/api/status")).json();
  assert.equal(payload.recent_decisions.length, 18);
  assert.equal(payload.recent_decisions[0].decision_id, "legacy-0");
  assert.equal(payload.recent_decisions[0].features, undefined);
  assert.equal(payload.recent_decisions[0].predictions.length, 8);
});

test("backfills fixed Live news metrics during the single-owner handover", async () => {
  if (isPreviewBuild) return;
  const metrics = {
    schema_version: "news-metrics-v1",
    articles: {
      received: 7_678, stored_revisions: 7_681, readable: 4_117,
      semantic_reviews_complete: 4_000, current_model_candidates: 115,
    },
    events: {
      independent: 3_469, auditable: 3_400, currently_model_eligible: 115,
      used_in_predictions: 90, never_used: 25,
    },
    prediction_usage: { decision_event_exposures: 200, frozen_model_uses: 150 },
    training: { current_contract_rows: 80, distinct_events: 70 },
  };
  insertSnapshot(1, JSON.stringify({
    generated_at: new Date().toISOString(),
    system: { online: true, quote_age_seconds: 1 },
    counts: { news_revisions: 7_681 },
  }));
  insertSnapshot(9, JSON.stringify({ news_metrics: metrics }));

  const response = await invoke("/api/status");
  assert.equal(response.status, 200);
  assert.equal(response.headers.get("x-aurum-d1-operations"), "1");
  assert.deepEqual((await response.json()).news_metrics, metrics);
});

test("uses the freshest compatible audit source during split-snapshot transition", async () => {
  if (isPreviewBuild) return;
  const older = "2026-08-20T00:00:01Z";
  const newer = "2026-08-20T00:00:02Z";
  insertSnapshot(6, JSON.stringify({ generated_at: older, recent_decisions: [{ decision_id: "split" }] }), older);
  insertSnapshot(7, JSON.stringify({ generated_at: older, daily_news_briefs: [{ brief_date: "split" }] }), older);
  insertSnapshot(8, JSON.stringify({ generated_at: older, storylines: [{ storyline_id: "split" }] }), older);
  insertSnapshot(9, JSON.stringify({ generated_at: older, news_metrics: { source: "split" } }), older);
  insertSnapshot(4, JSON.stringify({
    generated_at: newer,
    news_metrics: { source: "legacy" },
    recent_decisions: [{
      decision_id: "legacy", features: { growing: "x".repeat(2_000) },
      predictions: Array.from({ length: 20 }, (_, index) => ({ index })),
    }],
    daily_news_briefs: [{ brief_date: "legacy", brief_json: "not-copied-to-JS" }],
    storylines: [{ storyline_id: "legacy" }],
  }), newer);

  assert.equal((await (await invoke("/api/audit")).json()).news_metrics.source, "legacy");
  const decisions = await (await invoke("/api/audit-decisions")).json();
  assert.equal(decisions.recent_decisions[0].decision_id, "legacy");
  assert.equal(decisions.recent_decisions[0].features, undefined);
  assert.equal(decisions.recent_decisions[0].predictions.length, 8);
  const briefs = await (await invoke("/api/audit-briefs")).json();
  assert.equal(briefs.daily_news_briefs[0].brief_date, "legacy");
  assert.equal(briefs.daily_news_briefs[0].brief_json, undefined);
  assert.equal((await (await invoke("/api/audit-stories")).json()).storylines[0].storyline_id, "legacy");

  insertSnapshot(4, "{invalid", "2026-08-20T00:00:03Z");
  assert.equal((await (await invoke("/api/audit-decisions")).json()).recent_decisions[0].decision_id, "split");
});

test("oversized fresh legacy stories stay ahead of stale split validation data after bounded projection", async () => {
  if (isPreviewBuild) return;
  const older = "2026-08-20T00:00:01Z";
  const newer = "2026-08-20T00:00:02Z";
  insertSnapshot(8, JSON.stringify({
    generated_at: older,
    storylines: [{ storyline_id: "validation-fixture" }],
  }), older);
  insertSnapshot(4, JSON.stringify({
    generated_at: newer,
    storyline_summary: { total: 500 },
    storylines: Array.from({ length: 20 }, (_, index) => ({
      storyline_id: `authority-${index}`,
      timeline: Array.from({ length: 8 }, () => ({ headline: "黄金".repeat(100) })),
    })),
    story_event_candidates: Array.from({ length: 50 }, (_, index) => ({
      candidate_id: index, headline: "候选".repeat(100),
    })),
    unassigned_story_events: Array.from({ length: 50 }, (_, index) => ({
      event_key: index, headline: "未分配".repeat(100),
    })),
  }), newer);

  const response = await invoke("/api/audit-stories");
  const stories = await response.json();
  assert.equal(response.status, 200);
  assert.equal(stories.storylines[0].storyline_id, "authority-0");
  assert.equal(stories.storylines.length, 12);
  assert.equal(stories.story_event_candidates.length, 12);
  assert.equal(stories.unassigned_story_events.length, 12);
  assert.equal(stories.storyline_summary.total, 500);
});

test("keeps migrated market history schema out of the request hot path", () => {
  const source = readFileSync(
    new URL("../app/api/market-history/route.ts", import.meta.url), "utf8",
  );
  assert.doesNotMatch(source, /CREATE TABLE IF NOT EXISTS market_/);
  assert.match(readFileSync(
    new URL("../drizzle/0004_market_history.sql", import.meta.url), "utf8",
  ), /CREATE TABLE IF NOT EXISTS `market_candles`/);
  assert.match(readFileSync(
    new URL("../drizzle/0006_materialized_history_overviews.sql", import.meta.url), "utf8",
  ), /CREATE TABLE IF NOT EXISTS `market_history_overview`/);
});

test("storage sync families accept identical payloads without repeating physical writes", async () => {
  if (isPreviewBuild) return;
  const headers = { Authorization: `Bearer ${token}`, "Content-Type": "application/json" };
  const post = async (path, payload) => {
    const response = await invoke(path, {
      method: "POST", headers, body: JSON.stringify(payload),
    });
    assert.equal(response.status, 200, `${path}: ${await response.clone().text()}`);
    return response.json();
  };

  const retryJob = {
    job_id: "f".repeat(64), task_type: "ACTIVE_IMPACT", title: "Write contract",
    state: "BACKING_OFF", priority: "NORMAL",
    available_at: "2026-08-25T01:00:00+00:00", attempt_count: 2,
    last_error: "provider unavailable", last_failure_at: "2026-08-25T00:55:00Z",
    lease_expires_at: null, override_mode: null, override_requested_at: null,
    original_available_at: "2026-08-25T01:00:00Z",
  };
  const retryFirst = await post("/api/operator-retry-worker", {
    action: "SYNC_JOBS", items: [retryJob],
  });
  assert.equal(retryFirst.accepted, 1);
  assert.equal(retryFirst.written, 1);
  const retryReplay = await post("/api/operator-retry-worker", {
    action: "SYNC_JOBS", items: [{ ...retryJob, job_id: retryJob.job_id.toUpperCase() }],
  });
  assert.equal(retryReplay.accepted, 1);
  assert.equal(retryReplay.written, 0);
  assert.equal(retryReplay.unchanged, true);
  const retryChanged = await post("/api/operator-retry-worker", {
    action: "SYNC_JOBS", items: [{ ...retryJob, title: "Changed contract" }],
  });
  assert.equal(retryChanged.written, 1);
  const retryEmpty = await post("/api/operator-retry-worker", {
    action: "SYNC_JOBS", items: [],
  });
  assert.equal(retryEmpty.accepted, 0);
  assert.equal(retryEmpty.deleted, 1);

  const candle = {
    time: "2026-08-25T01:00:00.000Z", open: 3380, high: 3382,
    low: 3379, close: 3381, ticks: 42,
  };
  const decision = {
    source_decision_id: "write-contract-decision",
    decision_time: "2026-08-25T01:00:00.000Z",
    model_identity: "BROAD_FULL", direction: "WAIT",
  };
  const marketPayload = {
    overview: {
      candles: [candle], source_candle_count: 1,
      history_start: candle.time, history_end: candle.time,
    },
    decision_overviews: [{
      model_identity: "BROAD_FULL", frequency: "30m", decisions: [decision],
      source_decision_count: 1, decision_count: 1, decision_downsampled: false,
    }],
    candles: [candle], decisions: [decision],
  };
  const marketFirst = await post("/api/market-history", marketPayload);
  assert.equal(marketFirst.accepted, 4);
  assert.equal(marketFirst.written, 4);
  const marketReplay = await post("/api/market-history", marketPayload);
  assert.equal(marketReplay.accepted, 4);
  assert.equal(marketReplay.written, 0);
  const marketChanged = await post("/api/market-history", {
    candles: [{ ...candle, close: 3381.5 }],
  });
  assert.equal(marketChanged.accepted, 1);
  assert.equal(marketChanged.written, 1);

  const learningRecord = {
    resource: "model", record_key: "write-contract-model", sort_epoch: 1,
    payload_hash: "a".repeat(64), payload: { model_identity: "WRITE_CONTRACT" },
  };
  const learningFirst = await post("/api/learning-history", { records: [learningRecord] });
  assert.equal(learningFirst.accepted, 1);
  assert.equal(learningFirst.written, 1);
  const learningReplay = await post("/api/learning-history", { records: [learningRecord] });
  assert.equal(learningReplay.accepted, 1);
  assert.equal(learningReplay.written, 0);
  const learningChanged = await post("/api/learning-history", {
    records: [{
      ...learningRecord, sort_epoch: 2, payload_hash: "b".repeat(64),
      payload: { model_identity: "WRITE_CONTRACT_V2" },
    }],
  });
  assert.equal(learningChanged.accepted, 1);
  assert.equal(learningChanged.written, 1);
});

test("bounds empty, oversized, maximum legal, and concurrent snapshot writes", async () => {
  if (isPreviewBuild) return;
  const headers = { Authorization: `Bearer ${token}`, "Content-Type": "application/json" };
  const empty = await invoke("/api/ingest", { method: "POST", headers, body: "" });
  assert.equal(empty.status, 400);
  assert.equal(empty.headers.get("x-aurum-failure-stage"), "json_validation");

  const tooLarge = await invoke("/api/ingest", {
    method: "POST",
    headers: { ...headers, "Content-Length": "800001" },
    body: "{}",
  });
  assert.equal(tooLarge.status, 413);
  assert.equal(tooLarge.headers.get("x-aurum-d1-operations"), "0");

  const maximum = jsonOfBytes(800_000, {
    generated_at: new Date().toISOString(),
    system: { online: true, quote_age_seconds: 0 },
  });
  assert.equal(Buffer.byteLength(maximum), 800_000);
  const maximumResponse = await invoke("/api/ingest", {
    method: "POST", headers, body: maximum,
  });
  assert.equal(maximumResponse.status, 200);
  assert.equal(maximumResponse.headers.get("x-aurum-request-bytes"), "800000");

  for (const [path, limit, fields] of [
    ["/api/audit", 16_000, { news_metrics: {} }],
    ["/api/audit-briefs", 120_000, { daily_news_briefs: [] }],
    ["/api/audit-stories", 120_000, { storylines: [] }],
    ["/api/audit-decisions", 120_000, { recent_decisions: [] }],
  ]) {
    const bounded = await invoke(path, {
      method: "POST", headers, body: jsonOfBytes(limit, fields),
    });
    assert.equal(bounded.status, 200, path);
    assert.equal(bounded.headers.get("x-aurum-request-bytes"), String(limit), path);
    const oversized = await invoke(path, {
      method: "POST", headers, body: jsonOfBytes(limit + 1, fields),
    });
    assert.equal(oversized.status, 413, path);
    assert.equal(oversized.headers.get("x-aurum-d1-operations"), "0", path);
  }

  const concurrent = await Promise.all([
    ["/api/ingest", jsonOfBytes(300_000, { generated_at: new Date().toISOString(), system: {} })],
    ["/api/audit", jsonOfBytes(16_000, { news_metrics: {} })],
    ["/api/learning", jsonOfBytes(300_000, { models: [] })],
    ["/api/market-chart", jsonOfBytes(300_000, { candles: [] })],
  ].map(([path, body]) => invoke(path, { method: "POST", headers, body })));
  assert.deepEqual(concurrent.map(response => response.status), [200, 200, 200, 200]);
});

test("production-shaped writes honor authenticated release dry-run without mutation", async () => {
  if (isPreviewBuild) return;
  const validationHeaders = {
    Authorization: `Bearer ${token}`,
    "Content-Type": "application/json",
    "X-Aurum-Release-Validation": "dry-run",
    "X-Aurum-Validation-Run": "worker-router-family-run",
  };
  const routes = [
    ["/api/ingest", "status-ingest", { generated_at: new Date().toISOString(), system: {} }, "1"],
    ["/api/audit", "audit-write", { news_metrics: {} }, "1"],
    ["/api/audit-briefs", "audit-briefs-write", { daily_news_briefs: [] }, "1"],
    ["/api/audit-stories", "audit-stories-write", { storylines: [] }, "1"],
    ["/api/audit-decisions", "audit-decisions-write", { recent_decisions: [] }, "1"],
    ["/api/learning", "learning-write", { models: [] }, "1"],
    ["/api/market-chart", "market-chart-write", { candles: [] }, "1"],
    ["/api/news-index", "news-index-write", {
      action: "prepare", generation_id: "1".repeat(64), manifest: {
        generation_id: "1".repeat(64), snapshot_id: "2".repeat(64),
        contract_version: "news-projection-generation-v4",
        window_start: "2026-06-21T00:00:00Z", watermark: "2026-08-20T00:00:00Z",
        expected_index_count: 1, expected_detail_count: 1, withdrawal_count: 0,
        source_digest: "3".repeat(64), expected_receipt_digest: "4".repeat(64),
      },
    }, "unknown"],
    ["/api/news-content", "news-content-write", {
      action: "stage_details", generation_id: "1".repeat(64), offset: 0, items: [{
      detail_key: "1".repeat(64), detail_hash: "2".repeat(64),
      payload: { headline: "候选版本新闻详情", body: "只验证，不写入。" },
    }], }, "unknown"],
  ];
  const state = () => JSON.stringify({
    snapshots: database.database.prepare(
      "SELECT id,payload,received_at FROM dashboard_snapshots ORDER BY id",
    ).all(),
    newsIndex: database.database.prepare(
      "SELECT generation_id,detail_key,payload,received_at FROM news_projection_index ORDER BY generation_id,detail_key",
    ).all(),
    newsDetails: database.database.prepare(
      "SELECT generation_id,detail_key,payload,received_at FROM news_projection_details ORDER BY generation_id,detail_key",
    ).all(),
  });
  const before = state();
  for (const [path, routeFamily, payload, expectedD1Operations] of routes) {
    const response = await invoke(path, {
      method: "POST",
      headers: {
        ...validationHeaders,
        "X-Aurum-Request-ID": `request-${routeFamily}`,
      },
      body: JSON.stringify(payload),
    });
    assert.equal(response.status, 200, path);
    assert.equal(
      response.headers.get("x-aurum-d1-operations"), expectedD1Operations, path,
    );
    const body = await response.json();
    assert.equal(body.status, "DRY_RUN_OK", path);
    assert.equal(body.route_family, routeFamily, path);
    assert.equal(body.validation_run, "worker-router-family-run", path);
    assert.equal(body.mutated, false, path);
    assert.doesNotMatch(JSON.stringify(body), new RegExp(token), path);
  }
  const after = state();
  assert.equal(after, before);
  assert.doesNotMatch(after, new RegExp(token));
});

test("accepts the exact Python News release fixture and rejects a noncanonical clock", async () => {
  if (isPreviewBuild) return;
  const fixture = JSON.parse(readFileSync(new URL(
    "../../tests/fixtures/release_validation_news_index_stage.json",
    import.meta.url,
  ), "utf8"));
  const validationHeaders = {
    Authorization: `Bearer ${token}`,
    "Content-Type": "application/json",
    "X-Aurum-Release-Validation": "dry-run",
    "X-Aurum-Validation-Run": "exact-python-news-fixture-run",
    "X-Aurum-Request-ID": "exact-python-news-fixture-request",
  };
  const state = () => JSON.stringify(database.database.prepare(
    "SELECT generation_id,detail_key,payload,received_at FROM news_projection_index ORDER BY generation_id,detail_key",
  ).all());
  const before = state();
  const accepted = await invoke("/api/news-index", {
    method: "POST", headers: validationHeaders, body: JSON.stringify(fixture),
  });
  assert.equal(accepted.status, 200, await accepted.clone().text());
  assert.equal((await accepted.json()).status, "DRY_RUN_OK");

  const malformed = structuredClone(fixture);
  const visible = malformed.items.find(item => item.model_visibility === "MODEL_VISIBLE");
  assert.ok(visible);
  visible.impact_expires_at = "2026-08-13T06:00:00+00:00";
  const rejected = await invoke("/api/news-index", {
    method: "POST", headers: validationHeaders, body: JSON.stringify(malformed),
  });
  assert.equal(rejected.status, 400, await rejected.clone().text());
  assert.equal(state(), before);
});

test("accepts every exact Python-built production-shaped release fixture", async () => {
  if (isPreviewBuild) return;
  const repositoryRoot = fileURLToPath(new URL("../..", import.meta.url));
  const fixtureRoot = mkdtempSync(join(tmpdir(), "aurum-worker-release-fixtures-"));
  const python = process.platform === "win32" ? "python.exe" : "python3";
  try {
    const built = spawnSync(python, [
      join(repositoryRoot, "scripts", "build_release_validation_fixtures.py"),
      "--output", fixtureRoot,
    ], {
      cwd: repositoryRoot,
      encoding: "utf8",
      env: { ...process.env, PYTHONUTF8: "1" },
    });
    assert.equal(built.status, 0, built.stderr || built.stdout);

    const manifest = JSON.parse(readFileSync(
      join(repositoryRoot, "web", "worker-validation-manifest.json"), "utf8",
    ));
    const exactWrites = [];
    for (const route of manifest.routes.filter(row =>
      row.boundary === "WORKER_WRITE" && row.strategy === "PRODUCTION_SHAPED_DRY_RUN")) {
      const scenarios = route.scenarios?.length
        ? route.scenarios
        : [{ name: "default", fixture: route.fixture }];
      for (const scenario of scenarios) {
        exactWrites.push({
          path: route.path,
          family: route.family,
          scenario: scenario.name,
          fixture: scenario.fixture,
        });
      }
    }
    assert.equal(exactWrites.length, 19);
    assert.deepEqual(new Set(exactWrites.map(row => row.family)), new Set([
      "status-ingest",
      "audit-write",
      "audit-briefs-write",
      "audit-stories-write",
      "audit-decisions-write",
      "learning-write",
      "market-chart-write",
      "market-history-write",
      "learning-history-write",
      "news-evidence-write",
      "news-index-write",
      "news-content-write",
    ]));

    const state = () => JSON.stringify({
      snapshots: database.database.prepare(
        "SELECT id,payload,received_at FROM dashboard_snapshots ORDER BY id",
      ).all(),
      newsIndex: database.database.prepare(
        "SELECT generation_id,detail_key,payload,received_at FROM news_projection_index ORDER BY generation_id,detail_key",
      ).all(),
      newsDetails: database.database.prepare(
        "SELECT generation_id,detail_key,payload,received_at FROM news_projection_details ORDER BY generation_id,detail_key",
      ).all(),
    });
    const before = state();
    for (const [index, route] of exactWrites.entries()) {
      const exactBytes = readFileSync(join(fixtureRoot, route.fixture));
      const response = await invoke(route.path, {
        method: "POST",
        headers: {
          Authorization: `Bearer ${token}`,
          "Content-Type": "application/json",
          "X-Aurum-Release-Validation": "dry-run",
          "X-Aurum-Validation-Run": "exact-python-fixture-family-run",
          "X-Aurum-Request-ID": `exact-python-fixture-${index}`,
        },
        body: exactBytes,
      });
      assert.equal(
        response.status, 200,
        `${route.family}/${route.scenario}: ${await response.clone().text()}`,
      );
      const payload = await response.json();
      assert.equal(payload.status, "DRY_RUN_OK", route.fixture);
      assert.equal(payload.route_family, route.family, route.fixture);
      assert.equal(payload.mutated, false, route.fixture);
    }
    assert.equal(state(), before);
  } finally {
    rmSync(fixtureRoot, { recursive: true, force: true });
  }
});

test("news release dry-runs reject invalid payloads without mutation", async () => {
  if (isPreviewBuild) return;
  const validationHeaders = {
    Authorization: `Bearer ${token}`,
    "Content-Type": "application/json",
    "X-Aurum-Release-Validation": "dry-run",
    "X-Aurum-Validation-Run": "invalid-news-payload-run",
  };
  const state = () => JSON.stringify({
    newsIndex: database.database.prepare(
      "SELECT generation_id,detail_key,payload,received_at FROM news_projection_index ORDER BY generation_id,detail_key",
    ).all(),
    newsDetails: database.database.prepare(
      "SELECT generation_id,detail_key,payload,received_at FROM news_projection_details ORDER BY generation_id,detail_key",
    ).all(),
  });
  const before = state();
  for (const [caseIndex, [path, body, expectedStatus]] of [
    ["/api/news-index", JSON.stringify({ action: "stage_index", generation_id: "1".repeat(64), offset: 0, items: [{
      detail_key: "1".repeat(64), category: "增长/经济", cluster_id: "cluster-1",
      collector_first_seen_time: "2026-08-20T00:00:00Z",
      annotation_status: "NOT_A_REVIEW_STATE", model_visibility: "MODEL_VISIBLE",
      mirror_contract: "release-validation-v1",
    }] }), 400],
    ["/api/news-content", JSON.stringify({ action: "stage_details", generation_id: "1".repeat(64), offset: 0, items: [{
      detail_key: "1".repeat(64), detail_hash: "not-a-sha256", payload: {},
    }] }), 400],
    ["/api/news-index", "{not-json", 400],
    ["/api/news-content", "{not-json", 400],
    ["/api/news-index", JSON.stringify({
      action: "stage_index", generation_id: "1".repeat(64), offset: null,
      items: [{
        detail_key: "1".repeat(64), category: "增长/经济", cluster_id: "cluster-1",
        collector_first_seen_time: "2026-08-20T00:00:00Z",
        annotation_status: "READY", model_visibility: "MODEL_VISIBLE",
        parsed_at: "2026-08-20T00:00:00Z", mirror_contract: "release-validation-v1",
      }],
    }), 400],
    ["/api/news-index", JSON.stringify({
      action: "stage_index", generation_id: "1".repeat(64), offset: 0,
      items: Array.from({ length: 5 }, (_, index) => ({
        detail_key: String(index + 1).repeat(64), category: "增长/经济",
        cluster_id: `cluster-${index}`,
        collector_first_seen_time: "2026-08-20T00:00:00Z",
        annotation_status: "READY", model_visibility: "MODEL_VISIBLE",
        parsed_at: "2026-08-20T00:00:00Z",
        mirror_contract: "news-projection-generation-v4",
      })),
    }), 400],
    ["/api/news-content", JSON.stringify({
      action: "stage_details", generation_id: "1".repeat(64), offset: 0,
      items: Array.from({ length: 9 }, (_, index) => ({
        detail_key: String(index + 1).repeat(64),
        detail_hash: String(index + 2).repeat(64), payload: {},
      })),
    }), 400],
    ["/api/news-evidence", JSON.stringify({
      contract_version: "news-evidence-paged-v2", snapshot_id: "1".repeat(64), offset: 0,
      items: Array.from({ length: 9 }, (_, index) => ({
        event_key: String(index + 1).repeat(64),
        collector_first_seen_time: "2026-08-20T00:00:00Z",
        broad_model_eligible: true, model_seen: false,
      })),
    }), 400],
    ["/api/news-content", JSON.stringify({ action: "reset" }), 400],
  ].entries()) {
    const response = await invoke(path, {
      method: "POST", headers: {
        ...validationHeaders, "X-Aurum-Request-ID": `invalid-news-${caseIndex}`,
      }, body,
    });
    assert.equal(response.status, expectedStatus, `${path}: ${await response.clone().text()}`);
    assert.equal(response.headers.get("x-aurum-d1-operations"), "unknown", path);
  }
  assert.equal(state(), before);
});

test("turns a temporary D1 failure into a bounded resource-owned 503", async () => {
  if (isPreviewBuild) return;
  const failingEnv = {
    ...runtimeEnv,
    DB: { prepare() { throw new Error("temporary D1 failure"); } },
  };
  for (const path of [
    "/api/status", "/api/audit", "/api/audit-briefs",
    "/api/audit-stories", "/api/audit-decisions",
    "/api/learning", "/api/market-chart",
  ]) {
    const response = await invoke(path, {}, failingEnv);
    assert.equal(response.status, 503, path);
    assert.equal(response.headers.get("x-aurum-failure-stage"), "d1_read", path);
    assert.equal(response.headers.get("x-aurum-d1-operations"), "1", path);
    assert.ok((await response.text()).length < 100, path);
  }
});

test("soaks mixed reads without framework fallback or 5xx responses", async () => {
  const oldLog = console.log;
  console.log = () => {};
  try {
    const routes = [
      "/api/status", "/api/audit", "/api/audit-briefs",
      "/api/audit-stories", "/api/audit-decisions",
      "/api/learning", "/api/market-chart",
    ];
    for (let cycle = 0; cycle < 100; cycle += 1) {
      const responses = await Promise.all(routes.map(path => invoke(path)));
      assert.ok(responses.every(response => response.status === 200));
      assert.ok(responses.every(response => response.headers.get("x-aurum-resource") !== "unmatched-api"));
      await Promise.all(responses.map(response => response.arrayBuffer()));
    }
  } finally {
    console.log = oldLog;
  }
});

test("keeps Preview reads immutable and rejects sync writes before D1", async () => {
  if (!isPreviewBuild) return;
  const failingEnv = {
    ...runtimeEnv,
    DB: { prepare() { throw new Error("Preview must not touch D1 for embedded status"); } },
  };
  const status = await invoke("/api/status", {}, failingEnv);
  assert.equal(status.status, 200);
  assert.equal(status.headers.get("x-aurum-resource"), "status");
  const write = await invoke("/api/ingest", {
    method: "POST",
    headers: { Authorization: `Bearer ${token}` },
    body: "{}",
  }, failingEnv);
  assert.equal(write.status, 403);
  assert.match(await write.text(), /只读.*不接受写入/);
});
