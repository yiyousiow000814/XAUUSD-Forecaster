import assert from "node:assert/strict";
import { cpSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import {
  assertExactFixtureBytes,
  checkedFixtureRoot,
  isWorkersCi,
  prepareReleaseValidationFixtures,
} from "../build/release-validation-fixtures.mjs";
import {
  releaseFixtureContractTestName,
  requiresWorkersPreviewFixturePreflight,
  verifyWorkersReleaseFixtures,
} from "../build/verify-workers-release-fixtures.mjs";

test("selects the checked-fixture path only for explicit Workers Builds", () => {
  assert.equal(isWorkersCi({ WORKERS_CI: "1" }), true);
  assert.equal(isWorkersCi({}), false);
  assert.equal(isWorkersCi({ WORKERS_CI: "true" }), false);
  assert.equal(isWorkersCi({ WORKERS_CI_COMMIT_SHA: "a".repeat(40) }), false);
});

test("Workers Builds consumes checked bytes without invoking Python", () => {
  let invoked = false;
  const prepared = prepareReleaseValidationFixtures({
    env: { WORKERS_CI: "1" },
    spawn: () => {
      invoked = true;
      throw new Error("Python must not run in Workers Builds");
    },
  });
  assert.equal(invoked, false);
  assert.equal(prepared.generated, false);
  assert.equal(prepared.fixtureRoot, checkedFixtureRoot);
  assert.ok(readFileSync(join(prepared.fixtureRoot, "status-ingest.json")).length > 0);
});

test("non-Workers CI invokes the builder command and verifies checked bytes", () => {
  const calls = [];
  const prepared = prepareReleaseValidationFixtures({
    env: {},
    platform: "linux",
    spawn: (executable, args, options) => {
      calls.push({ executable, args, options });
      cpSync(checkedFixtureRoot, args.at(-1), { recursive: true });
      return { status: 0, stdout: "", stderr: "" };
    },
  });
  try {
    assert.equal(prepared.generated, true);
    assert.equal(calls.length, 1);
    assert.equal(calls[0].executable, "python3");
    assert.match(calls[0].args[0], /build_release_validation_fixtures\.py$/);
    assert.equal(calls[0].args.at(-2), "--output");
    assert.equal(calls[0].options.env.PYTHONUTF8, "1");
  } finally {
    prepared.dispose();
  }
});

test("missing Python outside Workers Builds fails closed without fallback", () => {
  assert.throws(() => prepareReleaseValidationFixtures({
    env: {
      WORKERS_CI: "0",
      WORKERS_CI_COMMIT_SHA: "a".repeat(40),
    },
    spawn: () => ({ status: 1, stderr: "missing sqlite3" }),
  }), /RELEASE_VALIDATION_FIXTURE_BUILD_FAILED:missing sqlite3/);
});

test("fixture byte drift fails closed", () => {
  const changedRoot = mkdtempSync(join(tmpdir(), "aurum-changed-release-fixtures-"));
  try {
    cpSync(checkedFixtureRoot, changedRoot, { recursive: true });
    writeFileSync(join(changedRoot, "status-ingest.json"), "{}", "utf8");
    assert.throws(
      () => assertExactFixtureBytes(changedRoot),
      /RELEASE_VALIDATION_FIXTURE_BYTE_DRIFT:status-ingest\.json/,
    );
    cpSync(
      join(checkedFixtureRoot, "status-ingest.json"),
      join(changedRoot, "status-ingest.json"),
    );
    writeFileSync(join(changedRoot, "unexpected.json"), "{}", "utf8");
    assert.throws(
      () => assertExactFixtureBytes(changedRoot),
      /RELEASE_VALIDATION_FIXTURE_SET_DRIFT/,
    );
  } finally {
    rmSync(changedRoot, { recursive: true, force: true });
  }
});

test("branch Preview validates checked fixtures through a production-shaped Worker build", () => {
  assert.equal(requiresWorkersPreviewFixturePreflight({ WORKERS_CI: "1" }), false);
  assert.equal(requiresWorkersPreviewFixturePreflight({
    WORKERS_CI: "1", WORKERS_CI_BRANCH: "main",
  }), false);
  assert.equal(requiresWorkersPreviewFixturePreflight({
    WORKERS_CI: "1", WORKERS_CI_BRANCH: "feature",
  }), true);

  const calls = [];
  const result = verifyWorkersReleaseFixtures({
    env: {
      WORKERS_CI: "1",
      WORKERS_CI_BRANCH: "feature",
      WORKERS_CI_COMMIT_SHA: "a".repeat(40),
      npm_execpath: "/fixed/npm-cli.js",
    },
    nodeExecutable: "/fixed/node",
    spawn: (command, args, options) => {
      calls.push({ command, args, options });
      return { status: 0 };
    },
  });
  assert.equal(result.verified, true);
  assert.equal(calls.length, 2);
  assert.equal(calls[0].command, "/fixed/node");
  assert.deepEqual(calls[0].args, ["/fixed/npm-cli.js", "run", "build"]);
  assert.equal(calls[0].options.env.WORKERS_CI, "1");
  assert.equal(calls[0].options.env.WORKERS_CI_BRANCH, "");
  assert.equal(calls[0].options.env.WORKERS_CI_COMMIT_SHA, "");
  assert.equal(calls[1].command, "/fixed/node");
  assert.ok(calls[1].args.includes(
    `--test-name-pattern=^${releaseFixtureContractTestName}$`,
  ));
  assert.equal(calls.some(call =>
    call.command.includes("python")
    || call.args.some(arg => arg.includes("build_release_validation_fixtures.py"))
  ), false);
});

test("Workers Preview fixture preflight fails closed", () => {
  assert.throws(() => verifyWorkersReleaseFixtures({
    env: {
      WORKERS_CI: "1",
      WORKERS_CI_BRANCH: "feature",
      npm_execpath: "/fixed/npm-cli.js",
    },
    spawn: () => ({ status: 1, stderr: "production-shaped build failed" }),
  }), /WORKERS_RELEASE_FIXTURE_PREFLIGHT_FAILED:production-shaped build failed/);
});
