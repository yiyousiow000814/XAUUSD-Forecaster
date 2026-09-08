// Adapts the shared #321-derived source facts to the existing #304/#328 Explorer.
// No SQL, native arguments, local paths, credentials or runtime state are bundled.
import { openSync, readSync, closeSync, lstatSync, fstatSync, realpathSync, opendirSync, constants } from 'node:fs';
import { createHash } from 'node:crypto';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const maximumFileBytes = 2 * 1024 * 1024;
const maximumTransportBytes = 3 * 1024 * 1024;
const maximumParts = 40;
const maximumRecords = 10240;
const families = ['symbols', 'edges', 'tests'];
const digestPattern = /^[a-f0-9]{64}$/;
const invalid = () => { throw new Error('ARCHITECTURE_INDEX_TRANSPORT_INVALID'); };
const budget = () => { throw new Error('ARCHITECTURE_INDEX_BUDGET_EXCEEDED'); };
const object = value => value !== null && typeof value === 'object' && !Array.isArray(value);
const exactKeys = (value, keys) => object(value) && Object.keys(value).length === keys.length
  && keys.every(key => Object.hasOwn(value, key));
const sha256 = bytes => createHash('sha256').update(bytes).digest('hex');
const samePath = (left, right) => process.platform === 'win32'
  ? left.toLowerCase() === right.toLowerCase() : left === right;

// Match Python's ensure_ascii=False, sort_keys=True compact JSON, including
// non-BMP keys. JSON.stringify(object) would reorder integer-looking keys.
function canonicalJson(value, depth = 0) {
  if (depth > 32) invalid();
  if (value === null || typeof value === 'boolean') return JSON.stringify(value);
  if (typeof value === 'number') {
    if (!Number.isSafeInteger(value) || Object.is(value, -0)) invalid();
    return String(value);
  }
  if (typeof value === 'string') {
    for (const character of value) {
      const point = character.codePointAt(0);
      if (point >= 0xd800 && point <= 0xdfff) invalid();
    }
    return JSON.stringify(value);
  }
  if (Array.isArray(value)) return `[${value.map(item => canonicalJson(item, depth + 1)).join(',')}]`;
  if (!object(value)) invalid();
  return `{${Object.keys(value).sort((left, right) => Buffer.compare(Buffer.from(left), Buffer.from(right)))
    .map(key => `${canonicalJson(key, depth + 1)}:${canonicalJson(value[key], depth + 1)}`).join(',')}}`;
}

function readBoundedJson(path, maximum = maximumFileBytes) {
  // Open the capability first. On POSIX a substituted FIFO must not block
  // before fstat can reject it; no bytes are read from an unverified handle.
  const fd = openSync(path, constants.O_RDONLY
    | (process.platform === 'win32' ? 0 : constants.O_NOFOLLOW | constants.O_NONBLOCK));
  let bytes;
  let count = 0;
  try {
    const opened = fstatSync(fd);
    if (!opened.isFile()) invalid();
    const current = lstatSync(path);
    if (!current.isFile() || current.isSymbolicLink()
        || current.dev !== opened.dev || current.ino !== opened.ino
        || !samePath(realpathSync(dirname(path)), dirname(path))
        || !samePath(realpathSync(path), path)) invalid();
    bytes = Buffer.alloc(maximum + 1);
    while (count < bytes.length) {
      const read = readSync(fd, bytes, count, bytes.length - count, null);
      if (!read) break;
      count += read;
    }
  } finally { closeSync(fd); }
  if (count > maximum) budget();
  const content = bytes.subarray(0, count);
  const text = new TextDecoder('utf-8', { fatal: true, ignoreBOM: true }).decode(content);
  const value = JSON.parse(text);
  // Canonical physical bytes reject duplicate object keys, lossy numbers and
  // alternate encodings before either hashes or schema can authorize facts.
  if (`${canonicalJson(value)}\n` !== text) invalid();
  return { bytes: content, value };
}

function recordCount(counts) {
  if (!exactKeys(counts, families)) invalid();
  let total = 0;
  for (const family of families) {
    if (!Number.isSafeInteger(counts[family]) || counts[family] < 0 || counts[family] > maximumRecords) budget();
    total += counts[family];
  }
  if (total > maximumRecords) budget();
  return total;
}

function sourcePath(value) {
  return typeof value === 'string' && value.length > 0 && !value.includes('\\') && !value.includes(':')
    && value.split('/').every(segment => segment !== '' && segment !== '.' && segment !== '..');
}

