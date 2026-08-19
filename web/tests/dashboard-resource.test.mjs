import assert from "node:assert/strict";
import test from "node:test";

import {
  DashboardResourceError,
  clearDashboardResource,
  loadDashboardResource,
  primeDashboardResources,
  readDashboardResource,
  readDashboardResourceState,
  subscribeDashboardResource,
} from "../app/_lib/dashboard-resource.ts";
import { systemStatePresentation } from "../app/_lib/system-state.ts";

const originalFetch = globalThis.fetch;
const originalWindow = globalThis.window;
globalThis.window = {
  clearTimeout: globalThis.clearTimeout,
  setTimeout: globalThis.setTimeout,
};

test.after(() => {
  globalThis.fetch = originalFetch;
  globalThis.window = originalWindow;
});

function jsonResponse(body, status = 200) {
  return new Response(JSON.stringify(body), {
    headers: { "content-type": "application/json" },
    status,
  });
}

function presentationOf(state) {
  const payload = state.data;
  return systemStatePresentation({
    loading: state.loading,
    error: state.error !== null,
    hasSnapshot: state.hasSnapshot,
    online: Boolean(payload?.system?.online),
    marketSession: payload?.system?.market_session,
    operationalStatus: payload?.operational_health?.status,
  });
}

test("exposes initial loading and unavailable read state without a snapshot", async () => {
  const url = "/api/status?resource-test=initial-failure";
  globalThis.fetch = async () => { throw new Error("offline"); };

  const request = loadDashboardResource(url, { force: true });
  const loading = readDashboardResourceState(url);
  assert.equal(loading.hasSnapshot, false);
  assert.equal(loading.loading, true);
  assert.equal(loading.error, null);
  assert.equal(presentationOf(loading).readState, "REFRESHING");
  assert.equal(presentationOf(loading).label, "连接中");

  await assert.rejects(request, /offline/);
  const failed = readDashboardResourceState(url);
  assert.equal(failed.hasSnapshot, false);
  assert.equal(failed.loading, false);
  assert.match(failed.error.message, /offline/);
  assert.equal(presentationOf(failed).readState, "UNAVAILABLE");
  assert.equal(presentationOf(failed).label, "状态不可用");
});

test("preserves machine-readable resource failure codes for generation recovery", async () => {
  const url = "/api/news-evidence?resource-test=stale-generation";
  globalThis.fetch = async () => jsonResponse({
    error: "evidence generation changed",
    error_code: "NEWS_EVIDENCE_CURSOR_STALE",
    active_snapshot_id: "b".repeat(64),
  }, 409);

  await assert.rejects(
    loadDashboardResource(url, { force: true }),
    error => error instanceof DashboardResourceError
      && error.status === 409
      && error.code === "NEWS_EVIDENCE_CURSOR_STALE"
      && error.details.active_snapshot_id === "b".repeat(64),
  );
});

test("shares stale status with the shell subscriber and clears it after recovery", async () => {
  const url = "/api/status";
  const first = {
    system: { online: true, market_session: "OPEN" },
    operational_health: { status: "HEALTHY" },
    version: 1,
  };
  const recovered = { ...first, version: 2 };
  const responses = [
    async () => jsonResponse(first),
    async () => { throw new Error("refresh failed"); },
    async () => jsonResponse(recovered),
  ];
  globalThis.fetch = async () => responses.shift()();

  const observed = [];
  const unsubscribe = subscribeDashboardResource(url, () => {
    observed.push(readDashboardResourceState(url));
  });

  await loadDashboardResource(url, { force: true });
  assert.equal(presentationOf(readDashboardResourceState(url)).readState, "CURRENT");

  await assert.rejects(
    loadDashboardResource(url, { force: true }),
    /refresh failed/,
  );
  const stale = readDashboardResourceState(url);
  assert.equal(stale.hasSnapshot, true);
  assert.equal(stale.loading, false);
  assert.match(stale.error.message, /refresh failed/);
  assert.equal(stale.data.version, 1);
  assert.equal(readDashboardResource(url).version, 1);
  assert.equal(presentationOf(stale).readState, "STALE_SNAPSHOT");
  assert.equal(presentationOf(stale).label, "状态更新失败");
  assert.equal(observed.at(-1).error.message, "refresh failed");

  await loadDashboardResource(url, { force: true });
  const current = readDashboardResourceState(url);
  assert.equal(current.data.version, 2);
  assert.equal(current.error, null);
  assert.equal(current.loading, false);
  assert.equal(presentationOf(current).readState, "CURRENT");
  assert.equal(observed.at(-1).data.version, 2);
  assert.deepEqual(observed.map(state => presentationOf(state).readState), [
    "REFRESHING",
    "CURRENT",
    "REFRESHING",
    "STALE_SNAPSHOT",
    "STALE_SNAPSHOT",
    "CURRENT",
  ]);
  unsubscribe();
});

