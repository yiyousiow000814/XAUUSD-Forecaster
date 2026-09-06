import type { ArchitectureManifest, ArchitectureNode } from "./architecture-explorer";

export type EvidenceCategory = "DECLARED" | "STATIC_MATCH" | "TEST_BOUND" | "TEST_EXECUTED" | "RUNTIME_OBSERVED" | "MUTATION_KILLED" | "STALE" | "CONTRADICTED" | "UNRESOLVED";
export type EvidenceClaim = {
  claim_id: string; categories: EvidenceCategory[]; bindings: string[]; selector: string;
  relationship_static_match?: boolean; required?: boolean;
};
export type ContractEvidence = {
  id: string; statement: string; owner: string; risk: string; status: "VERIFIED" | "PARTIAL" | "STALE" | "CONTRADICTED";
  fact_ids: string[]; bound_test_ids: string[]; categories: EvidenceCategory[]; missing_evidence: string[];
  mutation_ids?: string[]; mutation_outcomes?: string[];
};
export type RuntimeTrace = {
  trace_id: string; contract_id: string; test_id: string; source_digest: string; probe_kind: string;
  source: { path: string; line: number }; events: Array<{ event_type: string; sequence: number }>;
};
export type MutationEvidence = {
  id: string; contract_id: string; platform: string; outcome: "KILLED" | "SURVIVED" | "INVALID" | "TIMEOUT" | "ERROR";
  reason: string; mutation_duration_ms: number;
};
export type ArchitectureEvidenceBundle = {
  sourceDigest: string;
  claims: EvidenceClaim[];
  contracts: ContractEvidence[];
  traces: RuntimeTrace[];
  mutations: MutationEvidence[];
  testCounts: { collected: number; contract: number; touches_only: number; unclassified: number };
  executionDigestState: string;
  duplicateCandidates: unknown[];
};
export type CodeSymbol = { id: string; type: string; path: string; line: number; end_line: number; name?: string; route?: string };
export type CodeModule = { id: string; label: string; path: string; shim: boolean; children: CodeSymbol[] };
export type CodeSurface = { id: string; label: string; children: CodeModule[] };
export type ArchitectureCodeIndex = {
  sourceDigest: string; facts: Array<Record<string, unknown>>; hierarchy: { id: string; label: string; children: CodeSurface[] };
  counts: Record<string, number>;
  dependencies: {
    observed: Array<{ from: string; to: string; policy: string }>;
    allowed_unused: Array<{ from: string; to: string }>;
    unlisted_observed: Array<{ from: string; to: string }>;
    violations: Array<{ from: string; to: string }>;
    unresolved: string[];
  };
};

let evidencePromise: Promise<ArchitectureEvidenceBundle> | null = null;
let codePromise: Promise<ArchitectureCodeIndex> | null = null;

function record(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" ? value as Record<string, unknown> : {};
}

export function parseArchitectureEvidence(value: unknown): ArchitectureEvidenceBundle {
  const root = record(value); const evidence = record(root.evidence_index); const tests = record(root.test_evidence);
  const runtime = record(root.runtime_evidence); const mutations = record(root.mutation_report); const digest = record(root.source_digest);
  if (!Array.isArray(evidence.claims) || !Array.isArray(tests.contracts) || !Array.isArray(runtime.traces)
      || !Array.isArray(mutations.mutations) || typeof digest.source_digest !== "string") throw new Error("Invalid architecture evidence bundle");
  return {
    sourceDigest: digest.source_digest,
    claims: evidence.claims as EvidenceClaim[], contracts: tests.contracts as ContractEvidence[], traces: runtime.traces as RuntimeTrace[],
    mutations: mutations.mutations as MutationEvidence[], testCounts: record(tests.counts) as ArchitectureEvidenceBundle["testCounts"],
    executionDigestState: String(tests.execution_digest_state ?? "UNAVAILABLE"),
    duplicateCandidates: Array.isArray(mutations.duplicate_test_ast_fingerprints) ? mutations.duplicate_test_ast_fingerprints : [],
  };
}

export function parseArchitectureCodeIndex(value: unknown): ArchitectureCodeIndex {
  const root = record(value); const index = record(root.code_index);
  if (!Array.isArray(index.facts) || !index.hierarchy || !index.dependencies || typeof index.source_digest !== "string") {
    throw new Error("Invalid architecture code index");
  }
  return {
    sourceDigest: index.source_digest, facts: index.facts as Array<Record<string, unknown>>,
    hierarchy: index.hierarchy as ArchitectureCodeIndex["hierarchy"], counts: record(index.counts) as Record<string, number>,
    dependencies: index.dependencies as ArchitectureCodeIndex["dependencies"],
  };
}

