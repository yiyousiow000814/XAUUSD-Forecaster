// Isolated connected-recovery boundary, not Cloudflare/CPU production evidence.
// Run: node --import ./web/tests/register-cloudflare-worker-loader.mjs
//   ./tests/fixtures/connected_worker_adapter.mjs
// First JSON line: {command:"initialize", origin:"https://connected-worker.invalid",
//   ingest_token:"fixture-only", migrations_sha256:"<describe-inputs digest>",
//   capabilities_sql_sha256:"<describe-inputs digest>",
//   workers:[{role:"candidate", source_root:"<checkout>", git_sha:"<40 hex>",
//     worker_version_id:"<UUID>", entry_sha256:"<dist/server/index.js SHA256>",
//     bundle_files:[{path:"web/dist/server/...",size:0,sha256:"<digest>"}],
//     bundle_files_sha256:"<canonical list digest>",
//     source_inputs:[{path:"web/package.json",size:0,sha256:"<digest>"}],
//     source_inputs_sha256:"<canonical list digest>"}]}
// Both caller-frozen lists are sorted by path with exactly path,size,sha256
// keys; their digest is SHA256 of UTF-8 JSON.stringify(list). Bundle coverage is
// every file under web/dist/server and client, including lazy imported chunks.
// Source inputs cover package/lock/Vite/Wrangler, web/build, and an existing
// architecture/generated/critical-index.json. The producer separately proves
// clean source/build execution; file digests alone do not prove execution.
// The isolated producer owns immutable source/output directories for the whole
// process lifetime; it must not rebuild or modify a bundle while serving it.
// Stable and Candidate descriptors load distinct real bundles; both share the
// same ephemeral D1 database, as two versions in one declared environment do.
// request: {command:"request", role, git_sha, worker_version_id, method, url,
//   headers:{...}, body_base64:"", surface:"worker"|"assets"}
// inspect_news and migrate_learning_history require the same exact identity.
// EOF closes the in-memory database. Restart never claims retained remote state.
import { createHash } from "node:crypto";
import { Console } from "node:console";
import { lstatSync, opendirSync, readFileSync, readdirSync, realpathSync } from "node:fs";
import http from "node:http";
import https from "node:https";
import net from "node:net";
import tls from "node:tls";
import { syncBuiltinESMExports } from "node:module";
import { dirname, isAbsolute, join, relative, resolve, sep } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { D1TestDatabase } from "../../web/tests/d1-test-database.mjs";
import { NEWS_PROJECTION_MAX_ITEMS, NEWS_INDEX_MAX_BATCH_ITEMS, NEWS_DETAIL_MAX_BATCH_ITEMS } from "../../web/app/api/_shared/news-projection-store.ts";

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "../..");
const MAX_COMMANDS = 1024;
let maximumCommands = MAX_COMMANDS;
const MAX_BODY_BYTES = 800_000;
const MAX_RESPONSE_BYTES = 2_000_000;
const MAX_LINE_BYTES = 1_100_000;
const MAX_HEADER_BYTES = 16_384;
const MAX_FILE_SET_COUNT = 2048;
const MAX_FILE_SET_BYTES = 32_000_000;
const MAX_BUNDLE_FILE_BYTES = 5_000_000;
const REQUEST_TIMEOUT_MS = 15_000;
const MIGRATION = "0031_bounded_learning_history_reads.sql";
const sha256 = bytes => createHash("sha256").update(bytes).digest("hex");
const fail = code => { throw Object.assign(new Error(code), { code }); };
const samePath = (left, right) => process.platform === "win32"
  ? left.toLowerCase() === right.toLowerCase() : left === right;
const validSha = value => typeof value === "string" && /^[a-f0-9]{40}$/.test(value);
const validDigest = value => typeof value === "string" && /^[a-f0-9]{64}$/.test(value);
const validVersion = value => typeof value === "string"
  && /^[a-f0-9]{8}-(?:[a-f0-9]{4}-){3}[a-f0-9]{12}$/.test(value);

