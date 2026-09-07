import assert from "node:assert/strict";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { createRequire } from "node:module";
import { fileURLToPath, pathToFileURL } from "node:url";
import test from "node:test";

const dependencyPackage = process.env.AURUM_TEST_DEPENDENCY_PACKAGE
  ?? fileURLToPath(new URL("../package.json", import.meta.url));
const require = createRequire(dependencyPackage);
const { build } = require("esbuild");
const viewPath = fileURLToPath(new URL("../app/_views/AuditView.tsx", import.meta.url));
const resourcesPath = fileURLToPath(new URL("../app/_lib/dashboard-resource.ts", import.meta.url));
const temporaryRoot = mkdtempSync(join(tmpdir(), "aurum-audit-content-"));
test.after(() => rmSync(temporaryRoot, { recursive: true, force: true }));

const resourceUrls = ["/api/status", "/api/audit", "/api/learning", "/api/audit-briefs", "/api/audit-stories", "/api/audit-decisions"];
const built = await build({
  bundle: true, write: false, platform: "node", format: "esm", jsx: "automatic",
  define: {__AURUM_DEPLOYMENT__: JSON.stringify({is_preview: false})},
  nodePaths: [join(dependencyPackage, "..", "node_modules")],
  banner: { js: "import {createRequire} from 'node:module'; const require=createRequire(import.meta.url);" },
  stdin: {
    resolveDir: fileURLToPath(new URL("..", import.meta.url)), loader: "tsx",
    contents: `import React from 'react';
      import {renderToStaticMarkup} from 'react-dom/server';
      import AuditView from ${JSON.stringify(viewPath)};
      import {clearDashboardResource,updateDashboardResource} from ${JSON.stringify(resourcesPath)};
      export function render(view, resources) {
        for (const url of ${JSON.stringify(resourceUrls)}) clearDashboardResource(url);
        for (const [url,body] of Object.entries(resources)) updateDashboardResource(url,()=>body);
        return renderToStaticMarkup(React.createElement(AuditView,{initialView:view}));
      }`,
  },
});
const renderedModule = join(temporaryRoot, "audit.mjs");
writeFileSync(renderedModule, built.outputFiles[0].contents);
const { render } = await import(pathToFileURL(renderedModule).href);
const generatedAt = "2026-09-06T11:00:00Z";
const baseline = {
  "/api/status": {generated_at: "2026-09-06T12:00:00Z", system: {online: false, market_session: "CLOSED"}, factor_coverage: []},
  "/api/audit": {generated_at: generatedAt},
  "/api/learning": {generated_at: generatedAt, learning_curves: {models: []}},
};
const details = {
  briefs: {projection_contract: "audit-detail-source-v1", generated_at: generatedAt, daily_news_briefs: []},
  stories: {projection_contract: "audit-detail-source-v1", generated_at: generatedAt, storylines: []},
  decisions: {projection_contract: "audit-detail-source-v1", generated_at: generatedAt, recent_decisions: []},
};

function selectedBody(html) {
  return html.slice(html.indexOf("</select></label>") + "</select></label>".length, html.indexOf('<footer class="audit-footer">'));
}

test("every Audit view exposes selected content or a visible initial read state", () => {
  for (const view of ["briefs", "search", "news", "evidence", "stories", "decisions", "league", "coverage"]) {
    const body = selectedBody(render(view, baseline));
    assert.match(body, />[^<]*[\p{L}\p{N}][^<]*</u, `${view} must not be blank`);
    if (view in details) assert.match(body, /role="status"/, `${view} idle must be visible pending state`);
  }
});

test("successful empty decisions and coverage have visible empty states", () => {
  for (const view of ["decisions", "coverage"]) {
    const body = selectedBody(render(view, {...baseline, ...(details[view] ? {[`/api/audit-${view}`]: details[view]} : {})}));
    assert.match(body, /role="status"/);
    assert.match(body, />[^<]*[\p{L}\p{N}][^<]*</u);
  }
});

