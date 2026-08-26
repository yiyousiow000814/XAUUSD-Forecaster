import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import {
  architectureSourceSpanHref, claimEvidence, codeModulesForNode, compactEvidenceStatus,
  dependencyRows, evidenceBadgeLabels, parseArchitectureCodeIndex, parseArchitectureEvidence,
} from "../app/_lib/architecture-evidence.ts";

const root = new URL("../../", import.meta.url);
const generated = name => JSON.parse(readFileSync(new URL(`architecture/generated/${name}`, root), "utf8"));
const bundle = parseArchitectureEvidence({
  evidence_index: generated("evidence-index.json"), test_evidence: generated("test-evidence.json"),
  runtime_evidence: generated("runtime-evidence.json"), mutation_report: generated("mutation-report.json"),
  source_digest: generated("source-digest.json"),
});
const codeIndex = parseArchitectureCodeIndex({ code_index: generated("code-index.json") });
const viewSource = readFileSync(new URL("../app/_views/ArchitectureExplorerView.tsx", import.meta.url), "utf8");
const panelsSource = readFileSync(new URL("../app/_views/ArchitectureEvidencePanels.tsx", import.meta.url), "utf8");

test("evidence 1: static structural evidence is never labelled declared only", () => {
  for (const claim of bundle.claims.filter(item => item.categories.includes("STATIC_MATCH"))) {
    const result = claimEvidence(bundle, claim.claim_id);
    assert.equal(compactEvidenceStatus(result.categories).label, "STATIC MATCH", claim.claim_id);
    assert.equal(evidenceBadgeLabels(result.categories).includes("DECLARED ONLY"), false, claim.claim_id);
  }
});

test("evidence 2: declaration alone never receives a verified structural badge", () => {
  const claim = bundle.claims.find(item => item.categories.length === 1 && item.categories[0] === "DECLARED");
  assert.ok(claim);
  const result = claimEvidence(bundle, claim.claim_id);
  assert.equal(compactEvidenceStatus(result.categories).label, "DECLARED ONLY");
  assert.equal(evidenceBadgeLabels(result.categories).includes("STATIC MATCH"), false);
});

test("evidence 3: stale and contradicted states outrank positive evidence", () => {
  assert.equal(compactEvidenceStatus(["DECLARED", "STATIC_MATCH", "STALE"]).label, "STALE");
  assert.equal(compactEvidenceStatus(["DECLARED", "STATIC_MATCH", "CONTRADICTED"]).label, "CONTRADICTED");
  assert.match(panelsSource, /data-evidence-status/);
});

test("evidence 4: source links bind exact SHA and line span", () => {
  const sha = "a".repeat(40);
  assert.equal(architectureSourceSpanHref({ repository: "owner/repo" }, "pkg/file.py", sha, 12, 18),
    `https://github.com/owner/repo/blob/${sha}/pkg/file.py#L12-L18`);
  assert.equal(architectureSourceSpanHref({ repository: "owner/repo" }, "../secret", sha, 1, 2), null);
  assert.equal(architectureSourceSpanHref({ repository: "owner/repo" }, "pkg/file.py", "main", 1, 2), null);
});

test("evidence 5: code hierarchy follows generated fixture additions and removals", () => {
  const node = { code_paths: ["pkg/decision"] };
  const fixture = {
    sourceDigest: "digest", facts: [], counts: {}, dependencies: { observed: [], allowed_unused: [], unlisted_observed: [], violations: [], unresolved: [] },
    hierarchy: { id: "repository", label: "repo", children: [{ id: "surface:python", label: "python", children: [
      { id: "module:one", label: "pkg.decision.one", path: "pkg/decision/one.py", shim: false, children: [] },
    ] }] },
  };
  assert.deepEqual(codeModulesForNode(fixture, node).map(item => item.id), ["module:one"]);
  fixture.hierarchy.children[0].children.push({ id: "module:two", label: "pkg.decision.two", path: "pkg/decision/two.py", shim: false, children: [] });
  assert.deepEqual(codeModulesForNode(fixture, node).map(item => item.id), ["module:one", "module:two"]);
  fixture.hierarchy.children[0].children.shift();
  assert.deepEqual(codeModulesForNode(fixture, node).map(item => item.id), ["module:two"]);
});

test("evidence 6: observed imports, allowed policy, and violations stay distinct", () => {
  const observed = dependencyRows(codeIndex, "OBSERVED"); const allowed = dependencyRows(codeIndex, "ALLOWED");
  const violations = dependencyRows(codeIndex, "VIOLATIONS");
  assert.ok(observed.every(item => item.state !== "ALLOWED_UNUSED"));
  assert.ok(allowed.some(item => item.state === "ALLOWED_UNUSED"));
  assert.deepEqual(violations, codeIndex.dependencies.violations.map(item => ({ ...item, state: "PROHIBITED" })));
});

test("evidence 7: surviving mutations remain explicit", () => {
  assert.deepEqual(bundle.mutations.filter(item => item.outcome === "SURVIVED").map(item => item.id).sort(), [
    "MUT-EVIDENCE-APPEND-ONLY", "MUT-RELEASE-PREVIEW-PROMOTION", "MUT-SYNC-HEARTBEAT-FIRST",
  ]);
  assert.match(panelsSource, /Surviving designated mutations/);
});

test("evidence 8: raw test count is secondary and never presented as trust", () => {
  assert.match(panelsSource, /Many tests ≠ protected contract/);
  assert.doesNotMatch(panelsSource, /trust score|safety score/i);
});

test("evidence 9: Explorer states the generated boundary and exposes Evidence without a runtime API", () => {
  assert.match(viewSource, /Generated from repository source at/);
  assert.match(viewSource, /Semantic declarations are shown separately from observed evidence/);
  assert.match(viewSource, /"evidence"/);
  assert.doesNotMatch(viewSource + panelsSource, /fetch\s*\(|\/api\/architecture|\/architecture\/api/);
});
