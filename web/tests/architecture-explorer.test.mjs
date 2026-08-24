import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import test from "node:test";

import {
  architectureCanvasHeight, architectureFitOptions, architectureGithubHref, architectureRelations, bestViewForNode, buildArchitectureGraph,
  parseArchitectureManifest, searchArchitectureNodes,
} from "../app/_lib/architecture-explorer.ts";
import { createArchitectureCameraController } from "../app/_lib/architecture-camera.ts";
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
  assert.match(viewSource, /<ReactFlow<ArchitectureCanvasNode, ArchitectureFlowEdge>/);
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

test("8. Closing the inspector restores full graph width through the camera owner", () => {
  assert.match(viewSource, /aria-label="关闭详情"/);
  assert.match(viewSource, /closeInspector/);
  assert.match(viewSource, /REFIT_AFTER_INSPECTOR_CLOSE/);
  assert.match(viewSource, /onTransitionEnd=/);
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
  assert.match(viewSource, /打开子系统/);
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
  assert.match(viewSource, /适配画布 · Fit/);
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
    assert.equal(new Set(graph.nodes.map(node => node.position.x)).size, 1);
  }
  assert.match(cssSource, /@media \(max-width: 720px\)[\s\S]*\.inspector \{ position: fixed; inset: auto 0 0/);
});

