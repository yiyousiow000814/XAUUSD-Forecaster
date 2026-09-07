import assert from "node:assert/strict";
import test from "node:test";

import {
  DashboardResourceError,
  clearDashboardResource,
  clearPrivateDashboardResources,
  loadDashboardResource,
  primeDashboardResources,
  readDashboardResource,
  readDashboardResourceState,
  subscribeDashboardResource,
} from "../app/_lib/dashboard-resource.ts";
import { systemStatePresentation } from "../app/_lib/system-state.ts";
import { AUDIT_DETAIL_PROJECTION_CONTRACT, validAuditDetailPayload } from "../app/_lib/audit-detail-contract.ts";
import { writeDashboardSnapshotBytes } from "../app/api/_shared/dashboard-snapshot.ts";
import { D1TestDatabase } from "./d1-test-database.mjs";
import { admitPreviewAuditDetails } from "../build/preview-learning.ts";

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

test("purges private Admin snapshots after confirmed authentication expiry", async () => {
  const privateUrl = "/admin/api/admin-status";
  const siblingPrivateUrl = "/admin/api/assistant-health?resource-test=private-purge-boundary";
  const publicUrl = "/api/status?resource-test=private-purge-boundary";
  clearDashboardResource(privateUrl);
  primeDashboardResources({
    [privateUrl]: { gemini_quota: { total_remaining: 7 } },
    [siblingPrivateUrl]: { status: "HEALTHY" },
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
  clearPrivateDashboardResources();

  assert.equal(readDashboardResource(privateUrl), null);
  assert.equal(readDashboardResource(siblingPrivateUrl), null);
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
  const privateUrl = "/admin/api/admin-status?resource-test=503-last-good";
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

test("audit resource family rejects malformed success envelopes and preserves accepted work for retry", async () => {
  for (const [view, field] of [["briefs", "daily_news_briefs"], ["stories", "storylines"], ["decisions", "recent_decisions"]]) {
    const url = `/api/audit-${view}?resource-test=accepted-envelope`;
    const accepted = {projection_contract: AUDIT_DETAIL_PROJECTION_CONTRACT, generated_at: "2026-09-06T11:00:00Z", [field]: []};
    const validate = body => validAuditDetailPayload(view, body);
    globalThis.fetch = async () => jsonResponse(accepted);
    await loadDashboardResource(url, {force: true, validate});
    const invalidRows = view === "briefs"
      ? [{[field]: [{model_version:"fixture",phase:3,brief:{items:[]}}]}]
      : view === "stories" ? [{[field]: [], archived_storylines:[{}]}] : [];
    for (const invalid of [null, [], {}, {error: "unavailable"}, {[field]: []}, {...accepted, projection_contract:"unknown"}, {...accepted, projection_contract:null}, {[field]: null}, {[field]: [{}]}, {[field]: [null]}, {[field]: [], generated_at: "invalid"}, ...invalidRows]) {
      globalThis.fetch = async () => jsonResponse(invalid);
      await assert.rejects(loadDashboardResource(url, {force: true, validate}), error => error.code === "INVALID_RESOURCE_PAYLOAD");
      assert.deepEqual(readDashboardResource(url), accepted);
      assert.equal(readDashboardResourceState(url).hasSnapshot, true);
    }
    const recovered = {...accepted, generated_at: "2026-09-06T12:00:00Z"};
    globalThis.fetch = async () => jsonResponse(recovered);
    assert.deepEqual(await loadDashboardResource(url, {force:true, validate}), recovered);
    assert.equal(readDashboardResourceState(url).error, null);
  }
});

test("Audit UI and the actual D1 writer agree on explicit source and nested row validity", async () => {
  const database = new D1TestDatabase([]);
  database.database.exec("CREATE TABLE dashboard_snapshots(id INTEGER PRIMARY KEY,payload TEXT,received_at TEXT)");
  try {
    for (const [view,id,field,row] of [
      ["briefs",7,"daily_news_briefs",{model_version:"v",brief:{items:[{headline:"headline",summary:"summary",evidence_ids:["e1"]}]}}],
      ["stories",8,"storylines",{covered_roles:[],missing_roles:[],timeline:[],market_reactions:[],commentary:[],background:[]}],
      ["decisions",6,"recent_decisions",{predictions:[{ev_long_u5:0.1}],bid:5000,ask:5001}],
    ]) {
      const baseline = {projection_contract:AUDIT_DETAIL_PROJECTION_CONTRACT,generated_at:"2026-09-06T11:00:00Z",[field]:[row]};
      const bytes = new TextEncoder().encode(JSON.stringify(baseline));
      assert.equal(await writeDashboardSnapshotBytes(bytes,database,id),"stored");
      const before = database.row(id,"dashboard_snapshots");
      const familyInvalid = view === "briefs" ? [
        {...baseline,[field]:[{...row,brief:{items:["bad"]}}]},
        {...baseline,[field]:[{...row,brief:{items:[{headline:"h",summary:"s",evidence_ids:[1]}]}}]},
      ] : view === "stories" ? [
        {...baseline,theme_streams:null}, {...baseline,story_event_candidates:{}},
        {...baseline,archived_storylines:[{}]}, {...baseline,[field]:[{...row,timeline:["bad"]}]},
      ] : [
        {...baseline,[field]:[{predictions:["bad"]}]},
        {...baseline,[field]:[{predictions:[{ev_long_u5:"wrong-unit-type"}]}]},
        {...baseline,[field]:[{predictions:[],bid:"5000"}]},
      ];
      const valid = [baseline,{[field]:[row]},{...baseline,[field]:[]}];
      const invalid = [{},null,[],{[field]:[]},{...baseline,projection_contract:null},
        {...baseline,projection_contract:"unsupported"},{...baseline,[field]:[{}]},
        {...baseline,[field]:["bad"]},{...baseline,[field]:null},...familyInvalid];
      for (const [expected, cases] of [[true,valid],[false,invalid]]) {
        for (const value of cases) {
          assert.equal(validAuditDetailPayload(view,value),expected,`${view}/${JSON.stringify(value)}`);
          assert.equal(await writeDashboardSnapshotBytes(new TextEncoder().encode(JSON.stringify(value)),database,id,{dryRun:true}),expected ? "validated" : "invalid");
          assert.deepEqual(database.row(id,"dashboard_snapshots"),before);
          if (!expected) {
            assert.equal(await writeDashboardSnapshotBytes(new TextEncoder().encode(JSON.stringify(value)),database,id),"invalid");
            assert.deepEqual(database.row(id,"dashboard_snapshots"),before);
          }
        }
      }
    }
  } finally {
    database.database.close();
  }
});

test("Preview build admission preserves source time and fallback provenance only for valid detail", () => {
  for (const [view,field,row] of [
    ["briefs","daily_news_briefs",{model_version:"v",brief:{items:[]}}],
    ["stories","storylines",{covered_roles:[],missing_roles:[],timeline:[],market_reactions:[],commentary:[],background:[]}],
    ["decisions","recent_decisions",{predictions:[]}],
  ]) {
    const key = `audit_${view}`;
    const provenance = {availability:"AVAILABLE",source_path:"/api/audit",compatibility_fallback:true};
    const valid = {generated_at:"2026-09-01T11:00:00Z",[field]:[row]};
    for (const detail of [valid,{...valid,projection_contract:AUDIT_DETAIL_PROJECTION_CONTRACT,[field]:[]}]) {
      const bundle = {status:{generated_at:"2026-09-06T11:00:00Z",preview:{resources:{[key]:provenance}}},[key]:detail};
      admitPreviewAuditDetails(bundle);
      assert.equal(bundle[key],detail);
      assert.equal(bundle[key].generated_at,"2026-09-01T11:00:00Z");
      assert.equal(bundle.status.preview.resources[key],provenance);
    }
    for (const detail of [null,{}, {[field]:[]}, {...valid,[field]:[{}]}, {...valid,[field]:["bad"]}]) {
      const bundle = {status:{preview:{resources:{[key]:provenance}}},[key]:detail};
      admitPreviewAuditDetails(bundle);
      assert.equal(bundle[key],null);
      assert.deepEqual(bundle.status.preview.resources[key],{
        availability:"UNAVAILABLE_IN_BUILD_SNAPSHOT",requested_path:`/api/audit-${view}`,
        source_path:null,compatibility_fallback:false,reason:"INVALID_AUDIT_DETAIL_SOURCE",
      });
    }
  }
});
