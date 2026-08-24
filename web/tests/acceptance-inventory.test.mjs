import assert from "node:assert/strict";
import { existsSync, readFileSync, readdirSync } from "node:fs";
import test from "node:test";

const webRoot = new URL("../", import.meta.url);
const appRoot = new URL("../app/", import.meta.url);
const inventory = JSON.parse(readFileSync(
  new URL("../acceptance-inventory.json", import.meta.url), "utf8",
));
const workerManifest = JSON.parse(readFileSync(
  new URL(`../${inventory.api_contract_source}`, import.meta.url), "utf8",
));

const HTTP_METHOD = /^(GET|POST|PUT|PATCH|DELETE)$/;

function walk(directory, filename, prefix = "") {
  const found = [];
  for (const entry of readdirSync(directory, { withFileTypes: true })) {
    if (entry.isDirectory()) {
      found.push(...walk(new URL(`${entry.name}/`, directory), filename, `${prefix}/${entry.name}`));
    } else if (entry.name === filename) {
      found.push({ path: prefix || "/", file: new URL(entry.name, directory) });
    }
  }
  return found;
}

function methodsFromRoute(source) {
  const methods = new Set();
  for (const match of source.matchAll(
    /export\s+(?:async\s+)?function\s+(GET|POST|PUT|PATCH|DELETE)\b/g,
  )) methods.add(match[1]);
  for (const match of source.matchAll(
    /export\s+const\s+(GET|POST|PUT|PATCH|DELETE)\s*=/g,
  )) methods.add(match[1]);
  for (const match of source.matchAll(/export\s*\{([^}]+)\}\s*from/g)) {
    for (const entry of match[1].split(",").map(value => value.trim())) {
      const exported = entry.split(/\s+as\s+/).at(-1);
      if (HTTP_METHOD.test(exported)) methods.add(exported);
    }
  }
  return methods;
}

function uniqueKeys(rows, key, label) {
  const keys = rows.map(key);
  assert.equal(new Set(keys).size, keys.length, `${label} contains duplicate entries`);
  return new Set(keys);
}

test("acceptance inventory owns every page bidirectionally", () => {
  const discovered = uniqueKeys(walk(appRoot, "page.tsx"), row => row.path, "discovered pages");
  const declared = uniqueKeys(inventory.pages, row => row.route, "declared pages");
  assert.deepEqual([...declared].sort(), [...discovered].sort());

  const allowedClasses = new Set([
    "PUBLIC", "AUTHENTICATED", "PAUSED_AUTHENTICATED", "AUTH_CALLBACK", "COMPAT_REDIRECT",
  ]);
  for (const page of inventory.pages) {
    assert.ok(allowedClasses.has(page.class), `invalid page class: ${page.route}`);
    assert.ok(existsSync(new URL(page.source, webRoot)), `missing page source: ${page.source}`);
    assert.ok(Number.isInteger(page.version_host?.status), `missing version status: ${page.route}`);
    if (page.class === "COMPAT_REDIRECT") {
      assert.ok(page.version_host.redirect_path, `missing redirect target: ${page.route}`);
    } else {
      assert.ok(page.version_host.content_type, `missing content type: ${page.route}`);
      assert.ok(page.version_host.marker, `missing semantic marker: ${page.route}`);
    }
    if (["AUTHENTICATED", "PAUSED_AUTHENTICATED", "AUTH_CALLBACK"].includes(page.class)) {
      assert.equal(page.production_anonymous?.behavior, "CLOUDFLARE_ACCESS_REDIRECT");
      assert.ok(page.production_authenticated?.marker, `missing authenticated marker: ${page.route}`);
    }
  }
});

