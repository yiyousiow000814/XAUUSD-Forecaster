import assert from "node:assert/strict";
import { readFileSync, readdirSync } from "node:fs";
import test from "node:test";

const workerUrl = new URL("../dist/server/index.js", import.meta.url);
workerUrl.searchParams.set("test", `${process.pid}-${Date.now()}`);
const { default: worker } = await import(workerUrl.href);
const { applyFreshness } = await import("../app/api/status/freshness.js");

async function render(path) {
  return worker.fetch(
    new Request(`http://localhost${path}`, { headers: { accept: "text/html" } }),
    { ASSETS: { fetch: async () => new Response("Not found", { status: 404 }) } },
    { waitUntil() {}, passThroughOnException() {} },
  );
}

async function renderSettled(path, marker) {
  let response = await render(path);
  let html = await response.text();
  if (!marker.test(html) && html.includes("app-view-loading")) {
    await new Promise(resolve => setTimeout(resolve, 25));
    response = await render(path);
    html = await response.text();
  }
  return { response, html };
}

test("renders the live room with an audit-page navigation button", async () => {
  const response = await render("/");
  assert.equal(response.status, 200);
  const html = await response.text();
  assert.match(html, /Aurum Signal Room/);
  assert.match(html, /新闻、决策与结果/);
  assert.match(html, /新闻 \/ 结果/);
  assert.match(html, /系统状态/);
  assert.doesNotMatch(html, /next\/link|rel="prefetch"/);
  assert.doesNotMatch(html, /codex-preview|react-loading-skeleton/);
});

test("keeps branch Preview identity and blocks writes", async () => {
  const source = readFileSync(new URL("../app/api/_shared/preview.ts", import.meta.url), "utf8");
  const layout = readFileSync(new URL("../app/layout.tsx", import.meta.url), "utf8");
  assert.match(source, /PR Preview 是只读快照/);
  assert.match(source, /X-Aurum-Preview/);
  assert.match(layout, /<PreviewBanner \/>/);

  if (!process.env.WORKERS_CI_BRANCH || process.env.WORKERS_CI_BRANCH === "main") return;
  const builtPreview = readdirSync(new URL("../dist/server/", import.meta.url), {
    recursive: true,
    withFileTypes: true,
  }).filter(entry => entry.isFile() && entry.name.endsWith(".js"))
    .map(entry => readFileSync(`${entry.parentPath}/${entry.name}`, "utf8"))
    .join("\n");
  assert.match(builtPreview, /PREVIEW_SNAPSHOT/);
  assert.match(builtPreview, new RegExp(process.env.WORKERS_CI_BRANCH.replaceAll("/", "\\/")));
});

