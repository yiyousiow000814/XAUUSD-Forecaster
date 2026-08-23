import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const { LiveBroadcastTransport } = await import("../app/_lib/live-broadcast.ts");
const { readDashboardResource } = await import("../app/_lib/dashboard-resource.ts");
const { statusPollingSuppressed } = await import("../app/_lib/dashboard-refresh.ts");

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

function fullState(sequence = 1) {
  return {
    schema_version: "PUBLIC_LIVE_V1", sequence,
    generated_at: "2026-08-23T05:00:00.000Z", source_revision: "abc", market_session: "OPEN",
    freshness: { online: true, state: "FRESH" },
    quote: { bid: 3370, ask: 3370.2, spread: 0.2, source_received_time: "2026-08-23T05:00:00.000Z" },
    forecast: { action: "WAIT" }, health: { status: "HEALTHY", alerts: [] }, recent_decisions: [],
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
