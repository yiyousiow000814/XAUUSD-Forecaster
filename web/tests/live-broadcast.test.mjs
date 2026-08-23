import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const {
  LiveBroadcastTransport, effectiveQuoteAgeSeconds, mergeRecentDecisions,
} = await import("../app/_lib/live-broadcast.ts");
const {
  readDashboardResource, updateDashboardResource,
} = await import("../app/_lib/dashboard-resource.ts");
const {
  ensureStatusBaseline, statusPollingSuppressed,
} = await import("../app/_lib/dashboard-refresh.ts");

class FakeSocket {
  listeners = new Map();
  closed = false;
  addEventListener(type, listener) {
    const values = this.listeners.get(type) ?? [];
    values.push(listener);
    this.listeners.set(type, values);
  }
  emit(type, value = {}) {
    for (const listener of this.listeners.get(type) ?? []) listener(value);
  }
  close() { this.closed = true; this.emit("close"); }
}

function forecast(action = "WAIT") {
  return {
    model_identity: "FULL", model_version: "v18", recommended_action: action,
    prediction_status: "READY", ev_long_u5: 0.2, ev_short_u5: -0.1,
    interval_width: 0.3, decision_time: "2026-08-23T05:00:00.000Z",
    signal_expiry_seconds: 20, forecast_horizon_seconds: 1800,
    directional_bias: action === "WAIT" ? "NEUTRAL" : action, frozen_record: true,
  };
}

function fullState(sequence = 1, action = "WAIT") {
  return {
    schema_version: "PUBLIC_LIVE_V1", sequence,
    generated_at: "2026-08-23T05:00:00.000Z", source_revision: "abc", market_session: "OPEN",
    freshness: { online: true, state: "FRESH" },
    quote: { bid: 3370, ask: 3370.2, spread: 0.2, source_received_time: "2026-08-23T05:00:00.000Z" },
    forecast: forecast(action), health: { status: "HEALTHY", alerts: [] }, recent_decisions: [],
  };
}

function withTimers(run) {
  const originalSetTimeout = globalThis.setTimeout;
  const originalClearTimeout = globalThis.clearTimeout;
  const timers = [];
  globalThis.setTimeout = (callback, delay) => {
    const timer = { callback, delay, cleared: false };
    timers.push(timer);
    return timer;
  };
  globalThis.clearTimeout = timer => { if (timer) timer.cleared = true; };
  try { return run(timers); } finally {
    globalThis.setTimeout = originalSetTimeout;
    globalThis.clearTimeout = originalClearTimeout;
  }
}

test("one tab singleton transport opens one socket and healthy push updates status", () => withTimers(() => {
  const sockets = [];
  const transport = new LiveBroadcastTransport("wss://broadcast.test/subscribe", () => {
    const socket = new FakeSocket(); sockets.push(socket); return socket;
  }, () => 0.5);
  transport.start();
  transport.start();
  assert.equal(sockets.length, 1);
  sockets[0].emit("message", { data: JSON.stringify({ type: "FULL_STATE", state: fullState() }) });
  assert.equal(transport.sourceMode(), "LIVE_PUSH");
  assert.equal(statusPollingSuppressed("status", transport.healthy()), true);
  assert.equal(statusPollingSuppressed("news", transport.healthy()), false);
  assert.equal(readDashboardResource("/api/status").latest.bid, 3370);
  transport.stop();
}));

