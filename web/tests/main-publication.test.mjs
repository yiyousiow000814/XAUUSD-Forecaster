import assert from "node:assert/strict";
import { test } from "node:test";
import { fileURLToPath } from "node:url";
import { spawnSync } from "node:child_process";
import { uploadMain } from "../build/upload-main.mjs";

const sha = "a".repeat(40);
const env = { WORKERS_CI_BRANCH: "main", WORKERS_CI_COMMIT_SHA: sha };

test("matching main build uploads an artifact without changing traffic", () => {
  let calls = 0;
  const status = uploadMain({ env, head: () => sha, deploy: (args) => {
    calls++;
    assert.deepEqual(args, ["versions", "upload", "--message", `main:${sha}`]);
    return 7;
  } });
  assert.equal(calls, 1);
  assert.equal(status, 7);
});

test("other branches, missing identity and mismatched source cannot upload", () => {
  for (const build of [{}, { ...env, WORKERS_CI_BRANCH: "preview" }, { ...env, WORKERS_CI_COMMIT_SHA: "bad" }, env]) {
    assert.throws(() => uploadMain({ env: build, head: () => "b".repeat(40), deploy: () => assert.fail("must not deploy") }));
  }
});

test("actual node entrypoint rejects a non-main build without starting Wrangler", () => {
  const result = spawnSync(process.execPath, [fileURLToPath(new URL("../build/upload-main.mjs", import.meta.url))], {
    env: { ...process.env, WORKERS_CI_BRANCH: "preview", WORKERS_CI_COMMIT_SHA: sha },
    encoding: "utf8", windowsHide: true,
  });
  assert.equal(result.status, 1);
  assert.match(result.stderr, /requires a Cloudflare main build/);
});