function contained(root, parts, { optional = false, limit = MAX_RESPONSE_BYTES } = {}) {
  const target = resolve(root, ...parts);
  const suffix = relative(root, target);
  if (!suffix || suffix === ".." || suffix.startsWith(`..${sep}`) || isAbsolute(suffix)) {
    fail("WORKER_ADAPTER_PATH_ESCAPE");
  }
  let current = root;
  for (const part of suffix.split(sep)) {
    current = join(current, part);
    let info;
    try { info = lstatSync(current); }
    catch (error) { if (optional && error.code === "ENOENT") return null; throw error; }
    if (info.isSymbolicLink() || !samePath(realpathSync(current), current)) {
      fail("WORKER_ADAPTER_PATH_REDIRECTED");
    }
  }
  const info = lstatSync(target);
  if (!info.isFile() || info.size > limit) fail("WORKER_ADAPTER_FILE_BOUND");
  return target;
}

function sourceRoot(value) {
  if (typeof value !== "string" || !isAbsolute(value) || value.length > 1024) {
    fail("WORKER_ADAPTER_SOURCE_ROOT_INVALID");
  }
  const root = resolve(value);
  if (!samePath(realpathSync(root), root) || lstatSync(root).isSymbolicLink()) {
    fail("WORKER_ADAPTER_SOURCE_ROOT_REDIRECTED");
  }
  const manifest = JSON.parse(readFileSync(contained(root, ["web", "package.json"], { limit: 100_000 }), "utf8"));
  if (manifest.name !== "site-creator-vinext-starter" || manifest.private !== true) {
    fail("WORKER_ADAPTER_SOURCE_ROOT_INVALID");
  }
  return root;
}

function fileSet(root, trees, individual = []) {
  const found = [];
  let totalBytes = 0;
  let directories = 0;
  function add(path) {
    const target = contained(root, path.split("/"), { limit: MAX_BUNDLE_FILE_BYTES });
    const size = lstatSync(target).size;
    totalBytes += size;
    if (found.length >= MAX_FILE_SET_COUNT || totalBytes > MAX_FILE_SET_BYTES) fail("WORKER_ADAPTER_FILE_SET_BOUND");
    found.push({ path, size });
  }
  function walk(path, depth = 0) {
    if (++directories > MAX_FILE_SET_COUNT || depth > 16) fail("WORKER_ADAPTER_FILE_SET_BOUND");
    const directory = resolve(root, ...path.split("/"));
    if (lstatSync(directory).isSymbolicLink() || !samePath(realpathSync(directory), directory)
      || !lstatSync(directory).isDirectory()) fail("WORKER_ADAPTER_PATH_REDIRECTED");
    const listing = opendirSync(directory);
    try {
      let entry;
      while ((entry = listing.readSync()) !== null) {
        const child = `${path}/${entry.name}`;
        const info = lstatSync(join(directory, entry.name));
        if (info.isSymbolicLink()) fail("WORKER_ADAPTER_PATH_REDIRECTED");
        if (info.isDirectory()) walk(child, depth + 1);
        else add(child);
      }
    } finally { listing.closeSync(); }
  }
  for (const path of trees) walk(path);
  for (const path of individual) add(path);
  return found.sort((left, right) => left.path < right.path ? -1 : left.path > right.path ? 1 : 0);
}

