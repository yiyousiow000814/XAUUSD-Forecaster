import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import test from "node:test";

import {
  architectureGithubHref,
  architectureRelations,
  parseArchitectureManifest,
  searchArchitectureNodes,
} from "../app/_lib/architecture-explorer.ts";
import { loadArchitectureManifest } from "../build/architecture-manifest.ts";

const root = new URL("../..", import.meta.url).pathname.replace(/^\/(.:)/, "$1");
const manifest = loadArchitectureManifest(root);
const source = (path) => readFileSync(new URL(path, import.meta.url), "utf8");

test("loads the versioned bounded manifest and first Overview view", () => {
  const parsed = parseArchitectureManifest(manifest);
  assert.ok(parsed);
  assert.equal(parsed.views[0].id, "system-overview");
  assert.equal(parsed.views.length, 11);
  assert.ok(parsed.nodes.length > 20);
});

test("malformed architecture data fails closed", () => {
  assert.equal(parseArchitectureManifest(null), null);
  assert.equal(parseArchitectureManifest({ schema: "wrong", nodes: [], edges: [], views: [] }), null);
  const broken = structuredClone(manifest);
  broken.edges[0].to = "missing";
  assert.equal(parseArchitectureManifest(broken), null);
});

test("search covers owner, file, test, tag and state", () => {
  const parsed = parseArchitectureManifest(manifest);
  assert.ok(searchArchitectureNodes(parsed, "change release", "ALL").some(node => node.id === "control-center"));
  assert.ok(searchArchitectureNodes(parsed, "test_control_plane_install", "ALL").some(node => node.id === "control-plane"));
  assert.ok(searchArchitectureNodes(parsed, "xauusd_control_center_release.ps1", "CURRENT").some(node => node.id === "control-center"));
  assert.ok(searchArchitectureNodes(parsed, "", "PAUSED").every(node => node.runtime_state === "PAUSED"));
});

test("dependency selection exposes upstream, downstream and unaffected text equivalents", () => {
  const parsed = parseArchitectureManifest(manifest);
  const relations = architectureRelations(parsed, "decision");
  assert.ok(relations.upstream.includes("business-runtime"));
  assert.ok(relations.downstream.includes("evidence"));
  assert.ok(relations.unaffected.includes("control-plane"));
});

test("source links require an exact immutable SHA", () => {
  const parsed = parseArchitectureManifest(manifest);
  const sha = "a".repeat(40);
  assert.equal(architectureGithubHref(parsed, "architecture/manifest.json", sha), `https://github.com/${parsed.repository}/blob/${sha}/architecture/manifest.json`);
  assert.equal(architectureGithubHref(parsed, "architecture/manifest.json", "main"), null);
  assert.equal(architectureGithubHref(parsed, "../secret", sha), null);
});

test("the Explorer is an Admin-only prerendered lazy route with no Architecture API", () => {
  const app = source("../app/_components/DashboardApp.tsx");
  const navigation = source("../app/_components/DashboardNavigation.tsx");
  const route = source("../app/admin/architecture/page.tsx");
  assert.match(app, /import\("\.\.\/_views\/ArchitectureExplorerView"\)/);
  assert.match(app, /\/admin\/architecture/);
  assert.match(route, /dynamic = "force-static"/);
  assert.match(navigation, /label: "系统架构"/);
  const globalBlock = navigation.slice(navigation.indexOf("DASHBOARD_GLOBAL_DESTINATIONS"), navigation.indexOf("DASHBOARD_ADMIN_DESTINATIONS"));
  assert.doesNotMatch(globalBlock, /admin\/architecture/);
  assert.equal(existsSync(new URL("../app/api/architecture", import.meta.url)), false);
  assert.equal(existsSync(new URL("../app/admin/api/architecture", import.meta.url)), false);
});

test("interaction and mobile contracts remain explicit in source and scoped CSS", () => {
  const view = source("../app/_views/ArchitectureExplorerView.tsx");
  const css = source("../app/globals.css");
  assert.match(view, /aria-selected/);
  assert.match(view, /architecture-breadcrumbs/);
  assert.match(view, /该故障不影响/);
  assert.match(view, /architecture-mobile-controls/);
  assert.match(css, /@media \(max-width: 720px\)[\s\S]*\.architecture-node-grid \{ display:grid; grid-template-columns:1fr/);
  assert.match(css, /\.architecture-search-row input[\s\S]*min-height:44px/);
});

test("the build boundary injects the manifest without runtime fetch", () => {
  const config = source("../vite.config.ts");
  const view = source("../app/_views/ArchitectureExplorerView.tsx");
  assert.match(config, /__AURUM_ARCHITECTURE_MANIFEST__/);
  assert.doesNotMatch(view, /fetch\s*\(/);
  assert.doesNotMatch(view, /\/api\/architecture/);
});
