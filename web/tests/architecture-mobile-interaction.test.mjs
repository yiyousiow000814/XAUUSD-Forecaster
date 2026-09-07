import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { getViewportForBounds } from "@xyflow/react";

import { createArchitectureCameraController } from "../app/_lib/architecture-camera.ts";
import {
  ARCHITECTURE_MIN_ZOOM,
  ARCHITECTURE_NODE_BOX,
  architectureCanvasHeight,
  architectureFitOptions,
  architectureGraphBounds,
  architectureMobileViewport,
  buildArchitectureGraph,
  parseArchitectureManifest,
} from "../app/_lib/architecture-explorer.ts";
import {
  INITIAL_ARCHITECTURE_MOBILE_INTERACTION as initial,
  architectureMobileInteractionIsValid as valid,
  architectureMobileInteractionReducer as reduce,
  architectureSheetTabIndex,
  lockArchitecturePageScroll,
  restoreArchitecturePageScroll,
} from "../app/_lib/architecture-mobile-interaction.ts";
import { projectCurrentSource, readCurrentSourceIndex } from "../build/architecture-current-source.mjs";

const index = readCurrentSourceIndex(new URL("../../architecture/generated/critical-index.json", import.meta.url));
const manifest = parseArchitectureManifest(projectCurrentSource(index).manifest);
assert.ok(manifest);
const viewSource = readFileSync(new URL("../app/_views/ArchitectureExplorerView.tsx", import.meta.url), "utf8");
const cssSource = readFileSync(new URL("../app/_views/ArchitectureExplorerView.module.css", import.meta.url), "utf8");
const step = (state, ...events) => events.reduce(reduce, state);

test("mobile 1: first node tap selects a path and leaves Inspector closed", () => {
  const state = reduce(initial, { type: "NODE_TAP", nodeId: "decision" });
  assert.equal(state.activePathNodeId, "decision"); assert.equal(state.mobilePanel, "NONE"); assert.equal(state.inspectorOpen, false); assert.ok(valid(state));
});

test("mobile 2: 查看详情 opens Inspector for the active node", () => {
  const state = step(initial, { type: "NODE_TAP", nodeId: "decision" }, { type: "OPEN_INSPECTOR" });
  assert.equal(state.inspectorNodeId, "decision"); assert.equal(state.mobilePanel, "INSPECTOR"); assert.ok(valid(state));
});

test("mobile 3: closing Inspector preserves the active path", () => {
  const state = step(initial, { type: "NODE_TAP", nodeId: "decision" }, { type: "OPEN_INSPECTOR" }, { type: "CLOSE_INSPECTOR" });
  assert.equal(state.activePathNodeId, "decision"); assert.equal(state.inspectorNodeId, null); assert.equal(state.mobilePanel, "NONE");
});

test("mobile 4: 清除路径 clears path and panel disclosure owner", () => {
  const state = step(initial, { type: "NODE_TAP", nodeId: "decision" }, { type: "OPEN_INSPECTOR" }, { type: "CLEAR_PATH" });
  assert.deepEqual(state, initial);
});

test("mobile 5: Advanced and Inspector are mutually exclusive", () => {
  const state = step(initial, { type: "NODE_TAP", nodeId: "decision" }, { type: "OPEN_INSPECTOR" }, { type: "OPEN_ADVANCED" });
  assert.equal(state.mobilePanel, "ADVANCED"); assert.equal(state.inspectorNodeId, null); assert.ok(valid(state));
});

test("mobile 6: sheet focus wraps in both directions", () => {
  assert.equal(architectureSheetTabIndex(2, 1, 3), 0); assert.equal(architectureSheetTabIndex(0, -1, 3), 2);
});

test("mobile 7: backdrop closes Advanced without clearing the path", () => {
  const state = step(initial, { type: "NODE_TAP", nodeId: "decision" }, { type: "OPEN_ADVANCED" }, { type: "BACKDROP_CLICK" });
  assert.equal(state.mobilePanel, "NONE"); assert.equal(state.activePathNodeId, "decision");
});

test("mobile 8: Escape closes the topmost sheet", () => {
  const state = step(initial, { type: "OPEN_ADVANCED" }, { type: "ESCAPE" });
  assert.deepEqual(state, initial);
});

