import assert from "node:assert/strict";
import test from "node:test";

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
  let touched = false;
  const bindings = {
    DB: new Proxy({}, { get() { touched = true; throw new Error("D1 touched"); } }),
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
  assert.equal(touched, false);
});
