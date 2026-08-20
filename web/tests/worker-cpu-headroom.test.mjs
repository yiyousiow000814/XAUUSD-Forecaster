import assert from "node:assert/strict";
import { readdirSync, readFileSync, statSync } from "node:fs";
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

function insertSnapshot(id, payload) {
  database.database.prepare(
    `INSERT INTO dashboard_snapshots(id,payload,received_at) VALUES(?,?,?)
     ON CONFLICT(id) DO UPDATE SET payload=excluded.payload,received_at=excluded.received_at`,
  ).run(id, payload, new Date().toISOString());
}

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

test("serves the public shell and favicon as static assets before Worker execution", () => {
  const config = readFileSync(new URL("../wrangler.jsonc", import.meta.url), "utf8");
  const redirects = readFileSync(new URL("../dist/client/_redirects", import.meta.url), "utf8");
  assert.ok(statSync(new URL("../dist/client/index.html", import.meta.url)).size > 10_000);
  assert.ok(statSync(new URL("../dist/client/favicon.svg", import.meta.url)).size > 0);
  assert.match(redirects, /^\/favicon\.ico \/favicon\.svg 301/m);
  assert.match(config, /"run_worker_first": \[[\s\S]*"\/api\/\*", "\/admin\/api\/\*", "\/_vinext\/image"/);
  assert.doesNotMatch(config, /run_worker_first[^\]]*favicon/);
});

test("keeps legacy redirects on the minimal Worker path", async () => {
  for (const [path, destination] of [
    ["/status", "/admin/ai-usage"],
    ["/health", "/?room=health"],
    ["/assistant", "/admin/assistant"],
    ["/retry-jobs", "/admin/retry-jobs"],
    ["/audit?view=league", "/?room=audit&view=league"],
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
  insertSnapshot(4, jsonOfBytes(319_000, { decisions: [] }));
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
    ["/api/learning", 200],
    ["/api/learning-history?resource=model&limit=6", 200],
    ["/api/market-chart", 200],
    ["/api/market-history?range=24&identity=BROAD_FULL&frequency=30m", 200],
    ["/api/news-evidence?mode=all&page=1&limit=20", 200],
    [`/api/news-content?key=${"a".repeat(64)}`, 404],
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

  const concurrent = await Promise.all([
    ["/api/ingest", jsonOfBytes(300_000, { generated_at: new Date().toISOString(), system: {} })],
    ["/api/audit", jsonOfBytes(300_000, { decisions: [] })],
    ["/api/learning", jsonOfBytes(300_000, { models: [] })],
    ["/api/market-chart", jsonOfBytes(300_000, { candles: [] })],
  ].map(([path, body]) => invoke(path, { method: "POST", headers, body })));
  assert.deepEqual(concurrent.map(response => response.status), [200, 200, 200, 200]);
});

test("turns a temporary D1 failure into a bounded resource-owned 503", async () => {
  if (isPreviewBuild) return;
  const failingEnv = {
    ...runtimeEnv,
    DB: { prepare() { throw new Error("temporary D1 failure"); } },
  };
  for (const path of ["/api/status", "/api/audit", "/api/learning", "/api/market-chart"]) {
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
    const routes = ["/api/status", "/api/audit", "/api/learning", "/api/market-chart"];
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
