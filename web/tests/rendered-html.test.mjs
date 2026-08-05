import assert from "node:assert/strict";
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
  assert.match(html, /新闻与 Gemini/);
  assert.match(html, /news-row-placeholder/);
  assert.match(html, /决策与30分钟结果/);
  assert.match(html, /Live OOS 学习曲线/);
  assert.match(html, /大视野覆盖/);
  assert.match(html, /LEARNING PROGRESS/);
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
