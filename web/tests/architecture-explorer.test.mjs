import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import test from "node:test";

import {
  architectureGithubHref, architectureRelations, bestViewForNode, buildArchitectureGraph,
  parseArchitectureManifest, searchArchitectureNodes,
} from "../app/_lib/architecture-explorer.ts";
import { loadArchitectureManifest } from "../build/architecture-manifest.ts";

const root = new URL("../..", import.meta.url).pathname.replace(/^\/(.:)/, "$1");
const rawManifest = loadArchitectureManifest(root);
const manifest = parseArchitectureManifest(rawManifest);
assert.ok(manifest);
const source = (path) => readFileSync(new URL(path, import.meta.url), "utf8");
const viewSource = source("../app/_views/ArchitectureExplorerView.tsx");
const cssSource = source("../app/_views/ArchitectureExplorerView.module.css");

test("1. Initial System Overview contains a real graph canvas", () => {
  assert.equal(manifest.schema, "architecture-explorer-v2");
  assert.equal(manifest.views[0].id, "system-overview");
  assert.match(viewSource, /<ReactFlow<ArchitectureFlowNode, ArchitectureFlowEdge>/);
  assert.match(viewSource, /data-testid="architecture-graph"/);
  assert.equal(parseArchitectureManifest({ schema: "wrong", nodes: [], edges: [], views: [] }), null);
});

test("2. Every visible view edge becomes one directed graph edge", () => {
  for (const view of manifest.views) {
    const graph = buildArchitectureGraph(manifest, view.id);
    assert.deepEqual(new Set(graph.nodes.map(node => node.id)), new Set(view.node_ids));
    assert.deepEqual(new Set(graph.edges.map(edge => edge.id)), new Set(view.edge_ids));
    assert.ok(graph.edges.every(edge => edge.source === edge.from && edge.target === edge.to));
    assert.ok(graph.nodes.every(node => Number.isFinite(node.position.x) && Number.isFinite(node.position.y)));
  }
});

test("3. Directed edges render an arrow marker", () => {
  assert.match(viewSource, /MarkerType\.ArrowClosed/);
  assert.match(viewSource, /markerEnd=/);
});

test("4. The rejected architecture node grid is absent", () => {
  assert.doesNotMatch(viewSource, /architecture-node-grid/);
  assert.doesNotMatch(source("../app/globals.css"), /architecture-node-grid|Private Architecture Explorer/);
});

test("5. Initial load has no forced selected node", () => {
  assert.match(viewSource, /useState<string \| null>\(null\)/);
  assert.doesNotMatch(viewSource, /visible\[0\]|manifest\?\.nodes\[0\]/);
});

test("6. Selecting Decision exposes real upstream and downstream path edges", () => {
  const relations = architectureRelations(manifest, "decision", "system-overview");
  assert.ok(relations.upstream.includes("business-runtime"));
  assert.ok(relations.downstream.includes("evidence"));
  assert.ok(relations.connectedEdges.includes("runtime-to-decision"));
  assert.ok(relations.connectedEdges.includes("decision-to-evidence"));
});

