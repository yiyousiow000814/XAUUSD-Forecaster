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
const overviewPath = fileURLToPath(new URL("../app/_components/OverviewNews.tsx", import.meta.url));
const resourcesPath = fileURLToPath(new URL("../app/_lib/dashboard-resource.ts", import.meta.url));
const temporaryRoot = mkdtempSync(join(tmpdir(), "aurum-audit-content-"));
test.after(() => rmSync(temporaryRoot, { recursive: true, force: true }));

const resourceUrls = ["/api/status", "/api/audit", "/api/audit-briefs", "/api/audit-stories"];
const built = await build({
  bundle: true, write: false, platform: "node", format: "esm", jsx: "automatic",
  define: {__AURUM_DEPLOYMENT__: JSON.stringify({is_preview: false})},
  nodePaths: [join(dependencyPackage, "..", "node_modules")],
  banner: { js: "import {createRequire} from 'node:module'; const require=createRequire(import.meta.url);" },
  stdin: {
    resolveDir: fileURLToPath(new URL("..", import.meta.url)), loader: "tsx",
    contents: `import React from 'react';
      import {renderToStaticMarkup} from 'react-dom/server';
      import AuditView, {NewsRow, StoryCard} from ${JSON.stringify(viewPath)};
      import PreviewBanner from ${JSON.stringify(fileURLToPath(new URL("../app/_components/PreviewBanner.tsx", import.meta.url)))};
      import LiveRoomView from ${JSON.stringify(fileURLToPath(new URL('../app/_views/LiveRoomView.tsx', import.meta.url)))};
      import {OverviewCards,validOverviewBriefs,validOverviewEvents} from ${JSON.stringify(fileURLToPath(new URL('../app/_components/OverviewNews.tsx', import.meta.url)))};
      export {validOverviewBriefs,validOverviewEvents};
      export function renderOverview(props) {return renderToStaticMarkup(React.createElement(OverviewCards,props));}
      import StatusView from ${JSON.stringify(fileURLToPath(new URL("../app/_views/StatusView.tsx", import.meta.url)))};
      import {clearDashboardResource,updateDashboardResource} from ${JSON.stringify(resourcesPath)};
      export function renderPreview(payload) { updateDashboardResource("/api/status",()=>payload); return renderToStaticMarkup(React.createElement(PreviewBanner)); }
      export function renderLive(payload) { updateDashboardResource("/api/status",()=>payload); return renderToStaticMarkup(React.createElement(LiveRoomView)); }
      export function renderStory(story,expanded=false) {return renderToStaticMarkup(React.createElement(StoryCard,{story,expanded}));}
      export function renderNews(row) { return renderToStaticMarkup(React.createElement(NewsRow,{row})); }
      export function renderStatus(payload) { return renderToStaticMarkup(React.createElement(StatusView,{initialPayload:payload})); }
      export function render(view, resources) {
        for (const url of ${JSON.stringify(resourceUrls)}) clearDashboardResource(url);
        for (const [url,body] of Object.entries(resources)) updateDashboardResource(url,()=>body);
        return renderToStaticMarkup(React.createElement(AuditView,{initialView:view}));
      }`,
  },
});
const renderedModule = join(temporaryRoot, "audit.mjs");
writeFileSync(renderedModule, built.outputFiles[0].contents);
const { render, renderNews, renderStatus, renderLive, renderPreview, renderOverview, renderStory, validOverviewBriefs, validOverviewEvents } = await import(pathToFileURL(renderedModule).href);

test("article row expands publisher provenance with one coherent public label", () => {
  const row = {headline: "政策会议展望", emerging_topic_zh: "政策会议展望", event_type: "macro_preview",
    source: "google_news_fed_rates", source_item_id: "one", category: "利率/Fed",
    model_visibility: "MODEL_INELIGIBLE", content_status: "FULL_TEXT", content_characters: 500,
    annotation_status: "READY", summary_zh: "决议尚未公布", body: "Readable original",
    collector_first_seen_time: "2026-09-12T08:00:00Z", syndicated_source_count: 2,
    syndicated_sources: [{source: "wire", source_item_id: "one", link: "https://publisher.test/article", collector_first_seen_time: "2026-09-12T08:00:00Z"},
      {source: "copy", source_item_id: "two", link: "https://reprint.test/article", collector_first_seen_time: "2026-09-12T09:00:00Z", source_text_incomplete: true}]};
  const original = JSON.stringify(row);
  const html = renderNews(row);
  assert.match(html, /2 个转载来源/);
  assert.match(html, /publisher\.test/);
  assert.match(html, /reprint\.test/);
  assert.match(html, /原文部分可读/);
  assert.match(html, /决议尚未公布/);
  assert.doesNotMatch(html, /macro_preview/);
  assert.equal(JSON.stringify(row), original);
  assert.doesNotThrow(() => renderNews({...row, syndicated_sources: [{source: "invalid", source_item_id: "bad", link: "not a url"}]}));
});
const generatedAt = "2026-09-06T11:00:00Z";
const baseline = {
  "/api/status": {generated_at: "2026-09-06T12:00:00Z", system: {online: false, market_session: "CLOSED"}, factor_coverage: []},
  "/api/audit": {generated_at: generatedAt},
};
const details = {
  briefs: {projection_contract: "audit-detail-source-v1", generated_at: generatedAt, daily_news_briefs: []},
  stories: {projection_contract: "audit-detail-source-v1", generated_at: generatedAt, storylines: []},
};

