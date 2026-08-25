"use client";

import { useEffect, useMemo, useState } from "react";
import {
  architectureSourceSpanHref, claimEvidence, codeModulesForNode, compactEvidenceStatus, dependencyRows,
  evidenceBadgeLabels, loadArchitectureCodeIndex, loadArchitectureEvidence,
  type ArchitectureCodeIndex, type ArchitectureEvidenceBundle, type CodeModule,
} from "../_lib/architecture-evidence";
import type { ArchitectureEdge, ArchitectureManifest, ArchitectureNode } from "../_lib/architecture-explorer";
import styles from "./ArchitectureExplorerView.module.css";

export function useArchitectureEvidenceBundle() {
  const [bundle, setBundle] = useState<ArchitectureEvidenceBundle | null>(null);
  const [error, setError] = useState(false);
  useEffect(() => { let live = true; loadArchitectureEvidence().then(value => { if (live) setBundle(value); }, () => { if (live) setError(true); }); return () => { live = false; }; }, []);
  return { bundle, error };
}

function useCodeIndex(enabled = true) {
  const [index, setIndex] = useState<ArchitectureCodeIndex | null>(null);
  const [error, setError] = useState(false);
  useEffect(() => {
    if (!enabled) return;
    let live = true; loadArchitectureCodeIndex().then(value => { if (live) setIndex(value); }, () => { if (live) setError(true); });
    return () => { live = false; };
  }, [enabled]);
  return { index, error };
}

function StateMessage({ error, label }: { error: boolean; label: string }) {
  return <p className={error ? styles.evidenceError : styles.evidenceLoading}>{error ? `${label} UNAVAILABLE` : `Loading ${label.toLowerCase()}…`}</p>;
}

export function EvidenceInspector({ manifest, node, edge, sha, bundle, error }: {
  manifest: ArchitectureManifest; node: ArchitectureNode; edge?: ArchitectureEdge | null; sha: string | null;
  bundle: ArchitectureEvidenceBundle | null; error: boolean;
}) {
  const entityId = edge ? `edge:${edge.id}` : `node:${node.id}`;
  const { index, error: codeError } = useCodeIndex(Boolean(bundle));
  if (!bundle) return <StateMessage error={error} label="Evidence" />;
  const evidence = claimEvidence(bundle, entityId); const badges = evidenceBadgeLabels(evidence.categories);
  const status = compactEvidenceStatus(evidence.categories);
  const traces = bundle.traces.filter(trace => evidence.contracts.some(contract => contract.id === trace.contract_id));
  const mutations = bundle.mutations.filter(mutation => evidence.contracts.some(contract => contract.id === mutation.contract_id));
  const bindings = evidence.claim?.bindings ?? [];
  const sourceFacts = index?.facts.filter(fact => bindings.includes(String(fact.path)) && Number(fact.line) > 0).slice(0, 18) ?? [];
  return <div className={styles.evidencePanel} data-evidence-status={status.label}>
    <div className={`${styles.evidenceVerdict} ${styles[`evidenceTone${status.tone}`]}`}><b aria-hidden="true">{status.symbol}</b><div><strong>{status.label}</strong><span>Semantic declaration and observed evidence remain separate.</span></div></div>
    <div className={styles.evidenceBadges}>{badges.map(label => <span key={label}>{label === "CONTRADICTED" ? "!" : label === "STALE" ? "◷" : "✓"} {label}</span>)}</div>
    <dl className={styles.evidenceFacts}>
      <div><dt>Semantic declaration</dt><dd>architecture/declarations/explorer.json · {entityId}</dd></div>
      <div><dt>Selector</dt><dd>{evidence.claim?.selector ?? "UNRESOLVED"}</dd></div>
      <div><dt>Last execution digest</dt><dd><code>{bundle.sourceDigest}</code> · {bundle.executionDigestState}</dd></div>
      <div><dt>Contracts</dt><dd>{evidence.contracts.map(item => `${item.id} (${item.status})`).join(" · ") || "None bound"}</dd></div>
      <div><dt>Bound tests</dt><dd>{evidence.contracts.flatMap(item => item.bound_test_ids).join(" · ") || "None bound"}</dd></div>
      <div><dt>Runtime traces</dt><dd>{traces.map(item => item.trace_id).join(" · ") || "None observed"}</dd></div>
      <div><dt>Mutation outcomes</dt><dd>{mutations.map(item => `${item.id}: ${item.outcome}`).join(" · ") || "Not designated"}</dd></div>
      <div><dt>Architecture diff</dt><dd>UNAVAILABLE · base metadata was not supplied to this build.</dd></div>
    </dl>
    <section className={styles.sourceEvidence}><h3>Exact source facts</h3>
      {!index ? <StateMessage error={codeError} label="Code index" /> : sourceFacts.length ? <ul>{sourceFacts.map(fact => {
        const path = String(fact.path); const line = Number(fact.line); const endLine = Number(fact.end_line);
        const href = architectureSourceSpanHref(manifest, path, sha, line, endLine);
        return <li key={String(fact.id)}>{href ? <a href={href} rel="noreferrer" target="_blank"><b>{String(fact.type)}</b><span>{path}:L{line}{endLine > line ? `–L${endLine}` : ""}</span><small>{String(fact.extractor)} · {String(fact.certainty)}</small></a> : <span>{path} · exact SHA unavailable</span>}</li>;
      })}</ul> : <p>No line-level fact is available for this declaration binding.</p>}
    </section>
  </div>;
}

