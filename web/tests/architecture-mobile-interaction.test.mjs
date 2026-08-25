import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import { createArchitectureCameraController } from "../app/_lib/architecture-camera.ts";
import {
  architectureCanvasHeight,
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
import { loadArchitectureManifest } from "../build/architecture-manifest.ts";

const root = new URL("../..", import.meta.url).pathname.replace(/^\/(.:)/, "$1");
const manifest = parseArchitectureManifest(loadArchitectureManifest(root));
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
  assert.notEqual(narrow.x, wide.x); assert.equal(narrow.y, wide.y);
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

test("mobile 19: node selection issues no duplicate Fit", () => {
  assert.match(viewSource, /dispatchInteraction\(\{ type: "NODE_TAP", nodeId: id \}\)[\s\S]*camera\.request\(\{ type: "FOCUS_NODE"/);
  assert.doesNotMatch(viewSource, /NODE_TAP[\s\S]{0,180}FIT_VIEW/);
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
});

test("mobile 24: Advanced owns a controlled dialog, backdrop, close, and focus return", () => {
  assert.doesNotMatch(viewSource, /<details className=\{styles\.advancedMenu\}>/);
  assert.match(viewSource, /architecture-advanced-title/); assert.match(viewSource, /sheetBackdrop/); assert.match(viewSource, /aria-label="关闭高级视图"/);
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

test("mobile 27: Explore Advanced lists only the four advanced destinations", () => {
  assert.deepEqual(manifest.views.filter(view => ["ADVANCED", "CAMPAIGN"].includes(view.navigation.role)).map(view => view.id),
    ["execution-topology", "runtime-release", "package-dependencies", "modularization-campaign"]);
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
  const decision = manifest.nodes.find(node => node.id === "decision"); assert.ok(decision.subsystem_view);
  const state = step(initial, { type: "NODE_TAP", nodeId: decision.id }, { type: "OPEN_SUBSYSTEM" }); assert.deepEqual(state, initial);
  assert.match(viewSource, /className=\{styles\.selectedDock\}[\s\S]*打开子系统/);
});

test("mobile 31: breadcrumb back remains available after subsystem drill-down", () => {
  assert.match(viewSource, /setViewHistory\(items => \[\.\.\.items, viewId\]\)/);
  assert.match(viewSource, />Back<\/button>/); assert.match(viewSource, /items\.slice\(0, -1\)/);
});
