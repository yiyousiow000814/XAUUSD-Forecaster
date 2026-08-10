import vinext from "vinext";
import { defineConfig } from "vite";
import { sites } from "./build/sites-vite-plugin";
import { execFileSync } from "node:child_process";
import { resolve } from "node:path";

// macOS Seatbelt blocks FSEvents, so Codex previews need polling for HMR.
const isCodexSeatbeltSandbox = process.env.CODEX_SANDBOX === "seatbelt";

export default defineConfig(async () => {
  // Keep Wrangler and Miniflare state project-local. These are non-secret tool
  // settings; application environment belongs in ignored `.env*` files.
  process.env.WRANGLER_WRITE_LOGS ??= "false";
  process.env.WRANGLER_LOG_PATH ??= ".wrangler/logs";
  process.env.MINIFLARE_REGISTRY_PATH ??= ".wrangler/registry";

  // Wrangler snapshots its log path while the Cloudflare plugin is imported.
  const { cloudflare } = await import("@cloudflare/vite-plugin");
  const branch = process.env.WORKERS_CI_BRANCH ?? "";
  const commit = process.env.WORKERS_CI_COMMIT_SHA ?? "";
  const isWorkerPreview = Boolean(branch && commit && branch !== "main");
  let previewBundle: unknown = null;
  if (isWorkerPreview) {
    const python = process.platform === "win32" ? "python" : "python3";
    const output = execFileSync(
      python,
      [resolve("../scripts/build_preview_bundle.py"), "--branch", branch, "--commit", commit],
      { cwd: resolve("."), encoding: "utf8", maxBuffer: 5 * 1024 * 1024 },
    );
    previewBundle = JSON.parse(output);
  }

  return {
    server: isCodexSeatbeltSandbox
      ? { watch: { useFsEvents: false, usePolling: true } }
      : undefined,
    plugins: [
      vinext(),
      sites(),
      cloudflare({
        viteEnvironment: { name: "rsc", childEnvironments: ["ssr"] },
        configPath: "./wrangler.jsonc",
      }),
    ],
    define: {
      __AURUM_PREVIEW_BUNDLE__: JSON.stringify(previewBundle),
      __AURUM_DEPLOYMENT__: JSON.stringify({ branch, commit_sha: commit }),
    },
  };
});
