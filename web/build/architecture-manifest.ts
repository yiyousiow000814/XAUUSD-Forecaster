import { readFileSync } from "node:fs";
import { resolve } from "node:path";

const MANIFEST_LIMIT = 65_536;

export function loadArchitectureManifest(root = resolve("..")) {
  const path = resolve(root, "architecture/manifest.json");
  const raw = readFileSync(path, "utf8");
  if (new TextEncoder().encode(raw).byteLength > MANIFEST_LIMIT) {
    throw new Error(`Architecture manifest exceeds ${MANIFEST_LIMIT} bytes`);
  }
  const manifest = JSON.parse(raw) as Record<string, unknown>;
  if (manifest.schema !== "architecture-explorer-v1"
      || !Array.isArray(manifest.nodes)
      || !Array.isArray(manifest.edges)
      || !Array.isArray(manifest.views)) {
    throw new Error("Architecture manifest has an invalid build contract");
  }
  return manifest;
}
