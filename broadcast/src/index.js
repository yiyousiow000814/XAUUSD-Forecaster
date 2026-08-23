import { LIVE_SCHEMA_VERSION, MAX_LIVE_BYTES, stateUpdate, validateLiveState } from "./contract.js";

const LATEST_KEY = "latest-state";
const DIGEST_ALGORITHM = "SHA-256";

function json(value, status = 200) {
  return Response.json(value, { status, headers: { "cache-control": "no-store" } });
}

async function digest(value) {
  return new Uint8Array(await crypto.subtle.digest(DIGEST_ALGORITHM, new TextEncoder().encode(value)));
}

async function constantTimeTokenMatch(actual, expected) {
  if (typeof actual !== "string" || typeof expected !== "string" || !expected) return false;
  const [left, right] = await Promise.all([digest(actual), digest(expected)]);
  let difference = left.byteLength ^ right.byteLength;
  const length = Math.max(left.byteLength, right.byteLength);
  for (let index = 0; index < length; index += 1) {
    difference |= (left[index] ?? 0) ^ (right[index] ?? 0);
  }
  return difference === 0;
}

function hub(env) {
  return env.LIVE_HUB.get(env.LIVE_HUB.idFromName("public-live-v1"));
}

export async function acceptSubscriber(ctx, pairFactory = () => new WebSocketPair()) {
  const pair = pairFactory();
  const [client, server] = Object.values(pair);
  ctx.acceptWebSocket(server);
  const latest = await ctx.storage.get(LATEST_KEY);
  if (latest) server.send(JSON.stringify({ type: "FULL_STATE", state: latest }));
  return client;
}

async function boundedBody(request) {
  const declared = Number(request.headers.get("content-length") ?? 0);
  if (declared > MAX_LIVE_BYTES) throw new RangeError("payload too large");
  if (!request.body) return null;
  const reader = request.body.getReader();
  const chunks = [];
  let total = 0;
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    total += value.byteLength;
    if (total > MAX_LIVE_BYTES) {
      await reader.cancel("payload too large");
      throw new RangeError("payload too large");
    }
    chunks.push(value);
  }
  const bytes = new Uint8Array(total);
  let offset = 0;
  for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.byteLength; }
  return JSON.parse(new TextDecoder().decode(bytes));
}

export class LiveHub {
  constructor(ctx, env) {
    this.ctx = ctx;
    this.env = env;
  }

  async fetch(request) {
    const url = new URL(request.url);
    if (url.pathname === "/subscribe") {
      if (request.headers.get("upgrade")?.toLowerCase() !== "websocket") {
        return json({ error: "websocket required" }, 426);
      }
      const client = await acceptSubscriber(this.ctx);
      return new Response(null, { status: 101, webSocket: client });
    }
    if (url.pathname === "/publish" && request.method === "POST") {
      const state = validateLiveState(await request.json());
      const latest = await this.ctx.storage.get(LATEST_KEY);
      if (latest && state.sequence <= latest.sequence) {
        return json({ error: "stale sequence", latest_sequence: latest.sequence }, 409);
      }
      const message = latest
        ? { type: "STATE_UPDATE", sequence: state.sequence, state: stateUpdate(latest, state) }
        : { type: "FULL_STATE", state };
      await this.ctx.storage.put(LATEST_KEY, state);
      const encoded = JSON.stringify(message);
      let delivered = 0;
      for (const socket of this.ctx.getWebSockets()) {
        try { socket.send(encoded); delivered += 1; } catch { try { socket.close(1011, "delivery failed"); } catch {} }
      }
      return json({ stored: true, sequence: state.sequence, delivered });
    }
    if (url.pathname === "/health") {
      const latest = await this.ctx.storage.get(LATEST_KEY);
      return json({
        binding_ready: true,
        latest_available: Boolean(latest),
        latest_generated_at: latest?.generated_at ?? null,
        latest_sequence: latest?.sequence ?? null,
        subscribers: this.ctx.getWebSockets().length,
      });
    }
    return json({ error: "not found" }, 404);
  }

  webSocketMessage(socket) {
    try { socket.close(1008, "subscribers are read-only"); } catch {}
  }

  webSocketClose(socket, code, reason) {
    try { socket.close(code, reason); } catch {}
  }
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (url.pathname === "/publish" && request.method === "POST") {
      const token = request.headers.get("authorization")?.replace(/^Bearer\s+/i, "") ?? "";
      if (!await constantTimeTokenMatch(token, env.LIVE_BROADCAST_PUBLISH_TOKEN)) {
        return json({ error: "unauthorized" }, 401);
      }
      let state;
      try { state = validateLiveState(await boundedBody(request)); }
      catch (error) {
        return json({ error: error instanceof RangeError ? "payload too large" : "invalid payload" }, error instanceof RangeError ? 413 : 400);
      }
      if (url.searchParams.get("dry_run") === "true") {
        return json({ valid: true, dry_run: true, schema_version: LIVE_SCHEMA_VERSION, bytes: new TextEncoder().encode(JSON.stringify(state)).byteLength });
      }
      return hub(env).fetch(new Request("https://live-hub.internal/publish", {
        method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(state),
      }));
    }
    if (url.pathname === "/subscribe") return hub(env).fetch(request);
    if (url.pathname === "/health") {
      let binding = { binding_ready: false };
      try { binding = await (await hub(env).fetch("https://live-hub.internal/health")).json(); } catch {}
      return json({
        service: "aurum-live-broadcast",
        code_revision: env.AURUM_GIT_COMMIT_SHA ?? "UNSET",
        worker_version_id: env.CF_VERSION_METADATA?.id ?? "local",
        schema_version: LIVE_SCHEMA_VERSION,
        ...binding,
      }, binding.binding_ready ? 200 : 503);
    }
    return json({ error: "not found" }, 404);
  },
};