function selectedBody(html) {
  return html.slice(html.indexOf("</select></label>") + "</select></label>".length, html.indexOf('<footer class="audit-footer">'));
}

test("every Audit view exposes selected content or a visible initial read state", () => {
  for (const view of ["briefs", "search", "news", "evidence", "stories", "coverage"]) {
    const body = selectedBody(render(view, baseline));
    assert.match(body, />[^<]*[\p{L}\p{N}][^<]*</u, `${view} must not be blank`);
    if (view in details) assert.match(body, /role="status"/, `${view} idle must be visible pending state`);
  }
});

test("successful empty briefs and coverage have visible empty states", () => {
  for (const view of ["briefs", "coverage"]) {
    const body = selectedBody(render(view, {...baseline, ...(details[view] ? {[`/api/audit-${view}`]: details[view]} : {})}));
    if (view === "coverage") assert.match(body, /role="status"/);
    assert.match(body, />[^<]*[\p{L}\p{N}][^<]*</u);
  }
});

test("audit footer uses its resource timestamp independently of status heartbeat", () => {
  const footer = html => html.slice(html.indexOf('<footer class="audit-footer">'));
  for (const view of ["briefs", "coverage"]) {
    const status = {...baseline["/api/status"], preview: {
      is_preview: true,
      branch_snapshot: {generated_at: generatedAt, status_paths: ["factor_coverage"]},
    }};
    const resources = {...baseline, "/api/status": status, "/api/audit-briefs": details.briefs};
    const before = footer(render(view, resources));
    const after = footer(render(view, {...resources, "/api/status": {...status, generated_at:"2026-09-06T13:00:00Z"}}));
    assert.equal(before, after, `${view} must retain its own source timestamp`);
    assert.match(before, /19:00:00/);
    assert.doesNotMatch(before, /2026-09-06T/);
  }
});