test("7. Unrelated nodes are visually and semantically dimmed", () => {
  assert.match(viewSource, /dimmed: hasFocus && !highlightedNodes\.has/);
  assert.match(cssSource, /\.graphNode\.dimmed \{ opacity:/);
  assert.match(cssSource, /\.edgeDimmed \{ opacity:/);
});

test("8. Closing the inspector restores full graph width", () => {
  assert.match(viewSource, /aria-label="Close inspector"/);
  assert.match(viewSource, /setSelectedId\(null\)/);
  assert.match(cssSource, /\.withInspector \.canvas \{ width: calc\(100% - 380px\)/);
});

test("9. Search selects and centers a graph node without a card result grid", () => {
  const training = searchArchitectureNodes(manifest, "model generation", "ALL")[0];
  assert.equal(training.id, "training");
  assert.equal(bestViewForNode(manifest, training, "web-cloudflare"), "training-models");
  assert.match(viewSource, /flow\.setCenter/);
  assert.doesNotMatch(viewSource, /search result grid/i);
});

test("10. Follow one Decision is a continuous manifest-owned path", () => {
  const scenario = manifest.scenarios.find(item => item.id === "follow-decision");
  assert.deepEqual(scenario.node_ids, ["ctrader","business-runtime","decision","evidence","dashboard","dashboard-api","dashboard-sync","d1","web-worker"]);
  scenario.edge_ids.forEach((id, index) => {
    const edge = manifest.edges.find(item => item.id === id);
    assert.equal(edge.from, scenario.node_ids[index]); assert.equal(edge.to, scenario.node_ids[index + 1]);
  });
});

test("11. Training failure explicitly says Decision continues", () => {
  const impact = manifest.failure_impacts.find(item => item.node_id === "training");
  assert.ok(impact.affected.some(item => item.node_id === "published-model" && item.message.includes("AFFECTED")));
  assert.ok(impact.continues.some(item => item.node_id === "decision" && item.message.includes("CONTINUES")));
});

test("12. Cloudflare failure explicitly says local Decision continues", () => {
  const impact = manifest.failure_impacts.find(item => item.node_id === "cloudflare");
  assert.ok(impact.affected.some(item => item.node_id === "web-worker"));
  assert.ok(impact.continues.some(item => item.node_id === "decision"));
  assert.doesNotMatch(viewSource, /relations\.unaffected|non-neighbour/);
});

test("13. Node-owned drill-down changes graph membership", () => {
  const training = manifest.nodes.find(item => item.id === "training");
  assert.equal(training.subsystem_view, "training-models");
  assert.notDeepEqual(buildArchitectureGraph(manifest, "system-overview").view.node_ids, buildArchitectureGraph(manifest, training.subsystem_view).view.node_ids);
  assert.match(viewSource, /Open subsystem/);
});

test("14. Back breadcrumb restores the parent graph", () => {
  assert.match(viewSource, /viewHistory/);
  assert.match(viewSource, />Back</);
  assert.match(viewSource, /items\.slice\(0, -1\)/);
});

test("15. Pan, zoom, fit, and read-only controls are explicit", () => {
  assert.match(viewSource, /<Controls/);
  assert.match(viewSource, /panOnDrag zoomOnPinch zoomOnScroll/);
  assert.match(viewSource, /nodesConnectable=\{false\} nodesDraggable=\{false\}/);
  assert.match(viewSource, />Fit</);
});

test("16. MiniMap exists only on desktop", () => {
  assert.match(viewSource, /!mobile \? <MiniMap/);
  assert.match(viewSource, /matchMedia\("\(max-width: 720px\)"\)/);
});

test("17. Mobile transforms every view to a finite top-to-bottom graph", () => {
  for (const view of manifest.views) {
    const graph = buildArchitectureGraph(manifest, view.id, "TB");
    assert.equal(graph.direction, "TB");
    assert.ok(graph.nodes.every(node => Number.isFinite(node.position.x) && Number.isFinite(node.position.y)));
  }
  assert.match(cssSource, /@media \(max-width: 720px\)[\s\S]*\.inspector \{ inset: auto 0 0/);
});

test("18. A collapsible relationship text equivalent remains secondary", () => {
  assert.match(viewSource, /<details className=\{styles\.textFallback\}>/);
  assert.match(viewSource, /Relationship text fallback/);
});

test("19. Explorer makes no Architecture API or runtime fetch", () => {
  assert.doesNotMatch(viewSource, /fetch\s*\(/);
  assert.doesNotMatch(viewSource, /\/api\/architecture/);
  assert.equal(existsSync(new URL("../app/api/architecture", import.meta.url)), false);
  assert.equal(existsSync(new URL("../app/admin/api/architecture", import.meta.url)), false);
});

test("20. Architecture remains Admin-only and absent from global navigation", () => {
  const navigation = source("../app/_components/DashboardNavigation.tsx");
  const globalBlock = navigation.slice(navigation.indexOf("DASHBOARD_GLOBAL_DESTINATIONS"), navigation.indexOf("DASHBOARD_ADMIN_DESTINATIONS"));
  assert.doesNotMatch(globalBlock, /admin\/architecture/);
  assert.match(navigation, /label: "系统架构"/);
});

test("21. Page stays statically prerendered behind the lazy Admin route", () => {
  const app = source("../app/_components/DashboardApp.tsx");
  const route = source("../app/admin/architecture/page.tsx");
  assert.match(app, /import\("\.\.\/_views\/ArchitectureExplorerView"\)/);
  assert.match(route, /dynamic = "force-static"/);
});

test("22. Source links require one exact immutable build SHA", () => {
  const sha = "a".repeat(40);
  assert.equal(architectureGithubHref(manifest, "architecture/manifest.json", sha), `https://github.com/${manifest.repository}/blob/${sha}/architecture/manifest.json`);
  assert.equal(architectureGithubHref(manifest, "architecture/manifest.json", "main"), null);
  assert.equal(architectureGithubHref(manifest, "../secret", sha), null);
});
