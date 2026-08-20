import { readdirSync } from "node:fs";
import { Console } from "node:console";
import { Writable } from "node:stream";
import { D1TestDatabase } from "../tests/d1-test-database.mjs";

const SAMPLE_COUNT = 120;
const token = "production-shaped-benchmark-token";
const migrations = readdirSync(new URL("../drizzle", import.meta.url))
  .filter(name => name.endsWith(".sql"))
  .sort();
const sourceDatabase = new D1TestDatabase(migrations);
let d1CpuMicroseconds = 0;
let d1WallMilliseconds = 0;

function measuredStatement(statement) {
  const invoke = async (method, args = []) => {
    const started = process.cpuUsage();
    const wallStarted = performance.now();
    try { return await statement[method](...args); }
    finally {
      const used = process.cpuUsage(started);
      d1CpuMicroseconds += used.user + used.system;
      d1WallMilliseconds += performance.now() - wallStarted;
    }
  };
  return {
    bind(...values) { return measuredStatement(statement.bind(...values)); },
    first(...args) { return invoke("first", args); },
    all(...args) { return invoke("all", args); },
    run(...args) { return invoke("run", args); },
    execute(...args) { return invoke("execute", args); },
  };
}

const database = {
  prepare(sql) { return measuredStatement(sourceDatabase.prepare(sql)); },
  batch(statements) { return sourceDatabase.batch(statements); },
};
const runtimeEnv = {
  DB: database,
  INGEST_TOKEN: token,
  CF_VERSION_METADATA: { id: "benchmark-worker-version" },
  ASSETS: { fetch: async () => new Response("asset") },
  IMAGES: {},
  ASSISTANT_MEMORY_VECTOR: {},
};
globalThis.__AURUM_TEST_WORKER_ENV = runtimeEnv;
const { default: worker } = await import("../dist/server/index.js");
const context = { waitUntil() {}, passThroughOnException() {} };

function jsonOfBytes(targetBytes, fields = {}) {
  const shell = JSON.stringify({ ...fields, padding: "" });
  return JSON.stringify({ ...fields, padding: "x".repeat(targetBytes - shell.length) });
}

function insertSnapshot(id, payload) {
  sourceDatabase.database.prepare(
    `INSERT INTO dashboard_snapshots(id,payload,received_at) VALUES(?,?,?)
     ON CONFLICT(id) DO UPDATE SET payload=excluded.payload,received_at=excluded.received_at`,
  ).run(id, payload, new Date().toISOString());
}

insertSnapshot(1, jsonOfBytes(250_000, {
  generated_at: new Date().toISOString(),
  system: { online: true, quote_age_seconds: 1 },
  annotation_queue: { private: true },
}));
insertSnapshot(2, jsonOfBytes(390_000, { candles: [] }));
insertSnapshot(3, jsonOfBytes(208_000, { models: [] }));
insertSnapshot(4, jsonOfBytes(319_000, { decisions: [] }));
sourceDatabase.database.prepare(
  "INSERT INTO market_history_overview(overview_key,payload,received_at) VALUES('all',?,?)",
).run(JSON.stringify({
  candles: [], source_candle_count: 1,
  history_start: "2026-08-20T00:00:00Z", history_end: "2026-08-20T01:00:00Z",
}), new Date().toISOString());
sourceDatabase.database.prepare(
  `INSERT INTO news_evidence_state
   (id,active_snapshot_id,contract_version,record_count,activated_at)
   VALUES(1,?,?,0,?)`,
).run("e".repeat(64), "news-evidence-paged-v2", new Date().toISOString());

const routes = [
  ["/api/status"],
  ["/api/audit"],
  ["/api/learning"],
  ["/api/learning-history?resource=model&limit=6"],
  ["/api/market-chart"],
  ["/api/market-history?range=24&identity=BROAD_FULL&frequency=30m"],
  ["/api/news-evidence?mode=all&page=1&limit=20"],
  [`/api/news-content?key=${"a".repeat(64)}`],
  ["/api/operator-retry-worker?worker_id=worker-benchmark", {
    headers: { Authorization: `Bearer ${token}` },
  }],
  ["/api/ingest"],
];

