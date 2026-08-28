import { spawnSync } from "node:child_process";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

import { isWorkersCi } from "./release-validation-fixtures.mjs";

const buildRoot = dirname(fileURLToPath(import.meta.url));
const webRoot = resolve(buildRoot, "..");

export const releaseFixtureContractTestName =
  "accepts every exact production-shaped fixture across the build boundary";

export function requiresWorkersPreviewFixturePreflight(env = process.env) {
  return isWorkersCi(env)
    && Boolean(env.WORKERS_CI_BRANCH)
    && env.WORKERS_CI_BRANCH !== "main";
}

function run(command, args, options, spawn) {
  const result = spawn(command, args, options);
  if (result.error || result.status !== 0) {
    const reason = result.error?.message || result.stderr || result.stdout || result.status;
    throw new Error(`WORKERS_RELEASE_FIXTURE_PREFLIGHT_FAILED:${reason}`);
  }
}

export function verifyWorkersReleaseFixtures({
  env = process.env,
  spawn = spawnSync,
  nodeExecutable = process.execPath,
} = {}) {
  if (!requiresWorkersPreviewFixturePreflight(env)) {
    return { verified: false };
  }

  const validationEnv = {
    ...env,
    WORKERS_CI_BRANCH: "",
    WORKERS_CI_COMMIT_SHA: "",
  };
  delete validationEnv.NEXT_PUBLIC_PREVIEW_BUILD;
  const options = {
    cwd: webRoot,
    env: validationEnv,
    encoding: "utf8",
    stdio: "inherit",
  };
  if (!env.npm_execpath) {
    throw new Error("WORKERS_RELEASE_FIXTURE_PREFLIGHT_FAILED:npm_execpath missing");
  }
  run(nodeExecutable, [env.npm_execpath, "run", "build"], options, spawn);
  run(nodeExecutable, [
    "--import", pathToFileURL(
      join(webRoot, "tests", "register-cloudflare-worker-loader.mjs"),
    ).href,
    "--test",
    `--test-name-pattern=^${releaseFixtureContractTestName}$`,
    join(webRoot, "tests", "worker-cpu-headroom.test.mjs"),
  ], options, spawn);
  return { verified: true };
}

const invokedPath = process.argv[1]
  ? pathToFileURL(resolve(process.argv[1])).href
  : "";
if (invokedPath === import.meta.url) {
  try {
    verifyWorkersReleaseFixtures();
  } catch (error) {
    console.error(error instanceof Error ? error.message : String(error));
    process.exitCode = 1;
  }
}