test("mobile 9: destination selection closes Advanced", () => {
  const state = step(initial, { type: "OPEN_ADVANCED" }, { type: "SELECT_ADVANCED_DESTINATION" });
  assert.equal(state.advancedOpen, false); assert.equal(state.mobilePanel, "NONE");
});

test("mobile 10: view changes clear incompatible sheet and path state", () => {
  const state = step(initial, { type: "NODE_TAP", nodeId: "decision" }, { type: "OPEN_INSPECTOR" }, { type: "CHANGE_VIEW" });
  assert.deepEqual(state, initial);
});

test("mobile 11: search selects a path without opening Inspector", () => {
  const state = reduce(initial, { type: "SELECT_SEARCH_RESULT", nodeId: "training" });
  assert.equal(state.activePathNodeId, "training"); assert.equal(state.inspectorOpen, false);
});

test("mobile 12: scenario start does not force an Inspector", () => {
  const selected = reduce(initial, { type: "NODE_TAP", nodeId: "decision" });
  assert.deepEqual(reduce(selected, { type: "START_SCENARIO" }), selected);
});

test("mobile 13: scenario camera keeps only the latest step intent", () => {
  const frames = new Map(); const executed = []; let nextFrame = 0;
  const camera = createArchitectureCameraController({
    requestFrame: callback => { const id = ++nextFrame; frames.set(id, callback); return id; },
    cancelFrame: id => frames.delete(id),
    readLayout: () => ({ viewId: "system-overview", nodesInitialized: true, flowInitialized: true, canvasTransitionComplete: true, width: 390, height: 574 }),
    execute: intent => executed.push(intent),
  });
  camera.request({ type: "FOCUS_NODE", viewId: "system-overview", nodeId: "news", source: "SCENARIO_STEP" });
  camera.request({ type: "FOCUS_NODE", viewId: "system-overview", nodeId: "decision", source: "SCENARIO_STEP" });
  while (frames.size) { const entries = [...frames.values()]; frames.clear(); entries.forEach(callback => callback(0)); }
  assert.equal(executed.length, 1); assert.equal(executed[0].nodeId, "decision");
});

test("mobile 14: closing a scenario leaves no stale sheet mutation", () => {
  const selected = reduce(initial, { type: "NODE_TAP", nodeId: "decision" });
  assert.deepEqual(step(selected, { type: "START_SCENARIO" }, { type: "MOVE_SCENARIO" }, { type: "CLOSE_SCENARIO" }), selected);
});

test("mobile 15: visible canvas height derives only from viewport dimensions", () => {
  assert.equal(architectureCanvasHeight(320, 568, true), 480); assert.equal(architectureCanvasHeight(430, 932, true), 634);
  assert.equal(architectureCanvasHeight(800, 360, true), 280); assert.equal(architectureCanvasHeight(844, 390, true), 281);
});

test("mobile 16: automatic framing uses actual canvas client dimensions", () => {
  const graph = buildArchitectureGraph(manifest, "system-overview", "TB");
  const narrow = architectureMobileViewport(graph.nodes, graph.laneBoxes, 320, 480);
  const wide = architectureMobileViewport(graph.nodes, graph.laneBoxes, 430, 634);
  const bounds = architectureGraphBounds(graph.nodes, graph.laneBoxes);
  assert.notEqual(narrow.x, wide.x);
  for (const viewport of [narrow, wide]) {
    assert.ok(Math.abs(bounds.y * viewport.zoom + viewport.y - 48) < .001);
  }
});

test("mobile 17: manual Fit remains one explicit camera intent", () => {
  assert.match(viewSource, /camera\.request\(\{ type: "MANUAL_FIT", viewId \}\)/);
  assert.equal((viewSource.match(/type: "MANUAL_FIT"/g) ?? []).length, 1);
});

test("mobile 18: automatic framing places meaningful graph bounds near the top", () => {
  for (const view of manifest.views) {
    const graph = buildArchitectureGraph(manifest, view.id, "TB"); const bounds = architectureGraphBounds(graph.nodes, graph.laneBoxes);
    const viewport = architectureMobileViewport(graph.nodes, graph.laneBoxes, 390, 574);
    const topDistance = bounds.y * viewport.zoom + viewport.y;
    assert.ok(topDistance <= 574 * .25, `${view.id} top distance ${topDistance}`); assert.ok(topDistance >= 0);
  }
});

