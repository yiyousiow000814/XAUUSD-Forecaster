import { spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import { mkdtempSync, readdirSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const buildRoot = dirname(fileURLToPath(import.meta.url));
export const repositoryRoot = resolve(buildRoot, "../..");
export const checkedFixtureRoot = join(
  repositoryRoot, "tests", "fixtures", "release_validation",
);

export function isWorkersCi(env = process.env) {
  return env.WORKERS_CI === "1";
}

function fixtureFiles(root) {
  return readdirSync(root, { withFileTypes: true })
    .filter(entry => entry.isFile() && entry.name.endsWith(".json"))
    .map(entry => entry.name)
    .sort();
}

function sha256(bytes) {
  return createHash("sha256").update(bytes).digest("hex");
}

export function assertExactFixtureBytes(generatedRoot, goldenRoot = checkedFixtureRoot) {
  const generatedFiles = fixtureFiles(generatedRoot);
  const goldenFiles = fixtureFiles(goldenRoot);
  if (JSON.stringify(generatedFiles) !== JSON.stringify(goldenFiles)) {
    throw new Error(
      `RELEASE_VALIDATION_FIXTURE_SET_DRIFT:${JSON.stringify({ generatedFiles, goldenFiles })}`,
    );
  }
  const hashes = {};
  for (const name of goldenFiles) {
    const generated = readFileSync(join(generatedRoot, name));
    const golden = readFileSync(join(goldenRoot, name));
    if (!generated.equals(golden)) {
      throw new Error(`RELEASE_VALIDATION_FIXTURE_BYTE_DRIFT:${name}`);
    }
    hashes[name] = sha256(golden);
  }
  return hashes;
}

export function prepareReleaseValidationFixtures({
  env = process.env,
  platform = process.platform,
  spawn = spawnSync,
  goldenRoot = checkedFixtureRoot,
} = {}) {
  if (isWorkersCi(env)) {
    const files = fixtureFiles(goldenRoot);
    if (files.length === 0) {
      throw new Error("RELEASE_VALIDATION_CHECKED_FIXTURES_MISSING");
    }
    return {
      fixtureRoot: goldenRoot,
      generated: false,
      dispose() {},
    };
  }

  const generatedRoot = mkdtempSync(join(tmpdir(), "aurum-worker-release-fixtures-"));
  const executable = platform === "win32" ? "python.exe" : "python3";
  const args = [
    join(repositoryRoot, "scripts", "build_release_validation_fixtures.py"),
    "--output", generatedRoot,
  ];
  const result = spawn(executable, args, {
    cwd: repositoryRoot,
    encoding: "utf8",
    env: { ...env, PYTHONUTF8: "1" },
  });
  if (result.error || result.status !== 0) {
    rmSync(generatedRoot, { recursive: true, force: true });
    const reason = result.error?.message || result.stderr || result.stdout || result.status;
    throw new Error(`RELEASE_VALIDATION_FIXTURE_BUILD_FAILED:${reason}`);
  }
  try {
    assertExactFixtureBytes(generatedRoot, goldenRoot);
  } catch (error) {
    rmSync(generatedRoot, { recursive: true, force: true });
    throw error;
  }
  return {
    fixtureRoot: generatedRoot,
    generated: true,
    dispose() {
      rmSync(generatedRoot, { recursive: true, force: true });
    },
  };
}
