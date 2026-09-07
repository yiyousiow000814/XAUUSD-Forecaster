import assert from "node:assert/strict";
import { cpSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { pathToFileURL } from "node:url";
import test from "node:test";
import { resolveVinextRootAssetImports, vinextRootAssets } from "../build/vinext-root-assets.mjs";

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

test("RSC chunks resolve generated assets to their one authoritative root", async () => {
  for (const fileName of ["entry.mjs", "_next/static/entry.mjs", "_next\\static\\entry.mjs", "nested/deeper/chunks/entry.mjs"]) {
    const root = mkdtempSync(join(tmpdir(), "aurum-vinext-root-assets-"));
    try {
      writeFileSync(join(root, "package.json"), '{"type":"module"}', "utf8");
      const assets = join(root, "vinext-client-assets.js");
      const manifest = join(root, "__vinext_cacheability_manifest.js");
      writeFileSync(assets, 'export default "before-client-finalization";', "utf8");
      writeFileSync(manifest, 'export default "authoritative-cache-policy";', "utf8");
      const code = 'import assets from "./vinext-client-assets.js";\n'
        + 'export {default as manifest} from "./__vinext_cacheability_manifest.js";\n'
        + 'export const dynamic = () => import("./vinext-client-assets.js");\n'
        + 'export default assets;\n';
      const plugin = vinextRootAssets();
      assert.equal(plugin.renderChunk.call({ environment: { name: "client" } }, code, { fileName }), null);
      const result = plugin.renderChunk.call({ environment: { name: "rsc" } }, code, { fileName });
      const entry = join(root, ...fileName.replaceAll("\\", "/").split("/"));
      mkdirSync(dirname(entry), { recursive: true });
      writeFileSync(entry, result?.code ?? code, "utf8");
      // Exercise the real late producer boundary: no copied pre-finalization bytes.
      writeFileSync(assets, 'export default "final-client-assets";', "utf8");
      const loaded = await import(pathToFileURL(entry).href);
      assert.equal(loaded.default, "final-client-assets");
      assert.equal((await loaded.dynamic()).default, "final-client-assets");
      assert.equal(loaded.manifest, "authoritative-cache-policy");
      if (dirname(entry) !== root) {
        for (const asset of ["vinext-client-assets.js", "__vinext_cacheability_manifest.js"]) {
          assert.throws(() => readFileSync(join(dirname(entry), asset)), { code: "ENOENT" });
        }
      }
    } finally {
      rmSync(root, { recursive: true, force: true });
    }
  }
});

test("RSC asset correction changes only exact module specifiers and rejects path escape", async () => {
  const untouched = 'import x from "./other/vinext-client-assets.js";\n'
    + 'export const text = "./vinext-client-assets.js";\n'
    + '// import x from "./__vinext_cacheability_manifest.js";\n';
  assert.equal(resolveVinextRootAssetImports(untouched, "_next/static/entry.mjs"), null);
  const code = 'import "./__vinext_cacheability_manifest.js";';
  assert.ok(resolveVinextRootAssetImports(`${untouched}\n${code}`, "_next/static/entry.mjs").code.startsWith(untouched));
  for (const fileName of ["", "../entry.js", "a/../../entry.js", "..\\entry.js", "/entry.js", "C:\\entry.js", "a//entry.js"]) {
    assert.throws(() => resolveVinextRootAssetImports(code, fileName), /VINEXT_PRERENDER_CHUNK_PATH_INVALID/);
  }
  assert.throws(() => resolveVinextRootAssetImports(`${code} broken(`, "entry.js"), /VINEXT_PRERENDER_IMPORT_PARSE_FAILED/);
  const root = mkdtempSync(join(tmpdir(), "aurum-vinext-root-missing-"));
  try {
    const entry = join(root, "nested", "entry.mjs");
    mkdirSync(dirname(entry));
    writeFileSync(entry, resolveVinextRootAssetImports(code, "nested/entry.mjs").code, "utf8");
    await assert.rejects(import(pathToFileURL(entry).href), { code: "ERR_MODULE_NOT_FOUND" });
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});