test("deduplicates concurrent requests and stops notifying after unsubscribe", async () => {
  const url = "/api/status?resource-test=dedupe";
  let resolveFetch;
  let fetchCount = 0;
  globalThis.fetch = () => {
    fetchCount += 1;
    return new Promise(resolve => { resolveFetch = resolve; });
  };
  let notifications = 0;
  const unsubscribe = subscribeDashboardResource(url, () => { notifications += 1; });

  const first = loadDashboardResource(url, { force: true });
  const second = loadDashboardResource(url, { force: true });
  assert.equal(fetchCount, 1);
  assert.equal(notifications, 1);
  resolveFetch(jsonResponse({ value: 1 }));
  assert.deepEqual(await Promise.all([first, second]), [{ value: 1 }, { value: 1 }]);
  assert.equal(notifications, 2);

  unsubscribe();
  globalThis.fetch = async () => jsonResponse({ value: 2 });
  await loadDashboardResource(url, { force: true });
  assert.equal(notifications, 2);
});

test("serves a fresh cached snapshot without changing its read state", async () => {
  const url = "/api/status?resource-test=fresh-cache";
  primeDashboardResources({ [url]: { value: 1 } });
  let fetchCount = 0;
  let notifications = 0;
  globalThis.fetch = async () => {
    fetchCount += 1;
    return jsonResponse({ value: 2 });
  };
  const unsubscribe = subscribeDashboardResource(url, () => { notifications += 1; });

  assert.deepEqual(await loadDashboardResource(url), { value: 1 });
  assert.equal(fetchCount, 0);
  assert.equal(notifications, 0);
  assert.deepEqual(readDashboardResourceState(url), {
    data: { value: 1 },
    hasSnapshot: true,
    loading: false,
    error: null,
    updatedAt: readDashboardResourceState(url).updatedAt,
  });
  unsubscribe();
});

test("purges one private Admin snapshot after confirmed authentication expiry", async () => {
  const privateUrl = "/api/admin-status";
  const publicUrl = "/api/status?resource-test=private-purge-boundary";
  clearDashboardResource(privateUrl);
  primeDashboardResources({
    [privateUrl]: { gemini_quota: { total_remaining: 7 } },
    [publicUrl]: { system: { online: true }, version: 1 },
  });
  const observed = [];
  const unsubscribe = subscribeDashboardResource(privateUrl, () => {
    observed.push(readDashboardResourceState(privateUrl));
  });

  globalThis.fetch = async () => jsonResponse({ error: "操作员身份验证失败" }, 401);
  let authError;
  try {
    await loadDashboardResource(privateUrl, { force: true });
  } catch (reason) {
    authError = reason;
  }
  assert.equal(authError.status, 401);
  clearDashboardResource(privateUrl);

  assert.equal(readDashboardResource(privateUrl), null);
  assert.equal(readDashboardResourceState(privateUrl).hasSnapshot, false);
  assert.equal(readDashboardResourceState(privateUrl).error.status, 401);
  assert.equal(observed.at(-1).hasSnapshot, false);
  assert.deepEqual(readDashboardResource(publicUrl), { system: { online: true }, version: 1 });

  globalThis.fetch = async () => jsonResponse({ gemini_quota: { total_remaining: 9 } });
  await loadDashboardResource(privateUrl, { force: true });
  assert.deepEqual(readDashboardResource(privateUrl), { gemini_quota: { total_remaining: 9 } });
  unsubscribe();
});

test("retains last-good private and public snapshots after service failures", async () => {
  const privateUrl = "/api/admin-status?resource-test=503-last-good";
  const publicUrl = "/api/status?resource-test=503-last-good";
  primeDashboardResources({
    [privateUrl]: { private: "last-good" },
    [publicUrl]: { public: "last-good" },
  });
  globalThis.fetch = async () => jsonResponse({ error: "unavailable" }, 503);

  await assert.rejects(loadDashboardResource(privateUrl, { force: true }), /unavailable/);
  await assert.rejects(loadDashboardResource(publicUrl, { force: true }), /unavailable/);
  assert.deepEqual(readDashboardResource(privateUrl), { private: "last-good" });
  assert.deepEqual(readDashboardResource(publicUrl), { public: "last-good" });
});