test("audit footer uses its resource timestamp independently of status heartbeat", () => {
  const footer = html => html.slice(html.indexOf('<footer class="audit-footer">'));
  for (const view of ["decisions", "coverage"]) {
    const status = {...baseline["/api/status"], preview: {
      is_preview: true,
      branch_snapshot: {generated_at: generatedAt, status_paths: ["factor_coverage"]},
    }};
    const resources = {...baseline, "/api/status": status, "/api/audit-decisions": details.decisions};
    const before = footer(render(view, resources));
    const after = footer(render(view, {...resources, "/api/status": {...status, generated_at:"2026-09-06T13:00:00Z"}}));
    assert.equal(before, after, `${view} must retain its own source timestamp`);
    assert.match(before, /19:00:00/);
    assert.doesNotMatch(before, /2026-09-06T/);
  }
});

test("accepted detail content is independent of missing or compact status data", () => {
  const decisions = {generated_at: generatedAt, recent_decisions: [{decision_id:"retained-decision", decision_time:generatedAt, bid:5000, ask:5001, predictions:[]}]};
  for (const status of [null, {...baseline["/api/status"], recent_decisions:[]}]) {
    const html = render("decisions", {"/api/status": status, "/api/audit-decisions": decisions});
    assert.match(selectedBody(html), /5000\.00/);
    assert.match(selectedBody(html), /5001\.00/);
  }
});

test("invalid cached detail envelopes remain pending rather than becoming successful empty results", () => {
  for (const view of Object.keys(details)) {
    const html = render(view, {...baseline, [`/api/audit-${view}`]: {generated_at: generatedAt}});
    assert.match(selectedBody(html), /is-loading/);
    assert.match(selectedBody(html), /role="status"/);
  }
});

