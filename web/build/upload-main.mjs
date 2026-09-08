import { execFileSync, spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import path from "node:path";

const webRoot = fileURLToPath(new URL("../", import.meta.url));

export function uploadMain({ env = process.env, head, deploy } = {}) {
  if (env.WORKERS_CI_BRANCH !== "main" || !/^[0-9a-f]{40}$/.test(env.WORKERS_CI_COMMIT_SHA ?? "")) {
    throw new Error("Production artifact upload requires a Cloudflare main build and its commit SHA");
  }
  const revision = head();
  if (revision !== env.WORKERS_CI_COMMIT_SHA) {
    throw new Error("Cloudflare build commit does not match the source checkout");
  }
  return deploy(["versions", "upload", "--message", `main:${revision}`]);
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try {
    process.exitCode = uploadMain({
      head: () => execFileSync("git", ["rev-parse", "HEAD"], { cwd: webRoot, encoding: "utf8" }).trim(),
      deploy: (args) => {
        const result = spawnSync(process.execPath, [path.join(webRoot, "node_modules/wrangler/bin/wrangler.js"), ...args], {
          cwd: webRoot, env: process.env, stdio: "inherit", windowsHide: true,
        });
        if (result.error) throw result.error;
        return result.status ?? 1;
      },
    });
  } catch (error) {
    console.error(error.message);
    process.exitCode = 1;
  }
}
