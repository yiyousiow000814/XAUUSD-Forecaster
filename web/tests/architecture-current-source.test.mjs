import assert from 'node:assert/strict';
import { readFileSync, mkdtempSync, writeFileSync, unlinkSync, readdirSync, rmdirSync, symlinkSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import { build } from 'esbuild';
import { renderToStaticMarkup } from 'react-dom/server';
import { projectCurrentSource, readCurrentSourceIndex } from '../build/architecture-current-source.mjs';
import { parseArchitectureManifest, buildArchitectureGraph } from '../app/_lib/architecture-explorer.ts';
import { parseArchitectureEvidence, parseArchitectureCodeIndex, sourceFactsForClaim } from '../app/_lib/architecture-evidence.ts';

let generatedIndex;
const currentIndex = () => (generatedIndex ??= readCurrentSourceIndex(new URL('../../architecture/generated/critical-index.json', import.meta.url)));
const families = ['symbols', 'edges', 'tests'];
const hash = bytes => createHash('sha256').update(bytes).digest('hex');
const canonical = value => {
  if (Array.isArray(value)) return `[${value.map(canonical).join(',')}]`;
  if (value !== null && typeof value === 'object') {
    const keys = Object.keys(value).sort((a, b) => {
      const left = Array.from(a, letter => letter.codePointAt(0));
      const right = Array.from(b, letter => letter.codePointAt(0));
      for (let i = 0; i < Math.min(left.length, right.length); i += 1) {
        if (left[i] !== right[i]) return left[i] - right[i];
      }
      return left.length - right.length;
    });
    return `{${keys.map(key => `${JSON.stringify(key)}:${canonical(value[key])}`).join(',')}}`;
  }
  return JSON.stringify(value);
};
const encoded = value => Buffer.from(`${canonical(value)}\n`);

function transportFixture() {
  const directory = mkdtempSync(join(tmpdir(), 'architecture-reader-'));
  const path = join(directory, 'critical-index.json');
  const logical = { schema: 'critical-source-index-v1', source_input_digest: 'a'.repeat(64),
    inputs: { 'alpha.py': '1'.repeat(64), 'beta.py': '2'.repeat(64) }, allowed: { views: {} },
    observed: { symbols: [
      { id: 'alpha.py::中文', path: 'alpha.py', name: '中文', extra: { '\uE000': 'BMP', '𐀀': 'non-BMP', 10: 'ten', 2: 'two' } },
      { id: 'beta.py::second', path: 'beta.py', name: 'second' },
      { id: 'alpha.py::中文', path: 'alpha.py', name: '中文', extra: { '\uE000': 'BMP', '𐀀': 'non-BMP', 10: 'ten', 2: 'two' } },
    ], edges: [
      { source: 'beta.py::second', kind: 'calls', target: 'alpha.py::中文' },
      { source: 'alpha.py::中文', kind: 'calls', target: 'beta.py::second' },
    ], tests: [{ id: 'alpha.py::test', path: 'alpha.py', name: 'test' }] } };
  const { observed, ...index } = logical;
  const manifest = { schema: 'critical-source-index-parts-v1', index,
    counts: Object.fromEntries(families.map(family => [family, observed[family].length])),
    logical_sha256: hash(encoded(logical)), parts: [] };
  const parts = ['alpha.py', 'beta.py'].map((source, ordinal) => {
    const part = { schema: 'critical-source-facts-v1', source_input_digest: logical.source_input_digest,
      source_path: source, observed: Object.fromEntries(families.map(family => [family,
        observed[family].map((record, position) => [position, record]).filter(([, record]) =>
          (family === 'edges' ? record.source.split('::')[0] : record.path) === source)])) };
    const bytes = encoded(part);
    manifest.parts.push({ file: `critical-facts-${String(ordinal).padStart(5, '0')}.json`, source_path: source,
      bytes: bytes.length, sha256: hash(bytes),
      counts: Object.fromEntries(families.map(family => [family, part.observed[family].length])) });
    writeFileSync(join(directory, manifest.parts[ordinal].file), bytes);
    return part;
  });
  const writeManifest = () => writeFileSync(path, encoded(manifest));
  const writePart = ordinal => {
    const bytes = encoded(parts[ordinal]);
    manifest.parts[ordinal].bytes = bytes.length;
    manifest.parts[ordinal].sha256 = hash(bytes);
    writeFileSync(join(directory, manifest.parts[ordinal].file), bytes);
    writeManifest();
  };
  writeManifest();
  return { directory, path, logical, manifest, parts, writeManifest, writePart,
    cleanup: () => { for (const file of readdirSync(directory)) unlinkSync(join(directory, file)); rmdirSync(directory); } };
}

test('build reader restores exact array order, duplicate facts, raw Unicode and unchanged logical fields', () => {
  const fixture = transportFixture();
  try {
    assert.deepEqual(readCurrentSourceIndex(fixture.path), fixture.logical);
    assert.match(readFileSync(fixture.path, 'utf8'), /critical-source-index-parts-v1/);
    const text = readFileSync(join(fixture.directory, fixture.manifest.parts[0].file), 'utf8');
    assert.ok(text.includes('"10":"ten","2":"two","\uE000":"BMP","𐀀":"non-BMP"'));
    fixture.parts[0].observed.symbols.reverse();
    fixture.writePart(0);
    assert.deepEqual(readCurrentSourceIndex(fixture.path), fixture.logical, 'part order is not logical array order');
  } finally { fixture.cleanup(); }
});

test('build reader rejects malformed, noncanonical and lossy physical JSON', () => {
  const fixture = transportFixture();
  try {
    for (const bytes of [Buffer.from('{truncated'), Buffer.from([0xff]),
      Buffer.from('{"schema":"first","schema":"second"}\n'),
      Buffer.from('{"value":9007199254740992}\n'), Buffer.from('{"value":1.5}\n'),
      Buffer.from('{"value":-0}\n'), Buffer.from('{"value":"\\ud800"}\n'),
      Buffer.from(`${'['.repeat(34)}0${']'.repeat(34)}\n`),
      Buffer.from(JSON.stringify(fixture.manifest)), Buffer.from(`${canonical(fixture.manifest)}\r\n`),
      encoded(fixture.logical)]) {
      writeFileSync(fixture.path, bytes);
      assert.throws(() => readCurrentSourceIndex(fixture.path));
    }
    writeFileSync(fixture.path, Buffer.alloc(2 * 1024 * 1024 + 1, 32));
    assert.throws(() => readCurrentSourceIndex(fixture.path), /ARCHITECTURE_INDEX_BUDGET_EXCEEDED/);
  } finally { fixture.cleanup(); }
});

test('descriptor byte, work and part budgets fail before missing parts are read', () => {
  const cases = [
    manifest => { manifest.parts[0].bytes = 2 * 1024 * 1024 + 1; },
    manifest => { manifest.parts.forEach(part => { part.bytes = 2 * 1024 * 1024; }); },
    manifest => { manifest.counts.symbols = 10241; },
    manifest => { manifest.counts = { symbols: 6000, edges: 6000, tests: 0 }; },
    manifest => { manifest.parts = Array(41).fill(manifest.parts[0]); },
    manifest => { manifest.parts[0].bytes = Number.MAX_SAFE_INTEGER; },
  ];
  for (const mutate of cases) {
    const fixture = transportFixture();
    try {
      for (const part of fixture.manifest.parts) unlinkSync(join(fixture.directory, part.file));
      mutate(fixture.manifest); fixture.writeManifest();
      assert.throws(() => readCurrentSourceIndex(fixture.path), /ARCHITECTURE_INDEX_BUDGET_EXCEEDED/);
    } finally { fixture.cleanup(); }
  }
});

test('complete source binding rejects missing, extra, mixed, corrupt and wrong-ordinal parts', () => {
  const cases = [
    fixture => { unlinkSync(join(fixture.directory, fixture.manifest.parts[0].file)); },
    fixture => { writeFileSync(join(fixture.directory, 'critical-facts-00002.json'), '{}'); },
    fixture => { fixture.manifest.parts[0].file = '../outside.json'; fixture.writeManifest(); },
    fixture => { fixture.manifest.parts[0].source_path = 'absent.py'; fixture.writeManifest(); },
    fixture => { fixture.manifest.parts[0].sha256 = 'f'.repeat(64); fixture.writeManifest(); },
    fixture => { fixture.manifest.logical_sha256 = 'f'.repeat(64); fixture.writeManifest(); },
    fixture => { fixture.parts[0].source_input_digest = 'b'.repeat(64); fixture.writePart(0); },
    fixture => { fixture.parts[0].source_path = 'beta.py'; fixture.writePart(0); },
    fixture => { fixture.parts[0].observed.symbols[0][1].path = 'beta.py'; fixture.writePart(0); },
    fixture => { fixture.parts[0].observed.symbols[1][0] = 0; fixture.writePart(0); },
    fixture => { fixture.parts[0].observed.symbols[1][0] = 3; fixture.writePart(0); },
    fixture => { fixture.parts[0].observed.symbols[0][0] = 1; fixture.writePart(0); },
    fixture => { fixture.parts[0].observed.symbols.pop(); fixture.writePart(0); },
    fixture => { fixture.parts[0].observed.symbols[0][1].name = 'changed'; fixture.writePart(0); },
    fixture => { const part = join(fixture.directory, fixture.manifest.parts[0].file); writeFileSync(part, readFileSync(part).subarray(0, 20)); },
    fixture => { const part = join(fixture.directory, fixture.manifest.parts[0].file); writeFileSync(part, Buffer.alloc(fixture.manifest.parts[0].bytes + 1, 32)); },
    fixture => { writeFileSync(join(fixture.directory, 'unexpected.json'), '{}'); },
  ];
  for (const mutate of cases) {
    const fixture = transportFixture();
    try { mutate(fixture); assert.throws(() => readCurrentSourceIndex(fixture.path)); }
    finally { fixture.cleanup(); }
  }
});

test('real generated-directory links cannot redirect the build reader outside its trusted root', () => {
  const fixture = transportFixture();
  const container = mkdtempSync(join(tmpdir(), 'architecture-link-'));
  const link = join(container, 'generated');
  try {
    symlinkSync(fixture.directory, link, process.platform === 'win32' ? 'junction' : 'dir');
    assert.throws(() => readCurrentSourceIndex(join(link, 'critical-index.json')), /ARCHITECTURE_INDEX_TRANSPORT_INVALID/);
  } finally { unlinkSync(link); rmdirSync(container); fixture.cleanup(); }
});

test('actual symbol claims retain their exact source span, never the first file symbols', () => {
  const index = currentIndex();
  const projection = projectCurrentSource(index);
  const code = parseArchitectureCodeIndex(projection.code);
  const evidence = parseArchitectureEvidence(projection.evidence);
  for (const name of ['Update-MainRuntime', '_sync_news_evidence', 'Handler.do_GET',
    '_build_news_projection_source', '_build_news_evidence_resource']) {
    const original = index.observed.symbols.find(symbol => symbol.name === name);
    assert.ok(original, name);
    const claim = evidence.claims.find(item => item.selector === original.id);
    assert.ok(claim, name);
    const selected = sourceFactsForClaim(code, claim);
    assert.equal(selected.label, 'Exact selected source symbol');
    assert.deepEqual(selected.facts.map(fact => [fact.id, fact.path, fact.line, fact.end_line]),
      [[original.id, original.path, original.line, original.end_line]]);
    assert.deepEqual(sourceFactsForClaim(code, { ...claim, selector: 'unknown::symbol' }).facts, []);
    assert.deepEqual(sourceFactsForClaim(code, { ...claim, bindings: ['unrelated.py'] }).facts, []);
    const overview = sourceFactsForClaim(code, { ...claim, selector: original.path });
    assert.match(overview.label, /^File source overview/);
    assert.ok(overview.facts.length <= 18 && overview.facts.every(fact => fact.path === original.path));
  }
  const overview = sourceFactsForClaim(code, evidence.claims.find(claim => claim.selector === 'slice:source-first-ack'));
  assert.match(overview.label, /^Subsystem source overview/);
  assert.ok(overview.facts.length <= 18);
});

test('rendered source rows separate keyboard selection from source navigation', async () => {
  const output = await build({
    absWorkingDir: fileURLToPath(new URL('../', import.meta.url)),
    entryPoints: ['./app/_views/ArchitectureEvidencePanels.tsx'],
    bundle: true, write: false, outdir: 'unused-render-output', platform: 'node', format: 'cjs', packages: 'external', jsx: 'automatic',
    external: ['virtual:*'],
  });
  const loaded = { exports: {} };
  new Function('require', 'module', 'exports', output.outputFiles.find(file => file.path.endsWith('.js')).text)(createRequire(import.meta.url), loaded, loaded.exports);
  const { CodeSymbolRow } = loaded.exports;
  let selected = null;
  const item = { id: 'actual::symbol', name: 'source_symbol', type: 'function', line: 42 };
  for (const href of ['https://github.com/example/repo/blob/exact/file.py#L42', null]) {
    const row = CodeSymbolRow({ item, href, selected: true, onSelect: id => { selected = id; } });
    const [button, source] = row.props.children;
    assert.equal(button.type, 'button');
    assert.equal(button.props['aria-pressed'], true);
    button.props.onClick(); assert.equal(selected, item.id);
    assert.equal(source.type, href ? 'a' : 'small');
    assert.equal(source.props.onClick, undefined, 'navigation must not change selected owner');
    const html = renderToStaticMarkup(row);
    assert.doesNotMatch(html, /<button\b[^>]*>(?:(?!<\/button>)[\s\S])*<a\b/);
    if (href) {
      assert.match(html, /<\/button><a /);
      assert.match(html, /aria-label="Open source at line 42"/);
      assert.equal(source.props.tabIndex, undefined, 'native link stays keyboard accessible');
    } else assert.doesNotMatch(html, /<a\b/);
  }
  const css = readFileSync(new URL('../app/_views/ArchitectureExplorerView.module.css', import.meta.url), 'utf8');
  assert.match(css, /\.symbolRow > a \{[^}]*min-height: 44px;[^}]*min-width: 44px;/);
});