function verifyFileSet(root, supplied, digest, actual) {
  if (!Array.isArray(supplied) || supplied.length < 1 || supplied.length > MAX_FILE_SET_COUNT
    || !validDigest(digest)) fail("WORKER_ADAPTER_FILE_SET_INVALID");
  let previous = "";
  let totalBytes = 0;
  const canonical = supplied.map(item => {
    if (!item || typeof item.path !== "string" || item.path.length > 512
      || /[\\:\x00-\x1f\x7f]/.test(item.path)
      || item.path.split("/").some(part => !part || part === "." || part === "..")
      || item.path <= previous || !Number.isSafeInteger(item.size) || item.size < 0
      || item.size > MAX_BUNDLE_FILE_BYTES || !validDigest(item.sha256)
      || Object.keys(item).sort().join(",") !== "path,sha256,size") fail("WORKER_ADAPTER_FILE_SET_INVALID");
    previous = item.path;
    totalBytes += item.size;
    return { path: item.path, size: item.size, sha256: item.sha256 };
  });
  if (totalBytes > MAX_FILE_SET_BYTES || sha256(JSON.stringify(canonical)) !== digest) {
    fail("WORKER_ADAPTER_FILE_SET_DIGEST_MISMATCH");
  }
  if (JSON.stringify(canonical.map(({ path, size }) => ({ path, size }))) !== JSON.stringify(actual)) {
    fail("WORKER_ADAPTER_FILE_SET_COVERAGE_MISMATCH");
  }
  for (const item of canonical) {
    const bytes = readFileSync(contained(root, item.path.split("/"), { limit: MAX_BUNDLE_FILE_BYTES }));
    if (bytes.length !== item.size || sha256(bytes) !== item.sha256) fail("WORKER_ADAPTER_FILE_INTEGRITY_FAILED");
  }
  return { sha256: digest, file_count: canonical.length, total_bytes: totalBytes };
}

function sourceFiles(root) {
  const paths = ["web/package.json", "web/package-lock.json", "web/vite.config.ts", "web/wrangler.jsonc"];
  const architecture = "architecture/generated/critical-index.json";
  if (contained(root, architecture.split("/"), { optional: true, limit: MAX_BUNDLE_FILE_BYTES })) paths.push(architecture);
  return fileSet(root, ["web/build"], paths);
}

const migrationNames = readdirSync(join(ROOT, "web", "drizzle"))
  .filter(name => /^\d{4}_[a-z0-9_]+\.sql$/.test(name)).sort();
const migrationInputs = migrationNames.map(name => ({
  name, sha256: sha256(readFileSync(contained(ROOT, ["web", "drizzle", name], { limit: 1_000_000 }))),
}));
const migrationsDigest = sha256(JSON.stringify(migrationInputs));
const controllerSource = readFileSync(contained(ROOT, ["scripts", "control_center_evidence_authority.ps1"], { limit: 1_000_000 }), "utf8");
const capabilityMatches = [...controllerSource.matchAll(/\$capabilitySql = @"\r?\n([\s\S]*?)\r?\n"@/g)];
const ledgerSql = "SELECT name,applied_at FROM d1_migrations ORDER BY id";
if (capabilityMatches.length !== 1 || !controllerSource.includes(`"${ledgerSql}"`)
  || /\$(?:[A-Za-z_({])/.test(capabilityMatches[0][1])) fail("WORKER_ADAPTER_CONTROLLER_QUERY_INVALID");
// Apply exactly the controller's whitespace-only native transport conversion.
const capabilitySql = capabilityMatches[0][1].replace(/\r\n|\n|\r/g, " ").trim();
const capabilityDigest = sha256(capabilitySql);

function denyNetwork() {
  const denied = () => fail("WORKER_ADAPTER_EXTERNAL_NETWORK_FORBIDDEN");
  globalThis.fetch = async () => denied();
  globalThis.WebSocket = class { constructor() { denied(); } };
  http.request = http.get = https.request = https.get = denied;
  net.connect = net.createConnection = tls.connect = denied;
  net.Socket.prototype.connect = denied;
  syncBuiltinESMExports();
}

const mime = {
  ".html": "text/html; charset=utf-8", ".js": "application/javascript; charset=utf-8",
  ".css": "text/css; charset=utf-8", ".json": "application/json; charset=utf-8",
  ".svg": "image/svg+xml", ".png": "image/png", ".ico": "image/x-icon",
  ".rsc": "text/x-component", ".woff2": "font/woff2",
};

export function assetFetch(root, request) {
  if (!["GET", "HEAD"].includes(request.method)) return new Response(null, { status: 405 });
  let pathname;
  try { pathname = decodeURIComponent(new URL(request.url).pathname); }
  catch { fail("WORKER_ADAPTER_ASSET_PATH_INVALID"); }
  if (!pathname.startsWith("/") || /[\\\x00-\x1f:]/.test(pathname)
    || pathname.split("/").some(part => part === ".." || part === ".")) {
    fail("WORKER_ADAPTER_ASSET_PATH_INVALID");
  }
  const redirects = contained(root, ["web", "dist", "client", "_redirects"], { optional: true, limit: 8192 });
  if (redirects) {
    const rules = readFileSync(redirects, "utf8").split(/\r?\n/).map(line => line.trim())
      .filter(line => line && !line.startsWith("#"));
    // Only the literal redirect grammar present in the built artifacts is
    // supported. This is declared asset-provider behavior, not a validator.
    if (rules.length > 32) fail("WORKER_ADAPTER_REDIRECT_BOUND");
    const parsed = rules.map(rule => {
      const match = /^(\/[A-Za-z0-9_./-]+)\s+(\/[A-Za-z0-9_./-]+)\s+(301|302|307|308)$/.exec(rule);
      if (!match || match[1].includes("..") || match[2].includes("..") || match[2].startsWith("//")) {
        fail("WORKER_ADAPTER_REDIRECT_UNDECLARED");
      }
      return match;
    });
    const redirect = parsed.find(match => pathname === match[1]);
    if (redirect) return new Response(null, { status: Number(redirect[3]), headers: { Location: redirect[2] } });
  }
  const stem = pathname === "/" ? "index.html" : pathname.slice(1).replace(/\/$/, "");
  const choices = stem.split("/").at(-1).includes(".") ? [stem] : [`${stem}.html`, `${stem}/index.html`];
  for (const choice of choices) {
    const path = contained(root, ["web", "dist", "client", ...choice.split("/")], { optional: true });
    if (!path) continue;
    const content = readFileSync(path);
    const extension = choice.slice(choice.lastIndexOf("."));
    return new Response(request.method === "HEAD" ? null : content, {
      headers: { "Content-Type": mime[extension] ?? "application/octet-stream", "Content-Length": String(content.length) },
    });
  }
  return new Response("Asset not found", { status: 404 });
}

async function readBoundedBody(response) {
  if (!response.body) return Buffer.alloc(0);
  const reader = response.body.getReader();
  const chunks = [];
  let length = 0;
  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      length += value.byteLength;
      if (length > MAX_RESPONSE_BYTES) fail("WORKER_ADAPTER_RESPONSE_BOUND");
      chunks.push(Buffer.from(value));
    }
  } finally { await reader.cancel(); reader.releaseLock(); }
  return Buffer.concat(chunks, length);
}

