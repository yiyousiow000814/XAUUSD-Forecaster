import { performance } from "node:perf_hooks";
import { LiveHub } from "../src/index.js";

const sizes = process.argv.slice(2).map(Number).filter(value => value > 0);
for (const count of sizes.length ? sizes : [500, 1000]) {
  const latencies = [];
  const sockets = Array.from({ length: count }, () => ({
    delivered: 0,
    send() { this.delivered += 1; latencies.push(performance.now() - started); },
    close() {},
  }));
  const values = new Map();
  const ctx = {
    storage: { get: async key => values.get(key), put: async (key, value) => values.set(key, value) },
    getWebSockets: () => sockets,
  };
  const payload = {
    schema_version: "PUBLIC_LIVE_V1", sequence: 1,
    generated_at: new Date().toISOString(), source_revision: "benchmark", market_session: "OPEN",
    freshness: { online: true, state: "FRESH" },
    quote: { bid: 1, ask: 2, spread: 1, source_received_time: new Date().toISOString() },
    forecast: {
      model_identity: "BENCHMARK", model_version: "local",
      recommended_action: "WAIT", prediction_status: "READY",
      ev_long_u5: 0, ev_short_u5: 0, interval_width: 0,
      decision_time: new Date().toISOString(), signal_expiry_seconds: 20,
      forecast_horizon_seconds: 1800, directional_bias: "NEUTRAL",
      frozen_record: true,
    },
    health: { status: "HEALTHY", alerts: [] },
  };
  const started = performance.now();
  const response = await new LiveHub(ctx, {}).fetch(new Request("https://hub/publish", {
    method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(payload),
  }));
  const elapsed = performance.now() - started;
  latencies.sort((a, b) => a - b);
  const percentile = p => latencies[Math.min(latencies.length - 1, Math.floor(latencies.length * p))] ?? 0;
  console.log(JSON.stringify({
    sockets: count, connected: sockets.length, delivered: sockets.filter(value => value.delivered === 1).length,
    dropped: sockets.filter(value => value.delivered !== 1).length, publisher_requests: 1,
    payload_bytes: Buffer.byteLength(JSON.stringify(payload)), duration_ms: elapsed,
    latency_p50_ms: percentile(0.5), latency_p95_ms: percentile(0.95), status: response.status,
  }));
}
