import assert from "node:assert/strict";
import test from "node:test";

import {
  installWorkersCiPythonDependencies,
  requiresWorkersCiPythonInstall,
} from "../build/install-workers-ci-python-deps.mjs";

test("installs Python fixture dependencies only for production Workers CI", () => {
  assert.equal(requiresWorkersCiPythonInstall({}), false);
  assert.equal(requiresWorkersCiPythonInstall({
    WORKERS_CI_COMMIT_SHA: "a".repeat(40),
    NEXT_PUBLIC_PREVIEW_BUILD: "1",
  }), false);
  assert.equal(requiresWorkersCiPythonInstall({
    WORKERS_CI_COMMIT_SHA: "a".repeat(40),
  }), true);

  const calls = [];
  const result = installWorkersCiPythonDependencies({
    env: { WORKERS_CI_COMMIT_SHA: "a".repeat(40) },
    platform: "linux",
    spawn: (executable, args, options) => {
      calls.push({ executable, args, options });
      return { status: 0 };
    },
  });
  assert.equal(result.installed, true);
  assert.equal(calls.length, 1);
  assert.equal(calls[0].executable, "python3");
  assert.deepEqual(calls[0].args.slice(0, 4), ["-m", "pip", "install", "-e"]);
  assert.equal(calls[0].args[4], calls[0].options.cwd);
  assert.equal(calls[0].options.env.PYTHONUTF8, "1");
});

test("fails closed if Workers CI is not the expected Linux environment", () => {
  assert.throws(
    () => installWorkersCiPythonDependencies({
      env: { WORKERS_CI_COMMIT_SHA: "a".repeat(40) },
      platform: "win32",
    }),
    /WORKERS_CI_PYTHON_INSTALL_REQUIRES_LINUX/,
  );
});