async function bounded(operation) {
  let timer;
  try {
    return await Promise.race([operation(), new Promise((_, reject) => {
      timer = setTimeout(() => reject(Object.assign(new Error("WORKER_ADAPTER_REQUEST_TIMEOUT"), {
        code: "WORKER_ADAPTER_REQUEST_TIMEOUT", fatal: true,
      })), REQUEST_TIMEOUT_MS);
    })]);
  } finally { clearTimeout(timer); }
}

let database = null;
let origin = null;
const workers = new Map();
let initialized = false;
let migrationApplied = false;

async function invokeWorker(target, request) {
  globalThis.__AURUM_TEST_WORKER_ENV = target.env;
  const pending = [];
  try {
    const response = await target.worker.fetch(request, target.env, {
      waitUntil(promise) {
        if (pending.length >= 64) fail("WORKER_ADAPTER_BACKGROUND_WORK_BOUND");
        const task = Promise.resolve(promise);
        task.catch(() => {}); // Keep early rejection handled until checked below.
        pending.push(task);
      },
      passThroughOnException() { fail("WORKER_ADAPTER_PASSTHROUGH_FORBIDDEN"); },
    });
    let settled = 0;
    while (settled < pending.length) {
      const batch = pending.slice(settled);
      settled = pending.length;
      await Promise.all(batch);
    }
    if (!(response instanceof Response)) fail("WORKER_ADAPTER_RESPONSE_INVALID");
    if (response.headers.get("X-Aurum-Git-SHA") !== target.git_sha
      || response.headers.get("X-Aurum-Worker-Version") !== target.worker_version_id) {
      fail("WORKER_ADAPTER_BUILT_IDENTITY_MISMATCH");
    }
    return response;
  } catch (error) {
    // A rejected handler/background task may still have live sibling work.
    // Exit this disposable process; never switch global env for another role.
    throw Object.assign(new Error(error?.code ?? "WORKER_ADAPTER_INVOCATION_FAILED"), {
      code: error?.code ?? "WORKER_ADAPTER_INVOCATION_FAILED", fatal: true,
    });
  }
}