const percentile = (values, fraction) => {
  const ordered = [...values].sort((a, b) => a - b);
  return ordered[Math.min(ordered.length - 1, Math.ceil(ordered.length * fraction) - 1)];
};
const rounded = value => Number(value.toFixed(3));

async function sample(label, factory, count = SAMPLE_COUNT) {
  const activeWallValues = [];
  let failures = 0;
  let cpuMicroseconds = 0;
  for (let index = 0; index < count + 5; index += 1) {
    const request = factory();
    d1CpuMicroseconds = 0;
    d1WallMilliseconds = 0;
    const started = process.cpuUsage();
    const wallStarted = performance.now();
    const response = await worker.fetch(request, runtimeEnv, context);
    const used = process.cpuUsage(started);
    const workerCpu = Math.max(0, used.user + used.system - d1CpuMicroseconds) / 1_000;
    const activeWall = Math.max(0, performance.now() - wallStarted - d1WallMilliseconds);
    if (response.status >= 500) failures += 1;
    if (index >= 5) {
      cpuMicroseconds += workerCpu * 1_000;
      activeWallValues.push(activeWall);
    }
  }
  return {
    route: label,
    samples: activeWallValues.length,
    mean_cpu_ms_windows_timer: rounded(cpuMicroseconds / activeWallValues.length / 1_000),
    p50_active_wall_ms: rounded(percentile(activeWallValues, 0.5)),
    p95_active_wall_ms: rounded(percentile(activeWallValues, 0.95)),
    max_active_wall_ms: rounded(Math.max(...activeWallValues)),
    failures,
  };
}

async function runRouteFamily() {
  const results = [];
  for (const [path, init = {}] of routes) {
    results.push(await sample(path, () => new Request(`https://example.test${path}`, init)));
  }
  const maximum = jsonOfBytes(800_000, {
    generated_at: new Date().toISOString(), system: { online: true, quote_age_seconds: 0 },
  });
  results.push(await sample("POST /api/ingest (800KB)", () => new Request(
    "https://example.test/api/ingest",
    {
      method: "POST",
      headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
      body: maximum,
    },
  ), 40));
  return results;
}

const originalLog = console.log;
let diagnosticLogBytes = 0;
let loggingDisabled;
let loggingEnabled;
try {
  console.log = () => {};
  loggingDisabled = await runRouteFamily();
  const diagnosticSink = new Writable({
    write(chunk, _encoding, callback) {
      diagnosticLogBytes += Buffer.byteLength(chunk);
      callback();
    },
  });
  const diagnosticConsole = new Console({ stdout: diagnosticSink, stderr: diagnosticSink });
  console.log = diagnosticConsole.log.bind(diagnosticConsole);
  loggingEnabled = await runRouteFamily();
} finally {
  console.log = originalLog;
}

const loggingDelta = loggingEnabled.map((enabled, index) => ({
  route: enabled.route,
  mean_cpu_ms_delta: rounded(
    enabled.mean_cpu_ms_windows_timer
      - loggingDisabled[index].mean_cpu_ms_windows_timer,
  ),
  p95_active_wall_ms_delta: rounded(
    enabled.p95_active_wall_ms - loggingDisabled[index].p95_active_wall_ms,
  ),
}));

const staticRows = ["/", "/favicon.ico"].map(route => ({
  route, samples: SAMPLE_COUNT, mean_cpu_ms_windows_timer: 0,
  p50_active_wall_ms: 0, p95_active_wall_ms: 0,
  max_active_wall_ms: 0, failures: 0, delivery: "static asset; Worker not invoked",
}));
const report = {
  methodology: "Warmed production bundle; active wall subtracts measured local D1 execution. Logging-enabled samples serialize and write every diagnostic to an in-memory Node Console sink. Windows CPU timers are aggregate local evidence only and cannot prove Cloudflare CPU safety or resolution of Error 1102; validate a 0% Candidate with platform invocation logs before promotion.",
  static: staticRows,
  logging_disabled: loggingDisabled,
  logging_enabled: loggingEnabled,
  logging_delta: loggingDelta,
  diagnostic_log_bytes_written: diagnosticLogBytes,
};
console.log(JSON.stringify(report, null, 2));