test("graph fit and minimum user zoom preserve physical hit targets in every node state", () => {
  const borders = [...cssSource.matchAll(/\.graphNode(?:\.[\w-]+)*\s*\{([^}]*)\}/g)].flatMap(([, declarations]) => {
    const border = declarations.match(/(?:^|;)\s*border(?:-width)?\s*:\s*([^;]+)/);
    if (!border) return [];
    const pixels = border[1].match(/^([\d.]+)px(?:\s|$)/);
    assert.ok(pixels, 'node border must retain a measurable pixel inset');
    return [Number(pixels[1])];
  });
  assert.equal(Math.max(...borders), ARCHITECTURE_NODE_BOX.maximumBorder);
  let denseGraphRequiresPanning = false;
  for (const view of manifest.views) for (const direction of ['LR', 'TB']) {
    const graph = buildArchitectureGraph(manifest, view.id, direction);
    const bounds = architectureGraphBounds(graph.nodes, graph.laneBoxes);
    for (const [width, height, mobile] of [[1200, 650, false], [390, 574, true], [360, 544, true]]) {
      const options = architectureFitOptions(graph.nodes.length, mobile);
      const fitted = getViewportForBounds(bounds, width, height, options.minZoom, options.maxZoom, options.padding);
      const automaticMobile = architectureMobileViewport(graph.nodes, graph.laneBoxes, width, height);
      for (const zoom of [ARCHITECTURE_MIN_ZOOM, fitted.zoom, automaticMobile.zoom]) {
        for (const node of graph.nodes) for (const border of borders) {
          assert.ok((node.width - border * 2) * zoom >= 44, `${view.id}: target width`);
          assert.ok((node.height - border * 2) * zoom >= 44, `${view.id}: target height`);
        }
      }
      if (bounds.width * fitted.zoom > width || bounds.height * fitted.zoom > height) denseGraphRequiresPanning = true;
    }
  }
  assert.ok(denseGraphRequiresPanning, 'readable interaction scale is not a promise to show the whole graph');
  assert.match(viewSource, /elementsSelectable minZoom=\{ARCHITECTURE_MIN_ZOOM\}/);
  assert.match(viewSource, /flow\.fitView\(\{ \.\.\.architectureFitOptions\(/);
  assert.match(viewSource, /const zoom = Math\.max\(ARCHITECTURE_MIN_ZOOM, current\.flow\.getZoom\(\)\)/);
  assert.match(viewSource, /panOnDrag preventScrolling/);
});

test("mobile 19: node selection issues no duplicate Fit", () => {
  assert.match(viewSource, /dispatchInteraction\(\{ type: "NODE_TAP", nodeId: id \}\)[\s\S]*camera\.request\(\{ type: "FOCUS_NODE"/);
  assert.doesNotMatch(viewSource, /NODE_TAP[\s\S]{0,180}FIT_VIEW/);
});

test("canvas overlays reserve separate control, minimap and information corners", () => {
  // React Flow panels at the same corner share a z-index and intercept each
  // other's clicks. Preserve both desktop controls, not a higher z-index cover.
  assert.match(viewSource, /<Controls position=\{mobile \? "bottom-right" : "top-left"\} showInteractive=\{false\}/);
  assert.match(viewSource, /\{!mobile \? <MiniMap[^>]*position="bottom-right"[^>]*pannable zoomable/);
  assert.match(cssSource, /\.legend \{[^}]*left: 14px;[^}]*bottom: 14px;/);
  assert.match(cssSource, /\.keyboardHint \{[^}]*top: 12px;[^}]*right: 14px;/);
  // Phone controls stay above the full-width legend; MiniMap keeps its existing
  // mobile exclusion, including short landscape, rather than stealing targets.
  assert.match(cssSource, /\.canvas :global\(\.react-flow__controls\) \{ bottom: 58px; \}/);
  assert.match(viewSource, /max-height: 500px\) and \(max-width: 900px/);
});

test("mobile 20: visual viewport changes are coalesced and only orientation requests Fit", () => {
  assert.match(viewSource, /window\.cancelAnimationFrame\(frame\)[\s\S]*window\.requestAnimationFrame/);
  assert.match(viewSource, /if \(orientation === orientationRef\.current\) return;[\s\S]*camera\.request\(\{ type: "FIT_VIEW"/);
});

test("mobile 21: Inspector open and close do not request a mobile refit", () => {
  assert.match(viewSource, /if \(!mobile\) camera\.request\(\{ type: "REFIT_AFTER_INSPECTOR_CLOSE"/);
});

test("mobile 22: path selection remains valid after Inspector close", () => {
  const state = step(initial, { type: "NODE_TAP", nodeId: "decision" }, { type: "OPEN_INSPECTOR" }, { type: "CLOSE_INSPECTOR" });
  assert.ok(valid(state)); assert.equal(state.activePathNodeId, "decision");
});

test("mobile 23: Inspector owns dialog, close, scrolling, safe area, and focus restoration contracts", () => {
  assert.match(viewSource, /aria-modal=\{modal \|\| undefined\} className=\{styles\.inspector\} role=\{modal \? "dialog" : "complementary"\}/);
  assert.match(viewSource, /aria-label="关闭详情" data-sheet-initial-focus/);
  assert.match(cssSource, /\.inspectorBody \{[^}]*overflow-y: auto;[^}]*safe-area-inset-bottom/);
  assert.match(viewSource, /const returnFocus = returnFocusRef\.current/);
  assert.match(viewSource, /returnFocus\?\.focus/);
  // Long function/component names stay complete. Their min-content width must
  // not push the shared 44px close button outside desktop or phone panels.
  assert.match(viewSource, /<h2 id="architecture-inspector-title">\{edge \? edge\.label : node\.label\}<\/h2>/);
  assert.match(cssSource, /\.inspector > header > div \{[^}]*min-width: 0;[^}]*flex: 1 1 0;[^}]*overflow-wrap: anywhere;/);
  assert.match(cssSource, /\.inspector > header button \{[^}]*width: 44px;[^}]*height: 44px;[^}]*flex: 0 0 auto;/);
  assert.match(cssSource, /\.inspector \{[^}]*display: flex;[^}]*flex-direction: column;/);
  assert.match(cssSource, /\.inspector > header \{[^}]*flex-shrink: 0;/);
  assert.match(cssSource, /\.inspectorBody \{[^}]*flex: 1 1 auto;[^}]*min-height: 0;[^}]*overflow: auto;/);
  // A wrapped header has intrinsic height. No responsive sibling may deduct
  // an assumed one-line header height and clip the last scrollable control.
  for (const [, declarations] of cssSource.matchAll(/\.inspectorBody\s*\{([^}]*)\}/g)) {
    assert.doesNotMatch(declarations, /(?:^|;)\s*height\s*:/);
  }
});

