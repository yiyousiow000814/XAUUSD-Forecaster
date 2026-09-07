import assert from 'node:assert/strict';
import { readFileSync, mkdtempSync, writeFileSync, unlinkSync, rmdirSync } from 'node:fs';
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

const index = JSON.parse(readFileSync(new URL('../../architecture/generated/critical-index.json', import.meta.url), 'utf8'));

test('build reader bounds the actual bytes read, rejecting oversized and malformed sources', () => {
  const directory = mkdtempSync(join(tmpdir(), 'architecture-reader-'));
  const path = join(directory, 'index.json');
  try {
    writeFileSync(path, JSON.stringify(index));
    assert.deepEqual(readCurrentSourceIndex(path), index);
    writeFileSync(path, Buffer.alloc(2 * 1024 * 1024 + 1, 32));
    assert.throws(() => readCurrentSourceIndex(path), /ARCHITECTURE_INDEX_BUDGET_EXCEEDED/);
    writeFileSync(path, '{truncated');
    assert.throws(() => readCurrentSourceIndex(path), SyntaxError);
  } finally { unlinkSync(path); rmdirSync(directory); }
});

test('actual symbol claims retain their exact source span, never the first file symbols', () => {
  const projection = projectCurrentSource(index);
  const code = parseArchitectureCodeIndex(projection.code);
  const evidence = parseArchitectureEvidence(projection.evidence);
  for (const name of ['Start-ReleasePromotion', '_sync_news_evidence', 'Handler.do_GET',
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

  const api = 'scripts/run_dashboard_api.py';
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
  const shaped = structuredClone(index);
  shaped.runtime = { events: [{ secret: 'RUNTIME_SECRET_MUST_NOT_SHIP' }] };
  shaped.observed.edges.push({ kind: 'sql', source: shaped.observed.symbols[0].id, target: 'SELECT PRIVATE_SQL_MUST_NOT_SHIP' });
  shaped.native_arguments = ['NATIVE_SECRET_MUST_NOT_SHIP'];
  const output = JSON.stringify(projectCurrentSource(shaped));
  assert.doesNotMatch(output, /MUST_NOT_SHIP/);
  assert.ok(Buffer.byteLength(JSON.stringify(projectCurrentSource(shaped).manifest)) <= 300000);
});

test('broken generated index cannot silently become an empty or declared-success Explorer', () => {
  assert.throws(() => projectCurrentSource({}), /ARCHITECTURE_CURRENT_SOURCE_INVALID/);
  const shaped = structuredClone(index);
  shaped.allowed.views['clock-transaction'].roots[0] = 'missing::symbol';
  assert.throws(() => projectCurrentSource(shaped), /ARCHITECTURE_ROOT_UNRESOLVED/);
});