function ModuleList({ modules, active, onSelect }: { modules: Array<CodeModule & { surface: string }>; active: CodeModule | null; onSelect: (module: CodeModule) => void }) {
  return <div className={styles.codeModuleList}>{modules.map(module => <button aria-pressed={active?.id === module.id} key={module.id} onClick={() => onSelect(module)} type="button"><b>{module.label.split("/").at(-1)}</b><span>{module.surface} · {module.children.length} symbols{module.shim ? " · THIN SHIM" : ""}</span></button>)}</div>;
}

export function CodeStructure({ manifest, node, sha }: { manifest: ArchitectureManifest; node: ArchitectureNode; sha: string | null }) {
  const { index, error } = useCodeIndex(); const [module, setModule] = useState<CodeModule | null>(null); const [symbolId, setSymbolId] = useState<string | null>(null);
  const modules = useMemo(() => index ? codeModulesForNode(index, node) : [], [index, node]);
  if (!index) return <StateMessage error={error} label="Code structure" />;
  const active = module && modules.some(item => item.id === module.id) ? module : null;
  const symbol = active?.children.find(item => item.id === symbolId) ?? null;
  return <section className={styles.codeStructure}>
    <header><span>GENERATED CODE HIERARCHY</span><strong>{index.hierarchy.label}</strong></header>
    <nav aria-label="Code structure breadcrumb"><button onClick={() => { setModule(null); setSymbolId(null); }} type="button">System Overview</button><span>›</span><b>{node.short_label}</b>{active ? <><span>›</span><button onClick={() => setSymbolId(null)} type="button">{active.label}</button></> : null}{symbol ? <><span>›</span><em>{symbol.name ?? symbol.route ?? symbol.id}</em></> : null}</nav>
    {!active ? <ModuleList active={active} modules={modules} onSelect={value => { setModule(value); setSymbolId(null); }} /> : <div className={styles.symbolList}>
      <button className={styles.codeBack} onClick={() => { setModule(null); setSymbolId(null); }} type="button">← Modules</button>
      {active.children.length ? active.children.map(item => {
        const href = architectureSourceSpanHref(manifest, item.path, sha, item.line, item.end_line);
        return <button aria-pressed={symbolId === item.id} key={item.id} onClick={() => setSymbolId(item.id)} type="button"><span>{item.type}</span><b>{item.name ?? item.route ?? item.id}</b>{href ? <a href={href} onClick={event => event.stopPropagation()} rel="noreferrer" target="_blank">L{item.line} ↗</a> : <small>L{item.line}</small>}</button>;
      }) : <p>No public top-level class or function was extracted for this module.</p>}
    </div>}
    {!modules.length ? <p>No generated module matches this semantic node&apos;s declared source bindings.</p> : null}
  </section>;
}