test("mobile 24: Advanced owns a controlled dialog, backdrop, close, and focus return", () => {
  assert.doesNotMatch(viewSource, /<details className=\{styles\.advancedMenu\}>/);
  assert.match(viewSource, /architecture-advanced-title/); assert.match(viewSource, /sheetBackdrop/); assert.match(viewSource, /aria-label="关闭高级视图"/);
  assert.match(viewSource, /className=\{`\$\{styles\.inspector\} \$\{styles\.advancedSheet\}`\}/);
  assert.match(viewSource, /<h2 id="architecture-advanced-title">高级视图<\/h2>/);
});

test("mobile 25: scroll lock restores exact prior body style and scroll position", () => {
  const style = { overflow: "auto", position: "relative", top: "2px", width: "90%" };
  const lock = lockArchitecturePageScroll(style, 417);
  assert.deepEqual(style, { overflow: "hidden", position: "fixed", top: "-417px", width: "100%" });
  assert.equal(restoreArchitecturePageScroll(style, lock), 417);
  assert.deepEqual(style, { overflow: "auto", position: "relative", top: "2px", width: "90%" });
});

test("mobile 26: reducer cannot retain two sheets or an orphan Inspector", () => {
  for (const event of [{ type: "OPEN_ADVANCED" }, { type: "OPEN_INSPECTOR", nodeId: "decision" }, { type: "BACKDROP_CLICK" }, { type: "ESCAPE" }]) {
    assert.ok(valid(reduce(initial, event)));
  }
});