test("18. A collapsible relationship text equivalent remains secondary", () => {
  assert.match(viewSource, /<details className=\{styles\.textFallback\}>/);
  assert.match(viewSource, /关系文字版 · Relationship text fallback/);
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

test("23. Purpose is explicit and ownership remains a separate answer", () => {
  assert.ok(manifest.nodes.every(node => typeof node.purpose === "string" && node.purpose.trim()));
  assert.match(viewSource, /<dt>它是什么？<\/dt><dd>\{node\.summary\}<\/dd>/);
  assert.match(viewSource, /<dt>为什么需要它？<\/dt><dd>\{node\.purpose\}<\/dd>/);
  assert.match(viewSource, /<dt>谁负责它？<\/dt>/);
  assert.match(viewSource, /\{node\.owner\}/);
  assert.match(viewSource, /\{node\.architecture\.ownership\}/);
});

test("24. Canonical Package Dependencies is an import graph only", () => {
  const view = manifest.views.find(item => item.id === "package-dependencies");
  const expectedNodes = new Set(["foundational", "ai", "evidence", "news", "training", "decision", "runtime", "assistant", "dashboard"].map(id => `package-${id}`));
  assert.deepEqual(new Set(view.node_ids), expectedNodes);
  assert.ok(view.edge_ids.length > 0);
  assert.ok(view.edge_ids.every(id => manifest.edges.find(edge => edge.id === id)?.kind === "DEPENDENCY"));
  assert.equal(view.relationship_note, "A → B means A may import or depend on B.");
  assert.ok(view.prohibited_directions.length >= 4);
  assert.ok(!view.node_ids.some(id => ["published-model", "training-materialization", "candidate-validation"].includes(id)));
  assert.match(viewSource, /禁止的反向依赖 · Prohibited reverse directions/);
});

test("25. Every view exposes non-interactive labelled lane regions", () => {
  for (const view of manifest.views) {
    const graph = buildArchitectureGraph(manifest, view.id);
    assert.equal(graph.laneBoxes.length, view.lanes.length);
    assert.ok(graph.laneBoxes.every(lane => Number.isFinite(lane.position.x) && Number.isFinite(lane.position.y) && lane.width > 0 && lane.height > 0));
  }
  assert.match(viewSource, /type: "lane"/);
  assert.match(viewSource, /selectable: false, focusable: false/);
  assert.match(cssSource, /react-flow__node-lane[^}]+pointer-events: none/s);
  assert.match(viewSource, /if \(!architectureNode\) return "#d7e2e0"/);
});

test("26. Fit zoom is bounded by view size and selection does not refit", () => {
  assert.ok(architectureFitOptions(4, false).maxZoom > architectureFitOptions(11, false).maxZoom);
  assert.ok(architectureFitOptions(4, true).maxZoom <= architectureFitOptions(4, false).maxZoom);
  assert.equal(architectureCanvasHeight(4, true), 650);
  assert.equal(architectureCanvasHeight(11, true), 1485);
  assert.equal(architectureCanvasHeight(99, true), 1600);
  assert.equal(architectureCanvasHeight(11, false), 650);
  assert.match(viewSource, /const zoom = current\.flow\.getZoom\(\)/);
  assert.match(viewSource, /current\.flow\.setViewport\(\{/);
  assert.match(viewSource, /y: 64 - item\.position\.y \* zoom/);
  assert.match(viewSource, /defaultNodes=\{flowElements\}/);
  assert.match(viewSource, /flow\.setNodes\(flowElements\)/);
  assert.doesNotMatch(viewSource, /nodes=\{\[\.\.\.laneNodes/);
});

test("27. Edge labels follow critical, release, interaction, guide, and sparse-view rules", () => {
  assert.match(viewSource, /item\.criticality === "CRITICAL"/);
  assert.match(viewSource, /item\.criticality === "CONTROL_PLANE" && viewId === "runtime-release"/);
  assert.match(viewSource, /graph\.edges\.length <= 4/);
  assert.match(viewSource, /hoveredEdgeId === item\.id/);
  assert.match(viewSource, /data\.showLabel \?/);
  assert.match(viewSource, /graph\.edges\.map\(edge =>/);
});

test("28. Beginner and failure copy is Chinese-primary", () => {
  for (const label of ["它是什么？", "为什么需要它？", "谁负责它？", "输入来自哪里？", "输出到哪里？", "它坏了会停止什么？", "什么仍会继续？", "打开子系统"]) {
    assert.ok(viewSource.includes(label), label);
  }
});

test("29. Required failure impacts are explicit and missing contracts stay disabled", () => {
  const required = ["training", "cloudflare", "decision", "evidence", "news", "dashboard-sync", "d1", "control-plane"];
  assert.ok(required.every(id => manifest.failure_impacts.some(item => item.node_id === id)));
  assert.ok(manifest.failure_impacts.every(impact => impact.affected.every(item => item.message.includes("AFFECTED"))));
  assert.ok(manifest.failure_impacts.every(impact => impact.continues.every(item => item.message.includes("CONTINUES"))));
  assert.match(viewSource, /disabled=\{!impactForSelected\}/);
  assert.match(viewSource, /没有显式 failure impact contract/);
});

function cameraHarness(initial = {}) {
  let nextFrame = 1;
  const frames = new Map();
  const commands = [];
  const layout = {
    viewId: "system-overview", nodesInitialized: true, canvasTransitionComplete: true,
    width: 1200, height: 650, ...initial,
  };
  const controller = createArchitectureCameraController({
    requestFrame(callback) { const id = nextFrame++; frames.set(id, callback); return id; },
    cancelFrame(id) { frames.delete(id); },
    readLayout() { return { ...layout }; },
    execute(intent) { commands.push(intent); },
  });
  const flush = () => {
    let guard = 0;
    while (frames.size && guard++ < 20) {
      const current = [...frames.entries()]; frames.clear();
      current.forEach(([, callback]) => callback(0));
    }
  };
  return { commands, controller, flush, frames, layout };
}

test("30. Camera owner performs one initial automatic Fit", () => {
  const h = cameraHarness();
  h.controller.request({ type: "FIT_VIEW", viewId: "system-overview" }); h.flush();
  assert.deepEqual(h.commands.map(item => item.type), ["FIT_VIEW"]);
});

test("31. One view switch performs one automatic Fit", () => {
  const h = cameraHarness({ viewId: "training-models" });
  h.controller.request({ type: "FIT_VIEW", viewId: "training-models" }); h.flush();
  assert.deepEqual(h.commands.map(item => item.type), ["FIT_VIEW"]);
});

test("32. Rapid A to B to C navigation executes only C", () => {
  const h = cameraHarness({ viewId: "view-c" });
  h.controller.request({ type: "FIT_VIEW", viewId: "view-a" });
  h.controller.request({ type: "FIT_VIEW", viewId: "view-b" });
  h.controller.request({ type: "FIT_VIEW", viewId: "view-c" }); h.flush();
  assert.deepEqual(h.commands, [{ type: "FIT_VIEW", viewId: "view-c" }]);
});

test("33. Cross-view search performs one final Focus without Fit", () => {
  const h = cameraHarness({ viewId: "training-models" });
  h.controller.request({ type: "FOCUS_NODE", viewId: "training-models", nodeId: "training", source: "SEARCH" }); h.flush();
  assert.deepEqual(h.commands.map(item => item.type), ["FOCUS_NODE"]);
});

test("34. A cross-view scenario starts with one Focus and no duplicate Fit", () => {
  const h = cameraHarness({ viewId: "runtime-release" });
  h.controller.request({ type: "FOCUS_NODE", viewId: "runtime-release", nodeId: "github", source: "SCENARIO_STEP" }); h.flush();
  assert.deepEqual(h.commands.map(item => item.type), ["FOCUS_NODE"]);
});

test("35. Each scenario step performs exactly one camera command", () => {
  const h = cameraHarness({ viewId: "system-overview" });
  for (const nodeId of ["ctrader", "business-runtime", "decision"]) {
    h.controller.request({ type: "FOCUS_NODE", viewId: "system-overview", nodeId, source: "SCENARIO_STEP" }); h.flush();
  }
  assert.deepEqual(h.commands.map(item => item.nodeId), ["ctrader", "business-runtime", "decision"]);
});

test("36. Inspector close waits for width transition then Fits once", () => {
  const h = cameraHarness({ canvasTransitionComplete: false });
  h.controller.request({ type: "REFIT_AFTER_INSPECTOR_CLOSE", viewId: "system-overview" }); h.flush();
  assert.equal(h.commands.length, 0);
  h.layout.width = 1580; h.layout.canvasTransitionComplete = true; h.controller.layoutChanged(); h.flush();
  assert.deepEqual(h.commands.map(item => item.type), ["REFIT_AFTER_INSPECTOR_CLOSE"]);
});

test("37. Manual Fit performs exactly one Fit", () => {
  const h = cameraHarness();
  h.controller.request({ type: "MANUAL_FIT", viewId: "system-overview" }); h.flush();
  assert.deepEqual(h.commands.map(item => item.type), ["MANUAL_FIT"]);
});

test("38. Cancelled stale frames cannot move the current view", () => {
  const h = cameraHarness({ viewId: "view-b" });
  h.controller.request({ type: "FIT_VIEW", viewId: "view-a" });
  const staleFrames = [...h.frames.values()];
  h.controller.request({ type: "FIT_VIEW", viewId: "view-b" });
  staleFrames.forEach(callback => callback(0)); h.flush();
  assert.deepEqual(h.commands, [{ type: "FIT_VIEW", viewId: "view-b" }]);
});

test("39. Mobile initialization exposes only one TB layout Fit", () => {
  const h = cameraHarness({ viewId: "system-overview" });
  h.controller.request({ type: "FIT_VIEW", viewId: "system-overview" }); h.flush();
  assert.equal(buildArchitectureGraph(manifest, "system-overview", "TB").direction, "TB");
  assert.deepEqual(h.commands.map(item => item.type), ["FIT_VIEW"]);
  assert.match(viewSource, /mobile === null[\s\S]*Preparing architecture layout/);
  assert.doesNotMatch(viewSource, /elementsSelectable fitView/);
  assert.doesNotMatch(viewSource, /window\.setTimeout/);
});
