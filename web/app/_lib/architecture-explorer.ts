declare const __AURUM_ARCHITECTURE_MANIFEST__: unknown;
declare const __AURUM_DEPLOYMENT__: { commit_sha?: string };

export type ArchitectureState = "CURRENT" | "PENDING" | "TARGET" | "PAUSED" | "RETAINED";
export type ArchitecturePathState = "CURRENT_PATH" | "PENDING_PATH" | "LEGACY_SHIM" | "TARGET_PATH";

export type ArchitectureNode = {
  id: string; label: string; short_label: string; kind: string;
  runtime_state: ArchitectureState; implementation_state: ArchitecturePathState;
  owner: string; summary: string;
  architecture: Record<"ownership" | "boundary" | "critical_path" | "bounded_work" | "incremental" | "failure_isolation", string>;
  inputs: string[]; outputs: string[]; code_paths: string[]; test_paths: string[];
  document_paths: string[]; tags: string[];
};
export type ArchitectureView = { id: string; label: string; summary: string; node_ids: string[]; drill_down: string };
export type ArchitectureEdge = { from: string; to: string; type: string };
export type ArchitectureManifest = {
  schema: "architecture-explorer-v1"; repository: string;
  views: ArchitectureView[]; nodes: ArchitectureNode[]; edges: ArchitectureEdge[];
  campaign: Array<{ id: string; label: string; branch: string; pr: number | null; state: "PENDING" }>;
};

const states = new Set(["CURRENT", "PENDING", "TARGET", "PAUSED", "RETAINED"]);

export function parseArchitectureManifest(value: unknown): ArchitectureManifest | null {
  if (!value || typeof value !== "object") return null;
  const candidate = value as Partial<ArchitectureManifest>;
  if (candidate.schema !== "architecture-explorer-v1"
      || !candidate.repository
      || !Array.isArray(candidate.views)
      || !Array.isArray(candidate.nodes)
      || !Array.isArray(candidate.edges)
      || !candidate.nodes.every(node => node && states.has(node.runtime_state))) return null;
  const ids = new Set(candidate.nodes.map(node => node.id));
  if (ids.size !== candidate.nodes.length
      || candidate.edges.some(edge => !ids.has(edge.from) || !ids.has(edge.to))) return null;
  return candidate as ArchitectureManifest;
}

export function bundledArchitectureManifest(): ArchitectureManifest | null {
  return parseArchitectureManifest(
    typeof __AURUM_ARCHITECTURE_MANIFEST__ === "undefined" ? null : __AURUM_ARCHITECTURE_MANIFEST__,
  );
}

export function architectureCommitSha(): string | null {
  const sha = typeof __AURUM_DEPLOYMENT__ === "undefined" ? "" : __AURUM_DEPLOYMENT__.commit_sha ?? "";
  return /^[0-9a-f]{40}$/i.test(sha) ? sha : null;
}

export function architectureGithubHref(manifest: Pick<ArchitectureManifest, "repository">, path: string, sha: string | null): string | null {
  if (!sha || !/^[0-9a-f]{40}$/i.test(sha) || path.includes("..") || path.startsWith("/")) return null;
  return `https://github.com/${manifest.repository}/blob/${sha}/${path}`;
}

export function architectureRelations(manifest: ArchitectureManifest, nodeId: string) {
  const upstream = manifest.edges.filter(edge => edge.to === nodeId).map(edge => edge.from);
  const downstream = manifest.edges.filter(edge => edge.from === nodeId).map(edge => edge.to);
  const connected = new Set([nodeId, ...upstream, ...downstream]);
  return {
    upstream,
    downstream,
    unaffected: manifest.nodes.filter(node => !connected.has(node.id)).map(node => node.id),
  };
}

export function searchArchitectureNodes(manifest: ArchitectureManifest, query: string, state: string) {
  const normalized = query.trim().toLocaleLowerCase();
  return manifest.nodes.filter(node => {
    if (state !== "ALL" && node.runtime_state !== state) return false;
    if (!normalized) return true;
    return [node.label, node.short_label, node.owner, node.summary, ...node.code_paths,
      ...node.test_paths, ...node.document_paths, ...node.tags]
      .some(value => value.toLocaleLowerCase().includes(normalized));
  });
}
