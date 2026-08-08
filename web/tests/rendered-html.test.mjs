import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
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

test("renders the live room with an audit-page navigation button", async () => {
  const response = await render("/");
  assert.equal(response.status, 200);
  const html = await response.text();
  assert.match(html, /Aurum Signal Room/);
  assert.match(html, /新闻与决策/);
  assert.match(html, /系统状态/);
  assert.doesNotMatch(html, /next\/link|rel="prefetch"/);
  assert.doesNotMatch(html, /codex-preview|react-loading-skeleton/);
});

test("renders the Gemini quota status route", async () => {
  const response = await render("/status");
  assert.equal(response.status, 200);
  const html = await response.text();
  assert.match(html, /AI 模型使用状态/);
  assert.match(html, /Gemini 3.5 Flash-Lite/);
  assert.match(html, /Gemini 3.1 Flash-Lite/);
  assert.match(html, /Gemma 4 31B/);
  assert.match(html, /reset-countdown/);
  assert.match(html, /逐 Key 配额/);
  assert.match(html, /Pacific midnight/);
  assert.match(html, /组件与新闻源/);
  assert.match(html, /正在读取状态/);
});

test("renders component and news-source health on a separate route", async () => {
  const response = await render("/health");
  assert.equal(response.status, 200);
  const html = await response.text();
  assert.match(html, /系统健康状态/);
  assert.match(html, /系统组件状态/);
  assert.match(html, /新闻来源状态/);
  assert.match(html, /AI 模型用量/);
});

