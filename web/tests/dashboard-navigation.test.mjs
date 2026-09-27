import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { transform } from "esbuild";

// Execute the production owner with deterministic hooks and deferred room imports.
// Browser Preview checks additionally exercise real React, history and painting.
const source = readFileSync(new URL("../app/_components/DashboardApp.tsx", import.meta.url), "utf8");
const compiled = await transform(source, { loader: "tsx", format: "cjs", jsx: "automatic" });

function harness() {
  let cursor = 0;
  const slots = [], effects = [], listeners = new Map(), imports = [], pending = [];
  const deferred = new Set();
  const writes = [];
  let tree;
  const hooks = {
    useState(initial) {
      const index = cursor++;
      if (!(index in slots)) slots[index] = typeof initial === "function" ? initial() : initial;
      return [slots[index], value => { slots[index] = typeof value === "function" ? value(slots[index]) : value; }];
    },
    useRef(initial) {
      const index = cursor++;
      return slots[index] ??= { current: initial };
    },
    useCallback: value => value,
    useMemo: factory => factory(),
    useEffect: effect => effects.push(effect),
    useLayoutEffect: () => {},
    lazy: () => "LazyView", Suspense: "Suspense",
  };
  const window = {
    location: { href: "https://test.invalid/audit?view=news" }, scrollY: 120,
    history: Object.fromEntries(["pushState", "replaceState"].map(name => [name, (_state, _title, href) => {
      window.location.href = new URL(href, window.location.href).href;
      writes.push([name, href]);
    }])),
    addEventListener: (name, listener) => listeners.set(name, listener),
    removeEventListener: (name, listener) => { if (listeners.get(name) === listener) listeners.delete(name); },
    requestIdleCallback: () => 1, cancelIdleCallback: () => {},
  };
  const require = name => {
    if (name === "react") return hooks;
    if (name === "react/jsx-runtime") return { jsx: (type, props) => ({ type, props }), jsxs: (type, props) => ({ type, props }) };
    if (name.endsWith("dashboard-resource")) return { primeDashboardResources() {} };
    if (name.endsWith("responsive-scroll")) return { settleResponsiveScroll() { throw new Error("Unexpected scroll reset"); } };
    if (name.endsWith("DashboardNavigation")) return { DashboardNavigationProvider: "Provider" };
    return { __esModule: true, default: name };
  };
  const load = name => {
    imports.push(name);
    if (!deferred.has(name)) return Promise.resolve({ default: name });
    return new Promise((resolve, reject) => pending.push({ resolve, reject }));
  };
  const module = { exports: {} };
  new Function("require", "module", "exports", "window", "load", compiled.code.replace(/import\(("[^"]+")\)/g, "load($1)"))(require, module, module.exports, window, load);
  const cleanups = [];
  function render() {
    cleanups.splice(0).forEach(cleanup => cleanup?.());
    cursor = 0; effects.length = 0;
    tree = module.exports.default({ initialLocation: { room: "audit", auditView: "news" } });
    effects.forEach(effect => cleanups.push(effect()));
    return tree;
  }
  const walk = (node, predicate) => {
    if (!node || typeof node !== "object") return null;
    if (predicate(node)) return node;
    for (const child of [node.props?.children].flat(Infinity)) {
      const found = walk(child, predicate); if (found) return found;
    }
    return null;
  };
  render(); imports.length = 0;
  return {
    render, imports, writes, deferred, pending,
    navigate: (...args) => tree.props.value.navigate(...args),
    view: () => tree.props.children.props.location,
    hidden: () => walk(tree, node => node.props?.className === "dashboard-view-content").props.hidden,
    skeleton: () => Boolean(walk(tree, node => typeof node.type === "string" && node.type.endsWith("DashboardPageSkeleton"))),
    failure: () => Boolean(walk(tree, node => node.props?.role === "alert")),
    pop(href) { window.location.href = new URL(href, window.location.href).href; listeners.get("popstate")(); },
  };
}

test("all audit tabs and history commit without hiding the mounted reading surface", async () => {
  const app = harness();
  for (const view of ["briefs", "search", "news", "evidence", "stories", "coverage"]) {
    const transition = app.navigate(`/audit?view=${view}`);
    assert.equal(app.imports.length, 0, "same-room selection must not await room loading");
    app.render();
    assert.deepEqual(app.view(), { room: "audit", auditView: view });
    assert.equal(app.hidden(), false);
    assert.equal(app.skeleton(), false);
    await transition;
    app.imports.length = 0;
  }
  const count = app.writes.length;
  for (const view of ["stories", "evidence", "stories", "coverage"]) {
    app.pop(`/audit?view=${view}`);
    assert.equal(app.imports.length, 0);
    app.render();
    assert.equal(app.view().auditView, view);
    assert.equal(app.hidden(), false);
    assert.equal(app.skeleton(), false);
    app.imports.length = 0;
  }
  assert.equal(app.writes.length, count, "history traversal must not push a second entry");
});

test("same-room clicks and history supersede both success and failure of a pending room import", async () => {
  for (const reject of [false, true]) for (const history of [false, true]) {
    const app = harness();
    app.deferred.add("../_views/StatusView");
    const obsolete = app.navigate("/admin/ai-usage");
    app.render();
    assert.equal(app.hidden(), true);
    assert.equal(app.skeleton(), true);
    if (history) app.pop("/audit?view=briefs");
    else await app.navigate("/audit?view=briefs");
    app.render();
    assert.equal(app.hidden(), false);
    assert.equal(app.view().auditView, "briefs");
    if (reject) app.pending[0].reject(new Error("offline"));
    else app.pending[0].resolve({ default: "StatusView" });
    await obsolete;
    app.render();
    assert.equal(app.view().auditView, "briefs");
    assert.equal(app.hidden(), false);
    assert.equal(app.failure(), false);
  }
});

test("active cross-room failure restores content and a later retry succeeds", async () => {
  const app = harness();
  app.deferred.add("../_views/StatusView");
  const failed = app.navigate("/admin/ai-usage");
  app.pending[0].reject(new Error("offline"));
  await failed; app.render();
  assert.equal(app.view().room, "audit");
  assert.equal(app.hidden(), false);
  assert.equal(app.failure(), true);
  app.deferred.clear();
  await app.navigate("/admin/ai-usage"); app.render();
  assert.equal(app.view().room, "status");
  assert.equal(app.hidden(), false);
  assert.equal(app.failure(), false);
});