test('actual generated source feeds the existing Explorer without runtime or permission claims', () => {
  const index = currentIndex();
  const projection = projectCurrentSource(index);
  const manifest = parseArchitectureManifest(projection.manifest);
  assert.ok(manifest);
  assert.ok(manifest.nodes.every(node => node.runtime_state === 'UNKNOWN' && node.implementation_state === 'SOURCE_ONLY'));
  assert.ok(manifest.edges.every(edge => edge.criticality === 'UNKNOWN'));
  const evidence = parseArchitectureEvidence(projection.evidence);
  assert.deepEqual(evidence.traces, []);
  assert.deepEqual(evidence.mutations, []);
  assert.equal(evidence.executionDigestState, 'UNAVAILABLE');
  assert.equal(parseArchitectureCodeIndex(projection.code).facts.length, index.observed.symbols.length);
  for (const view of manifest.views) {
    for (const direction of ['LR', 'TB']) {
      const graph = buildArchitectureGraph(manifest, view.id, direction);
      assert.equal(graph.nodes.length, view.node_ids.length);
      assert.ok(graph.nodes.every(node => Number.isFinite(node.position.x) && Number.isFinite(node.position.y)));
    }
  }
  const phone = buildArchitectureGraph(manifest, 'system-overview', 'TB');
  assert.equal(new Set(phone.nodes.map(node => node.position.x)).size, 1);
  assert.equal(new Set(phone.nodes.map(node => node.position.y)).size, Object.keys(index.allowed.views).length);
  assert.equal(phone.edges.length, 0, 'display ordering must not invent execution edges');

  const api = 'scripts/runtime/run_dashboard_api.py';
  const selected = index.allowed.source_symbols[api];
  const news = manifest.views.find(view => view.id === 'news-worker-audit');
  assert.ok(selected.length > 0);
  for (const root of index.allowed.views['news-worker-audit'].roots.filter(id => id.startsWith(`${api}::`))) {
    assert.ok(news.node_ids.includes(root), root);
    assert.ok(projection.code.code_index.facts.some(fact => fact.id === root && fact.path === api));
  }
  assert.match(news.summary, /explicit symbol scopes are not whole-file coverage/);
  assert.doesNotMatch(news.summary, /Full selected-file/);
  assert.ok(!manifest.edges.some(edge => edge.from === `${api}::_news_projection_source_for_request`
    && edge.to === `${api}::_finish_news_projection_source_build`), 'thread target is not a direct call');
  assert.ok(!projection.code.code_index.facts.some(fact => fact.id === `${api}::_dashboard_payload`));
  assert.ok(!manifest.edges.some(edge => edge.from.startsWith(`${api}::`) && edge.to.startsWith('web/')),
    'source selection must not fabricate HTTP or import dispatch');
});

