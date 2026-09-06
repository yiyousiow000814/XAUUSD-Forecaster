import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import { projectCurrentSource } from '../build/architecture-current-source.mjs';
import { parseArchitectureManifest, buildArchitectureGraph } from '../app/_lib/architecture-explorer.ts';
import { parseArchitectureEvidence, parseArchitectureCodeIndex } from '../app/_lib/architecture-evidence.ts';

const index = JSON.parse(readFileSync(new URL('../../architecture/generated/critical-index.json', import.meta.url), 'utf8'));

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
  assert.equal(new Set(phone.nodes.map(node => node.position.y)).size, 3);
  assert.equal(phone.edges.length, 0, 'display ordering must not invent execution edges');
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