export function DependencyEvidenceReference({ selectedId }: { selectedId: string | null }) {
  const { index, error } = useCodeIndex(); const [mode, setMode] = useState<"OBSERVED" | "ALLOWED" | "VIOLATIONS">("OBSERVED");
  if (!index) return <StateMessage error={error} label="Dependency evidence" />;
  const packageId = selectedId?.replace(/^package-/, "") ?? null;
  const rows = dependencyRows(index, mode).filter(item => !packageId || item.from === packageId || item.to === packageId);
  const incoming = packageId ? index.dependencies.observed.filter(item => item.to === packageId) : [];
  return <section className={styles.dependencyEvidence}>
    <header><div><b>Actual ≠ allowed</b><span>Observed imports come from source. Policy is permission, not proof of use.</span></div><nav aria-label="Dependency evidence mode">{(["OBSERVED", "ALLOWED", "VIOLATIONS"] as const).map(item => <button aria-pressed={mode === item} key={item} onClick={() => setMode(item)} type="button">{item === "OBSERVED" ? "Observed imports" : item === "ALLOWED" ? "Allowed policy" : "Violations"}</button>)}</nav></header>
    {packageId ? <p className={styles.dependencySelection}><b>{packageId}</b> · incoming actual dependents: {incoming.map(item => item.from).join(", ") || "none"}</p> : null}
    <div className={styles.dependencyRows}>{rows.length ? rows.map(item => <p key={`${item.from}:${item.to}:${item.state}`}><b>{item.from}</b><span>→</span><b>{item.to}</b><em>{item.state}</em></p>) : <p>No {mode.toLowerCase()} dependency matches this selection.</p>}</div>
    {index.dependencies.unresolved.length ? <details><summary>UNRESOLVED dynamic relationships ({index.dependencies.unresolved.length})</summary><p>Static extraction could not establish these targets; they are not presented as verified imports.</p></details> : null}
  </section>;
}

export function TestEffectivenessSummary({ bundle, error }: { bundle: ArchitectureEvidenceBundle | null; error: boolean }) {
  if (!bundle) return <StateMessage error={error} label="Test effectiveness" />;
  const survivors = bundle.mutations.filter(item => item.outcome === "SURVIVED");
  return <section className={styles.effectiveness}>
    <header><span>CONTRACT EFFECTIVENESS</span><strong>{bundle.contracts.filter(item => item.status === "VERIFIED").length}/{bundle.contracts.length} verified</strong></header>
    <p className={styles.effectivenessPrinciple}><b>Many tests ≠ protected contract.</b><span>Mutation killed = the selected tests detected this intentional break.</span></p>
    <dl><div><dt>Tests protecting a contract</dt><dd>{bundle.testCounts.contract}</dd></div><div><dt>Touching only</dt><dd>{bundle.testCounts.touches_only}</dd></div><div><dt>Unclassified</dt><dd>{bundle.testCounts.unclassified}</dd></div><div><dt>Exact duplicate candidates</dt><dd>{bundle.duplicateCandidates.length}</dd></div></dl>
    <details open={survivors.length > 0}><summary>Surviving designated mutations ({survivors.length})</summary>{survivors.map(item => <p key={item.id}><b>{item.id}</b><span>{item.contract_id} · {item.platform} · SURVIVED</span></p>)}</details>
    <details><summary>Critical contract registry ({bundle.contracts.length})</summary>{bundle.contracts.map(item => <p key={item.id}><b>{item.id}</b><span>{item.status} · {item.bound_test_ids.length} bound test{item.bound_test_ids.length === 1 ? "" : "s"}</span></p>)}</details>
  </section>;
}
