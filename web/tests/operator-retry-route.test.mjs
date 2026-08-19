import assert from "node:assert/strict";
import test from "node:test";

import { publicDashboardStatus } from "../app/api/_shared/dashboard-status.ts";

const workerUrl = new URL("../dist/server/index.js", import.meta.url);
workerUrl.searchParams.set("operator-retry-test", `${process.pid}-${Date.now()}`);
const { default: worker } = await import(workerUrl.href);
const executionContext = { waitUntil() {}, passThroughOnException() {} };
const assets = { fetch: async () => new Response("Not found", { status: 404 }) };

test("Preview retry surfaces are empty and reject writes before auth or D1", async t => {
  if (!process.env.WORKERS_CI_BRANCH || process.env.WORKERS_CI_BRANCH === "main") {
    t.skip("ordinary local builds have no embedded branch Preview bundle");
    return;
  }
  let d1Reads = 0;
  const bindings = {
    DB: {
      prepare() {
        d1Reads += 1;
        return { bind: () => ({ first: async () => null }) };
      },
    },
    ASSETS: assets,
  };
  const read = await worker.fetch(new Request("http://localhost/api/operator-retry"), bindings, executionContext);
  assert.equal(read.status, 200);
  assert.deepEqual(await read.json(), { items: [], requests: [], preview: true });
  assert.equal(read.headers.get("x-aurum-preview"), "synthetic-empty-operator-retry");
  const write = await worker.fetch(new Request("http://localhost/api/operator-retry", {
    method: "POST", body: "not-json",
  }), bindings, executionContext);
  assert.equal(write.status, 403);
  const machine = await worker.fetch(new Request("http://localhost/api/operator-retry-worker", {
    method: "POST", body: "not-json",
  }), bindings, executionContext);
  assert.equal(machine.status, 403);
  assert.equal(d1Reads, 0);
});

test("Preview exposes synthetic Admin status while public status omits private quota fields", async t => {
  if (!process.env.WORKERS_CI_BRANCH || process.env.WORKERS_CI_BRANCH === "main") {
    t.skip("ordinary local builds have no embedded branch Preview bundle");
    return;
  }
  let d1Reads = 0;
  const bindings = {
    DB: {
      prepare() {
        d1Reads += 1;
        return { bind: () => ({ first: async () => null }) };
      },
    },
    ASSETS: assets,
  };
  const admin = await worker.fetch(
    new Request("http://localhost/api/admin-status"), bindings, executionContext,
  );
  assert.equal(admin.status, 200);
  assert.equal(admin.headers.get("x-aurum-preview"), "synthetic-admin-status");
  const adminPayload = await admin.json();
  assert.ok(adminPayload.annotation_queue);
  assert.ok(adminPayload.llm_routing);
  assert.equal(d1Reads, 0, "Preview Admin status must not read production D1");

  const publicPayload = publicDashboardStatus(adminPayload);
  for (const field of [
    "annotation_queue", "gemini_quota", "gemini_31_quota", "gemma_quota",
    "gemini_embedding_quota", "llm_routing",
  ]) assert.equal(Object.hasOwn(publicPayload, field), false, field);
  assert.equal(d1Reads, 0, "the synthetic Admin read must not touch production D1");
});