export function sessionCommandBudget(declaration) {
  if (declaration === undefined) return { maximum_commands: MAX_COMMANDS };
  if (!declaration || declaration.schema !== "connected-recovery-session-v1"
    || Buffer.byteLength(JSON.stringify(declaration)) > 8192) fail("WORKER_ADAPTER_SESSION_BUDGET_INVALID");
  const raw = readFileSync(contained(ROOT, ["web", "worker-validation-manifest.json"], { limit: 150_000 }));
  const manifest = JSON.parse(raw.toString("utf8"));
  const hashes = declaration.input_sha256;
  if (!hashes || hashes.manifest !== sha256(raw)
    || ![hashes.capture, hashes.plan, hashes.resource_evidence].every(validDigest)
    || manifest.schema_version !== 4 || manifest.cpu_evidence_policy?.version !== "worker-cpu-policy-v2") {
    fail("WORKER_ADAPTER_SESSION_IDENTITY_INVALID");
  }
  const bootstrap = declaration.bootstrap, resource = declaration.news_evidence;
  const bounded = (value, minimum, maximum) => Number.isSafeInteger(value) && value >= minimum && value <= maximum;
  if (!bootstrap || !resource || bootstrap.index_count !== bootstrap.detail_count
    || !bounded(resource.records, 0, NEWS_PROJECTION_MAX_ITEMS)
    || !bounded(resource.pages, Math.ceil(resource.records / 8), Math.max(1, resource.records))) {
    fail("WORKER_ADAPTER_SESSION_COUNTS_INVALID");
  }
  for (const [kind, batchSize] of [["index", NEWS_INDEX_MAX_BATCH_ITEMS], ["detail", NEWS_DETAIL_MAX_BATCH_ITEMS]]) {
    const count = bootstrap[kind + "_count"], batches = bootstrap[kind + "_batches"];
    if (!bounded(count, 0, NEWS_PROJECTION_MAX_ITEMS)
      || !bounded(batches, Math.ceil(count / batchSize), count)) fail("WORKER_ADAPTER_SESSION_COUNTS_INVALID");
  }
  const policy = manifest.cpu_evidence_policy;
  const directed = manifest.routes.filter(route => route.cpu_required).reduce((total, route) => total
    + Math.max(1, route.scenarios?.length ?? 0) * (route.warmup_samples + route.acceptance_samples + policy.reserve_acceptance), 0);
  const batches = bootstrap.index_batches + bootstrap.detail_batches;
  // This is one declared 2700s rehearsal, including the real 900s Observe.
  // Counts come from bounded input plans; no production CPU/quota is changed.
  const phases = { setup: 2, cpu: directed + 16 + policy.headroom_top_up_acceptance + policy.outlier_confirmation_acceptance,
    static: manifest.static_assets.length + manifest.static_assets.filter(row => row.redirect_path).length,
    qualification_reads: 37 + 3 * 4 + 2 * 20 + 2, migration: 7,
    bootstrap: Math.max(1, Math.ceil(batches / 4)) + batches + 4,
    // Normal 300-second polling may perform eight bounded cleanup requests
    // before its unchanged fast return, including the initial poll.
    news_evidence: 2 * resource.pages + 10 + (1 + 2700 / 300) * 8,
    heartbeat: 1 + 2700 / 30,
    deferred_audit: 4, observe: 4 * (1 + 900 / 30), inspection: 2 };
  const maximum = Object.values(phases).reduce((sum, value) => sum + value, 0);
  if (!Number.isSafeInteger(maximum) || declaration.maximum_commands !== maximum
    || Object.keys(declaration.phases ?? {}).length !== Object.keys(phases).length
    || Object.entries(phases).some(([name, value]) => declaration.phases[name] !== value)) {
    fail("WORKER_ADAPTER_SESSION_FORMULA_MISMATCH");
  }
  return { maximum_commands: maximum, phases, input_sha256: hashes };
}