export function loadArchitectureEvidence() {
  evidencePromise ??= import("virtual:aurum-architecture-evidence").then(module => parseArchitectureEvidence(module.default));
  return evidencePromise;
}

export function loadArchitectureCodeIndex() {
  codePromise ??= import("virtual:aurum-architecture-code-index").then(module => parseArchitectureCodeIndex(module.default));
  return codePromise;
}

export function claimEvidence(bundle: ArchitectureEvidenceBundle | null, claimId: string) {
  const claim = bundle?.claims.find(item => item.claim_id === claimId) ?? null;
  const contracts = bundle?.contracts.filter(item => item.fact_ids.includes(claimId)) ?? [];
  const categories = new Set<EvidenceCategory>(claim?.categories ?? []);
  contracts.flatMap(item => item.categories).forEach(category => categories.add(category));
  for (const contract of contracts) {
    const mutations = bundle?.mutations.filter(item => item.contract_id === contract.id) ?? [];
    if (mutations.some(item => item.outcome === "KILLED")) categories.add("MUTATION_KILLED");
    if (contract.status === "STALE") categories.add("STALE");
    if (contract.status === "CONTRADICTED") categories.add("CONTRADICTED");
  }
  return { claim, contracts, categories: [...categories] };
}

export function compactEvidenceStatus(categories: Iterable<EvidenceCategory>) {
  const values = new Set(categories);
  if (values.has("CONTRADICTED")) return { label: "CONTRADICTED", symbol: "!", tone: "danger" };
  if (values.has("STALE")) return { label: "STALE", symbol: "◷", tone: "warning" };
  if (values.has("UNRESOLVED")) return { label: "UNRESOLVED", symbol: "?", tone: "warning" };
  if (values.has("STATIC_MATCH")) return { label: "STATIC MATCH", symbol: "✓", tone: "strong" };
  return { label: "DECLARED ONLY", symbol: "◇", tone: "neutral" };
}

export function evidenceBadgeLabels(categories: Iterable<EvidenceCategory>) {
  const values = new Set(categories); const labels: string[] = [];
  for (const [category, label] of [
    ["CONTRADICTED", "CONTRADICTED"], ["STALE", "STALE"], ["UNRESOLVED", "UNRESOLVED"],
    ["STATIC_MATCH", "STATIC MATCH"], ["TEST_EXECUTED", "TEST EXECUTED"], ["RUNTIME_OBSERVED", "RUNTIME OBSERVED"],
    ["MUTATION_KILLED", "MUTATION KILLED"],
  ] as const) if (values.has(category)) labels.push(label);
  if (!values.has("STATIC_MATCH") && values.has("DECLARED")) labels.push("DECLARED ONLY");
  return labels;
}

export function architectureSourceSpanHref(
  manifest: Pick<ArchitectureManifest, "repository">, path: string, sha: string | null, line?: number, endLine?: number,
) {
  if (!sha || !/^[0-9a-f]{40}$/i.test(sha) || path.includes("..") || path.startsWith("/") || path.includes("\\")) return null;
  const span = line && line > 0 ? `#L${line}${endLine && endLine > line ? `-L${endLine}` : ""}` : "";
  return `https://github.com/${manifest.repository}/blob/${sha}/${path}${span}`;
}

export function codeModulesForNode(index: ArchitectureCodeIndex, node: ArchitectureNode) {
  const matches = (path: string) => node.code_paths.some(binding => path === binding.replace(/\/$/, "") || path.startsWith(`${binding.replace(/\/$/, "")}/`));
  return index.hierarchy.children.flatMap(surface => surface.children.filter(module => matches(module.path)).map(module => ({ ...module, surface: surface.label })));
}

export function dependencyRows(index: ArchitectureCodeIndex, mode: "OBSERVED" | "ALLOWED" | "VIOLATIONS") {
  if (mode === "OBSERVED") return index.dependencies.observed.map(item => ({ ...item, state: item.policy }));
  if (mode === "VIOLATIONS") return index.dependencies.violations.map(item => ({ ...item, state: "PROHIBITED" }));
  const observedAllowed = index.dependencies.observed.filter(item => item.policy === "ALLOWED").map(item => ({ ...item, state: "OBSERVED" }));
  const unused = index.dependencies.allowed_unused.map(item => ({ ...item, state: "ALLOWED_UNUSED" }));
  return [...observedAllowed, ...unused].sort((left, right) => `${left.from}:${left.to}`.localeCompare(`${right.from}:${right.to}`));
}
