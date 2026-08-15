import assert from "node:assert/strict";
import test from "node:test";

const workerUrl = new URL("../dist/server/index.js", import.meta.url);
workerUrl.searchParams.set("news-questions-test", `${process.pid}-${Date.now()}`);
const { default: worker } = await import(workerUrl.href);

const executionContext = { waitUntil() {}, passThroughOnException() {} };
const assets = { fetch: async () => new Response("Not found", { status: 404 }) };

test("Preview rejects Assistant writes before authentication, body parsing, or D1", async t => {
  if (!process.env.WORKERS_CI_BRANCH || process.env.WORKERS_CI_BRANCH === "main") {
    t.skip("the ordinary local build has no embedded branch Preview bundle");
    return;
  }
  let touched = false;
  const response = await worker.fetch(
    new Request("http://localhost/api/news-questions", {
      method: "POST",
      body: "not-json",
    }),
    {
      DB: new Proxy({}, { get() { touched = true; throw new Error("D1 must stay untouched"); } }),
      ASSETS: assets,
    },
    executionContext,
  );
  assert.equal(response.status, 403);
  assert.match((await response.json()).error, /Preview.*只读/);
  assert.equal(touched, false);
});

test("Preview returns only an explicit synthetic empty private history", async t => {
  if (!process.env.WORKERS_CI_BRANCH || process.env.WORKERS_CI_BRANCH === "main") {
    t.skip("the ordinary local build has no embedded branch Preview bundle");
    return;
  }
  let touched = false;
  const response = await worker.fetch(
    new Request("http://localhost/api/news-questions?id=foreign-object"),
    {
      DB: new Proxy({}, { get() { touched = true; throw new Error("D1 must stay untouched"); } }),
      ASSETS: assets,
    },
    executionContext,
  );
  assert.equal(response.status, 200);
  assert.deepEqual(await response.json(), { items: [], preview: true });
  assert.equal(response.headers.get("x-aurum-preview"), "synthetic-empty-assistant");
  assert.equal(touched, false);

  const machineClaim = await worker.fetch(
    new Request("http://localhost/api/news-questions?mode=claim&worker_id=test-worker"),
    {
      DB: new Proxy({}, { get() { touched = true; throw new Error("D1 must stay untouched"); } }),
      ASSETS: assets,
    },
    executionContext,
  );
  assert.equal(machineClaim.status, 403);
  assert.equal(touched, false);
});

test("Preview conversation routes reject mutations and expose only labeled empty fixtures", async t => {
  if (!process.env.WORKERS_CI_BRANCH || process.env.WORKERS_CI_BRANCH === "main") {
    t.skip("the ordinary local build has no embedded branch Preview bundle");
    return;
  }
  let touched = false;
  const bindings = {
    DB: new Proxy({}, { get() { touched = true; throw new Error("D1 must stay untouched"); } }),
    ASSETS: assets,
  };
  const write = await worker.fetch(
    new Request("http://localhost/api/assistant-conversations", {
      method: "POST",
      body: "not-json",
    }),
    bindings,
    executionContext,
  );
  assert.equal(write.status, 403);
  assert.match((await write.json()).error, /Preview.*只读/);
  assert.equal(touched, false);

  const read = await worker.fetch(
    new Request("http://localhost/api/assistant-conversations?id=foreign-object"),
    bindings,
    executionContext,
  );
  assert.equal(read.status, 200);
  assert.deepEqual(await read.json(), { items: [], preview: true });
  assert.equal(read.headers.get("x-aurum-preview"), "synthetic-empty-assistant");
  assert.equal(touched, false);

  const machineClaim = await worker.fetch(
    new Request("http://localhost/api/assistant-conversations?mode=title-claim&worker_id=test-worker"),
    bindings,
    executionContext,
  );
  assert.equal(machineClaim.status, 403);
  assert.equal(touched, false);
});