test("route source, Worker manifest, and API classifications agree bidirectionally", () => {
  const discoveredRows = walk(appRoot, "route.ts").flatMap(row => {
    const source = readFileSync(row.file, "utf8");
    return [...methodsFromRoute(source)].map(method => `${method} ${row.path}`);
  });
  const directWorkerRows = workerManifest.routes
    .filter(route => route.boundary === "DIRECT_WORKER_ROUTE")
    .map(route => `${route.method} ${route.path}`);
  const effectiveDiscovered = new Set([...discoveredRows, ...directWorkerRows]);
  const manifestRows = uniqueKeys(
    workerManifest.routes, route => `${route.method} ${route.path}`, "Worker routes",
  );
  assert.deepEqual([...manifestRows].sort(), [...effectiveDiscovered].sort());

  const classifiedRows = [];
  for (const [apiClass, rows] of Object.entries(inventory.api_classes)) {
    assert.ok([
      "PUBLIC_READ", "AUTHENTICATED_READ_WRITE", "PRODUCTION_SHAPED_DRY_RUN_WRITE",
      "PAUSED_FEATURE", "INTERNAL_ONLY",
    ].includes(apiClass), `unknown API class: ${apiClass}`);
    for (const row of rows) classifiedRows.push(row);
  }
  const classified = uniqueKeys(classifiedRows, row => row, "classified APIs");
  assert.deepEqual([...classified].sort(), [...manifestRows].sort());

  for (const route of workerManifest.routes) {
    const key = `${route.method} ${route.path}`;
    if (inventory.api_classes.PRODUCTION_SHAPED_DRY_RUN_WRITE.includes(key)) {
      assert.equal(route.strategy, "PRODUCTION_SHAPED_DRY_RUN");
      assert.equal(route.auth_required, true);
    }
    if (inventory.api_classes.PUBLIC_READ.includes(key)) {
      assert.equal(route.method, "GET");
      assert.equal(route.auth_required, false);
    }
  }
});

test("every materialized resource has an explicit authority and consistency contract", () => {
  const resources = uniqueKeys(
    inventory.materialized_resources, row => row.id, "materialized resources",
  );
  const allowedConsistency = new Set([
    "LIVE_EPHEMERAL", "DERIVED_INVARIANT", "BOUNDED_LAG_PARITY", "EXACT_SNAPSHOT_PARITY",
  ]);
  for (const resource of inventory.materialized_resources) {
    assert.ok(resource.authority && resource.producer && resource.projection);
    assert.ok(Array.isArray(resource.api) && resource.api.length > 0);
    assert.ok(Array.isArray(resource.consumer) && resource.consumer.length > 0);
    assert.ok(allowedConsistency.has(resource.consistency), `invalid consistency: ${resource.id}`);
    assert.ok(resource.freshness && resource.recovery && resource.release_obligation);
  }
  assert.ok(resources.has("news-index") && resources.has("news-content"));
});

test("critical user-visible metrics have complete authority mappings", () => {
  uniqueKeys(inventory.metrics, row => row.id, "metrics");
  const requiredFamilies = ["news.", "audit.", "learning.", "market.", "operations.", "health."];
  for (const prefix of requiredFamilies) {
    assert.ok(inventory.metrics.some(metric => metric.id.startsWith(prefix)), `missing metric family: ${prefix}`);
  }
  for (const metric of inventory.metrics) {
    for (const field of [
      "authority", "projection", "api_field", "ui_field", "invariant", "freshness", "display_state",
    ]) assert.ok(metric[field], `${metric.id} missing ${field}`);
  }
});

test("pagination siblings are classified and point to inventoried APIs", () => {
  uniqueKeys(inventory.pagination_families, row => row.id, "pagination families");
  const publicApis = new Set(inventory.api_classes.PUBLIC_READ.map(row => row.split(" ")[1]));
  for (const family of inventory.pagination_families) {
    assert.ok(publicApis.has(family.api), `${family.id} has no public API contract`);
    assert.ok(["EXACT_SNAPSHOT", "BOUNDED_LAG_WATERMARK"].includes(family.semantics));
    assert.ok(family.cursor && family.generation_transition);
    assert.equal(family.requires_complete_walk, true);
  }
});

test("the three Cloudflare acceptance channels remain distinct", () => {
  const channels = inventory.access_channels.map(channel => channel.id);
  assert.deepEqual(channels, [
    "VERSION_HOST_RESULT",
    "PRODUCTION_ANONYMOUS_ACCESS_RESULT",
    "PRODUCTION_AUTHENTICATED_ACCESS_RESULT",
  ]);
  assert.equal(inventory.access_channels[2].authentication, "HUMAN_ACCESS_SESSION");
});
