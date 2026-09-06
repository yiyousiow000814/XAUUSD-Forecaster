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
  briefs: {generated_at: generatedAt, daily_news_briefs: []},
  stories: {generated_at: generatedAt, storylines: []},
  decisions: {generated_at: generatedAt, recent_decisions: []},
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
  const resources = {...baseline, "/api/audit-decisions": details.decisions};
  const footer = html => html.slice(html.indexOf('<footer class="audit-footer">'));
  const before = footer(render("decisions", resources));
  const after = footer(render("decisions", {...resources, "/api/status": {...baseline["/api/status"], generated_at:"2026-09-06T13:00:00Z"}}));
  assert.equal(before, after);
  assert.match(before, /19:00:00/);
  assert.doesNotMatch(before, /2026-09-06T/);
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
