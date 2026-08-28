import { spawnSync } from "node:child_process";
import { dirname, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const buildRoot = dirname(fileURLToPath(import.meta.url));
const repositoryRoot = resolve(buildRoot, "../..");

export function requiresWorkersCiPythonInstall(env) {
  return Boolean(env.WORKERS_CI_COMMIT_SHA) &&
    env.NEXT_PUBLIC_PREVIEW_BUILD !== "1";
}

export function installWorkersCiPythonDependencies({
  env = process.env,
  platform = process.platform,
  spawn = spawnSync,
} = {}) {
  if (!requiresWorkersCiPythonInstall(env)) {
    return { installed: false };
  }
  if (platform !== "linux") {
    throw new Error("WORKERS_CI_PYTHON_INSTALL_REQUIRES_LINUX");
  }
  const executable = "python3";
  const args = ["-m", "pip", "install", "-e", repositoryRoot];
  const result = spawn(executable, args, {
    cwd: repositoryRoot,
    env: { ...env, PYTHONUTF8: "1" },
    stdio: "inherit",
  });
  if (result.error) throw result.error;
  if (result.status !== 0) {
    throw new Error(`WORKERS_CI_PYTHON_INSTALL_FAILED:${result.status}`);
  }
  return { installed: true, executable, args, cwd: repositoryRoot };
}

const invokedPath = process.argv[1]
  ? pathToFileURL(resolve(process.argv[1])).href
  : "";
if (invokedPath === import.meta.url) {
  try {
    installWorkersCiPythonDependencies();
  } catch (error) {
    console.error(error instanceof Error ? error.message : String(error));
    process.exitCode = 1;
  }
}