async function initialize(command) {
  if (initialized || database) fail("WORKER_ADAPTER_ALREADY_INITIALIZED");
  if (command.origin !== "https://connected-worker.invalid"
    || typeof command.ingest_token !== "string" || command.ingest_token.length < 12
    || command.ingest_token.length > 128 || command.migrations_sha256 !== migrationsDigest
    || command.capabilities_sql_sha256 !== capabilityDigest
    || !Array.isArray(command.workers) || command.workers.length < 1 || command.workers.length > 2
    || (command.defer_migration !== undefined && command.defer_migration !== MIGRATION)) {
    fail("WORKER_ADAPTER_CONFIG_INVALID");
  }
  const sessionBudget = sessionCommandBudget(command.session_budget);
  origin = command.origin;
  for (const item of command.workers) {
    if (!["stable", "candidate"].includes(item.role) || workers.has(item.role)
      || !validSha(item.git_sha) || !validVersion(item.worker_version_id) || !validDigest(item.entry_sha256)
      || [...workers.values()].some(target => target.worker_version_id === item.worker_version_id)) {
      fail("WORKER_ADAPTER_IDENTITY_INVALID");
    }
    const root = sourceRoot(item.source_root);
    const bundleVerified = verifyFileSet(root, item.bundle_files, item.bundle_files_sha256,
      fileSet(root, ["web/dist/server", "web/dist/client"]));
    const inputsVerified = verifyFileSet(root, item.source_inputs, item.source_inputs_sha256, sourceFiles(root));
    const entry = contained(root, ["web", "dist", "server", "index.js"], { limit: 5_000_000 });
    if (sha256(readFileSync(entry)) !== item.entry_sha256) fail("WORKER_ADAPTER_ENTRY_INTEGRITY_FAILED");
    const externals = JSON.parse(readFileSync(contained(root, ["web", "dist", "server", "vinext-externals.json"], { limit: 100_000 }), "utf8"));
    if (!Array.isArray(externals) || externals.length !== 0) fail("WORKER_ADAPTER_UNDECLARED_BUILD_EXTERNALS");
    workers.set(item.role, { ...item, root, entry, bundleVerified, inputsVerified });
  }
  denyNetwork();
  const appliedNames = migrationNames.filter(name => name !== command.defer_migration);
  database = new D1TestDatabase(appliedNames);
  // Wrangler's external ledger is a declared fixture boundary. Record a row
  // only after the actual SQL has completed, never by assuming schema readiness.
  database.database.exec("CREATE TABLE d1_migrations (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE, applied_at TEXT NOT NULL)");
  for (const name of appliedNames) recordMigration(name);
  migrationApplied = command.defer_migration !== MIGRATION;
  for (const target of workers.values()) {
    target.env = {
      DB: database, INGEST_TOKEN: command.ingest_token,
      CF_VERSION_METADATA: { id: target.worker_version_id },
      ASSETS: { fetch: request => Promise.resolve(assetFetch(target.root, request)) },
      IMAGES: {}, ASSISTANT_MEMORY_VECTOR: {},
    };
    globalThis.__AURUM_TEST_WORKER_ENV = target.env;
    target.worker = (await import(pathToFileURL(target.entry).href)).default;
    if (typeof target.worker?.fetch !== "function") fail("WORKER_ADAPTER_MODULE_INVALID");
    // The redirect executes no business read/write; its actual response headers
    // expose the build's embedded source metadata, not caller-supplied Git text.
    const probe = await invokeWorker(target, new Request(`${origin}/status`));
    if (probe.status !== 307 || new URL(probe.headers.get("location")).pathname !== "/admin/ai-usage") {
      fail("WORKER_ADAPTER_IDENTITY_PROBE_FAILED");
    }
    await readBoundedBody(probe);
  }
  initialized = true;
  maximumCommands = sessionBudget.maximum_commands;
  return { state: "READY", evidence: "ISOLATED_WORKER_SQLITE_NOT_CLOUDFLARE_CPU",
    session_budget: sessionBudget,
    migrations_sha256: migrationsDigest, capabilities_sql_sha256: capabilityDigest,
    migration_applied: migrationApplied,
    workers: [...workers.values()].map(({ role, git_sha, worker_version_id, entry_sha256, bundleVerified, inputsVerified }) =>
      ({ role, git_sha, worker_version_id, entry_sha256, bundle_files: bundleVerified, source_inputs: inputsVerified })) };
}