test("actual Audit detail effects poll current data but never immutable Preview snapshots", async () => {
  // Execute the view's actual effect and scheduler with a visible, non-WebDriver
  // clock. React SSR collects effects here; no browser or production URL opens.
  const refreshPath = fileURLToPath(new URL("../app/_lib/dashboard-refresh.ts", import.meta.url));
  const effectBuild = await build({
    bundle: true, write: false, platform: "node", format: "esm", jsx: "automatic",
    define: {__AURUM_DEPLOYMENT__: "globalThis.__auditDeployment"},
    nodePaths: [join(dependencyPackage, "..", "node_modules")],
    banner: {js: "import {createRequire} from 'node:module'; const require=createRequire(import.meta.url);"},
    plugins: [{name: "audit-effect-boundary", setup(builder) {
      builder.onResolve({filter: /^react$/}, args => args.importer === viewPath
        ? {path: "react", namespace: "audit-effects"} : null);
      builder.onLoad({filter: /.*/, namespace: "audit-effects"}, () => ({
        contents: `export * from ${JSON.stringify(require.resolve("react"))};
          export function useEffect(effect) { globalThis.__auditEffects.push(effect); }`,
        loader: "js", resolveDir: fileURLToPath(new URL("..", import.meta.url)),
      }));
      builder.onResolve({filter: /^\.\.\/_lib\/dashboard-refresh$/}, args => args.importer === viewPath
        ? {path: "refresh", namespace: "audit-refresh"} : null);
      builder.onLoad({filter: /.*/, namespace: "audit-refresh"}, () => ({
        contents: `export * from ${JSON.stringify(refreshPath)};
          import {scheduleDashboardRefresh as schedule} from ${JSON.stringify(refreshPath)};
          export function scheduleDashboardRefresh(initial, poll, interval, mode, key) {
            const item = {initial: 0, polls: 0, key, mode, interval};
            globalThis.__auditSchedules.push(item);
            globalThis.__auditTimerKey = key;
            const cleanup = schedule(() => {item.initial++; initial();},
              () => {item.polls++; poll();}, interval, mode, key);
            globalThis.__auditTimerKey = null;
            return cleanup;
          }`,
        loader: "js", resolveDir: fileURLToPath(new URL("..", import.meta.url)),
      }));
    }}],
    stdin: {
      resolveDir: fileURLToPath(new URL("..", import.meta.url)), loader: "tsx",
      contents: `import React from 'react'; import {renderToStaticMarkup} from 'react-dom/server';
        import AuditView from ${JSON.stringify(viewPath)};
        import {clearDashboardResource,updateDashboardResource} from ${JSON.stringify(resourcesPath)};
        export function mountEffects(view, resources) {
          for (const url of ${JSON.stringify(resourceUrls)}) clearDashboardResource(url);
          for (const [url,body] of Object.entries(resources)) updateDashboardResource(url,()=>body);
          renderToStaticMarkup(React.createElement(AuditView,{initialView:view}));
          return globalThis.__auditEffects.map(effect => effect());
        }`,
    },
  });
  const effectModule = join(temporaryRoot, "audit-effects.mjs");
  writeFileSync(effectModule, effectBuild.outputFiles[0].contents);
  const {mountEffects} = await import(pathToFileURL(effectModule).href);
  const names = ["window", "document", "navigator", "fetch", "__auditEffects", "__auditSchedules", "__auditTimerKey", "__auditDeployment"];
  const original = new Map(names.map(name => [name, Object.getOwnPropertyDescriptor(globalThis, name)]));
  const originalNow = Date.now;
  try {
    for (const preview of [false, true]) for (const statusMissing of [false, true]) for (const view of Object.keys(details)) {
      const timers = new Map();
      const intervals = new Map();
      const storage = new Map();
      const requests = [];
      let timerId = 0;
      let now = 1_800_000_000_000;
      Date.now = () => now;
      Object.defineProperty(globalThis, "navigator", {configurable: true, value: {webdriver: false}});
      globalThis.document = {visibilityState: "visible", addEventListener() {}, removeEventListener() {}};
      globalThis.window = {
        setTimeout(callback) {const id = ++timerId; timers.set(id, {callback, key: globalThis.__auditTimerKey}); return id;},
        clearTimeout(id) {timers.delete(id);},
        setInterval(callback) {const id = ++timerId; intervals.set(id, {callback, key: globalThis.__auditTimerKey}); return id;},
        clearInterval(id) {intervals.delete(id);},
        localStorage: {getItem: key => storage.get(key), setItem: (key, value) => storage.set(key, value)},
      };
      globalThis.fetch = async url => {requests.push(String(url)); return Response.json(details[view]);};
      globalThis.__auditEffects = [];
      globalThis.__auditSchedules = [];
      globalThis.__auditTimerKey = null;
      globalThis.__auditDeployment = {is_preview: preview};
      const cleanups = mountEffects(view, {...baseline,
        "/api/status": statusMissing ? null : {...baseline["/api/status"], preview: {is_preview: preview}},
        [`/api/audit-${view}`]: details[view],
      });
      try {
        const key = `audit-detail:${view}`;
        const schedule = globalThis.__auditSchedules.find(item => item.key === key);
        assert.equal(schedule.mode, preview ? "build-snapshot" : "current");
        assert.equal(schedule.interval, 60_000);
        for (const timer of timers.values()) if (timer.key === key) timer.callback();
        assert.equal(schedule.initial, 1, "both modes retain initial detail admission");
        now += 120_001;
        for (const timer of intervals.values()) if (timer.key === key) timer.callback();
        await new Promise(resolve => setImmediate(resolve));
        assert.equal(schedule.polls, preview ? 0 : 1);
        assert.deepEqual(requests, preview ? [] : [`/api/audit-${view}`]);
      } finally {
        for (const cleanup of cleanups.reverse()) if (typeof cleanup === "function") cleanup();
      }
      assert.equal(intervals.size, 0, "all task-created polling timers are closed");
    }
  } finally {
    Date.now = originalNow;
    for (const [name, descriptor] of original) {
      if (descriptor) Object.defineProperty(globalThis, name, descriptor);
      else delete globalThis[name];
    }
  }
});
