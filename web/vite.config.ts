import vinext from "vinext";
import { defineConfig } from "vite";
import { sites } from "./build/sites-vite-plugin";
import { execFileSync } from "node:child_process";
import { copyFileSync, existsSync, mkdirSync } from "node:fs";
import { resolve } from "node:path";
import {
  compactPreviewLearning,
  compactPreviewNewsIndex,
  compactPreviewAudit,
  compactPreviewAuditDetail,
  compactPreviewStatus,
} from "./build/preview-learning";

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
  const git = (...args: string[]) => {
    try {
      return execFileSync("git", args, {
        cwd: resolve(".."), encoding: "utf8",
      }).trim();
    } catch {
      return "";
    }
  };
  const ciBranch = process.env.WORKERS_CI_BRANCH ?? "";
  const ciCommit = process.env.WORKERS_CI_COMMIT_SHA ?? "";
  const branch = ciBranch || git("branch", "--show-current");
  const commit = ciCommit || git("rev-parse", "HEAD");
  const isWorkerPreview = Boolean(ciBranch && ciCommit && ciBranch !== "main");
  let previewBundle: unknown = null;
  if (isWorkerPreview) {
    const python = process.platform === "win32" ? "python" : "python3";
    const output = execFileSync(
      python,
      [resolve("../scripts/build_preview_bundle.py"), "--branch", branch, "--commit", commit],
      { cwd: resolve("."), encoding: "utf8", maxBuffer: 5 * 1024 * 1024 },
    );
    previewBundle = JSON.parse(output);
    if (previewBundle && typeof previewBundle === "object") {
      const bundle = previewBundle as Record<string, unknown>;
      if (bundle.learning && typeof bundle.learning === "object") {
        bundle.learning_summary = compactPreviewLearning(bundle.learning as Record<string, unknown>);
        delete bundle.learning;
      }
      if (bundle.status && typeof bundle.status === "object") {
        bundle.status = compactPreviewStatus(bundle.status as Record<string, unknown>);
      }
      if (bundle.audit && typeof bundle.audit === "object") {
        bundle.audit = compactPreviewAudit(bundle.audit as Record<string, unknown>);
      }
      for (const key of ["audit_briefs", "audit_stories", "audit_decisions"]) {
        if (bundle[key] && typeof bundle[key] === "object") {
          bundle[key] = compactPreviewAuditDetail(bundle[key] as Record<string, unknown>);
        }
      }
      if (bundle.news_index && typeof bundle.news_index === "object") {
        bundle.news_index = compactPreviewNewsIndex(bundle.news_index as Record<string, unknown>);
      }
    }
  }

  return {
    server: isCodexSeatbeltSandbox
      ? { watch: { useFsEvents: false, usePolling: true } }
      : undefined,
    plugins: [
      vinext({ prerender: { routes: "*" } }),
      {
        name: "aurum-vinext-lazy-entry-prerender",
        closeBundle() {
          const source = resolve("dist/server/vinext-client-assets.js");
          const destination = resolve(
            "dist/server/_next/static/vinext-client-assets.js",
          );
          if (!existsSync(source)) return;
          mkdirSync(resolve("dist/server/_next/static"), { recursive: true });
          copyFileSync(source, destination);
        },
      },
      sites(),
      cloudflare({
        viteEnvironment: { name: "rsc", childEnvironments: ["ssr"] },
        configPath: "./wrangler.jsonc",
      }),
    ],
    define: {
      __AURUM_PREVIEW_BUNDLE__: JSON.stringify(previewBundle),
      __AURUM_DEPLOYMENT__: JSON.stringify({
        branch,
        commit_sha: commit,
        is_preview: isWorkerPreview,
      }),
    },
  };
});
