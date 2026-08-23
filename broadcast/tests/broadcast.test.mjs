import assert from "node:assert/strict";
import test from "node:test";
import worker, { acceptSubscriber, LiveHub } from "../src/index.js";
import { MAX_LIVE_BYTES, validateLiveState } from "../src/contract.js";

function state(sequence = 1, overrides = {}) {
  return {
    schema_version: "PUBLIC_LIVE_V1",
    sequence,
    generated_at: "2026-08-23T05:00:00.000Z",
    source_revision: "73e1d6518ac6ab540c012dd4e5f863fef41593a3",
    market_session: "OPEN",
    freshness: { online: true, state: "FRESH" },
    quote: { bid: 3370.1, ask: 3370.3, spread: 0.2, source_received_time: "2026-08-23T05:00:00.000Z" },
    forecast: {
      model_identity: "FULL", model_version: "v18", recommended_action: "WAIT",
      prediction_status: "READY", ev_long_u5: 0.1, ev_short_u5: -0.1,
      interval_width: 0.2, decision_time: "2026-08-23T05:00:00.000Z",
      signal_expiry_seconds: 20, forecast_horizon_seconds: 1800,
      directional_bias: "NEUTRAL", frozen_record: true,
    },
    health: { status: "HEALTHY", alerts: [] },
    recent_decisions: [{ decision_time: "2026-08-23T05:00:00.000Z", action: "WAIT" }],
    ...overrides,
  };
}

function context(socketCount = 0) {
  const values = new Map();
  const sockets = Array.from({ length: socketCount }, () => ({
    messages: [], closed: false,
    send(message) { this.messages.push(JSON.parse(message)); },
    close() { this.closed = true; },
  }));
  return {
    values, sockets,
    storage: {
      get: async key => values.get(key),
      put: async (key, value) => {
        if (key && typeof key === "object") {
          for (const [name, nested] of Object.entries(key)) values.set(name, nested);
        } else values.set(key, value);
      },
    },
    getWebSockets: () => sockets,
    acceptWebSocket(socket) { sockets.push(socket); },
  };
}

async function publish(hub, value) {
  return hub.fetch(new Request("https://live-hub.internal/publish", {
    method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(value),
  }));
}

function envFor(ctx, token = "publish-secret") {
  const object = new LiveHub(ctx, {});
  return {
    LIVE_BROADCAST_PUBLISH_TOKEN: token,
    CF_VERSION_METADATA: { id: "revision-1" },
    AURUM_GIT_COMMIT_SHA: "candidate-sha",
    LIVE_HUB: { idFromName: value => value, get: () => ({
      fetch: request => object.fetch(request instanceof Request ? request : new Request(request)),
    }) },
  };
}

test("the public contract is small and rejects private or malformed evidence", () => {
  assert.equal(validateLiveState(state()).sequence, 1);
  assert.ok(Buffer.byteLength(JSON.stringify(state())) < MAX_LIVE_BYTES);
  assert.throws(() => validateLiveState(state(1, { gemini_quota: {} })), /private/);
  assert.throws(() => validateLiveState(state(1, { quote: { bid: 2, ask: 1, spread: -1, source_received_time: "x" } })), /quote spread|crossed/);
  assert.throws(() => validateLiveState(state(1, { recent_decisions: Array(19).fill({}) })), /bounded/);
});

test("publisher auth happens before parsing and dry-run has no Durable Object effects", async () => {
  const ctx = context();
  const env = envFor(ctx);
  const unauthorized = await worker.fetch(new Request("https://service.test/publish", {
    method: "POST", body: "not json",
  }), env);
  assert.equal(unauthorized.status, 401);
  const wrong = await worker.fetch(new Request("https://service.test/publish", {
    method: "POST", headers: { authorization: "Bearer wrong" }, body: JSON.stringify(state()),
  }), env);
  assert.equal(wrong.status, 401);
  const dryRun = await worker.fetch(new Request("https://service.test/publish?dry_run=true", {
    method: "POST", headers: { authorization: "Bearer publish-secret" }, body: JSON.stringify(state()),
  }), env);
  assert.equal(dryRun.status, 200);
  assert.equal((await dryRun.json()).dry_run, true);
  assert.equal(ctx.values.size, 0);
  assert.equal(ctx.sockets.length, 0);
});