test('browser projection excludes raw SQL, native arguments and unselected operational data', () => {
  const index = currentIndex();
  const shaped = structuredClone(index);
  shaped.runtime = { events: [{ secret: 'RUNTIME_SECRET_MUST_NOT_SHIP' }] };
  shaped.observed.edges.push({ kind: 'sql', source: shaped.observed.symbols[0].id, target: 'SELECT PRIVATE_SQL_MUST_NOT_SHIP' });
  shaped.native_arguments = ['NATIVE_SECRET_MUST_NOT_SHIP'];
  const output = JSON.stringify(projectCurrentSource(shaped));
  assert.doesNotMatch(output, /MUST_NOT_SHIP/);
  assert.ok(Buffer.byteLength(JSON.stringify(projectCurrentSource(shaped).manifest)) <= 300000);
});

test('broken generated index cannot silently become an empty or declared-success Explorer', () => {
  const index = currentIndex();
  assert.throws(() => projectCurrentSource({}), /ARCHITECTURE_CURRENT_SOURCE_INVALID/);
  const shaped = structuredClone(index);
  shaped.allowed.views['clock-transaction'].roots[0] = 'missing::symbol';
  assert.throws(() => projectCurrentSource(shaped), /ARCHITECTURE_ROOT_UNRESOLVED/);
});