function exactTarget(command) {
  const target = workers.get(command.role);
  if (!initialized || !target || target.git_sha !== command.git_sha
    || target.worker_version_id !== command.worker_version_id) fail("WORKER_ADAPTER_COMMAND_IDENTITY_MISMATCH");
  return target;
}

async function request(command, target) {
  if (typeof command.url !== "string" || command.url.length > 8192
    || /[\\\x00-\x20]/.test(command.url) || !["GET", "HEAD", "POST", "DELETE"].includes(command.method)) {
    fail("WORKER_ADAPTER_REQUEST_INVALID");
  }
  const url = new URL(command.url);
  if (url.origin !== origin || url.username || url.password || url.hash) fail("WORKER_ADAPTER_ORIGIN_INVALID");
  if (command.surface !== undefined && !["worker", "assets"].includes(command.surface)) fail("WORKER_ADAPTER_SURFACE_INVALID");
  if (!command.headers || typeof command.headers !== "object" || Array.isArray(command.headers)
    || Buffer.byteLength(JSON.stringify(command.headers)) > MAX_HEADER_BYTES
    || Object.entries(command.headers).some(([name, value]) => typeof value !== "string" || /[\r\n]/.test(name + value))) {
    fail("WORKER_ADAPTER_HEADERS_INVALID");
  }
  const encoded = command.body_base64 ?? "";
  if (typeof encoded !== "string" || encoded.length > Math.ceil(MAX_BODY_BYTES / 3) * 4) fail("WORKER_ADAPTER_REQUEST_BOUND");
  const body = Buffer.from(encoded, "base64");
  if (body.length > MAX_BODY_BYTES || body.toString("base64") !== encoded
    || (["GET", "HEAD"].includes(command.method) && body.length)) fail("WORKER_ADAPTER_BODY_INVALID");
  const input = new Request(command.url, { method: command.method, headers: command.headers,
    ...(!["GET", "HEAD"].includes(command.method) ? { body } : {}) });
  const response = command.surface === "assets" ? assetFetch(target.root, input) : await invokeWorker(target, input);
  const bytes = await readBoundedBody(response);
  const headers = Object.fromEntries(response.headers);
  if (Buffer.byteLength(JSON.stringify(headers)) > MAX_HEADER_BYTES) fail("WORKER_ADAPTER_RESPONSE_HEADER_BOUND");
  return { status: response.status, headers, body_base64: bytes.toString("base64"), body_bytes: bytes.length,
    request_bytes: body.length, role: target.role, git_sha: target.git_sha,
    worker_version_id: target.worker_version_id, surface: command.surface ?? "worker" };
}

function inspectNews() {
  const state = database.database.prepare("SELECT * FROM news_projection_state WHERE id=1").get() ?? null;
  const evidence = database.database.prepare("SELECT * FROM news_evidence_state WHERE id=1").get() ?? null;
  const count = (sql, binding) => Number(database.database.prepare(sql).get(binding).count);
  return { current: state, evidence,
    current_index_count: state ? count("SELECT count(*) AS count FROM news_projection_index WHERE generation_id=?", state.active_generation_id) : 0,
    current_detail_count: state ? count("SELECT count(*) AS count FROM news_projection_details WHERE generation_id=?", state.active_generation_id) : 0,
    evidence_record_count: evidence ? count("SELECT count(*) AS count FROM news_evidence_records WHERE snapshot_id=?", evidence.active_snapshot_id) : 0,
    measurement: "IN_MEMORY_SQLITE_ROW_COUNTS_NOT_D1_PROVIDER_META" };
}