test("renders the news and decision audit route", async () => {
  const response = await render("/audit");
  assert.equal(response.status, 200);
  const html = await response.text();
  assert.match(html, /Aurum Evidence Desk/);
  assert.match(html, />新闻 <b>/);
  assert.match(html, /新闻证据管理/);
  const source = readFileSync(new URL("../app/audit/page.tsx", import.meta.url), "utf8");
  assert.match(source, /哪些新闻真的/);
  assert.match(source, /收到多少篇新闻/);
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
  assert.match(source, /api\/news-content\?key=/);
  assert.match(source, /api\/news-index\?/);
  assert.match(source, /api\/learning/);
  assert.match(source, /Promise\.allSettled/);
  assert.match(source, /Publish one coherent snapshot/);
  assert.equal((source.match(/setPayload\(/g) ?? []).length, 1);
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
  assert.match(source, /这些新闻处理到哪里了/);
  assert.match(source, /无需 AI 解析/);
  assert.match(source, /当前模型可用/);
  assert.ok(source.indexOf('<nav className="audit-tabs"') < source.indexOf('<section className="annotation-queue"'));
  assert.match(source, /已经积累多少结果/);
  assert.match(source, /真实上线后结果/);
  assert.match(source, /距离下次学习/);
  assert.match(source, /查看技术审计明细/);
  assert.doesNotMatch(source, /Legacy Engineering|Repaired Seed|Next fit/);
  assert.match(source, /最长 72 小时/);
  assert.match(source, /迟到发现只保留展示，不进入训练/);
  assert.match(source, /无效样本/);
  assert.match(source, /activeLearningIdentities/);
  assert.match(html, /news-row-placeholder/);
  assert.match(html, /决策与30分钟结果/);
  assert.match(html, /Live OOS 学习曲线/);
  assert.match(html, /大视野覆盖/);
  assert.match(html, /LEARNING PROGRESS/);
});

test("reloads an already-open dashboard after a deployment changes its client bundle", () => {
  const layout = readFileSync(new URL("../app/layout.tsx", import.meta.url), "utf8");
  const refresh = readFileSync(new URL("../app/_components/DeploymentRefresh.tsx", import.meta.url), "utf8");
  assert.match(layout, /<DeploymentRefresh\s*\/>/);
  assert.match(refresh, /\/_next\/static\//);
  assert.match(refresh, /cache:\s*"no-store"/);
  assert.match(refresh, /window\.location\.reload\(\)/);
  assert.match(refresh, /document\.visibilityState !== "visible"/);
});

test("reads the append-only D1 learning history before the compact live relay", () => {
  const source = readFileSync(new URL("../app/api/learning/route.ts", import.meta.url), "utf8");
  const d1Read = source.indexOf("dashboard_snapshots WHERE id = ?");
  const relayRead = source.indexOf("process.env.STATUS_RELAY_URL");
  assert.ok(d1Read >= 0, "learning route must read the dedicated D1 snapshot");
  assert.ok(relayRead > d1Read, "the compact relay must remain a fallback");
  assert.match(source, /append-only learning history stored in D1/);
});

test("renders generic story coverage without black empty grid placeholders", () => {
  const page = readFileSync(new URL("../app/audit/page.tsx", import.meta.url), "utf8");
  const css = readFileSync(new URL("../app/globals.css", import.meta.url), "utf8");
  assert.match(page, /主题流/);
  assert.match(page, /个事件/);
  assert.match(page, /市场反应流/);
  assert.match(page, /新事件候选/);
  assert.match(page, /DEPLOYMENT DRIFT/);
  assert.match(page, /未归属事件/);
  assert.match(page, /不进入 Ridge/);
  assert.match(css, /\.story-grid[^}]+background:#aaa59a/);
  assert.match(css, /html \{ background:var\(--paper\)/);
  assert.doesNotMatch(css, /\.story-grid[^}]+background:var\(--ink\)/);
});

test("labels the rolling lifecycle without presenting the safety baseline as AI", () => {
  const source = readFileSync(new URL("../app/audit/page.tsx", import.meta.url), "utf8");
  assert.match(source, /零收益安全基准/);
  assert.match(source, /最新版和前一版/);
  assert.match(source, /不训练、不使用 AI、不占 Ridge 版本名额/);
  assert.doesNotMatch(source, /Champion 始终是 Always Wait/);
});

test("uses one modal timeline for model generations and market decisions", () => {
  const page = readFileSync(new URL("../app/audit/page.tsx", import.meta.url), "utf8");
  const modal = readFileSync(new URL("../app/audit/LearningGraphModal.tsx", import.meta.url), "utf8");
  assert.match(page, /打开交互图表/);
  assert.match(page, /新闻修正量/);
  assert.match(page, /大视野新闻修正量/);
  assert.match(page, /return-flow/);
  assert.match(page, /本组开始前的历史累计/);
  assert.match(page, /加入本组后的连续累计/);
  assert.match(page, /本组独立贡献/);
  assert.match(modal, /长期 OOS 曲线/);
  assert.match(modal, /每组独立成绩/);
  assert.match(modal, /所有模型的训练组成绩/);
  assert.match(modal, /共同训练截止量对齐/);
  assert.match(modal, /查看模型明细/);
  assert.match(modal, /最近20个训练截止点/);
  assert.match(modal, /空缺代表该模型当轮没有合法新版本/);
  assert.match(modal, /crossesMissingCutoff/);
  assert.match(modal, /strokeDasharray=\{crossesMissingCutoff/);
  assert.match(modal, /这里只叠加显示，不会把收益相加/);
  assert.match(modal, /gx\(comparisonCutoff\(row\)\)/);
  assert.doesNotMatch(modal, /gx\(row\.generation\)/);
  assert.match(modal, /每30分钟（固定 :00 \/ :30）/);
  assert.match(modal, /同一坐标叠加比较/);
  assert.match(page, /五套模型，现在表现怎样/);
  assert.match(page, /方向收集/);
  assert.match(page, /含新闻的决策时点/);
  assert.match(page, /方向再收集/);
  assert.match(page, /重复决策样本，不是文章数/);
  assert.match(page, /已冻结可审计证据/);
  assert.match(page, /新闻特征随下一轮方向模型一起更新/);
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
  assert.match(modal, /目前没有提前退出/);
  assert.doesNotMatch(modal, /等待退出 OOS/);
  assert.match(modal, /WAIT 不创建仓位/);
  assert.match(modal, /点击图中的三角形/);
  assert.match(modal, /Ridge 预测未来30分钟连续收益/);
  assert.match(modal, /较高的一边只要大于0就记录为 Shadow 方向/);
  assert.match(modal, /return bestEv > 0 \? bestAction : "WAIT"/);
  assert.match(modal, /每根K线5分钟 · 每个箭头预测未来30分钟/);
  assert.match(modal, /历史＋实时成熟 OOS（只追加，不重写）/);
  assert.match(modal, /24小时/);
  assert.match(modal, /7天/);
  assert.match(modal, /30天/);
  assert.match(modal, /全部总览/);
  assert.match(modal, /较早一段/);
  assert.match(modal, /较晚一段/);
  assert.match(modal, /回到最新/);
  assert.match(modal, /全部历史只画压缩轮廓/);
  assert.match(modal, /成本后EV较高方向/);
  assert.match(modal, /模型版本/);
  assert.match(modal, /历史规则不一致/);
  assert.match(modal, /getUTCMinutes\(\) % 30 === 0/);
  assert.match(modal, /const xAtIndex/);
  assert.match(modal, /条模型评分/);
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
  const css = readFileSync(new URL("../app/globals.css", import.meta.url), "utf8");
  assert.match(css, /height:calc\(100dvh - 16px\)/);
  assert.match(css, /grid-template-rows:auto auto minmax\(0,1fr\) auto/);
  assert.match(modal, /graph-modal-\$\{tab\}/);
  assert.match(css, /graph-modal\.graph-modal-curve \{ height:auto; max-height:calc\(100dvh - 16px\); grid-template-rows:auto auto auto auto/);
  assert.match(css, /graph-modal\.graph-modal-curve>\.graph-modal-body \{ overflow:visible/);
  assert.match(css, /scrollbar-gutter:stable/);
  assert.match(css, /long-curve-block>\.learning-svg \{ height:clamp\(390px,48dvh,520px\)/);
  assert.match(css, /\.curve-navigation/);
  assert.match(css, /version-ledger-controls \{ display:grid; grid-template-columns:minmax\(210px,1\.16fr\) minmax\(190px,1fr\) minmax\(180px,\.9fr\)/);
  assert.match(css, /@media \(max-width:1100px\)\{[\s\S]*?\.version-ledger>header \{ grid-template-columns:1fr/);
  assert.match(css, /long-curve-block>\.chart-legend \{ margin-top:16px; padding-bottom:10px/);
  assert.match(modal, /预测 \/ 方向/);
  assert.match(modal, /row\.decision_time \?\? row\.time/);
  assert.match(modal, /row\.scored_at \?\? row\.time/);
});

test("explains U5 as a risk scale rather than a probability", () => {
  const source = readFileSync(new URL("../app/page.tsx", import.meta.url), "utf8");
  assert.match(source, /30分钟波动风险/);
  assert.match(source, /risk-scale/);
  assert.match(source, /它不是亏损概率，也不代表方向/);
  assert.match(source, /research_forecast/);
  assert.match(source, /黄金＋大视野新闻 Ridge/);
  assert.match(source, /固定观察30分钟 · 不下单/);
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