export function readCurrentSourceIndex(path) {
  const absolute = resolve(path instanceof URL ? fileURLToPath(path) : path);
  const directory = dirname(absolute);
  if (!samePath(realpathSync(directory), directory)) invalid();
  const { bytes, value: manifest } = readBoundedJson(absolute);
  if (!exactKeys(manifest, ['schema', 'index', 'counts', 'logical_sha256', 'parts'])
      || manifest.schema !== 'critical-source-index-parts-v1'
      || !object(manifest.index) || manifest.index.schema !== 'critical-source-index-v1'
      || Object.hasOwn(manifest.index, 'observed')
      || !object(manifest.index.inputs)
      || !object(manifest.index.allowed?.views)
      || !digestPattern.test(manifest.index.source_input_digest)
      || !digestPattern.test(manifest.logical_sha256)
      || !Array.isArray(manifest.parts)) invalid();
  recordCount(manifest.counts);
  if (manifest.parts.length > maximumParts) budget();
  let totalBytes = bytes.length;
  const summedCounts = { symbols: 0, edges: 0, tests: 0 };
  const expectedFiles = new Set();
  for (const [ordinal, descriptor] of manifest.parts.entries()) {
    const file = `critical-facts-${String(ordinal).padStart(5, '0')}.json`;
    if (!exactKeys(descriptor, ['file', 'source_path', 'bytes', 'sha256', 'counts'])
        || descriptor.file !== file || !sourcePath(descriptor.source_path)
        || !Object.hasOwn(manifest.index.inputs, descriptor.source_path)
        || !digestPattern.test(manifest.index.inputs[descriptor.source_path])
        || !digestPattern.test(descriptor.sha256)) invalid();
    if (!Number.isSafeInteger(descriptor.bytes) || descriptor.bytes < 1
        || descriptor.bytes > maximumFileBytes) budget();
    if (!recordCount(descriptor.counts)) invalid();
    totalBytes += descriptor.bytes;
    if (totalBytes > maximumTransportBytes) budget();
    for (const family of families) summedCounts[family] += descriptor.counts[family];
    expectedFiles.add(file);
  }
  if (families.some(family => summedCounts[family] !== manifest.counts[family])) invalid();
  const allowedFiles = new Set([...expectedFiles, 'critical-index.json']);
  for (const view of Object.keys(manifest.index.allowed.views)) {
    if (!/^[a-z][a-z0-9-]{0,63}$/.test(view)) invalid();
    allowedFiles.add(`${view}.mmd`);
  }
  const inventory = opendirSync(directory);
  try {
    let entry;
    while ((entry = inventory.readSync()) !== null) {
      if (!allowedFiles.has(entry.name) || !entry.isFile() || entry.isSymbolicLink()) invalid();
    }
  } finally { inventory.closeSync(); }
  // All advertised byte/work/count bounds are checked before reading a part
  // or allocating the complete logical arrays. No partial union is returned.
  const observed = Object.fromEntries(families.map(family => [family, new Array(manifest.counts[family])]));
  for (const descriptor of manifest.parts) {
    const part = readBoundedJson(join(directory, descriptor.file), descriptor.bytes);
    if (part.bytes.length !== descriptor.bytes || sha256(part.bytes) !== descriptor.sha256
        || !exactKeys(part.value, ['schema', 'source_input_digest', 'source_path', 'observed'])
        || part.value.schema !== 'critical-source-facts-v1'
        || part.value.source_input_digest !== manifest.index.source_input_digest
        || part.value.source_path !== descriptor.source_path
        || !exactKeys(part.value.observed, families)) invalid();
    for (const family of families) {
      const entries = part.value.observed[family];
      if (!Array.isArray(entries) || entries.length !== descriptor.counts[family]) invalid();
      for (const entry of entries) {
        if (!Array.isArray(entry) || entry.length !== 2) invalid();
        const [ordinal, record] = entry;
        if (!Number.isSafeInteger(ordinal) || ordinal < 0 || ordinal >= observed[family].length
            || Object.hasOwn(observed[family], ordinal) || !object(record)) invalid();
        if (family === 'edges' && typeof record.source !== 'string') invalid();
        const recordSource = family === 'edges' ? record.source.split('::', 1)[0] : record.path;
        if (recordSource !== descriptor.source_path) invalid();
        observed[family][ordinal] = record;
      }
    }
  }
  for (const family of families) {
    if (Object.keys(observed[family]).length !== manifest.counts[family]) invalid();
  }
  const index = { ...manifest.index, observed };
  if (sha256(Buffer.from(`${canonicalJson(index)}\n`)) !== manifest.logical_sha256) invalid();
  return index;
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
    return { id, label: id, summary: 'Selected roots + one syntactic candidate hop. Indexed symbols remain in Code Structure; explicit symbol scopes are not whole-file coverage.',
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