test("hydrates preview pages from their immutable build snapshot", () => {
  const page = readFileSync(new URL("../app/page.tsx", import.meta.url), "utf8");
  const app = readFileSync(new URL("../app/_components/DashboardApp.tsx", import.meta.url), "utf8");
  const resources = readFileSync(new URL("../app/_lib/dashboard-resource.ts", import.meta.url), "utf8");
  assert.match(page, /function previewResources/);
  assert.match(page, /previewBundle\.status/);
  assert.match(page, /previewBundle\.learning_summary/);
  const vite = readFileSync(new URL("../vite.config.ts", import.meta.url), "utf8");
  const learning = readFileSync(new URL("../build/preview-learning.ts", import.meta.url), "utf8");
  const contract = readFileSync(new URL("../preview-contract.json", import.meta.url), "utf8");
  const previewBuilder = readFileSync(new URL("../../scripts/build_preview_bundle.py", import.meta.url), "utf8");
  assert.match(vite, /compactPreviewLearning/);
  assert.match(vite, /compactPreviewStatus/);
  assert.match(vite, /compactPreviewNewsIndex/);
  assert.match(vite, /delete bundle\.learning/);
  assert.doesNotMatch(learning, /"recent_decisions"/);
  assert.doesNotMatch(learning, /"news_evidence"/);
  assert.match(learning, /items\.slice\(0, PREVIEW_NEWS_PAGE_SIZE\)/);
  assert.match(learning, /totals_scope: "BUILD_SNAPSHOT"/);
  assert.match(learning, /history_resource: market\.history_resource \?\? PREVIEW_RESOURCES\.marketHistory/);
  assert.match(learning, /training_markers: market\.training_markers \?\? \[\]/);
  for (const key of ["news_evidence", "story_event_candidates", "recent_decisions"]) {
    assert.match(contract, new RegExp(`"${key}"`), key);
  }
  assert.match(contract, /"marketHistory": "\/api\/market-history"/);
  assert.doesNotMatch(page, /function previewRoomResources/);
  assert.match(learning, /models\.filter/);
  assert.match(learning, /lifecycle_status === "LATEST"/);
  assert.match(learning, /identity_curves: \[\]/);
  assert.match(learning, /execution_learning:/);
  assert.match(learning, /points: points\.slice\(-48\)/);
  assert.match(learning, /results: results\.slice\(-20\)/);
  assert.match(previewBuilder, /resource=execution-point/);
  assert.match(previewBuilder, /for identity in \("LOT_RIDGE", "EXIT_RIDGE"\)/);
  assert.match(previewBuilder, /resource=curve-overview&cadence=\{cadence\}/);
  assert.match(previewBuilder, /for cadence in \("5m", "30m"\)/);
  assert.match(previewBuilder, /\[\*learning_history, \*execution_history, \*curve_overviews\]/);
  assert.doesNotMatch(page, /auditView === "league"/);
  assert.match(page, /\[PREVIEW_RESOURCES\.status\]: previewBundle\.status/);
  assert.match(app, /primeDashboardResources\(initialResources\);\s*const \[location/);
  assert.match(resources, /DEFAULT_TIMEOUT_MS = 10_000/);
  assert.match(resources, /数据读取超时，页面会自动重试/);
});

test("uses one current-data contract across every dashboard surface", () => {
  const component = readFileSync(new URL("../app/_components/CurrentDataState.tsx", import.meta.url), "utf8");
  const css = readFileSync(new URL("../app/globals.css", import.meta.url), "utf8");
  const statusRoute = readFileSync(new URL("../app/api/status/route.ts", import.meta.url), "utf8");
  const learningRoute = readFileSync(new URL("../app/api/learning/route.ts", import.meta.url), "utf8");

  assert.doesNotMatch(component, /正在同步页面当前指标/);
  assert.doesNotMatch(component, />同步</);
  assert.match(component, /current-metric-placeholder/);
  assert.match(component, /role="progressbar"/);
  assert.match(component, /显示构建快照/);
  assert.match(css, /@keyframes current-data-pulse/);
  assert.match(css, /prefers-reduced-motion:\s*reduce/);

  for (const view of ["AuditView", "LiveRoomView", "StatusView", "HealthView"]) {
    const source = readFileSync(new URL(`../app/_views/${view}.tsx`, import.meta.url), "utf8");
    assert.match(source, /CurrentDataNotice/, `${view} must expose current-data state`);
  }
  const audit = readFileSync(new URL("../app/_views/AuditView.tsx", import.meta.url), "utf8");
  assert.match(audit, /live_oos_model_groups !== undefined\s*\? statusState/);

  const statusD1 = statusRoute.indexOf("dashboard_snapshots WHERE id = ?");
  const statusPreviewFallback = statusRoute.indexOf("if (previewBundle) return previewJson(previewBundle.status)");
  const learningD1 = learningRoute.indexOf("dashboard_snapshots WHERE id = ?");
  const learningPreviewFallback = learningRoute.indexOf("if (previewBundle?.learning_summary)");
  assert.ok(statusD1 >= 0 && statusPreviewFallback > statusD1, "Preview status must prefer current D1 data");
  assert.ok(learningD1 >= 0 && learningPreviewFallback > learningD1, "Preview learning must prefer current D1 data");
  assert.match(statusRoute, /withPreviewIdentity\(current, previewBundle\.status\)/);
  assert.match(learningRoute, /"X-Aurum-Preview": "read-only-d1-snapshot"/);
});

test("only a current D1 archive may publish the 60-day news total", async () => {
  const { authoritativeNewsTotals } = await import("../app/_lib/news-index-contract.ts");
  const frozen = {
    total: 200, all_total: 200, readable_total: 200,
    parsed_total: 195, model_candidate_total: 14,
    totals_scope: "BUILD_SNAPSHOT",
  };
  assert.equal(authoritativeNewsTotals(frozen), null);
  assert.deepEqual(authoritativeNewsTotals({
    ...frozen,
    total: 1138,
    all_total: 1138,
    readable_total: 1138,
    parsed_total: 1100,
    model_candidate_total: 31,
    totals_scope: "D1_ARCHIVE",
  }), { category: 1138, readable: 1138, parsed: 1100, modelCandidates: 31 });
  assert.equal(authoritativeNewsTotals({
    ...frozen,
    totals_scope: "RECENT_WINDOW",
  }), null);
});

test("keeps every audit collection in compact Preview status", () => {
  const contract = readFileSync(new URL("../preview-contract.json", import.meta.url), "utf8");
  for (const key of [
    "news_evidence", "storylines", "story_event_candidates", "theme_streams",
    "market_reaction_streams", "recent_decisions",
  ]) {
    assert.match(contract, new RegExp(`"${key}"`), key);
  }
  assert.match(contract, /"preview"/);
  assert.match(contract, /"marketHistory": "\/api\/market-history"/);
});

test("falls through to read-only D1 for later Preview news and details", () => {
  const index = readFileSync(new URL("../app/api/news-index/route.ts", import.meta.url), "utf8");
  const detail = readFileSync(new URL("../app/api/news-content/route.ts", import.meta.url), "utf8");
  assert.doesNotMatch(index, /inlinePreviewItems/);
  assert.match(index, /D1 is the source of truth even on the first Preview page/);
  assert.match(index, /if \(previewBundle\) \{\s*return previewJson\(\{ error: "新闻档案暂时不可用，请稍后重试" \}, 503\)/);
  assert.match(detail, /if \(detail\) return previewJson\(detail\)/);
  assert.doesNotMatch(detail, /该新闻详情不在本次 Preview 快照中/);
});

test("keeps the 60-day news archive inside bounded D1 work", () => {
  const index = readFileSync(new URL("../app/api/news-index/route.ts", import.meta.url), "utf8");
  const detail = readFileSync(new URL("../app/api/news-content/route.ts", import.meta.url), "utf8");
  const migration = readFileSync(new URL("../drizzle/0007_bounded_news_archive.sql", import.meta.url), "utf8");
  assert.match(index, /body\.items\.length > 20/);
  assert.match(detail, /body\.items\.length > 20/);
  assert.match(index, /ORDER BY published_time DESC/);
  assert.match(index, /impact_expires_at>\?/);
  assert.match(index, /item\.model_visibility = "IMPACT_EXPIRED"/);
  assert.match(index, /DELETE FROM news_index WHERE mirror_contract <> \?/);
  assert.match(index, /s-maxage=30/);
  assert.match(migration, /news_index_published_idx/);
  assert.match(migration, /news_index_category_published_idx/);
});

test("does not poll immutable Preview snapshots", () => {
  const helper = readFileSync(new URL("../app/_lib/dashboard-refresh.ts", import.meta.url), "utf8");
  assert.match(helper, /live:\s*15_000/);
  assert.match(helper, /status:\s*60_000/);
  assert.match(helper, /news:\s*30_000/);
  assert.match(helper, /learning:\s*300_000/);
  assert.match(helper, /deployment:\s*120_000/);
  assert.match(helper, /immutablePreview\s*\?\s*null\s*:\s*window\.setInterval\(pollWhenEligible/);
  assert.match(helper, /is_preview[^=]*=== true/s);
  assert.match(helper, /document\.visibilityState !== "visible"/);
  assert.match(helper, /navigator\.webdriver/);
  assert.match(helper, /localStorage\.getItem/);
  assert.match(helper, /visibilitychange/);
  const statusView = readFileSync(new URL("../app/_views/StatusView.tsx", import.meta.url), "utf8");
  const healthView = readFileSync(new URL("../app/_views/HealthView.tsx", import.meta.url), "utf8");
  assert.match(statusView, /DASHBOARD_REFRESH_INTERVALS\.status \/ 1000/);
  assert.match(healthView, /DASHBOARD_REFRESH_INTERVALS\.status \/ 1000/);
  for (const path of [
    "../app/_views/LiveRoomView.tsx",
    "../app/_views/StatusView.tsx",
    "../app/_views/HealthView.tsx",
    "../app/_views/AuditView.tsx",
  ]) {
    const source = readFileSync(new URL(path, import.meta.url), "utf8");
    assert.match(source, /scheduleDashboardRefresh/);
    assert.match(source, /immutablePreview/);
  }
});

test("renders every preview room from the build snapshot", async () => {
  if (!process.env.WORKERS_CI_BRANCH || process.env.WORKERS_CI_BRANCH === "main") return;
  for (const [path, marker] of [
    ["/", /Aurum Signal Room/],
    ["/?room=status", /AI 模型使用状态/],
    ["/?room=health", /系统健康状态/],
  ]) {
    const { response, html } = await renderSettled(path, marker);
    assert.equal(response.status, 200, path);
    assert.match(html, marker, path);
  }
  for (const view of ["news", "evidence", "stories", "decisions", "league", "coverage"]) {
    const response = await render(`/?room=audit&view=${view}`);
    assert.equal(response.status, 200, view);
    const html = await response.text();
    assert.doesNotMatch(html, /正在同步页面当前指标/, view);
    assert.match(html, /current-metric-placeholder/, view);
  }
});

test("formats server-rendered preview times in one deterministic timezone", () => {
  for (const path of ["../app/_views/AuditView.tsx", "../app/_views/LiveRoomView.tsx", "../app/_views/StatusView.tsx", "../app/_views/HealthView.tsx"]) {
    assert.match(readFileSync(new URL(path, import.meta.url), "utf8"), /timeZone:\s*"Asia\/Kuala_Lumpur"/, path);
  }
});

test("returns a verified main revision through the deployment status endpoint", () => {
  const ingest = readFileSync(new URL("../app/api/ingest/route.ts", import.meta.url), "utf8");
  const snapshot = readFileSync(new URL("../app/api/_shared/dashboard-snapshot.ts", import.meta.url), "utf8");
  const vite = readFileSync(new URL("../vite.config.ts", import.meta.url), "utf8");
  assert.match(vite, /__AURUM_DEPLOYMENT__/);
  assert.match(vite, /WORKERS_CI_COMMIT_SHA/);
  assert.match(ingest, /deployment\.branch === "main"/);
  assert.match(ingest, /\^\[0-9a-f\]\{40\}\$/);
  assert.match(ingest, /main_revision/);
  assert.match(ingest, /export async function GET/);
  assert.match(ingest, /Cache-Control.*no-store/);
  assert.match(ingest, /writeDashboardSnapshot\(request, binding, 1\)/);
  assert.doesNotMatch(ingest, /request\.json\(\)|JSON\.stringify\(|TextEncoder/);
  assert.match(snapshot, /json_valid\(payload\)/);
  assert.match(snapshot, /content-length/);
  assert.match(snapshot, /MAX_DASHBOARD_SNAPSHOT_BYTES/);
});

test("replaces the forecast state with the broker reopening countdown", () => {
  const source = readFileSync(new URL("../app/_views/LiveRoomView.tsx", import.meta.url), "utf8");
  assert.match(source, /const forecastStatus = marketClosed/);
  assert.match(source, /距离重开/);
  assert.match(source, /marketClosed \|\| marketUnavailable \|\| \(signalRemaining > 0 && online\)/);
  assert.match(source, /等待行情恢复/);
  assert.match(source, /等待最新预测/);
  assert.doesNotMatch(source, /当前不可参考/);
});

test("renders the Gemini quota status route", async () => {
  const { response, html } = await renderSettled("/?room=status", /AI 模型使用状态/);
  assert.equal(response.status, 200);
  assert.match(html, /AI 模型使用状态/);
  assert.match(html, /Gemini 3.5 Flash-Lite/);
  assert.match(html, /Gemini 3.1 Flash-Lite/);
  assert.match(html, /Gemma 4 31B/);
  assert.match(html, /reset-countdown/);
  assert.match(html, /逐 Key 配额/);
  assert.match(html, /Pacific midnight/);
  assert.match(html, /组件与新闻源/);
  assert.match(html, /连接中|状态离线/);
});

test("renders component and news-source health on a separate route", async () => {
  const response = await render("/?room=health");
  assert.equal(response.status, 200);
  const html = await response.text();
  assert.match(html, /系统健康状态/);
  assert.match(html, /系统组件状态/);
  assert.match(html, /新闻来源状态/);
  assert.match(html, /AI 模型用量/);
});

test("uses one Chinese system-state presentation across every dashboard page", () => {
  const component = readFileSync(new URL("../app/_components/SystemStatePill.tsx", import.meta.url), "utf8");
  assert.match(component, /连接中/);
  assert.match(component, /系统在线/);
  assert.match(component, /市场休市/);
  assert.match(component, /状态离线/);
  for (const path of ["../app/_views/LiveRoomView.tsx", "../app/_views/AuditView.tsx", "../app/_views/StatusView.tsx", "../app/_views/HealthView.tsx"]) {
    const source = readFileSync(new URL(path, import.meta.url), "utf8");
    assert.match(source, /SystemStatePill/);
    assert.doesNotMatch(source, /MARKET CLOSED|CONNECTING|市场休市 · 新闻运行中/);
  }
});

test("live room presents broker-confirmed closure instead of a WAIT prediction", () => {
  const source = readFileSync(new URL("../app/_views/LiveRoomView.tsx", import.meta.url), "utf8");
  assert.match(source, /距离重开/);
  assert.match(source, /cTrader 已确认 XAUUSD 休市/);
  assert.match(source, /暂停新增预测与 30 分钟样本/);
  assert.match(source, /const dialAction = marketClosed/);
  assert.match(source, /marketUnavailable\s*\? "无行情"/);
});

test("live room hides a stale forecast when broker status is unavailable", () => {
  const source = readFileSync(new URL("../app/_views/LiveRoomView.tsx", import.meta.url), "utf8");
  assert.match(source, /const marketUnavailable = Boolean\(payload && !online && !marketClosed\)/);
  assert.match(source, /marketUnavailable\s*\? "unavailable"/);
  assert.match(source, /marketUnavailable\s*\? "无行情"/);
  assert.match(source, /marketClosed \|\| marketUnavailable \|\|/);
});

test("renders the news and decision audit route", async () => {
  const response = await render("/?room=audit&view=news");
  assert.equal(response.status, 200);
  const html = await response.text();
  assert.match(html, /Aurum Evidence Desk/);
  assert.match(html, />新闻 <b>/);
  assert.match(html, /当前可用新闻事件/);
  const source = readFileSync(new URL("../app/_views/AuditView.tsx", import.meta.url), "utf8");
  assert.match(source, /模型真正用过哪些新闻/);
  assert.match(source, /按独立事件说明模型用过什么、没用什么/);
  assert.match(source, /evidence-intro evidence-intro-compact/);
  assert.match(source, /查看统计规则/);
  assert.match(source, /收到多少篇文章/);
  assert.match(source, /历史上用过多少个事件/);
  assert.match(source, /影响过多少次预测/);
  assert.match(source, /模型一共读取多少次/);
  assert.match(source, /现在仍可用于预测/);
  assert.match(source, /这不是新闻数量/);
  assert.doesNotMatch(source, /文章 \/ Revision/);
  assert.doesNotMatch(source, /当前达到 Broad 门槛/);
  const css = readFileSync(new URL("../app/globals.css", import.meta.url), "utf8");
  assert.match(css, /\.evidence-summary \{[^}]*grid-template-columns:repeat\(3,1fr\)/);
  assert.match(source, /多源确认/);
  assert.match(source, /核心新闻要求一手完整证据或至少两个独立可靠来源确认/);
  assert.match(source, /大视野新闻还纳入单一可靠来源并降低权重/);
  assert.match(source, /api\/news-content\?key=/);
  assert.match(source, /api\/news-index\?/);
  assert.match(source, /api\/learning/);
  assert.match(source, /if \(view !== "news"\) \{[\s\S]*?fullNewsIndexReadyRef\.current[\s\S]*?refreshNews\(true\)/);
  assert.match(source, /Do not poll off-screen/);
  assert.match(source, /view !== "league"/);
  assert.match(source, /loadDashboardResource<Payload>\("\/api\/status"/);
  assert.doesNotMatch(source, /Promise\.allSettled/);
  assert.doesNotMatch(source, /row\.topics\.map/);
  assert.doesNotMatch(source, /row\.model_identities\.map/);
  assert.doesNotMatch(source, /row\.model_unseen_reason_codes\.map/);
  assert.doesNotMatch(source, /IDENTITY_LABELS/);
  assert.match(source, /MODEL_LABELS\[identity\] \?\? identity/);
  assert.match(source, /读取中…/);
  assert.match(source, /学习数据暂不可用|暂不可用/);
  assert.match(source, /页面会保留上一份成功数据并自动重试/);
  assert.doesNotMatch(source, /payload\?\.system\.online && !error/);
  assert.match(source, /列表与正文详情分开保存/);
  assert.doesNotMatch(source, /这些新闻处理到哪里了/);
  assert.match(source, /条近60天可读新闻/);
  assert.match(source, /条无需复核/);
  assert.match(source, /row\.model_visibility !== "NOT_YET_PARSED"/);
  assert.match(source, /个当前模型候选事件/);
  assert.doesNotMatch(source, /个 key 轮换|每分钟最多生成/);
  assert.ok(source.indexOf('<nav className="audit-tabs"') < source.indexOf('<section className="annotation-queue"'));
  assert.doesNotMatch(source, /已经积累多少结果|真实上线后结果|当前模型学到哪里|距离下次学习/);
  assert.match(source, /上一次学习/);
  assert.match(source, /下一次学习/);
  assert.match(source, /目标 − 目前已有 = 还差多少/);
  assert.doesNotMatch(source, /查看技术审计明细/);
  assert.doesNotMatch(source, /旧工程数据|修复后的训练种子|上线后前向结果/);
  assert.doesNotMatch(source, /Legacy Engineering|Repaired Seed|Next fit/);
  assert.match(source, /大视野新闻还纳入单一可靠来源并降低权重/);
  assert.match(source, /按事件类型和有效交易时间逐步衰减/);
  assert.match(source, /无效样本/);
  assert.match(source, /activeLearningIdentities/);
  assert.match(source, /counts\?\.live_oos_model_groups/);
  if (process.env.WORKERS_CI_BRANCH && process.env.WORKERS_CI_BRANCH !== "main") {
    assert.doesNotMatch(html, /news-row-placeholder/);
  } else {
    assert.match(html, /news-row-placeholder/);
  }
  assert.match(html, /决策与30分钟结果/);
  assert.match(html, /Live OOS 学习曲线/);
  assert.match(html, /大视野覆盖/);
  assert.match(html, /学习进度/);
});

test("switches dashboard rooms locally and reuses client data between views", () => {
  const cache = readFileSync(new URL("../app/_lib/dashboard-resource.ts", import.meta.url), "utf8");
  const link = readFileSync(new URL("../app/_components/DashboardLink.tsx", import.meta.url), "utf8");
  const app = readFileSync(new URL("../app/_components/DashboardApp.tsx", import.meta.url), "utf8");
  const css = readFileSync(new URL("../app/globals.css", import.meta.url), "utf8");
  for (const path of ["../app/_views/LiveRoomView.tsx", "../app/_views/AuditView.tsx", "../app/_views/StatusView.tsx", "../app/_views/HealthView.tsx"]) {
    const source = readFileSync(new URL(path, import.meta.url), "utf8");
    assert.match(source, /DashboardLink/);
    assert.match(source, /readDashboardResource/);
    assert.match(source, /loadDashboardResource/);
    assert.doesNotMatch(source, /useRouter/);
  }
  assert.match(link, /navigation\?\.preload\(href\)/);
  assert.match(link, /navigation\.navigate\(href, replace\)/);
  assert.match(link, /href=\{href\}/);
  assert.match(link, /setAttribute\("aria-busy", "true"\)/);
  assert.match(app, /window\.history\.pushState/);
  assert.match(app, /window\.history\.replaceState/);
  assert.match(app, /window\.addEventListener\("popstate"/);
  assert.match(app, /lazy\(loadAuditView\)/);
  assert.match(css, /\.audit-link\.is-navigating::after/);
  assert.match(css, /prefers-reduced-motion:reduce/);
  assert.match(cache, /const resources = new Map/);
  assert.match(cache, /if \(!options\.force && isFresh\)/);
  assert.match(cache, /if \(entry\.pending\)/);
  assert.match(cache, /cache: "no-store"/);
});

test("redirects legacy dashboard URLs to the single app shell", async () => {
  for (const [path, location] of [
    ["/status", "/?room=status"],
    ["/health", "/?room=health"],
    ["/audit?view=league", "/?room=audit&view=league"],
  ]) {
    const response = await render(path);
    assert.equal(response.status, 307);
    assert.equal(response.headers.get("location"), location);
  }
});

test("reloads an already-open dashboard after a deployment changes its client bundle", () => {
  const layout = readFileSync(new URL("../app/layout.tsx", import.meta.url), "utf8");
  const refresh = readFileSync(new URL("../app/_components/DeploymentRefresh.tsx", import.meta.url), "utf8");
  assert.match(layout, /<DeploymentRefresh\s*\/>/);
  assert.match(refresh, /\/_next\/static\//);
  assert.match(refresh, /cache:\s*"no-store"/);
  assert.match(refresh, /window\.location\.reload\(\)/);
  assert.match(refresh, /document\.visibilityState !== "visible"/);
  assert.match(refresh, /navigator\.webdriver/);
  assert.match(refresh, /DASHBOARD_REFRESH_INTERVALS\.deployment/);
});

test("keeps large chart snapshots off the Worker JSON serialization path", () => {
  const route = readFileSync(new URL("../app/api/market-chart/route.ts", import.meta.url), "utf8");
  assert.match(route, /return new Response\(row\.payload/);
  assert.doesNotMatch(route, /NextResponse\.json\(JSON\.parse\(row\.payload\)/);
});

test("reads the bounded learning first page before the compact live relay", () => {
  const source = readFileSync(new URL("../app/api/learning/route.ts", import.meta.url), "utf8");
  const d1Read = source.indexOf("dashboard_snapshots WHERE id = ?");
  const relayRead = source.indexOf("process.env.STATUS_RELAY_URL");
  assert.ok(d1Read >= 0, "learning route must read the dedicated D1 snapshot");
  assert.ok(relayRead > d1Read, "the compact relay must remain a fallback");
  assert.match(source, /append-only learning records stored in D1/);
  assert.match(source, /return new Response\(row\.payload/);
  assert.doesNotMatch(source, /NextResponse\.json\(JSON\.parse\(row\.payload\)/);
  assert.match(source, /previewBundle\?\.learning_summary/);
  assert.match(source, /writeDashboardSnapshot\(request, binding, 3\)/);
  assert.doesNotMatch(source, /JSON\.parse\(serialized\)|TextEncoder/);
});

test("stores growing learning history as bounded idempotent D1 records", () => {
  const route = readFileSync(new URL("../app/api/learning-history/route.ts", import.meta.url), "utf8");
  const sync = readFileSync(new URL("../../scripts/run_dashboard_sync.py", import.meta.url), "utf8");
  assert.match(route, /MAX_INGEST_BYTES = 350_000/);
  assert.match(route, /readBoundedBody\(request, MAX_INGEST_BYTES\)/);
  assert.match(route, /json_each\(json_extract\(doc,'\$\.records'\)\)/);
  assert.match(route, /ON CONFLICT\(resource,record_key\) DO UPDATE/);
  assert.doesNotMatch(route, /JSON\.parse\(body\.serialized\)/);
  assert.match(route, /MAX_RESPONSE_BYTES = 400_000/);
  assert.match(route, /json_group_array\(json\(payload\)\)/);
  assert.match(route, /length\(CAST\(payload AS BLOB\)\)/);
  assert.match(route, /running_bytes<=\?/);
  assert.doesNotMatch(route, /results\.map\(row => JSON\.parse\(row\.payload\)\)/);
  assert.match(route, /next_cursor/);
  assert.match(sync, /LEARNING_HISTORY_CONTRACT_VERSION = "learning-history-d1-v2"/);
  assert.match(route, /resource='curve-overview'/);
  assert.match(route, /resource='version-overview'/);
  assert.doesNotMatch(route, /row_number\(\) OVER \(PARTITION BY model_identity/);
  assert.match(sync, /learning_history_records/);
  assert.match(sync, /LEARNING_SUMMARY_GROUPS_PER_IDENTITY = 6/);
  assert.match(sync, /LEARNING_SUMMARY_CURVE_POINTS = 48/);
});

test("uses one D1-validated writer for every large dashboard snapshot", () => {
  for (const [path, id] of [
    ["../app/api/ingest/route.ts", 1],
    ["../app/api/market-chart/route.ts", 2],
    ["../app/api/learning/route.ts", 3],
  ]) {
    const source = readFileSync(new URL(path, import.meta.url), "utf8");
    assert.match(source, new RegExp(`writeDashboardSnapshot\\(request, binding, ${id}\\)`), path);
    assert.doesNotMatch(source, /INSERT INTO dashboard_snapshots/, path);
  }
});

test("rejects oversized snapshots before preparing a D1 statement", async () => {
  const { MAX_DASHBOARD_SNAPSHOT_BYTES, writeDashboardSnapshot } = await import(
    "../app/api/_shared/dashboard-snapshot.ts"
  );
  let prepared = false;
  const binding = { prepare() { prepared = true; } };
  const request = new Request("https://example.test/api/learning", {
    method: "POST",
    headers: { "content-length": String(MAX_DASHBOARD_SNAPSHOT_BYTES + 1) },
    body: "{}",
  });
  assert.equal(await writeDashboardSnapshot(request, binding, 3), "too_large");
  assert.equal(prepared, false);
});

test("rejects an oversized snapshot when content-length understates the body", async () => {
  const { MAX_DASHBOARD_SNAPSHOT_BYTES, writeDashboardSnapshot } = await import(
    "../app/api/_shared/dashboard-snapshot.ts"
  );
  let prepared = false;
  const binding = { prepare() { prepared = true; } };
  const body = JSON.stringify({ data: "x".repeat(MAX_DASHBOARD_SNAPSHOT_BYTES) });
  const request = new Request("https://example.test/api/learning", {
    method: "POST",
    headers: { "content-length": "2" },
    body,
  });
  assert.equal(await writeDashboardSnapshot(request, binding, 3), "too_large");
  assert.equal(prepared, false);
});

test("bounds a streamed snapshot without content-length", async () => {
  const { MAX_DASHBOARD_SNAPSHOT_BYTES, writeDashboardSnapshot } = await import(
    "../app/api/_shared/dashboard-snapshot.ts"
  );
  let prepared = false;
  let cancelled = false;
  let pulls = 0;
  const binding = { prepare() { prepared = true; } };
  const stream = new ReadableStream({
    pull(controller) {
      pulls += 1;
      controller.enqueue(new Uint8Array(
        pulls === 1 ? MAX_DASHBOARD_SNAPSHOT_BYTES : 1,
      ));
    },
    cancel() { cancelled = true; },
  });
  const request = new Request("https://example.test/api/market-chart", {
    method: "POST",
    body: stream,
    duplex: "half",
  });
  assert.equal(await writeDashboardSnapshot(request, binding, 2), "too_large");
  assert.equal(prepared, false);
  assert.equal(cancelled, true);
});

test("lets D1 validate raw snapshot JSON in the same write", async () => {
  const { writeDashboardSnapshot } = await import("../app/api/_shared/dashboard-snapshot.ts");
  const calls = [];
  const binding = {
    prepare(sql) {
      return {
        bind(...values) {
          calls.push({ sql, values });
          return { run: async () => ({ meta: { changes: 1 } }) };
        },
      };
    },
  };
  const body = JSON.stringify({ generated_at: "2026-08-11T12:00:00Z" });
  const request = new Request("https://example.test/api/ingest", {
    method: "POST",
    headers: { "content-length": String(Buffer.byteLength(body)) },
    body,
  });
  assert.equal(await writeDashboardSnapshot(request, binding, 1), "stored");
  assert.equal(calls.length, 1);
  assert.match(calls[0].sql, /WHERE json_valid\(payload\)/);
  assert.equal(calls[0].values[0], body);
  assert.equal(calls[0].values[1], 1);
});

test("handles a non-JSON service failure without exposing a parser error", () => {
  const resource = readFileSync(new URL("../app/_lib/dashboard-resource.ts", import.meta.url), "utf8");
  const audit = readFileSync(new URL("../app/_views/AuditView.tsx", import.meta.url), "utf8");
  assert.match(resource, /数据服务暂时不可用/);
  assert.match(resource, /await response\.text\(\)/);
  assert.doesNotMatch(resource, /await response\.json\(\)/);
  assert.match(audit, /DASHBOARD_REFRESH_INTERVALS\.learning/);
});

test("shows single events immediately and keeps later changes in one thread", () => {
  const page = readFileSync(new URL("../app/_views/AuditView.tsx", import.meta.url), "utf8");
  const css = readFileSync(new URL("../app/globals.css", import.meta.url), "utf8");
  assert.match(page, /主题流/);
  assert.match(page, /个事件/);
  assert.match(page, /市场反应流/);
  assert.match(page, /新发生/);
  assert.match(page, /有后续时会自动接成一条脉络/);
  assert.doesNotMatch(page, /暂无后续进展/);
  assert.match(page, /第一次进展立即显示，后续变化接在一起/);
  assert.ok(page.indexOf('className="story-grid"') < page.indexOf('className="theme-streams"'), "events must appear before secondary topic streams");
  assert.match(page, /版本需要更新/);
  assert.doesNotMatch(page, /还没有形成故事链/);
  assert.doesNotMatch(page, /故事开始/);
  assert.doesNotMatch(page, /TEMPORAL EVENT GRAPH V5/);
  assert.doesNotMatch(page, /Runtime Git SHA/);
  assert.doesNotMatch(page, /Story Policy/);
  assert.match(page, /未归属事件/);
  assert.match(css, /\.story-grid[^}]+background:#aaa59a/);
  assert.match(css, /html \{ background:var\(--paper\)/);
  assert.doesNotMatch(css, /\.story-grid[^}]+background:var\(--ink\)/);
});

test("keeps the learning disclaimer short and explicit", () => {
  const source = readFileSync(new URL("../app/_views/AuditView.tsx", import.meta.url), "utf8");
  assert.match(source, /仅供研究观察，不代表盈利，也不会自动下单/);
  assert.doesNotMatch(source, /早期曲线用于观察学习过程/);
  assert.doesNotMatch(source, /Champion 始终是 Always Wait/);
});

test("uses one modal timeline for model generations and market decisions", () => {
  const page = readFileSync(new URL("../app/_views/AuditView.tsx", import.meta.url), "utf8");
  const modal = readFileSync(new URL("../app/audit/LearningGraphModal.tsx", import.meta.url), "utf8");
  const css = readFileSync(new URL("../app/globals.css", import.meta.url), "utf8");
  assert.match(page, /打开交互图表/);
  assert.match(page, /新闻修正量/);
  assert.match(page, /核心新闻修正/);
  assert.match(page, /大视野新闻修正量/);
  assert.match(page, /return-flow/);
  assert.match(page, /本组开始前的历史累计/);
  assert.match(page, /加入本组后的连续累计/);
  assert.match(page, /本组独立贡献/);
  assert.match(modal, /长期 OOS 曲线/);
  assert.match(modal, /每组独立成绩/);
  assert.match(modal, /所有模型的训练组成绩/);
  assert.match(modal, /const pageSize = 6/);
  assert.match(modal, /visibleRows\.map/);
  assert.match(modal, /function VersionPagination/);
  assert.match(modal, /训练组分页（/);
  assert.match(modal, /aria-label="上一页训练组"/);
  assert.match(modal, /aria-label="下一页训练组"/);
  assert.match(modal, /pendingPageScrollRef/);
  assert.match(modal, /if \(!pendingPageScrollRef\.current \|\| pageLoading \|\| pageError\) return/);
  assert.match(modal, /scroller\.scrollTo/);
  assert.doesNotMatch(modal, /setPage\([\s\S]{0,160}scrollIntoView/);
  assert.match(modal, /className="version-page-stage" aria-busy=\{pageLoading\}/);
  assert.match(css, /version-page-stage \{ min-height:560px; display:flex; flex-direction:column/);
  assert.match(css, /version-pagination-bottom \{ margin-top:auto/);
  assert.match(modal, /position="bottom"/);
  assert.match(modal, /className="version-list-anchor"/);
  assert.match(modal, /共同训练截止量对齐/);
  assert.match(modal, /查看模型明细/);
  assert.match(modal, /最近20个训练截止点/);
  assert.match(modal, /crossesMissingCutoff/);
  assert.match(modal, /strokeDasharray=\{crossesMissingCutoff/);
  assert.match(modal, /gx\(comparisonCutoff\(row\)\)/);
  assert.doesNotMatch(modal, /gx\(row\.generation\)/);
  assert.match(modal, /每30分钟（固定 :00 \/ :30）/);
  assert.match(modal, /同一坐标叠加比较/);
  assert.match(page, /六套模型，现在表现怎样/);
  assert.match(page, /等待新版生成/);
  assert.match(page, /training-card-total/);
  assert.match(page, /还差/);
  assert.doesNotMatch(page, /含新闻的决策时点/);
  assert.doesNotMatch(page, /重复决策样本，不是文章数/);
  assert.doesNotMatch(page, /learning-data-flow/);
  assert.match(page, /方法与实盘边界/);
  assert.match(modal, /K线与决策/);
  assert.match(modal, /仓位与退出/);
  assert.doesNotMatch(modal, /冻结 Shadow 动作/);
  assert.match(modal, /每小时 :00 \/ :30/);
  assert.match(modal, /每5分钟/);
  assert.doesNotMatch(modal, /成本后 EV 较优方向/);
  assert.doesNotMatch(modal, /setArrowMode/);
  assert.match(modal, /U5 只是统一波动尺度，不是 WAIT 开关/);
  assert.match(modal, /模型选择 vs 固定 1\.0x/);
  assert.match(modal, /顺序 Exit Ridge vs 固定持有30分钟/);
  assert.match(modal, /两套独立实验/);
  assert.match(modal, /仓位倍率 OOS/);
  assert.match(modal, /提前退出 OOS/);
  assert.match(modal, /总计 \{count\} 笔/);
  assert.match(modal, /当前显示最新 \{visibleCount\} 笔/);
  assert.match(modal, /图中压缩为/);
  assert.match(modal, /resource=execution-point/);
  assert.match(modal, /第 \{page \+ 1\} 段 · 共 \{total\} 个历史绘图点/);
  assert.match(modal, /← 较早/);
  assert.match(modal, /较晚 →/);
  assert.match(css, /\.execution-chart-grid \{ display:grid; grid-template-columns:repeat\(2,minmax\(0,1fr\)\)/);
  assert.match(css, /\.execution-history-nav/);
  assert.match(modal, /目前没有提前退出/);
  assert.doesNotMatch(modal, /等待退出 OOS/);
  assert.match(modal, /WAIT 不创建仓位/);
  assert.match(modal, /点击图中的三角形/);
  assert.match(modal, /Ridge 预测未来30分钟连续收益/);
  assert.match(modal, /较高的一边只要大于0就记录为 Shadow 方向/);
  assert.match(modal, /return bestEv > 0 \? bestAction : "WAIT"/);
  assert.match(modal, /每根K线5分钟 · 每个箭头预测未来30分钟/);
  assert.match(modal, /全部历史/);
  assert.match(modal, /查看更早行情/);
  assert.match(modal, /查看较新行情/);
  assert.match(modal, /title="暂无行情数据"/);
  assert.match(modal, /模型当时尚未开始预测/);
  assert.match(modal, /这段时间没有预测/);
  assert.match(modal, /marketGaps/);
  assert.match(modal, /"数据缺口"/);
  assert.match(modal, /gap\.duration >= 45 \* 60_000/);
  assert.match(modal, /历史＋实时成熟 OOS（只追加，不重写）/);
  assert.match(modal, /24小时/);
  assert.match(modal, /7天/);
  assert.match(modal, /30天/);
  assert.match(modal, /全部总览/);
  assert.match(modal, /较早一段/);
  assert.match(modal, /较晚一段/);
  assert.match(modal, /回到最新/);
  assert.match(modal, /全部历史只画压缩轮廓/);
  assert.match(modal, /Page through windows that contain real matured results/);
  assert.match(modal, /Plot result time, not wall-clock time/);
  assert.match(modal, /curve-gap-bridge/);
  assert.match(modal, /压缩历史轮廓/);
  assert.match(modal, /curve-gap-carry-in/);
  assert.match(modal, /窗口开始前的压缩历史轮廓/);
  assert.doesNotMatch(modal, /points\.unshift\(\{ decision_time: new Date\(start\)/);
  assert.doesNotMatch(modal, /points\.push\(\{ decision_time: new Date\(end\)/);
  assert.match(modal, /成本后EV较高方向/);
  assert.match(modal, /模型版本/);
  assert.match(modal, /历史规则不一致/);
  assert.match(modal, /getUTCMinutes\(\) % 30 === 0/);
  assert.match(modal, /const xAtIndex/);
  assert.match(modal, /条模型评分/);
  assert.match(modal, /模型成绩对比/);
  assert.match(modal, /按同一训练截止点比较。/);
  assert.doesNotMatch(modal, /五种模型叠加在同一坐标/);
  assert.doesNotMatch(modal, /实线连接相邻训练截止点/);
  assert.match(modal, /versionBoundaries/);
  assert.match(modal, /新训练数据代/);
  assert.match(modal, /pools\.direction !== null && pools\.direction !== state\.lastDirectionRows/);
  assert.match(modal, /pools\.news !== null && pools\.news !== state\.lastNewsRows/);
  assert.match(modal, /sort\(\(a, b\) => Date\.parse\(a\) - Date\.parse\(b\)\)/);
  assert.match(modal, /方向 \$\{boundary\.direction\}/);
  assert.match(modal, /新闻 \$\{boundary\.news\}/);
  assert.match(modal, /version-boundary-badge/);
  assert.match(modal, /const laneEnds: number\[\] = \[\]/);
  assert.match(modal, /boundaryLayouts/);
  assert.match(modal, /version-boundary-leader/);
  assert.match(modal, /boundaryDividerY/);
  assert.doesNotMatch(modal, /标签分别显示方向池与新闻池/);
  assert.match(modal, /version-label-divider/);
  assert.doesNotMatch(modal, /changes\[0\]\?\.training_rows/);
  assert.match(modal, /模型换版本/);
  assert.match(modal, /30分钟结果/);
  assert.match(modal, /无效样本 · 已隔离/);
  assert.doesNotMatch(modal, /三种动作同一30分钟结果/);
  assert.doesNotMatch(modal, /30分钟退出线/);
  assert.match(css, /\.version-pagination/);
  assert.match(css, /\.version-pagination button \{ width:46px; height:46px/);
  assert.match(css, /font-size:clamp\(24px,7vw,28px\)/);
  assert.match(css, /height:calc\(100dvh - 16px\)/);
  assert.match(css, /grid-template-rows:auto auto minmax\(0,1fr\) auto/);
  assert.match(modal, /graph-modal-\$\{tab\}/);
  assert.match(css, /graph-modal\.graph-modal-curve,\.graph-modal\.graph-modal-versions \{ height:calc\(100dvh - 16px\); max-height:none; grid-template-rows:auto auto minmax\(0,1fr\) auto/);
  assert.match(css, /graph-modal\.graph-modal-curve>\.graph-modal-body,\.graph-modal\.graph-modal-versions>\.graph-modal-body \{ min-height:0; max-height:none; overflow:auto/);
  assert.match(css, /scrollbar-gutter:stable/);
  assert.match(css, /long-curve-block>\.learning-svg \{ height:clamp\(390px,48dvh,520px\)/);
  assert.match(css, /\.curve-navigation/);
  assert.match(css, /version-ledger-controls \{ display:grid; grid-template-columns:minmax\(210px,1\.16fr\) minmax\(190px,1fr\) minmax\(180px,\.9fr\)/);
  assert.doesNotMatch(css, /\.modal-version-ledger>header \{[^}]*position:sticky/);
  assert.match(modal, /className="version-result-head"/);
  assert.match(modal, /className="version-training"/);
  assert.match(modal, /className="version-result-metrics"/);
  assert.match(modal, /data-label="上线后"/);
  assert.match(modal, /data-label="本组收益"/);
  assert.match(modal, /data-label="PF \/ 出方向"/);
  assert.match(css, /\.version-result-metrics>\[data-label\]::before \{ content:attr\(data-label\)/);
  assert.match(css, /@media \(max-width:1100px\)\{[\s\S]*?\.version-ledger>header \{ grid-template-columns:1fr/);
  assert.match(css, /long-curve-block>\.chart-legend \{ margin-top:16px; padding-bottom:10px/);
  assert.match(modal, /预测 \/ 方向/);
  assert.match(modal, /row\.decision_time \?\? row\.time/);
  assert.match(modal, /row\.scored_at \?\? row\.time/);
});

test("keeps the learning page focused and folds secondary research below the scoreboard", () => {
  const page = readFileSync(new URL("../app/_views/AuditView.tsx", import.meta.url), "utf8");
  const summary = page.indexOf('<div className="learning-summary-grid">');
  const graph = page.indexOf('<section className="graph-launch">');
  const scoreboard = page.indexOf('<section className="model-score-summary">');
  const execution = page.indexOf("<ExecutionResearch", scoreboard);
  const methods = page.indexOf('<details className="model-method-note">', execution);
  assert.ok(summary >= 0 && graph > summary);
  assert.ok(scoreboard > graph && execution > scoreboard && methods > execution);
  assert.doesNotMatch(page, /learning-audit-details|NEWS MODEL CONTRACT/);
  assert.doesNotMatch(page, /league-cost-note/);
  assert.match(page, /仓位与退出研究/);
});

test("keeps dashboard navigation and graph controls usable on phones", () => {
  const page = readFileSync(new URL("../app/_views/AuditView.tsx", import.meta.url), "utf8");
  const modal = readFileSync(new URL("../app/audit/LearningGraphModal.tsx", import.meta.url), "utf8");
  const css = readFileSync(new URL("../app/globals.css", import.meta.url), "utf8");
  assert.match(page, /ref=\{auditTabsRef\} className="audit-tabs"/);
  assert.match(page, /active\.offsetLeft - \(nav\.clientWidth - active\.clientWidth\) \/ 2/);
  assert.match(page, /scrollAuditTabs/);
  assert.match(page, /aria-label="向左查看更多审计视图"/);
  assert.match(page, /aria-label="向右查看更多审计视图"/);
  assert.match(page, /aria-hidden="true">‹<\/span>/);
  assert.match(page, /aria-hidden="true">›<\/span>/);
  assert.match(css, /\.topbar \{ align-items:stretch; flex-direction:column/);
  assert.match(css, /\.audit-tabs-shell \{ position:sticky; top:0;[\s\S]*?grid-template-columns:44px minmax\(0,1fr\) 44px/);
  assert.match(css, /\.audit-tabs \{ position:static; display:flex;[\s\S]*?overflow-x:auto/);
  assert.match(css, /\.audit-main \.audit-intro>div:first-child \{ display:none; \}/);
  assert.match(css, /\.coverage-card \{ display:grid;[\s\S]*?min-height:0;/);
  assert.match(css, /\.evidence-summary \{ grid-template-columns:repeat\(3,minmax\(0,1fr\)\); \}/);
  assert.match(css, /\.quota-summary \{ grid-template-columns:repeat\(2,minmax\(0,1fr\)\); \}/);
  assert.match(css, /\.graph-modal>nav \{ grid-template-columns:repeat\(2,minmax\(0,1fr\)\)/);
  assert.match(css, /\.graph-modal,\.graph-modal\.graph-modal-curve,\.graph-modal\.graph-modal-versions \{ width:100vw; height:100dvh/);
  assert.match(page, /return-value return-history/);
  assert.match(page, /return-value return-total/);
  assert.match(page, /return-value return-group/);
  assert.match(css, /\.compact-model-summary article \{ grid-template-columns:minmax\(0,1fr\)/);
  assert.match(css, /\.return-flow \{ width:100%; grid-template-columns:minmax\(0,1fr\) 12px minmax\(0,1fr\) 10px minmax\(0,1fr\)/);
  assert.match(css, /\.story-grid>article \{ overflow:hidden/);
  assert.match(css, /\.unassigned-story-events>div \{ grid-template-columns:minmax\(0,1fr\)/);
  assert.match(css, /\.return-value>span,\.return-value>strong \{ overflow:visible;[\s\S]*?font-size:clamp\(14px,4\.4vw,17px\)/);
  assert.match(css, /\.summary-cadence \{ display:grid; grid-template-columns:minmax\(0,1fr\) minmax\(0,1fr\)/);
  assert.match(modal, /mobile-chart-scroll/);
  assert.match(modal, /左右滑动查看完整图表/);
  assert.match(modal, /closeButtonRef\.current\?\.focus\(\)/);
  assert.match(modal, /openerRef\.current\?\.focus\(\)/);
  assert.match(modal, /event\.key !== "Tab"/);
  assert.match(css, /\.mobile-chart-scroll \{ width:100%; overflow-x:auto/);
  assert.match(css, /\.execution-history-nav \{ display:flex; flex-wrap:wrap; justify-content:center; \}/);
  assert.match(css, /\.execution-history-nav button \{ flex:0 1 100px; min-width:0; min-height:44px; \}/);
  assert.match(css, /\.market-history-nav \{[^}]*margin:10px 0 0;[^}]*border:1px solid/);
  assert.match(css, /\.prediction-counts \{[^}]*border-top:0/);
  assert.match(css, /\.chart-block \{ overflow:visible/);
  assert.match(css, /\.graph-modal-backdrop \{ position:fixed; inset:0; z-index:1100/);
  assert.match(css, /\.audit-intro>div:first-child \.eyebrow \{ display:none/);
  assert.match(css, /\.audit-intro h1 \{ font-size:clamp\(32px,9vw,38px\)/);
});

test("explains U5 as a risk scale rather than a probability", () => {
  const source = readFileSync(new URL("../app/_views/LiveRoomView.tsx", import.meta.url), "utf8");
  assert.match(source, /30分钟波动风险/);
  assert.match(source, /risk-scale/);
  assert.match(source, /它不是亏损概率，也不代表方向/);
  assert.match(source, /research_forecast/);
  assert.match(source, /30分钟预测/);
  assert.match(source, /forecast-state/);
  assert.doesNotMatch(source, /成本后 EV 较高方向/);
  assert.doesNotMatch(source, /固定观察30分钟 · 不下单/);
  assert.doesNotMatch(source, /30分钟结果窗口已完成/);
  assert.match(readFileSync(new URL("../app/globals.css", import.meta.url), "utf8"), /timeline-panel \{ grid-column:1; grid-row:1 \/ span 3; \}/);
});

test("keeps live quotes online between five-minute decisions", async () => {
  const now = Date.now();
  const payload = applyFreshness({
    generated_at: new Date(now - 5_000).toISOString(),
    system: { online: true, quote_age_seconds: 2 },
    latest: { source_received_time: "2026-08-06T00:00:00+00:00" },
  }, now);
  assert.equal(payload.system.online, true);
  assert.equal(payload.system.quote_age_seconds, 7);
});

test("marks the mirror offline when the quote heartbeat stops arriving", () => {
  const now = Date.now();
  const payload = applyFreshness({
    generated_at: new Date(now - 90_000).toISOString(),
    system: { online: true, quote_age_seconds: 2 },
  }, now);
  assert.equal(payload.system.online, false);
  assert.equal(payload.system.quote_age_seconds, 92);
});

test("does not turn a locally offline collector back online", () => {
  const now = Date.now();
  const payload = applyFreshness({
    generated_at: new Date(now - 1_000).toISOString(),
    system: { online: false, quote_age_seconds: 1 },
  });
  assert.equal(payload.system.online, false);
});

test("loads market history by bounded range instead of one growing snapshot", () => {
  const modal = readFileSync(new URL("../app/audit/LearningGraphModal.tsx", import.meta.url), "utf8");
  const route = readFileSync(new URL("../app/api/market-history/route.ts", import.meta.url), "utf8");
  assert.match(modal, /history_resource/);
  assert.match(modal, /historyQuery\.set\("before", before\)/);
  assert.match(modal, /setBefore\(candles\[0\]\.time\)/);
  assert.match(route, /OVERVIEW_POINTS = 480/);
  assert.match(route, /OVERVIEW_DECISIONS = 480/);
  assert.match(route, /source_decision_count/);
  assert.match(route, /decision_downsampled/);
  assert.match(route, /WHERE time_epoch>=\? AND time_epoch<\?/);
  assert.match(route, /ON CONFLICT\(decision_key\) DO UPDATE/);
  assert.match(route, /MAX_INGEST_BYTES = 400_000/);
  assert.match(route, /ORDER BY decision_epoch,decision_key/);
  assert.match(modal, /loadDashboardResource<MarketData>/);
  assert.match(modal, /return \(\) => \{ cancelled = true; \}/);
  assert.match(modal, /!candles\.length \? <div className="graph-visual-stage market-empty-stage">/);
  assert.match(modal, /onClick=\{goLater\}>→ 返回较新行情/);
  assert.match(route, /previousCandleEnd/);
  assert.match(modal, /Plot trading time, not wall-clock time/);
  assert.doesNotMatch(modal, /休市 \$\{Math\.max/);
});

test("explains training rows separately from independent news events", () => {
  const source = readFileSync(new URL("../app/_views/AuditView.tsx", import.meta.url), "utf8");
  const metrics = readFileSync(new URL("../app/_lib/news-metrics.ts", import.meta.url), "utf8");
  assert.match(source, /newsMetrics\.training\.current_contract_rows/);
  assert.match(source, /newsMetrics\.training\.distinct_events/);
  assert.match(source, /文章、独立事件、预测读取和训练记录是四种不同口径/);
  assert.match(metrics, /schema_version: "news-metrics-v1"/);
  assert.match(metrics, /One compatibility boundary; views never reinterpret news counts themselves/);
  assert.doesNotMatch(source, /news_evidence_summary\?\./);
});

test("live room reports articles and independent events instead of revision rows", () => {
  const source = readFileSync(new URL("../app/_views/LiveRoomView.tsx", import.meta.url), "utf8");
  assert.match(source, /NEWS ARTICLES/);
  assert.match(source, /newsMetrics\.articles\.received/);
  assert.match(source, /newsMetrics\.events\.independent/);
  assert.doesNotMatch(source, /NEWS REVISIONS/);
  assert.doesNotMatch(source, /counts\.news_revisions/);
});

test("shows residual and news-only research directions without implying execution", () => {
  const source = readFileSync(new URL("../app/_views/AuditView.tsx", import.meta.url), "utf8");
  assert.match(source, /修正量自己的30分钟方向研究/);
  assert.match(source, /正修正显示 LONG，负修正显示 SHORT/);
  assert.match(source, /只看新闻的30分钟方向研究/);
  assert.match(source, /model\.recommended_action/);
  assert.doesNotMatch(source, /REPLAYED_FROM_FROZEN_POST_COST_EV/);
  assert.doesNotMatch(source, /暂不参考方向/);
  assert.doesNotMatch(source, /仅显示修正值，不单独判断方向/);
});

test("loads bounded learning history only when interactive charts need it", () => {
  const audit = readFileSync(new URL("../app/_views/AuditView.tsx", import.meta.url), "utf8");
  const modal = readFileSync(new URL("../app/audit/LearningGraphModal.tsx", import.meta.url), "utf8");
  const compact = readFileSync(new URL("../build/preview-learning.ts", import.meta.url), "utf8");
  assert.match(compact, /learning_preview_summary: true/);
  assert.match(compact, /preview_status_summary: true/);
  assert.match(compact, /identity_curves: \[\]/);
  assert.match(audit, /refreshStatus\(!fullStatusReadyRef\.current\)/);
  assert.match(audit, /refreshLearning\(!fullLearningReadyRef\.current\)/);
  assert.match(audit, /if \(!fullLearningReadyRef\.current\) void refreshLearning\(true\)/);
  assert.match(audit, /historyResource=\{payload\?\.learning_history_resource\}/);
  assert.match(modal, /resource: "version-group"/);
  assert.match(modal, /resource=curve-overview/);
  assert.match(modal, /const resolvedCurves = historyResource \? historyCurves\[cadence\] \?\? \[\] : curves/);
  assert.doesNotMatch(modal, /const resolvedCurves = historyCurves\[cadence\] \?\? curves/);
  assert.match(modal, /next_cursor/);
  assert.match(modal, /const pageCursor = pageCursors\[page\]/);
  assert.doesNotMatch(modal, /loadedPageKeys/);
});

test("distinguishes market history loading, empty, and failed states", () => {
  const modal = readFileSync(new URL("../app/audit/LearningGraphModal.tsx", import.meta.url), "utf8");
  const resource = readFileSync(new URL("../app/_lib/dashboard-resource.ts", import.meta.url), "utf8");
  const history = readFileSync(new URL("../app/api/market-history/route.ts", import.meta.url), "utf8");
  const schema = readFileSync(new URL("../db/schema.ts", import.meta.url), "utf8");
  const migration = readFileSync(new URL("../drizzle/0006_materialized_history_overviews.sql", import.meta.url), "utf8");
  const css = readFileSync(new URL("../app/globals.css", import.meta.url), "utf8");
  assert.match(modal, /historyState === "loading"/);
  assert.match(modal, /historyState === "error"/);
  assert.match(modal, /正在读取行情/);
  assert.match(modal, /正在读取长期曲线/);
  assert.match(modal, /正在读取这组成绩/);
  assert.match(modal, /graph-state-compact/);
  assert.doesNotMatch(modal, /if \(pageLoading \|\| overviewState === "loading"\)/);
  assert.doesNotMatch(modal, /if \(historyLoading\) return <GraphLoading/);
  assert.doesNotMatch(modal, /historyState === "loading"\) return <GraphLoading/);
  assert.match(modal, /graph-visual-stage market-empty-stage/);
  assert.doesNotMatch(modal, /compact-market-empty/);
  assert.match(css, /graph-visual-stage \{ min-height:clamp\(420px,58dvh,620px\)/);
  assert.match(modal, /title="暂无行情数据"/);
  assert.match(modal, /重新读取/);
  assert.doesNotMatch(modal, /还没有保存过可绘制的 Bid\/Ask 行情/);
  assert.doesNotMatch(modal, /等待可验证数据/);
  assert.doesNotMatch(resource, /MIN_VISIBLE_LOADING_MS|waitForMinimumLoading/);
  assert.match(modal, /loadDashboardResource/);
  assert.match(modal, /readDashboardResource/);
  assert.match(modal, /HISTORY_CACHE_MAX_AGE_MS = 60_000/);
  assert.match(modal, /Number\.POSITIVE_INFINITY/);
  assert.match(modal, /initialHistoryResult/);
  assert.doesNotMatch(modal, /waitForMinimumLoading|startedAt/);
  assert.match(modal, /point\.source_gap_before/);
  assert.match(modal, /first\.source_gap_before/);
  assert.match(modal, /overviewStep/);
  assert.match(modal, /Date\.parse\(point\.decision_time\) - Date\.parse\(previous\.decision_time\) >= overviewStep/);
  assert.doesNotMatch(modal, /source_gap_before \?\?/);
  assert.match(history, /market_history_overview/);
  assert.match(history, /market_decision_overviews/);
  assert.match(schema, /marketHistoryOverview/);
  assert.match(schema, /marketDecisionOverviews/);
  assert.match(migration, /CREATE TABLE IF NOT EXISTS `market_history_overview`/);
  assert.match(migration, /CREATE TABLE IF NOT EXISTS `market_decision_overviews`/);
  assert.doesNotMatch(history, /SELECT count\(\*\) count, min\(time_epoch\)/);
  assert.doesNotMatch(history, /row_number\(\) OVER/);
  assert.match(css, /graph-data-pulse/);
  assert.match(css, /prefers-reduced-motion:reduce/);
});

test("reflows news evidence into readable mobile cards", () => {
  const view = readFileSync(new URL("../app/_views/AuditView.tsx", import.meta.url), "utf8");
  const css = readFileSync(new URL("../app/globals.css", import.meta.url), "utf8");
  assert.match(view, /className="evidence-event-cell"/);
  assert.match(view, /className="evidence-status-cell"/);
  assert.match(view, /统一来源身份：/);
  assert.match(view, /原始发布域名：/);
  assert.match(view, /Gemini 与 Gemma 负责理解事件语义/);
  assert.match(view, /mergeNewsEvidenceByEvent/);
  assert.match(view, /new Map<string, NewsEvidence>/);
  assert.match(view, /evidenceMode}:\$\{row\.event_key}/);
  assert.doesNotMatch(view, /evidenceMode}:\$\{row\.event_key}:\$\{index}/);
  assert.match(css, /@media \(max-width:640px\)[\s\S]*\.evidence-table thead \{ position:absolute/);
  assert.match(css, /grid-template-areas:"event event" "status time" "usage usage"/);
  assert.match(css, /\.evidence-event-cell \{ grid-area:event/);
  assert.match(css, /\.evidence-status-cell \{ grid-area:status/);
  assert.match(css, /\.evidence-usage-cell \{ grid-area:usage/);
  assert.match(css, /\.evidence-time-cell \{ grid-area:time/);
  assert.match(css, /\.evidence-status-copy \{ display:none!important/);
  assert.match(css, /\.evidence-model-list \{ display:none!important/);
});
