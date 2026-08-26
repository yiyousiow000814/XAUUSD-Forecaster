import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import test from "node:test";

import {
  ARCHITECTURE_EDGE_OVERLAP_TOLERANCE, ARCHITECTURE_LANE_GAP, ARCHITECTURE_MOBILE_NODE_WIDTH_FLOOR, ARCHITECTURE_SEMANTIC_LAYOUT_PASSES,
  architectureCanvasHeight, architectureDisclosedEdgeIds, architectureDisclosedGraph, architectureEdgeRoute, architectureFitOptions, architectureGithubHref, architectureGraphBounds, architectureMobileViewport,
  architecturePortVisibility, architectureRelations, architectureRouteCrossesUnrelatedNode, architectureRoutePath, architectureSharedCollinearLength,
  bestViewForNode, buildArchitectureGraph,
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
  assert.match(viewSource, /panOnDrag preventScrolling={!mobile} zoomOnPinch/);
  assert.match(viewSource, /nodesConnectable=\{false\} nodesDraggable=\{false\}/);
  assert.match(viewSource, /适配画布 · Fit/);
});

test("16. MiniMap exists only on desktop", () => {
  assert.match(viewSource, /!mobile \? <MiniMap/);
  assert.match(viewSource, /matchMedia\("\(max-width: 720px\), \(max-height: 500px\) and \(max-width: 900px\)"\)/);
});

test("17. Mobile preserves finite top-to-bottom branch geometry instead of forcing one column", () => {
  for (const view of manifest.views) {
    const graph = buildArchitectureGraph(manifest, view.id, "TB");
    assert.equal(graph.direction, "TB");
    assert.ok(graph.nodes.every(node => Number.isFinite(node.position.x) && Number.isFinite(node.position.y)));
  }
  const cloudflare = buildArchitectureGraph(manifest, "web-cloudflare", "TB");
  const x = (id) => cloudflare.nodes.find(node => node.id === id).position.x;
  assert.notEqual(x("dashboard-sync"), x("stable-release"));
  assert.notEqual(x("d1"), x("cloudflare"));
  assert.ok(cloudflare.edges.some(edge => edge.source === "d1" && edge.target === "web-worker"));
  assert.ok(cloudflare.edges.some(edge => edge.source === "cloudflare" && edge.target === "web-worker"));
  assert.doesNotMatch(source("../app/_lib/architecture-explorer.ts"), /compactMobilePositions|x:\s*24,\s*y:\s*cursorY/);
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
  for (const view of manifest.views) for (const direction of ["LR", "TB"]) {
    const graph = buildArchitectureGraph(manifest, view.id, direction);
    assert.equal(graph.laneBoxes.length, view.lanes.length, `${view.id} ${direction}`);
    assert.ok(graph.laneBoxes.every(lane => Number.isFinite(lane.position.x) && Number.isFinite(lane.position.y) && lane.width > 0 && lane.height > 0));
    assert.deepEqual(new Set(graph.nodes.map(node => node.data.laneId)), new Set(view.lanes.map(lane => lane.id)));
    for (const node of graph.nodes) {
      const lane = graph.laneBoxes.find(item => item.id === `lane-${node.data.laneId}`);
      assert.ok(lane, `${view.id} ${direction} ${node.id} lane`);
      assert.ok(node.position.x > lane.position.x && node.position.y > lane.position.y, `${view.id} ${direction} ${node.id} leading border`);
      assert.ok(node.position.x + node.width < lane.position.x + lane.width, `${view.id} ${direction} ${node.id} right border`);
      assert.ok(node.position.y + node.height < lane.position.y + lane.height, `${view.id} ${direction} ${node.id} bottom border`);
    }
  }
  assert.match(viewSource, /type: "lane"/);
  assert.match(viewSource, /selectable: false, focusable: false/);
  assert.match(cssSource, /react-flow__node-lane[^}]+pointer-events: none/s);
  assert.match(viewSource, /if \(!architectureNode\) return "#d7e2e0"/);
});