function recordMigration(name) {
  database.database.prepare("INSERT INTO d1_migrations(name,applied_at) VALUES (?,?) ON CONFLICT(name) DO NOTHING")
    .run(name, new Date().toISOString());
}

async function execute(command) {
  if (!command || typeof command !== "object" || Array.isArray(command)) fail("WORKER_ADAPTER_COMMAND_INVALID");
  if (command.command === "describe_inputs" && !initialized) return {
    migrations_sha256: migrationsDigest, migrations: migrationInputs,
    capabilities_sql_sha256: capabilityDigest, ledger_sql: ledgerSql,
    controller_source_sha256: sha256(controllerSource),
  };
  if (command.command === "initialize") return initialize(command);
  const target = exactTarget(command);
  if (command.command === "request") return request(command, target);
  if (command.command === "inspect_news") return inspectNews();
  if (command.command === "d1_query" && ["ledger", "capabilities"].includes(command.key)) {
    const sql = command.key === "ledger" ? ledgerSql : capabilitySql;
    return { results: database.database.prepare(sql).all(), sql_sha256: sha256(sql),
      measurement: "ACTUAL_DECLARED_SQL_ON_IN_MEMORY_SQLITE_NOT_PROVIDER_META" };
  }
  if (command.command === "migrate_learning_history" && command.filename === MIGRATION) {
    const alreadyApplied = migrationApplied;
    database.applyMigration(MIGRATION);
    recordMigration(MIGRATION);
    migrationApplied = true;
    return { migration: MIGRATION, already_applied: alreadyApplied,
      sqlite_objects: database.database.prepare("SELECT name,type FROM sqlite_master WHERE name IN ('learning_record_counts','learning_records_resource_identity_time_idx','learning_record_count_insert','learning_record_count_delete','learning_record_count_identity_update') ORDER BY name").all() };
  }
  fail("WORKER_ADAPTER_COMMAND_NOT_ALLOWED");
}

// Worker invocation logs stay separate from the JSONL response protocol.
globalThis.console = new Console({ stdout: process.stderr, stderr: process.stderr });
const diagnostic = (...items) => process.stderr.write(`${JSON.stringify(items).slice(0, 16_384)}\n`);
for (const name of ["log", "info", "debug", "dir", "dirxml", "table", "warn", "error"]) console[name] = diagnostic;
let count = 0;
let buffered = Buffer.alloc(0);
try {
  for await (const chunk of process.stdin) {
    buffered = Buffer.concat([buffered, chunk]);
    while (true) {
      const newline = buffered.indexOf(10);
      if (newline < 0) break;
      if (newline > MAX_LINE_BYTES || ++count > maximumCommands) fail("WORKER_ADAPTER_COMMAND_BOUND");
      const line = buffered.subarray(0, newline);
      buffered = buffered.subarray(newline + 1);
      try {
        const command = JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(line));
        const result = await bounded(() => execute(command));
        const output = JSON.stringify({ result });
        if (Buffer.byteLength(output) > 2_700_000) fail("WORKER_ADAPTER_OUTPUT_BOUND");
        process.stdout.write(`${output}\n`);
      } catch (error) {
        const reason = typeof error?.code === "string" ? error.code : "WORKER_ADAPTER_EXECUTION_FAILED";
        process.stdout.write(`${JSON.stringify({ error: reason })}\n`);
        // A timed-out Worker cannot overlap a later command. Failed initial
        // binding or a non-Error rejection also ends the disposable process.
        if (!(error instanceof Error) || error.fatal || !initialized) throw error;
      }
    }
    if (buffered.length > MAX_LINE_BYTES) fail("WORKER_ADAPTER_COMMAND_BOUND");
  }
  if (buffered.length) fail("WORKER_ADAPTER_UNTERMINATED_COMMAND");
} catch (error) {
  const reason = typeof error?.code === "string" ? error.code : "WORKER_ADAPTER_EXECUTION_FAILED";
  process.stderr.write(`${reason}\n`);
  process.exitCode = 1;
} finally {
  database?.database.close();
  delete globalThis.__AURUM_TEST_WORKER_ENV;
}
if (process.exitCode) process.exit(process.exitCode);