test("mobile sheet preserves measured content width when scrollbar disappears", () => {
  const style = { overflow: 'auto', position: 'relative', top: '0px', width: '100%' };
  const lock = lockArchitecturePageScroll(style, 250, 375);
  assert.equal(style.width, '375px');
  assert.equal(restoreArchitecturePageScroll(style, lock), 250);
  assert.equal(style.width, '100%');
});

test("graph publication does not re-enter synchronous node measurement", () => {
  // React Flow measures nodes using ResizeObserver. Publishing the entire graph
  // inside a layout effect re-enters that measurement while a sheet resizes it.
  assert.match(viewSource, /useEffect\(\(\) => \{\s*flow\.setNodes\(flowElements\);\s*flow\.setEdges\(flowEdges\);/);
  assert.doesNotMatch(viewSource, /useLayoutEffect\(\(\) => \{\s*flow\.setNodes/);
});

test("mobile 27: Explore Advanced lists only the current source slices", () => {
  assert.deepEqual(manifest.views.filter(view => ["ADVANCED", "CAMPAIGN"].includes(view.navigation.role)).map(view => view.id),
    Object.keys(index.allowed.views));
});

test("mobile 28: Explore Advanced does not repeat beginner subsystem destinations", () => {
  const advancedBlock = viewSource.slice(viewSource.indexOf('experienceMode === "EXPLORE"'), viewSource.indexOf('aria-label="Explorer experience mode"'));
  assert.doesNotMatch(advancedBlock, /navigation\.role === "SUBSYSTEM"|Subsystems/);
});

test("mobile 29: Reference exposes all views and reference-only controls", () => {
  assert.match(viewSource, /Architecture reference view[\s\S]*manifest\.views\.map/);
  assert.match(viewSource, /Runtime state/); assert.match(viewSource, /Show all relationships/);
});

test("mobile 30: selected-node dock reaches subsystem drill-down", () => {
  const decision = manifest.nodes.find(node => node.id === "slice:clock-transaction"); assert.ok(decision.subsystem_view);
  const state = step(initial, { type: "NODE_TAP", nodeId: decision.id }, { type: "OPEN_SUBSYSTEM" }); assert.deepEqual(state, initial);
  assert.match(viewSource, /className=\{styles\.selectedDock\}[\s\S]*打开子系统/);
});

test("mobile 31: breadcrumb navigation preserves return paths and minimum targets", () => {
  assert.match(viewSource, /setViewHistory\(items => \[\.\.\.items, viewId\]\)/);
  assert.match(viewSource, />Back<\/button>/); assert.match(viewSource, /items\.slice\(0, -1\)/);
  // The shared rules serve desktop Inspector and phone modal navigation.
  // Inspect every matching rule so a responsive override cannot shrink them.
  for (const [owner, rules, minimumWidth] of [
    ['view breadcrumb', /\.breadcrumbs button\s*\{([^}]*)\}/g, false],
    ['code breadcrumb', /\.codeStructure > nav button\s*\{([^}]*)\}/g, true],
  ]) {
    const declarations = [...cssSource.matchAll(rules)].map(([, body]) => body);
    for (const property of minimumWidth ? ['min-height', 'min-width'] : ['min-height']) {
      const values = declarations.flatMap(body => [...body.matchAll(new RegExp(`(?:^|;)\\s*${property}:\\s*([^;]+)`, 'g'))].map(([, value]) => value.trim()));
      assert.ok(values.length, `${owner}: shared ${property} exists`);
      for (const value of values) {
        const pixels = value.match(/^([\d.]+)px$/);
        assert.ok(pixels && Number(pixels[1]) >= 44, `${owner}: minimum target ${property}`);
      }
    }
    if (minimumWidth) assert.ok(declarations.some(body => /(?:^|;)\s*flex-shrink:\s*0\s*(?:;|$)/.test(body)), 'long code breadcrumbs retain their text width and scroll');
  }
});