test("one complete HTTP baseline precedes push and recurring status polling is suppressed", async () => {
  const events = [];
  const first = ensureStatusBaseline(async () => { events.push("baseline"); });
  const second = ensureStatusBaseline(async () => { events.push("duplicate"); });
  await first;
  await second;
  events.push("socket");
  assert.deepEqual(events, ["baseline", "socket"]);
  assert.equal(statusPollingSuppressed("status", true), true);
  assert.equal(statusPollingSuppressed("news", true), false);
  const liveRoom = readFileSync(new URL("../app/_views/LiveRoomView.tsx", import.meta.url), "utf8");
  const shell = readFileSync(new URL("../app/_components/DashboardShell.tsx", import.meta.url), "utf8");
  const previewBanner = readFileSync(new URL("../app/_components/PreviewBanner.tsx", import.meta.url), "utf8");
  assert.match(liveRoom, /DASHBOARD_REFRESH_INTERVALS\.live,[\s\S]*"current",[\s\S]*"status"/);
  assert.doesNotMatch(liveRoom, /"live-status"/);
  assert.match(liveRoom, /effectiveQuoteAgeSeconds\([\s\S]*payload[\s\S]*now/);
  assert.match(liveRoom, /!current\.preview_status_summary\) setRefreshing\(false\)/);
  assert.match(shell, /ensureStatusBaseline[\s\S]*\.catch\([\s\S]*\.finally\(\(\) => \{ if \(active\) transport\?\.start\(\)/);
  assert.match(previewBanner, /subscribeDashboardResource\([\s\S]*"\/api\/status"/);
  assert.doesNotMatch(previewBanner, /fetch\([\s\S]*\/api\/status/);
});

test("push preserves complete baseline, forecast actions, and the 90-minute ledger", () => withTimers(() => {
  const baselineDecisions = Array.from({ length: 18 }, (_, index) => ({
    decision_id: String(index), decision_time: `2026-08-23T04:${String(index).padStart(2, "0")}:00.000Z`,
  }));
  updateDashboardResource("/api/status", () => ({
    counts: { decisions: 18 }, outcome_summary: { samples: 10 },
    u5_context: { label: "baseline" }, sources: { quote: "cTrader" },
    system: { quote_age_seconds: 2, components: { quote_bridge: { status: "RUNNING" } } },
    research_forecast: forecast("SHORT"), recent_decisions: baselineDecisions,
  }));
  const sockets = [];
  const transport = new LiveBroadcastTransport("wss://broadcast.test/subscribe", () => {
    const socket = new FakeSocket(); sockets.push(socket); return socket;
  });
  transport.start();
  for (const [index, action] of ["SHORT", "LONG", "WAIT"].entries()) {
    const state = fullState(index + 1, action);
    state.recent_decisions = [{
      decision_id: `new-${index}`, decision_time: `2026-08-23T05:0${index}:00.000Z`,
    }];
    sockets[0].emit("message", { data: JSON.stringify(
      index === 0 ? { type: "FULL_STATE", state } : {
        type: "STATE_UPDATE", sequence: index + 1, state,
      },
    ) });
    const status = readDashboardResource("/api/status");
    assert.equal(status.research_forecast.recommended_action, action);
    assert.equal(status.research_forecast.forecast_horizon_seconds, 1800);
    assert.equal(status.counts.decisions, 18);
    assert.equal(status.sources.quote, "cTrader");
    assert.equal(status.recent_decisions.length, 18);
  }
  transport.stop();
}));

test("quote age advances from the client clock and resets on a new quote", () => {
  const status = {
    system: { quote_age_seconds: 99 },
    latest: { source_received_time: "2026-08-23T05:00:00.000Z" },
    live_transport: { source_mode: "LIVE_PUSH" },
  };
  const t0 = Date.parse("2026-08-23T05:00:00.000Z");
  assert.equal(effectiveQuoteAgeSeconds(status, t0), 0);
  assert.equal(effectiveQuoteAgeSeconds(status, t0 + 3_000), 3);
  status.latest.source_received_time = "2026-08-23T05:00:02.500Z";
  assert.equal(effectiveQuoteAgeSeconds(status, t0 + 3_000), 0.5);
  status.latest.source_received_time = "invalid";
  assert.equal(effectiveQuoteAgeSeconds(status, t0 + 4_000), 99);
});

test("recent decision deltas merge without shrinking the 18-row baseline", () => {
  const baseline = Array.from({ length: 18 }, (_, index) => ({ decision_id: String(index) }));
  const merged = mergeRecentDecisions(baseline, [{ decision_id: "new" }]);
  assert.equal(merged.length, 18);
  assert.equal(merged[0].decision_id, "new");
});

test("disconnect enables fallback, reconnect is bounded, and recovery restores push", () => withTimers(timers => {
  const sockets = [];
  const transport = new LiveBroadcastTransport("wss://broadcast.test/subscribe", () => {
    const socket = new FakeSocket(); sockets.push(socket); return socket;
  }, () => 0.5);
  const modes = [];
  transport.subscribe(mode => modes.push(mode));
  transport.start();
  sockets[0].emit("close");
  assert.equal(transport.sourceMode(), "HTTP_FALLBACK");
  const reconnect = timers.find(timer => timer.delay === 1000);
  assert.ok(reconnect);
  reconnect.callback();
  assert.equal(sockets.length, 2);
  sockets[1].emit("message", { data: JSON.stringify({ type: "FULL_STATE", state: fullState(2) }) });
  assert.equal(transport.sourceMode(), "LIVE_PUSH");
  assert.ok(modes.includes("HTTP_FALLBACK"));
  transport.stop();
}));

test("stale streams enter fallback mode and sequence gaps reconnect", () => withTimers(timers => {
  const sockets = [];
  const transport = new LiveBroadcastTransport("wss://broadcast.test/subscribe", () => {
    const socket = new FakeSocket(); sockets.push(socket); return socket;
  }, () => 0.5);
  transport.start();
  sockets[0].emit("message", { data: JSON.stringify({ type: "FULL_STATE", state: fullState(3) }) });
  const stale = timers.find(timer => timer.delay === 75_000);
  assert.ok(stale);
  stale.callback();
  assert.equal(transport.sourceMode(), "STALE");
  assert.equal(readDashboardResource("/api/status").live_transport.source_mode, "STALE");
  assert.equal(statusPollingSuppressed("status", transport.healthy()), false);
  const latest = sockets.at(-1);
  latest.emit("message", { data: JSON.stringify({ type: "STATE_UPDATE", sequence: 5, state: { generated_at: "later" } }) });
  assert.equal(latest.closed, true);
  transport.stop();
}));

test("the public browser transport has no publish capability", () => {
  const source = String(LiveBroadcastTransport);
  assert.ok(!source.includes("/publish"));
  assert.ok(!source.includes("LIVE_BROADCAST_PUBLISH_TOKEN"));
  const moduleSource = readFileSync(new URL("../app/_lib/live-broadcast.ts", import.meta.url), "utf8");
  assert.match(moduleSource, /is_preview[\s\S]*VITE_LIVE_BROADCAST_PREVIEW_URL/);
});

test("reconnect backoff uses exponential bounded jitter", () => withTimers(timers => {
  const sockets = [];
  const transport = new LiveBroadcastTransport("wss://broadcast.test/subscribe", () => {
    const socket = new FakeSocket(); sockets.push(socket); return socket;
  }, () => 0);
  transport.start();
  for (const expected of [800, 1600, 3200, 6400, 12800, 24000, 24000]) {
    sockets.at(-1).emit("close");
    const timer = timers.at(-1);
    assert.equal(timer.delay, expected);
    timer.callback();
  }
  transport.stop();
}));
