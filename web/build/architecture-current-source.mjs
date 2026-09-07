// Adapts the shared #321-derived source facts to the existing #304/#328 Explorer.
// No SQL, native arguments, local paths, credentials or runtime state are bundled.
import { openSync, readSync, closeSync } from 'node:fs';

export function readCurrentSourceIndex(path) {
  const maximum = 2 * 1024 * 1024;
  const bytes = Buffer.alloc(maximum + 1);
  const fd = openSync(path, 'r');
  let count = 0;
  try {
    while (count < bytes.length) {
      const read = readSync(fd, bytes, count, bytes.length - count, null);
      if (!read) break;
      count += read;
    }
  } finally { closeSync(fd); }
  if (count > maximum) throw new Error('ARCHITECTURE_INDEX_BUDGET_EXCEEDED');
  return JSON.parse(bytes.subarray(0, count).toString('utf8'));
}

export function projectCurrentSource(index) {
  if (index?.coverage?.scope !== 'DECLARED_CRITICAL_SLICES_ONLY'
      || !/^[a-f0-9]{64}$/.test(index.source_input_digest)
      || !Array.isArray(index.observed?.symbols) || !Array.isArray(index.observed?.edges)) {
    throw new Error('ARCHITECTURE_CURRENT_SOURCE_INVALID');
  }
  const symbols = index.observed.symbols;
  const symbolById = new Map(symbols.map(symbol => [symbol.id, symbol]));
  const selections = Object.entries(index.allowed.views);
  const dimensions = {
    ownership: 'UNKNOWN: file location is syntactic ownership, not runtime authority.',
    boundary: 'Source AST only; dynamic dispatch is unresolved.',
    critical_path: 'Selected critical slice; no production execution inferred.',
    bounded_work: 'UNKNOWN: source syntax does not prove operational work bounds.',
    incremental: 'UNKNOWN: source syntax does not prove committed cursor progress.',
    failure_isolation: 'UNKNOWN: requires retained runtime or formal evidence.',
  };
  const nodes = symbols.map(symbol => ({
    id: symbol.id, label: symbol.name, short_label: symbol.name, kind: 'COMPONENT',
    runtime_state: 'UNKNOWN', implementation_state: 'SOURCE_ONLY',
    owner: `Syntactic file: ${symbol.path}`, summary: `${symbol.path}:${symbol.line} — runtime binding UNKNOWN`,
    purpose: 'Inspected source symbol; no business ownership or execution claim.',
    architecture: dimensions, code_paths: [symbol.path], test_paths: [],
    document_paths: ['architecture/README.md'], tags: [symbol.language, 'SOURCE_OBSERVED'],
  }));
  const edges = index.observed.edges.filter(edge => edge.kind === 'calls'
    && symbolById.has(edge.source) && symbolById.has(edge.candidate_symbol)).map((edge, ordinal) => ({
    id: `source-call-${ordinal}`, from: edge.source, to: edge.candidate_symbol,
    label: 'Syntactic candidate · UNKNOWN', kind: 'DEPENDENCY', criticality: 'UNKNOWN',
    description: `AST call at line ${edge.line}; runtime dispatch, permission and criticality NOT_PROVEN.`,
  }));
  const overviewIds = [];
  const views = selections.map(([id, selection]) => {
    const subsystemId = `slice:${id}`;
    overviewIds.push(subsystemId);
    nodes.push({ id: subsystemId, label: id, short_label: id, kind: 'SUBSYSTEM',
      runtime_state: 'UNKNOWN', implementation_state: 'SOURCE_ONLY', owner: 'Declared source selection',
      summary: `${selection.files.length} selected source files; not a complete subsystem proof.`,
      purpose: 'Drill into source-observed symbols; layout order does not imply transaction order.',
      architecture: dimensions, code_paths: selection.files, test_paths: selection.tests,
      document_paths: ['architecture/README.md'], tags: ['DECLARED_SELECTION'], subsystem_view: id });
    const roots = new Set(selection.roots);
    const displayed = new Set([...roots, ...edges.filter(edge => roots.has(edge.from)).map(edge => edge.to)]);
    if (!displayed.size || [...displayed].some(key => !symbolById.has(key))) throw new Error('ARCHITECTURE_ROOT_UNRESOLVED');
    const edgeIds = edges.filter(edge => displayed.has(edge.from) && displayed.has(edge.to)).map(edge => edge.id);
    return { id, label: id, summary: 'Selected roots + one syntactic candidate hop. Full selected-file symbols remain in Code Structure.',
      layout_direction: 'LR', node_ids: [...displayed], edge_ids: edgeIds,
      entry_node: selection.roots[0], primary_path: [selection.roots[0]],
      lanes: [{ id: `${id}-source`, label: 'Source syntax · runtime UNKNOWN', node_ids: [...displayed] }],
      navigation: { role: 'ADVANCED', audience: 'ADVANCED', parent_view: 'system-overview' },
      disclosure: { default_mode: 'SELECTED_NODE', always_visible_edge_ids: [], secondary_edge_ids: edgeIds, allow_show_all: true } };
  });
  views.unshift({ id: 'system-overview', label: 'Critical source slices', summary: `${selections.length} selected paths, not the full system. Runtime, allowed dependencies and transaction semantics remain UNKNOWN.`,
    layout_direction: 'LR', node_ids: overviewIds, edge_ids: [], entry_node: overviewIds[0], primary_path: [overviewIds[0]],
    layout_hints: { mode: 'SEMANTIC_GRID', auto_place_unlisted: true,
      rank_groups: overviewIds.map((id, ordinal) => ({ id: `display-order-${ordinal}`, node_ids: [id] })),
      track_groups: [{ id: 'display-track', node_ids: overviewIds }], convergences: [] },
    lanes: [{ id: 'slices', label: 'Declared source selections · no flow implied', node_ids: overviewIds }],
    navigation: { role: 'OVERVIEW', audience: 'BEGINNER' },
    disclosure: { default_mode: 'SELECTED_NODE', always_visible_edge_ids: [], secondary_edge_ids: [], allow_show_all: false } });
  const visibleNodes = new Set(views.flatMap(view => view.node_ids));
  const visibleEdges = new Set(views.flatMap(view => view.edge_ids));
  const manifest = { schema: 'architecture-explorer-v2', repository: 'yiyousiow000814/XAUUSD-Forecaster',
    byte_limit: 300000, nodes: nodes.filter(node => visibleNodes.has(node.id)),
    edges: edges.filter(edge => visibleEdges.has(edge.id)), views, scenarios: [], failure_impacts: [], campaign: [] };
  if (Buffer.byteLength(JSON.stringify(manifest)) > manifest.byte_limit) throw new Error('ARCHITECTURE_EXPLORER_BUDGET_EXCEEDED');
  const facts = symbols.map(symbol => ({ id: symbol.id, type: 'symbol', path: symbol.path,
    line: symbol.line, end_line: symbol.end_line, name: symbol.name, extractor: `${symbol.language}-ast`, certainty: 'SOURCE_SYNTAX_ONLY' }));
  const modules = [...new Set(symbols.map(symbol => symbol.path))].sort().map(path => ({
    id: path, label: path, path, shim: false, children: facts.filter(fact => fact.path === path) }));
  return { manifest,
    code: { code_index: { source_digest: index.source_input_digest, facts,
      hierarchy: { id: 'source', label: 'Selected current-source files only', children: [{ id: 'critical', label: 'Critical slices', children: modules }] },
      counts: { symbols: facts.length }, dependencies: { observed: [], allowed_unused: [], unlisted_observed: [], violations: [],
        unresolved: ['Dependency policy NOT_EVALUATED; runtime binding UNKNOWN.'] } } },
    evidence: { source_digest: { source_digest: index.source_input_digest },
      evidence_index: { claims: manifest.nodes.map(node => ({ claim_id: `node:${node.id}`, categories: ['UNRESOLVED'],
        bindings: node.code_paths, selector: node.id })) },
      test_evidence: { contracts: [], execution_digest_state: 'UNAVAILABLE', counts: { collected: index.observed.tests.length, contract: 0, touches_only: 0, unclassified: index.observed.tests.length } },
      runtime_evidence: { traces: [] }, mutation_report: { mutations: [] } } };
}