test("26. Lane regions keep the required pairwise gap in every LR and TB view", () => {
  for (const view of manifest.views) for (const direction of ["LR", "TB"]) {
    const graph = buildArchitectureGraph(manifest, view.id, direction); const gap = ARCHITECTURE_LANE_GAP[direction];
    for (let left = 0; left < graph.laneBoxes.length; left += 1) for (let right = left + 1; right < graph.laneBoxes.length; right += 1) {
      const a = graph.laneBoxes[left]; const b = graph.laneBoxes[right];
      const xSeparated = a.position.x >= b.position.x + b.width + gap || b.position.x >= a.position.x + a.width + gap;
      const ySeparated = a.position.y >= b.position.y + b.height + gap || b.position.y >= a.position.y + a.height + gap;
      assert.ok(xSeparated || ySeparated, `${view.id} ${direction}: ${a.id} overlaps ${b.id}`);
    }
  }
  assert.equal(ARCHITECTURE_LANE_GAP.LR, 24);
  assert.equal(ARCHITECTURE_LANE_GAP.TB, 20);
});

test("27. Visible edges own port visibility and terminal nodes have no phantom handles", () => {
  for (const view of manifest.views) {
    const graph = buildArchitectureGraph(manifest, view.id);
    for (const node of graph.nodes) assert.deepEqual(
      { hasIncomingEdge: node.data.hasIncomingEdge, hasOutgoingEdge: node.data.hasOutgoingEdge },
      architecturePortVisibility(graph.edges, node.id), `${view.id} ${node.id}`,
    );
  }
  const web = buildArchitectureGraph(manifest, "web-cloudflare", "LR");
  assert.deepEqual(architecturePortVisibility(web.edges, "architecture-explorer"), { hasIncomingEdge: true, hasOutgoingEdge: false });
  assert.deepEqual(architecturePortVisibility(web.edges, "stable-release"), { hasIncomingEdge: false, hasOutgoingEdge: true });
  assert.match(viewSource, /data\.incomingPorts\.map\(port => <Handle/);
  assert.match(viewSource, /data\.outgoingPorts\.map\(port => <Handle/);
  assert.match(cssSource, /\.handleLR \{ width: 6px !important; min-width: 6px !important; height: 22px !important;/);
  assert.match(cssSource, /\.handleTB \{ width: 22px !important; height: 6px !important; min-height: 6px !important;/);
  assert.match(cssSource, /\.handle \{[^}]*display: block !important;[^}]*border-radius: 999px !important;[^}]*padding: 0 !important;/);
});

test("28. Every routed edge clears unrelated nodes and keeps a unique complete path", () => {
  for (const view of manifest.views) for (const direction of ["LR", "TB"]) {
    const graph = buildArchitectureGraph(manifest, view.id, direction);
    const routes = graph.edges.map(edge => ({ edge, points: architectureEdgeRoute(graph.nodes, edge, direction) }));
    for (const { edge, points } of routes) {
      assert.ok(points.length >= 4, `${view.id} ${direction} ${edge.id} route`);
      assert.equal(architectureRouteCrossesUnrelatedNode(points, graph.nodes, edge.source, edge.target), false,
        `${view.id} ${direction} ${edge.id} crosses a node`);
      const sourceNode = graph.nodes.find(node => node.id === edge.source); const targetNode = graph.nodes.find(node => node.id === edge.target);
      const first = points[0]; const last = points.at(-1);
      if (direction === "LR") {
        assert.equal(first.x, sourceNode.position.x + sourceNode.width); assert.equal(last.x, targetNode.position.x);
      } else {
        assert.equal(first.y, sourceNode.position.y + sourceNode.height); assert.equal(last.y, targetNode.position.y);
      }
    }
    assert.equal(new Set(routes.map(item => architectureRoutePath(item.points))).size, routes.length, `${view.id} ${direction} duplicate routes`);
  }
});

test("29. Every edge owns exact source and target port anchors", () => {
  for (const view of manifest.views) for (const direction of ["LR", "TB"]) {
    const graph = buildArchitectureGraph(manifest, view.id, direction);
    for (const edge of graph.edges) {
      const source = graph.nodes.find(node => node.id === edge.source);
      const target = graph.nodes.find(node => node.id === edge.target);
      const sourcePort = source.data.outgoingPorts.find(port => port.edgeId === edge.id);
      const targetPort = target.data.incomingPorts.find(port => port.edgeId === edge.id);
      assert.deepEqual(sourcePort.anchor, edge.sourceAnchor, `${view.id} ${direction} ${edge.id} source port`);
      assert.deepEqual(targetPort.anchor, edge.targetAnchor, `${view.id} ${direction} ${edge.id} target port`);
      const route = architectureEdgeRoute(graph.nodes, edge, direction);
      assert.deepEqual(route[0], edge.sourceAnchor, `${view.id} ${direction} ${edge.id} route source`);
      assert.deepEqual(route.at(-1), edge.targetAnchor, `${view.id} ${direction} ${edge.id} route target`);
    }
  }
  assert.match(viewSource, /id=\{`\$\{port\.edgeId\}-target`\}/);
  assert.match(viewSource, /id=\{`\$\{port\.edgeId\}-source`\}/);
  assert.match(viewSource, /sourceHandle: `\$\{item\.id\}-source`, targetHandle: `\$\{item\.id\}-target`/);
});

test("30. Fan-in routes do not create a hidden collinear junction", () => {
  for (const view of manifest.views) for (const direction of ["LR", "TB"]) {
    const graph = buildArchitectureGraph(manifest, view.id, direction);
    const routes = graph.edges.map(edge => ({ edge, points: architectureEdgeRoute(graph.nodes, edge, direction) }));
    for (let left = 0; left < routes.length; left += 1) for (let right = left + 1; right < routes.length; right += 1) {
      assert.ok(architectureSharedCollinearLength(routes[left].points, routes[right].points) <= ARCHITECTURE_EDGE_OVERLAP_TOLERANCE,
        `${view.id} ${direction}: ${routes[left].edge.id} shares a trunk with ${routes[right].edge.id}`);
    }
  }
  for (const direction of ["LR", "TB"]) {
    const graph = buildArchitectureGraph(manifest, "web-cloudflare", direction);
    const d1 = graph.edges.find(edge => edge.id === "d1-to-web");
    const cloudflare = graph.edges.find(edge => edge.id === "cloudflare-to-web");
    const d1Route = architectureEdgeRoute(graph.nodes, d1, direction);
    const cloudflareRoute = architectureEdgeRoute(graph.nodes, cloudflare, direction);
    assert.notDeepEqual(d1.targetAnchor, cloudflare.targetAnchor, `${direction} distinct Worker target slots`);
    assert.equal(architectureSharedCollinearLength(d1Route, cloudflareRoute), 0, `${direction} hidden fan-in trunk`);
    assert.deepEqual(d1Route.at(-1), d1.targetAnchor);
    assert.deepEqual(cloudflareRoute.at(-1), cloudflare.targetAnchor);
    assert.ok(graph.edges.some(edge => edge.source === "web-worker" && edge.target === "architecture-explorer"));
  }
  assert.match(viewSource, /markerEnd: \{ type: MarkerType\.ArrowClosed/);
  assert.doesNotMatch(viewSource, /<h[1-6][^>]*>\{data\.edge\.label\}/);
});

test("31. Semantic ranks, tracks, and convergence remain exact in LR and TB", () => {
  const view = manifest.views.find(item => item.id === "web-cloudflare");
  assert.equal(view.layout_hints.mode, "SEMANTIC_GRID");
  assert.equal(view.layout_hints.auto_place_unlisted, true);
  assert.equal(ARCHITECTURE_SEMANTIC_LAYOUT_PASSES, 8);
  for (const direction of ["LR", "TB"]) {
    const graph = buildArchitectureGraph(manifest, view.id, direction);
    const repeated = buildArchitectureGraph(manifest, view.id, direction);
    const center = id => {
      const node = graph.nodes.find(item => item.id === id);
      return { primary: direction === "LR" ? node.position.x + node.width / 2 : node.position.y + node.height / 2,
        cross: direction === "LR" ? node.position.y + node.height / 2 : node.position.x + node.width / 2 };
    };
    assert.deepEqual(graph.nodes.map(node => node.position), repeated.nodes.map(node => node.position), `${direction} deterministic nodes`);
    assert.deepEqual(graph.laneBoxes, repeated.laneBoxes, `${direction} deterministic lanes`);
    assert.equal(center("dashboard-sync").primary, center("stable-release").primary, `${direction} source rank`);
    assert.equal(center("d1").primary, center("cloudflare").primary, `${direction} intermediate rank`);
    assert.equal(center("dashboard-sync").cross, center("d1").cross, `${direction} projection track`);
    assert.equal(center("stable-release").cross, center("cloudflare").cross, `${direction} release track`);
    assert.equal(center("web-worker").cross, center("architecture-explorer").cross, `${direction} presentation track`);
    assert.equal(center("web-worker").cross, (center("d1").cross + center("cloudflare").cross) / 2, `${direction} convergence midpoint`);
    const bounds = architectureGraphBounds(graph.nodes, graph.laneBoxes);
    assert.ok([bounds.x, bounds.y, bounds.width, bounds.height].every(Number.isFinite), `${direction} finite bounds`);
  }
});

test("32. Semantic views automatically place an unlisted connected node", () => {
  const expanded = structuredClone(manifest);
  const view = expanded.views.find(item => item.id === "web-cloudflare");
  const templateNode = expanded.nodes.find(item => item.id === "architecture-explorer");
  expanded.nodes.push({ ...templateNode, id: "automatic-extension", label: "Automatic Extension", short_label: "Extension" });
  const templateEdge = expanded.edges.find(item => item.id === "web-to-explorer");
  expanded.edges.push({ ...templateEdge, id: "explorer-to-extension", from: "architecture-explorer", to: "automatic-extension" });
  view.node_ids.push("automatic-extension");
  view.edge_ids.push("explorer-to-extension");
  view.lanes.push({ id: "extension", label: "Automatic Extension", node_ids: ["automatic-extension"] });

  for (const direction of ["LR", "TB"]) {
    const graph = buildArchitectureGraph(expanded, view.id, direction);
    const automatic = graph.nodes.find(node => node.id === "automatic-extension");
    const lane = graph.laneBoxes.find(item => item.id === "lane-extension");
    assert.ok(automatic && Number.isFinite(automatic.position.x) && Number.isFinite(automatic.position.y), `${direction} finite fallback`);
    assert.ok(graph.nodes.every(node => node.id === automatic.id || !(
      automatic.position.x < node.position.x + node.width && node.position.x < automatic.position.x + automatic.width
      && automatic.position.y < node.position.y + node.height && node.position.y < automatic.position.y + automatic.height
    )), `${direction} no node overlap`);
    assert.ok(automatic.position.x > lane.position.x && automatic.position.y > lane.position.y
      && automatic.position.x + automatic.width < lane.position.x + lane.width
      && automatic.position.y + automatic.height < lane.position.y + lane.height, `${direction} lane containment`);
    const route = architectureEdgeRoute(graph.nodes, graph.edges.find(edge => edge.id === "explorer-to-extension"), direction);
    assert.equal(architectureRouteCrossesUnrelatedNode(route, graph.nodes, "architecture-explorer", "automatic-extension"), false);
    const center = id => { const node = graph.nodes.find(item => item.id === id); return direction === "LR"
      ? { primary: node.position.x + node.width / 2, cross: node.position.y + node.height / 2 }
      : { primary: node.position.y + node.height / 2, cross: node.position.x + node.width / 2 }; };
    assert.equal(center("dashboard-sync").primary, center("stable-release").primary, `${direction} retained source rank`);
    assert.equal(center("web-worker").cross, (center("d1").cross + center("cloudflare").cross) / 2, `${direction} retained convergence`);
  }
});

test("33. Mobile automatic viewport preserves the node readability floor and permits canvas panning", () => {
  for (const view of manifest.views) {
    const graph = buildArchitectureGraph(manifest, view.id, "TB");
    for (const width of [390, 360]) {
      const height = architectureCanvasHeight(width, width === 390 ? 844 : 800, true);
      const viewport = architectureMobileViewport(graph.nodes, graph.laneBoxes, width, height);
      assert.ok(Math.min(...graph.nodes.map(node => node.width * viewport.zoom)) >= ARCHITECTURE_MOBILE_NODE_WIDTH_FLOOR);
    }
  }
  const web = buildArchitectureGraph(manifest, "web-cloudflare", "TB");
  const bounds = architectureGraphBounds(web.nodes, web.laneBoxes);
  const canvasWidth = 334;
  const viewport = architectureMobileViewport(web.nodes, web.laneBoxes, canvasWidth, architectureCanvasHeight(360, 800, true));
  assert.ok(bounds.width * viewport.zoom > canvasWidth, "wide branch graph remains horizontally pannable instead of shrinking below the floor");
  const worker = web.nodes.find(node => node.id === "web-worker");
  const stable = web.nodes.find(node => node.id === "stable-release");
  const projected = node => ({ left: node.position.x * viewport.zoom + viewport.x, right: (node.position.x + node.width) * viewport.zoom + viewport.x });
  assert.ok(Math.min(projected(worker).right, canvasWidth) - Math.max(projected(worker).left, 0) >= 140,
    "primary convergence node remains readable initially");
  assert.ok(Math.min(projected(stable).right, canvasWidth) - Math.max(projected(stable).left, 0) >= 60,
    "secondary release branch remains visually apparent initially");
  assert.equal(ARCHITECTURE_MOBILE_NODE_WIDTH_FLOOR, 168);
  assert.match(viewSource, /current\.mobile && intent\.type !== "MANUAL_FIT"/);
  assert.match(viewSource, /panOnDrag preventScrolling={!mobile} zoomOnPinch/);
  assert.match(cssSource, /\.stage \{[^}]*overflow: hidden;/);
  assert.match(cssSource, /@media \(max-width: 720px\)[\s\S]*\.graphNode strong \{ font-size: 17px; \}/);
  assert.match(cssSource, /\.laneRegion > span \{ top: 7px; left: 10px; font-size: 13px;/);
});

test("34. Mobile visible canvas height follows the actual viewport rather than graph bounds", () => {
  assert.equal(architectureCanvasHeight(320, 568, true), 480);
  assert.equal(architectureCanvasHeight(360, 800, true), 544);
  assert.equal(architectureCanvasHeight(390, 844, true), 574);
  assert.equal(architectureCanvasHeight(844, 390, true), 281);
  assert.equal(architectureCanvasHeight(1440, 900, false), 650);
  assert.match(viewSource, /architectureCanvasHeight\(visibleViewport\.width, visibleViewport\.height, mobile\)/);
  assert.doesNotMatch(viewSource, /architectureCanvasHeight\(graphBounds/);
  assert.doesNotMatch(source("../app/_lib/architecture-explorer.ts"), /nodeCount \* 135/);
});

test("35. Fit zoom is bounded by view size and selection does not refit", () => {
  assert.ok(architectureFitOptions(4, false).maxZoom > architectureFitOptions(11, false).maxZoom);
  assert.ok(architectureFitOptions(4, true).maxZoom <= architectureFitOptions(4, false).maxZoom);
  assert.match(viewSource, /const zoom = current\.flow\.getZoom\(\)/);
  assert.match(viewSource, /current\.flow\.setViewport\(\{/);
  assert.match(viewSource, /y: 64 - item\.position\.y \* zoom/);
  assert.match(viewSource, /defaultNodes=\{flowElements\}/);
  assert.match(viewSource, /flow\.setNodes\(flowElements\)/);
  assert.match(viewSource, /state\.nodeLookup\.get\(id\)\?\.measured/);
  assert.doesNotMatch(viewSource, /nodes=\{\[\.\.\.laneNodes/);
});

test("36. Edge labels follow critical, release, interaction, guide, and sparse-view rules", () => {
  assert.match(viewSource, /item\.criticality === "CRITICAL"/);
  assert.match(viewSource, /item\.criticality === "CONTROL_PLANE" && viewId === "runtime-release"/);
  assert.match(viewSource, /graph\.edges\.length <= 4/);
  assert.match(viewSource, /hoveredEdgeId === item\.id/);
  assert.match(viewSource, /data\.showLabel \?/);
  assert.match(viewSource, /graph\.edges\.map\(edge =>/);
});

test("37. Beginner and failure copy is Chinese-primary", () => {
  for (const label of ["它是什么？", "为什么需要它？", "谁负责它？", "输入来自哪里？", "输出到哪里？", "它坏了会停止什么？", "什么仍会继续？", "打开子系统"]) {
    assert.ok(viewSource.includes(label), label);
  }
});

test("38. Required failure impacts are explicit and missing contracts stay disabled", () => {
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
    viewId: "system-overview", nodesInitialized: true, flowInitialized: true, canvasTransitionComplete: true,
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

test("39. Camera owner performs one initial automatic Fit", () => {
  const h = cameraHarness({ flowInitialized: false });
  h.controller.request({ type: "FIT_VIEW", viewId: "system-overview" }); h.flush();
  assert.equal(h.commands.length, 0);
  h.layout.flowInitialized = true; h.controller.layoutChanged(); h.flush();
  assert.deepEqual(h.commands.map(item => item.type), ["FIT_VIEW"]);
});

test("40. One view switch performs one automatic Fit", () => {
  const h = cameraHarness({ viewId: "training-models" });
  h.controller.request({ type: "FIT_VIEW", viewId: "training-models" }); h.flush();
  assert.deepEqual(h.commands.map(item => item.type), ["FIT_VIEW"]);
});

test("41. Rapid A to B to C navigation executes only C", () => {
  const h = cameraHarness({ viewId: "view-c" });
  h.controller.request({ type: "FIT_VIEW", viewId: "view-a" });
  h.controller.request({ type: "FIT_VIEW", viewId: "view-b" });
  h.controller.request({ type: "FIT_VIEW", viewId: "view-c" }); h.flush();
  assert.deepEqual(h.commands, [{ type: "FIT_VIEW", viewId: "view-c" }]);
});

test("42. Cross-view search performs one final Focus without Fit", () => {
  const h = cameraHarness({ viewId: "training-models" });
  h.controller.request({ type: "FOCUS_NODE", viewId: "training-models", nodeId: "training", source: "SEARCH" }); h.flush();
  assert.deepEqual(h.commands.map(item => item.type), ["FOCUS_NODE"]);
});

test("43. A cross-view scenario starts with one Focus and no duplicate Fit", () => {
  const h = cameraHarness({ viewId: "runtime-release" });
  h.controller.request({ type: "FOCUS_NODE", viewId: "runtime-release", nodeId: "github", source: "SCENARIO_STEP" }); h.flush();
  assert.deepEqual(h.commands.map(item => item.type), ["FOCUS_NODE"]);
});

test("44. Each scenario step performs exactly one camera command", () => {
  const h = cameraHarness({ viewId: "system-overview" });
  for (const nodeId of ["ctrader", "business-runtime", "decision"]) {
    h.controller.request({ type: "FOCUS_NODE", viewId: "system-overview", nodeId, source: "SCENARIO_STEP" }); h.flush();
  }
  assert.deepEqual(h.commands.map(item => item.nodeId), ["ctrader", "business-runtime", "decision"]);
});

test("45. Inspector close waits for width transition then Fits once", () => {
  const h = cameraHarness({ canvasTransitionComplete: false });
  h.controller.request({ type: "REFIT_AFTER_INSPECTOR_CLOSE", viewId: "system-overview" }); h.flush();
  assert.equal(h.commands.length, 0);
  h.layout.width = 1580; h.layout.canvasTransitionComplete = true; h.controller.layoutChanged(); h.flush();
  assert.deepEqual(h.commands.map(item => item.type), ["REFIT_AFTER_INSPECTOR_CLOSE"]);
});

test("46. Manual Fit performs exactly one Fit", () => {
  const h = cameraHarness();
  h.controller.request({ type: "MANUAL_FIT", viewId: "system-overview" }); h.flush();
  assert.deepEqual(h.commands.map(item => item.type), ["MANUAL_FIT"]);
});

test("47. Cancelled stale frames cannot move the current view", () => {
  const h = cameraHarness({ viewId: "view-b" });
  h.controller.request({ type: "FIT_VIEW", viewId: "view-a" });
  const staleFrames = [...h.frames.values()];
  h.controller.request({ type: "FIT_VIEW", viewId: "view-b" });
  staleFrames.forEach(callback => callback(0)); h.flush();
  assert.deepEqual(h.commands, [{ type: "FIT_VIEW", viewId: "view-b" }]);
});

test("48. Mobile initialization exposes only one TB layout Fit", () => {
  const h = cameraHarness({ viewId: "system-overview" });
  h.controller.request({ type: "FIT_VIEW", viewId: "system-overview" }); h.flush();
  assert.equal(buildArchitectureGraph(manifest, "system-overview", "TB").direction, "TB");
  assert.deepEqual(h.commands.map(item => item.type), ["FIT_VIEW"]);
  assert.match(viewSource, /mobile === null[\s\S]*Preparing architecture layout/);
  assert.doesNotMatch(viewSource, /elementsSelectable fitView/);
  assert.doesNotMatch(viewSource, /window\.setTimeout/);
});

test("49. Navigation taxonomy has exactly one beginner overview", () => {
  const overviews = manifest.views.filter(view => view.navigation.role === "OVERVIEW");
  assert.deepEqual(overviews.map(view => [view.id, view.navigation.audience]), [["system-overview", "BEGINNER"]]);
});

test("50. Beginner subsystem taxonomy is manifest-owned", () => {
  assert.deepEqual(manifest.views.filter(view => view.navigation.role === "SUBSYSTEM").map(view => view.id),
    ["decision-evidence", "training-models", "news-ai", "dashboard-sync", "web-cloudflare", "assistant"]);
});

test("51. Advanced and campaign views stay outside beginner navigation", () => {
  const advanced = manifest.views.filter(view => view.navigation.audience === "ADVANCED").map(view => view.id);
  assert.deepEqual(advanced, ["execution-topology", "runtime-release", "package-dependencies", "modularization-campaign"]);
});

test("52. Every non-overview view returns to System Overview", () => {
  assert.ok(manifest.views.slice(1).every(view => view.navigation.parent_view === "system-overview"));
});

test("53. Overview initially exposes the critical spine", () => {
  const visible = architectureDisclosedEdgeIds(manifest, "system-overview");
  for (const id of ["quote-to-runtime", "runtime-to-decision", "decision-to-evidence", "evidence-to-dashboard", "dashboard-to-web-overview"]) assert.ok(visible.has(id));
});

test("54. Overview initially exposes optional News to Decision", () => {
  assert.ok(architectureDisclosedEdgeIds(manifest, "system-overview").has("news-to-decision"));
});

test("55. Overview keeps feedback and release-control relationships secondary", () => {
  const visible = architectureDisclosedEdgeIds(manifest, "system-overview");
  for (const id of ["evidence-to-training", "training-to-decision", "github-to-control-plane", "control-plane-to-center"]) assert.equal(visible.has(id), false);
});

test("56. Selecting Decision reveals its direct relationships", () => {
  const visible = architectureDisclosedEdgeIds(manifest, "system-overview", { selectedNodeId: "decision" });
  for (const id of ["runtime-to-decision", "decision-to-evidence", "news-to-decision", "training-to-decision", "decision-to-dashboard"]) assert.ok(visible.has(id));
});

test("57. Selecting Decision retains the overview spine", () => {
  const base = architectureDisclosedEdgeIds(manifest, "system-overview");
  const selected = architectureDisclosedEdgeIds(manifest, "system-overview", { selectedNodeId: "decision" });
  assert.ok([...base].every(id => selected.has(id)));
});

test("58. Disclosure preserves complete-graph node positions", () => {
  const full = buildArchitectureGraph(manifest, "system-overview");
  const disclosed = architectureDisclosedGraph(full, architectureDisclosedEdgeIds(manifest, "system-overview"));
  assert.deepEqual(disclosed.nodes.map(node => [node.id, node.position]), full.nodes.map(node => [node.id, node.position]));
});

test("59. Disclosure preserves preassigned route anchors", () => {
  const full = buildArchitectureGraph(manifest, "system-overview");
  const disclosed = architectureDisclosedGraph(full, architectureDisclosedEdgeIds(manifest, "system-overview"));
  for (const edge of disclosed.edges) assert.deepEqual(edge, full.edges.find(item => item.id === edge.id));
});

test("60. A guided scenario adds its owned relationships without replacing context", () => {
  const scenario = manifest.scenarios.find(item => item.id === "release-path");
  const visible = architectureDisclosedEdgeIds(manifest, scenario.view_id, { scenarioEdgeIds: scenario.edge_ids });
  assert.ok(scenario.edge_ids.every(id => visible.has(id)));
  assert.ok(manifest.views.find(view => view.id === scenario.view_id).disclosure.always_visible_edge_ids.every(id => visible.has(id)));
});

test("61. Explicit show-all reveals every relationship when allowed", () => {
  const visible = architectureDisclosedEdgeIds(manifest, "system-overview", { showAll: true });
  assert.deepEqual(visible, new Set(manifest.views[0].edge_ids));
});

test("62. Reference mode exposes the complete current view", () => {
  const view = manifest.views.find(item => item.id === "runtime-release");
  assert.deepEqual(architectureDisclosedEdgeIds(manifest, view.id, { referenceMode: true }), new Set(view.edge_ids));
});

test("63. Training is a monotonic five-node flow in no more than three lanes", () => {
  const view = manifest.views.find(item => item.id === "training-models");
  assert.deepEqual(view.primary_path, ["evidence", "training-materialization", "training", "published-model", "decision"]);
  assert.ok(view.lanes.length <= 3);
  assert.deepEqual(architectureDisclosedEdgeIds(manifest, view.id), new Set(view.edge_ids));
});

test("64. News defaults to news-owned relationships only", () => {
  const visible = architectureDisclosedEdgeIds(manifest, "news-ai");
  assert.deepEqual(visible, new Set(["collector-to-news", "ai-to-news", "news-to-decision", "news-to-evidence", "news-to-dashboard"]));
});

test("65. Dashboard defaults to its compact end-to-end projection", () => {
  const view = manifest.views.find(item => item.id === "dashboard-sync");
  assert.deepEqual(architectureDisclosedEdgeIds(manifest, view.id), new Set(view.edge_ids));
  assert.deepEqual(view.primary_path, ["decision", "dashboard", "dashboard-api", "dashboard-sync", "d1", "web-worker"]);
});

test("66. Runtime and Release defaults to release path, not supervision", () => {
  const visible = architectureDisclosedEdgeIds(manifest, "runtime-release");
  assert.equal(visible.has("center-to-runtime"), false);
  for (const id of ["github-to-control-plane", "control-plane-to-center", "center-to-candidate", "candidate-to-stable"]) assert.ok(visible.has(id));
});

test("67. Assistant remains a simple PAUSED subsystem", () => {
  const view = manifest.views.find(item => item.id === "assistant");
  assert.equal(view.lanes.length, 2);
  assert.equal(manifest.nodes.find(node => node.id === "assistant-owner").runtime_state, "PAUSED");
  assert.deepEqual(architectureDisclosedEdgeIds(manifest, view.id), new Set(view.edge_ids));
});

test("68. Package dependencies initially render nine nodes and zero edges", () => {
  const full = buildArchitectureGraph(manifest, "package-dependencies");
  const disclosed = architectureDisclosedGraph(full, architectureDisclosedEdgeIds(manifest, "package-dependencies"));
  assert.equal(disclosed.nodes.length, 9); assert.equal(disclosed.edges.length, 0);
});

test("69. Package selection reveals exactly incoming and outgoing dependencies", () => {
  const selected = "package-decision";
  const visible = architectureDisclosedEdgeIds(manifest, "package-dependencies", { selectedNodeId: selected });
  const expected = manifest.edges.filter(edge => edge.from === selected || edge.to === selected).filter(edge => edge.id.startsWith("dep-")).map(edge => edge.id);
  assert.deepEqual([...visible].sort(), expected.sort());
});

test("70. Package selection never recalculates the layout", () => {
  const full = buildArchitectureGraph(manifest, "package-dependencies", "TB");
  const visible = architectureDisclosedEdgeIds(manifest, "package-dependencies", { selectedNodeId: "package-dashboard" });
  assert.deepEqual(architectureDisclosedGraph(full, visible).nodes.map(node => node.position), full.nodes.map(node => node.position));
});

test("71. Show all dependencies is explicit and complete", () => {
  const view = manifest.views.find(item => item.id === "package-dependencies");
  const visible = architectureDisclosedEdgeIds(manifest, view.id, { showAll: true });
  assert.equal(visible.size, 28); assert.deepEqual(visible, new Set(view.edge_ids));
});

test("72. Visible ports correspond exactly to disclosed routed endpoints", () => {
  const full = buildArchitectureGraph(manifest, "package-dependencies");
  const visible = architectureDisclosedEdgeIds(manifest, "package-dependencies", { selectedNodeId: "package-decision" });
  const disclosed = architectureDisclosedGraph(full, visible);
  for (const node of disclosed.nodes) {
    assert.deepEqual(new Set(node.data.incomingPorts.map(port => port.edgeId)), new Set(disclosed.edges.filter(edge => edge.target === node.id).map(edge => edge.id)));
    assert.deepEqual(new Set(node.data.outgoingPorts.map(port => port.edgeId)), new Set(disclosed.edges.filter(edge => edge.source === node.id).map(edge => edge.id)));
  }
});