test("accepted detail content is independent of missing or compact status data", () => {
  const briefs = {...details.briefs, daily_news_briefs: [{brief_date:"2026-09-06", generated_at:generatedAt, phase:"FINAL", model_version:"gemma", brief:{overview:"Retained daily brief", items:[]}}]};
  for (const status of [null, baseline["/api/status"]]) {
    const html = render("briefs", {"/api/status": status, "/api/audit-briefs": briefs});
    assert.match(selectedBody(html), /Retained daily brief/);
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
  // clock. SSR renders actual state updates from the request/effect boundary;
  // this focused hook harness is not a browser or deployed acceptance run.
  const refreshPath = fileURLToPath(new URL("../app/_lib/dashboard-refresh.ts", import.meta.url));
  const effectBuild = await build({
    bundle: true, write: false, platform: "node", format: "esm", jsx: "automatic",
    define: {__AURUM_DEPLOYMENT__: "globalThis.__auditDeployment"},
    nodePaths: [join(dependencyPackage, "..", "node_modules")],
    banner: {js: "import {createRequire} from 'node:module'; const require=createRequire(import.meta.url);"},
    plugins: [{name: "audit-effect-boundary", setup(builder) {
      builder.onResolve({filter: /^react$/}, args => [viewPath,overviewPath].includes(args.importer)
        ? {path: "react", namespace: "audit-effects"} : null);
      builder.onLoad({filter: /.*/, namespace: "audit-effects"}, () => ({
        contents: `export * from ${JSON.stringify(require.resolve("react"))};
          export function useState(initial) {
            const values = globalThis.__auditStateValues;
            const index = globalThis.__auditStateIndex++;
            if (!(index in values)) values[index] = typeof initial === 'function' ? initial() : initial;
            return [values[index], next => {
              values[index] = typeof next === 'function' ? next(values[index]) : next;
            }];
          }
          export function useEffect(effect) { globalThis.__auditEffects.push(effect); }`,
        loader: "js", resolveDir: fileURLToPath(new URL("..", import.meta.url)),
      }));
      builder.onResolve({filter: /^\.\.\/_lib\/dashboard-refresh$/}, args => [viewPath,overviewPath].includes(args.importer)
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
        import OverviewNews from ${JSON.stringify(overviewPath)};
        import {clearDashboardResource,updateDashboardResource} from ${JSON.stringify(resourcesPath)};
        export function renderState(view) {
          globalThis.__auditStateIndex = 0;
          globalThis.__auditEffects = [];
          return renderToStaticMarkup(React.createElement(view === "overview" ? OverviewNews : AuditView,{initialView:view,snapshot:globalThis.__auditDeployment.is_preview}));
        }
        export function mountEffects(view, resources) {
          for (const url of ${JSON.stringify(resourceUrls)}) clearDashboardResource(url);
          for (const [url,body] of Object.entries(resources)) updateDashboardResource(url,()=>body);
          globalThis.__auditStateValues = [];
          renderState(view);
          return globalThis.__auditEffects.map(effect => effect());
        }`,
    },
  });
  const effectModule = join(temporaryRoot, "audit-effects.mjs");
  writeFileSync(effectModule, effectBuild.outputFiles[0].contents);
  const {mountEffects, renderState} = await import(pathToFileURL(effectModule).href);
  const names = ["window", "document", "navigator", "fetch", "__auditEffects", "__auditSchedules", "__auditTimerKey", "__auditDeployment", "__auditStateValues", "__auditStateIndex"];
  const original = new Map(names.map(name => [name, Object.getOwnPropertyDescriptor(globalThis, name)]));
  const originalNow = Date.now;
  try {
    for (const preview of [false, true]) for (const statusMissing of [false, true]) for (const failure of [false, true]) for (const view of [...Object.keys(details), "overview"]) {
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
      globalThis.fetch = async url => {
        requests.push(String(url));
        return failure
          ? Response.json({error: "等待审计详情", availability: "UNAVAILABLE_IN_BUILD_SNAPSHOT"}, {status: 503})
          : Response.json(view === "overview" ? String(url).includes("news-evidence") ? {mode:"eligible",snapshot_id:"snapshot",items:[]} : details.briefs : details[view]);
      };
      globalThis.__auditEffects = [];
      globalThis.__auditSchedules = [];
      globalThis.__auditTimerKey = null;
      globalThis.__auditDeployment = {is_preview: preview};
      const cleanups = mountEffects(view, {...baseline,
        "/api/news-evidence?mode=eligible&page=1&limit=3": null,
        "/api/status": statusMissing ? null : {...baseline["/api/status"], preview: {is_preview: preview}},
        [`/api/audit-${view}`]: failure ? null : details[view],
      });
      try {
        if (view === "overview") {
          const schedules = globalThis.__auditSchedules;
          assert.equal(schedules.length,2);
          for (const schedule of schedules) {
            assert.equal(schedule.mode,preview ? "build-snapshot" : "current");
            assert.equal(schedule.interval,300_000);
          }
          for (const timer of [...timers.values()]) if (timer.key) timer.callback();
          await new Promise(resolve => setImmediate(resolve));
          assert.equal(requests.length,2);
          assert.match(renderState(view), failure ? /暂时无法读取/ : /暂无可用事件/);
          now += 300_001;
          for (const timer of intervals.values()) timer.callback();
          await new Promise(resolve => setImmediate(resolve));
          assert.equal(requests.length,preview ? 2 : 4);
          // The same resource can recover after a rejected response.
          globalThis.fetch = async url => Response.json(String(url).includes("news-evidence")
            ? {mode:"eligible",snapshot_id:"snapshot",items:[]} : details.briefs);
          if (!preview && failure) {
            now += 300_001;
            for (const timer of intervals.values()) timer.callback();
            await new Promise(resolve => setImmediate(resolve));
            const recovered=renderState(view);
            assert.match(recovered,/暂无可用事件/);
            assert.doesNotMatch(recovered,/暂时无法读取/);
          }
        } else {
        const key = `audit-detail:${view}`;
        const schedule = globalThis.__auditSchedules.find(item => item.key === key);
        assert.equal(schedule.mode, preview ? "build-snapshot" : "current");
        assert.equal(schedule.interval, 60_000);
        for (const timer of timers.values()) if (timer.key === key) timer.callback();
        await new Promise(resolve => setImmediate(resolve));
        assert.equal(schedule.initial, 1, "both modes retain initial detail admission");
        now += 120_001;
        for (const timer of intervals.values()) if (timer.key === key) timer.callback();
        await new Promise(resolve => setImmediate(resolve));
        assert.equal(schedule.polls, preview ? 0 : 1);
        const expectedRequests = (failure ? 1 : 0) + (preview ? 0 : 1);
        assert.deepEqual(requests, Array(expectedRequests).fill(`/api/audit-${view}`));
        if (failure) {
          const html = renderState(view);
          assert.match(html, /role="alert"/);
          assert.match(html, /type="button"[^>]*>重试/);
          assert.doesNotMatch(html, /页面会自动重试/);
          if (preview) {
            assert.match(html, /构建快照不会自动刷新/);
            assert.match(html, /可手动重读当前快照/);
            assert.match(html, /资料更新需要新构建/);
          } else {
            assert.match(html, /可稍后重新载入页面/);
            assert.doesNotMatch(html, /资料更新需要新构建/);
          }
        }
        }
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

test("brief prose hides packet refs in every field without rewriting evidence", () => {
  const brief = {
    title: "每日简报 [E01]", overview: "黄金表现 [E04, E14, E23]",
    drivers: ["驱动一 [E05，E21]", "驱动二［E06、E30］"],
    watch_next: "关注 CPI [E04]",
    items: [{headline: "重点标题 [E07]", summary: "变化25 [bp]，保留型号E04，参考 [E07, E10, E26]。",
      evidence_ids: ["real-evidence-7", "real-evidence-10", "real-evidence-26"]}],
  };
  const original = JSON.stringify(brief);
  const html = render("briefs", {...baseline, "/api/audit-briefs": {
    ...details.briefs,
    daily_news_briefs: [{brief_date: "2026-09-06", revision_number: 21,
      cutoff_at: generatedAt, generated_at: generatedAt, model_version: "gemma", prompt_version: "retained",
      phase: "FINAL", reviewed_items: 407, brief}],
    daily_news_brief_summary: {brief_date: "2026-09-06", phase: "FINAL", reviewed_items: 407, pending_items: 0},
  }});
  assert.doesNotMatch(html, /[\[［]E\d/);
  for (const text of ["黄金表现", "驱动一", "驱动二", "关注 CPI", "重点标题", "25 [bp]", "型号E04", "3 份来源证据"]) {
    assert.ok(html.includes(text), text);
  }
  assert.equal(JSON.stringify(brief), original);
});


test("overview links to current events without asserting omitted status counts", () => {
  const html = renderLive({generated_at: "2026-09-26T08:00:00Z", system: {online:false,quote_age_seconds:null}, counts:{}, sources:{}, news_metrics:{articles:{received:10,stored_revisions:12},events:{independent:0,currently_model_eligible:0}}});
  assert.match(html, /href="\/audit\?view=evidence"/);
  assert.match(html, /<h2>[\s\S]*?当前事件<\/h2>/);
  assert.doesNotMatch(html, /0.*个独立事件/);
});


test("preview hydration starts identically even after another server route primes status", () => {
  assert.equal(renderPreview({}), "");
  assert.equal(renderPreview({preview:{is_preview:true,branch:"review",commit_sha:"abc12345"}}), "");
  assert.equal(renderPreview({}), "");
});


test("overview retains only three original headlines from latest date and revision", () => {
  const row = (date,rev,title) => ({brief_date:date,revision_number:rev,model_version:"test",brief:{items:Array.from({length:4},(_,i)=>({headline:`${title}${i} [E01]`,summary:"LONG SUMMARY",evidence_ids:["E01"]}))}});
  const data = {daily_news_briefs:[row("2026-09-25",99,"OLDER"),row("2026-09-26",1,"OLDREV"),row("2026-09-26",2,"LATEST")]};
  const events = {mode:"eligible",snapshot_id:"snapshot",items:Array.from({length:4},(_,i)=>({event_key:String(i),canonical_headline:`EVENT${i}`,broad_model_eligible:true}))};
  assert.equal(validOverviewBriefs(data),true);
  assert.equal(validOverviewEvents(events),true);
  assert.equal(validOverviewEvents({...events,mode:"unseen"}),false);
  assert.equal(validOverviewEvents({...events,items:[{...events.items[0],broad_model_eligible:false}]}),false);
  assert.equal(validOverviewBriefs({daily_news_briefs:[{...data.daily_news_briefs[0],brief_date:null}]}),false);
  const before=JSON.stringify(data);
  const html=renderOverview({briefs:{data,error:null},events:{data:events,error:null},snapshot:true});
  assert.match(html,/<time dateTime="2026-09-26" title="2026-09-26">09\/26<\/time>/i);
  assert.match(html,/LATEST2/); assert.match(html,/EVENT2/);
  assert.doesNotMatch(html,/LATEST3|EVENT3|OLDER|OLDREV|LONG SUMMARY|E01|今日/);
  assert.match(html,/预览快照/);
  assert.equal((html.match(/<li>/g)||[]).length,6);
  assert.match(html,/href="\/audit\?view=briefs"/);
  assert.match(html,/href="\/audit\?view=evidence"/);
  assert.equal(JSON.stringify(data),before);
});

test("overview distinguishes loading, confirmed empty, failure and retained content", () => {
  const empty={daily_news_briefs:[],projection_contract:"audit-detail-source-v1"};
  assert.equal(validOverviewBriefs(empty),true);
  assert.equal(validOverviewBriefs({daily_news_briefs:[]}),false);
  const pending={data:null,error:null};
  assert.match(renderOverview({briefs:pending,events:pending}),/正在读取/);
  const html=renderOverview({briefs:{data:empty,error:null},events:{data:{items:[]},error:null}});
  assert.match(html,/简报尚未生成/);assert.match(html,/暂无可用事件/);assert.doesNotMatch(html,/正在读取|重试/);
  const failed=renderOverview({briefs:{data:null,error:new Error("provider")},events:{data:{items:[{event_key:"one",canonical_headline:"KEPT"}]},error:new Error("refresh")}});
  assert.match(failed,/暂时无法读取/);assert.match(failed,/显示上次内容/);assert.match(failed,/KEPT/);
  assert.equal((failed.match(/>重试</g)||[]).length,2);
});


test("story cards default to latest summary and reveal a newest-first complete chain", () => {
  const event=(id,date,relation)=>({event_key:id,event_time:date,headline:`headline-${id}`,relation,first_seen:date,source_published_time:date,collector_first_seen_time:date,evidence_documents:1,independent_organizations:1});
  const story={storyline_id:"chain",title:"Story title",state:"REPORTED",event_count:3,latest_change:"Latest development",last_updated:"2026-09-27T01:00:00Z",timeline:[event("old","2026-09-25T01:00:00Z","STARTS"),event("new","2026-09-27T01:00:00Z","FOLLOWED_BY"),event("middle","2026-09-26T01:00:00Z","CONFIRMS")],evidence_document_count:3,independent_organization_count:2,coverage_count:1,coverage_total:2,covered_roles:[],missing_roles:[],market_reactions:[],commentary:[],background:[]};
  const original=JSON.stringify(story);
  const closed=renderStory(story);
  assert.match(closed,/Latest development/);assert.match(closed,/aria-expanded="false"/);
  assert.match(closed,/展开故事链/);assert.doesNotMatch(closed,/headline-old|headline-new|story-detail|证据覆盖/);
  const open=renderStory(story,true);
  assert.match(open,/aria-expanded="true"/);assert.match(open,/收起故事链/);
  assert.match(open,/最新在前/);assert.match(open,/证据覆盖/);
  assert.ok(open.indexOf('headline-new')<open.indexOf('headline-middle'));
  assert.ok(open.indexOf('headline-middle')<open.indexOf('headline-old'));
  assert.match(open,/首次进展/);assert.match(open,/随后发生/);
  assert.equal(JSON.stringify(story),original);
  assert.equal(renderStory(story),closed);
  assert.match(renderStory({...story,event_count:17},true),/当前仅载入 3 \/ 17 条进展/);
  assert.doesNotMatch(open,/完整记录尚未同步/);
  const fallback={...story,timeline:[event("unknown",null,"STARTS"),{...event("fallback",null,"FOLLOWED_BY"),source_published_time:"2026-09-28T00:00:00Z"},story.timeline[1]]};
  const fallbackHtml=renderStory(fallback,true);
  assert.ok(fallbackHtml.indexOf('headline-fallback')<fallbackHtml.indexOf('headline-new'));
  assert.ok(fallbackHtml.indexOf('headline-new')<fallbackHtml.indexOf('headline-unknown'));
});


test("branch coverage keeps compact provenance without a full-width snapshot notice", () => {
  const status = {...baseline["/api/status"], preview: {
    is_preview: true,
    branch_snapshot: {generated_at: generatedAt, status_paths: ["factor_coverage"]},
  }};
  const html = render("coverage", {...baseline, "/api/status": status});
  assert.doesNotMatch(html, /分支构建快照|此页使用分支重新计算/);
  assert.match(html, /<small>分支快照<\/small>/);
  assert.match(html, /<option value="coverage"[^>]*>[^<]*分支快照<\/option>/);
  assert.match(html, /本页没有覆盖记录/);
  assert.match(html, /所选资源时间[^<]*19:00:00/);
});
