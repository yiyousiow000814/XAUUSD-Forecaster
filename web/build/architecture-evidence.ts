import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import type { Plugin } from "vite";

const MODULES = {
  "virtual:aurum-architecture-evidence": [
    "evidence-index.json", "test-evidence.json", "runtime-evidence.json",
    "mutation-report.json", "source-digest.json",
  ],
  "virtual:aurum-architecture-code-index": ["code-index.json"],
} as const;

const LIMITS = {
  "evidence-index.json": 256_000,
  "test-evidence.json": 800_000,
  "runtime-evidence.json": 256_000,
  "mutation-report.json": 512_000,
  "source-digest.json": 512_000,
  "code-index.json": 4_500_000,
} as const;

export function architectureEvidenceModules(root = resolve("..")): Plugin {
  const prefix = "\0";
  return {
    name: "aurum-architecture-evidence-modules",
    enforce: "pre",
    resolveId(id) {
      return Object.hasOwn(MODULES, id) ? `${prefix}${id}` : null;
    },
    load(id) {
      const publicId = id.startsWith(prefix) ? id.slice(1) : id;
      if (!Object.hasOwn(MODULES, publicId)) return null;
      const documents: Record<string, unknown> = {};
      for (const name of MODULES[publicId as keyof typeof MODULES]) {
        const raw = readFileSync(resolve(root, "architecture/generated", name), "utf8");
        if (new TextEncoder().encode(raw).byteLength > LIMITS[name]) {
          throw new Error(`Architecture artifact ${name} exceeds its private lazy-module bound`);
        }
        const value = JSON.parse(raw) as Record<string, unknown>;
        const projected = name === "test-evidence.json"
          ? { contracts: value.contracts, counts: value.counts, execution_digest_state: value.execution_digest_state, source_digest: value.source_digest }
          : name === "mutation-report.json"
            ? { mutations: (value.mutations as Array<Record<string, unknown>>).map(item => ({
                id: item.id, contract_id: item.contract_id, platform: item.platform, outcome: item.outcome,
                reason: item.reason, mutation_duration_ms: item.mutation_duration_ms,
              })), duplicate_test_ast_fingerprints: value.duplicate_test_ast_fingerprints, source_digest: value.source_digest, status: value.status }
            : name === "source-digest.json" ? { source_digest: value.source_digest } : value;
        documents[name.replace(".json", "").replaceAll("-", "_")] = projected;
      }
      return `export default ${JSON.stringify(documents)};`;
    },
  };
}