test("malformed and oversized authenticated publishes fail closed", async () => {
  const env = envFor(context());
  const malformed = await worker.fetch(new Request("https://service.test/publish", {
    method: "POST", headers: { authorization: "Bearer publish-secret" }, body: "{}",
  }), env);
  assert.equal(malformed.status, 400);
  const oversized = await worker.fetch(new Request("https://service.test/publish", {
    method: "POST",
    headers: { authorization: "Bearer publish-secret", "content-length": String(MAX_LIVE_BYTES + 1) },
    body: JSON.stringify(state()),
  }), env);
  assert.equal(oversized.status, 413);
});

for (const count of [10, 100]) {
  test(`one publish reaches ${count} hibernating subscribers without per-client storage reads`, async () => {
    const ctx = context(count);
    const hub = new LiveHub(ctx, {});
    const response = await publish(hub, state(1));
    assert.equal(response.status, 200);
    assert.equal((await response.json()).delivered, count);
    assert.equal(ctx.values.get("latest-state").sequence, 1);
    assert.ok(ctx.sockets.every(socket => socket.messages.length === 1));
  });
}

test("latest state survives reconstruction and stale sequences cannot overwrite it", async () => {
  const ctx = context(2);
  assert.equal((await publish(new LiveHub(ctx, {}), state(7))).status, 200);
  const reconstructed = new LiveHub(ctx, {});
  const stale = await publish(reconstructed, state(6));
  assert.equal(stale.status, 409);
  assert.equal(ctx.values.get("latest-state").sequence, 7);
  assert.equal(ctx.sockets[0].messages.length, 1);
});

test("a new hibernating subscriber receives the durable latest full state", async () => {
  const ctx = context();
  await publish(new LiveHub(ctx, {}), state(11));
  const server = {
    messages: [], send(message) { this.messages.push(JSON.parse(message)); }, close() {},
  };
  const client = { side: "client" };
  const returned = await acceptSubscriber(ctx, () => ({ client, server }));
  assert.equal(returned, client);
  assert.equal(ctx.sockets.length, 1);
  assert.equal(server.messages[0].type, "FULL_STATE");
  assert.equal(server.messages[0].state.sequence, 11);
});

test("health exposes code identity and binding readiness without secrets", async () => {
  const ctx = context();
  await publish(new LiveHub(ctx, {}), state(4));
  const response = await worker.fetch(new Request("https://service.test/health"), envFor(ctx));
  assert.equal(response.status, 200);
  const health = await response.json();
  assert.equal(health.code_revision, "candidate-sha");
  assert.equal(health.schema_version, "PUBLIC_LIVE_V1");
  assert.equal(health.binding_ready, true);
  assert.equal(health.latest_sequence, 4);
  assert.equal(health.latest_source_revision, state().source_revision);
  assert.ok(Number.isFinite(Date.parse(health.latest_published_at)));
  assert.ok(!JSON.stringify(health).includes("publish-secret"));
});

test("unchanged recent decisions are omitted from deterministic updates", async () => {
  const ctx = context(1);
  const hub = new LiveHub(ctx, {});
  await publish(hub, state(1));
  await publish(hub, state(2, { quote: { ...state().quote, bid: 3371, ask: 3371.2 } }));
  const update = ctx.sockets[0].messages[1];
  assert.equal(update.type, "STATE_UPDATE");
  assert.equal(update.sequence, 2);
  assert.ok(!("recent_decisions" in update.state));
});

test("subscriber application messages are read-only", () => {
  const ctx = context(1);
  new LiveHub(ctx, {}).webSocketMessage(ctx.sockets[0], "{\"sequence\":99}");
  assert.equal(ctx.sockets[0].closed, true);
  assert.equal(ctx.values.size, 0);
});
