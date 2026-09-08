"""Source truth, incomplete-analysis visibility and generated drift contracts."""
import importlib.util
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from types import SimpleNamespace
import xml.etree.ElementTree as ET
import zipfile

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('source_architecture', ROOT / 'scripts/architecture_compiler.py')
compiler = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(compiler)


def test_repository_generated_architecture_is_current_in_required_python_gate():
    result = subprocess.run([sys.executable, str(ROOT / 'scripts/compile_architecture.py'), 'check'],
                            cwd=ROOT, capture_output=True, text=True, timeout=45,
                            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    assert result.returncode == 0, result.stderr


def test_mutation_archive_uses_physical_temp_authority_and_rejects_escape(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / 'scripts'))
    from run_architecture_mutations import unpack_source_archive
    physical = tmp_path / 'physical'
    physical.mkdir()
    alias = tmp_path / 'alias'
    if os.name == 'nt':
        subprocess.run(['cmd.exe', '/c', 'mklink', '/J', str(alias), str(physical)],
                       check=True, capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
    else:
        alias.symlink_to(physical, target_is_directory=True)
    def zipped(name):
        data = io.BytesIO()
        with zipfile.ZipFile(data, 'w') as archive:
            archive.writestr(name, 'owned source')
        return data.getvalue()
    copy = unpack_source_archive(zipped('README.md'), alias / 'source')
    assert copy == (physical / 'source').resolve()
    assert (copy / 'README.md').read_text() == 'owned source'
    for ordinal, member in enumerate(['../outside.txt', str(tmp_path / 'outside.txt')]):
        with pytest.raises(ValueError, match='ARCHIVE_PATH_ESCAPE'):
            unpack_source_archive(zipped(member), alias / f'bad-{ordinal}')
    assert not (physical / 'outside.txt').exists()
    assert not (tmp_path / 'outside.txt').exists()


@pytest.fixture
def source(tmp_path):
    manifest = {'schema': 'critical-path-selection-v1', 'views': {'fixture': {
        'files': ['scripts/example.py'], 'roots': ['scripts/example.py::execute'], 'tests': []}}}
    (tmp_path / 'architecture').mkdir()
    (tmp_path / 'scripts').mkdir()
    (tmp_path / compiler.SELECTION).write_text(json.dumps(manifest), encoding='utf-8')
    for relative in compiler.TOOL_INPUTS:
        shutil.copyfile(ROOT / relative, tmp_path / relative)
    (tmp_path / 'scripts/example.py').write_text(
        'def execute(connection):\n    connection.execute("BEGIN IMMEDIATE")\n'
        '    connection.execute("INSERT INTO facts VALUES (1)")\n'
        '    connection.commit()\n    helper()\ndef helper():\n    pass\n', encoding='utf-8')
    return tmp_path


def test_calls_sql_and_commit_are_observed_without_invented_ownership(source):
    index = compiler.compile_index(source)
    edges = index['observed']['edges']
    assert any(e['kind'] == 'sql' and e['statement'] == 'BEGIN IMMEDIATE' for e in edges)
    assert any(e['kind'] == 'commits' and e['resolution'] == 'UNKNOWN' for e in edges)
    helper = next(e for e in edges if e['target'] == 'helper')
    assert helper['candidate_symbol'] == 'scripts/example.py::helper'
    assert helper['resolution'] == 'UNKNOWN'
    assert index['runtime'] == {'status': 'UNKNOWN', 'observations': []}
    assert index['coverage']['transaction_atomicity'] == 'NOT_PROVEN_BY_STATIC_INDEX'


def test_imports_and_test_spans_do_not_claim_runtime_execution(source):
    path = source / 'scripts/example.py'
    path.write_text('from .owner import value\nimport sqlite3\n' + path.read_text(), encoding='utf-8')
    (source / 'tests').mkdir()
    (source / 'tests/test_fixture.py').write_text('def test_invariant():\n    raise RuntimeError("not executed")\n', encoding='utf-8')
    manifest_path = source / compiler.SELECTION
    manifest = json.loads(manifest_path.read_text())
    manifest['views']['fixture']['tests'] = ['tests/test_fixture.py']
    manifest_path.write_text(json.dumps(manifest), encoding='utf-8')
    index = compiler.compile_index(source)
    assert {e['target'] for e in index['observed']['edges'] if e['kind'] == 'requires'} == {'.owner', 'sqlite3'}
    assert index['observed']['tests'][0]['execution'] == 'NOT_OBSERVED'
    assert index['observed']['tests'][0]['line'] == 1


def test_explicit_symbol_scope_retains_ast_bodies_descendants_and_unresolved_frontier(source):
    path = source / 'scripts/example.py'
    path.write_text('''import external
class Owner:
    @decorate(option())
    async def execute(self, connection):
        def callback():
            connection.commit()
        helper()
        callback()
    def unrelated(self):
        outside()
def helper():
    expensive()
''', encoding='utf-8')
    selection_path = source / compiler.SELECTION
    selection = json.loads(selection_path.read_text())
    owner = 'scripts/example.py::Owner.execute'
    selection['views']['fixture']['roots'] = [owner]
    selection_path.write_text(json.dumps(selection), encoding='utf-8')
    complete = compiler.compile_index(source)
    selection['source_symbols'] = {'scripts/example.py': [owner]}
    selection_path.write_text(json.dumps(selection), encoding='utf-8')
    scoped = compiler.compile_index(source)
    retained = {owner, owner + '.callback'}
    assert scoped['observed']['symbols'] == [row for row in complete['observed']['symbols'] if row['id'] in retained]
    # Out-of-scope candidates cannot survive selection, but their call syntax
    # remains visible. Decorators/async bodies/nested definitions are AST facts.
    expected = []
    for row in complete['observed']['edges']:
        if row['source'] in retained or row['source'].endswith('::<module>'):
            row = dict(row)
            if row.get('candidate_symbol') not in retained:
                row.pop('candidate_symbol', None)
                if row['kind'] == 'calls': row['binding'] = 'Runtime binding not inferred from call syntax'
            expected.append(row)
    assert scoped['observed']['edges'] == expected
    assert {'helper', 'decorate', 'option', 'connection.commit'} <= {row['target'] for row in expected}
    assert scoped['allowed']['source_symbols'] == selection['source_symbols']
    assert scoped['inputs']['scripts/example.py'] == complete['inputs']['scripts/example.py']
    assert scoped['source_input_digest'] != complete['source_input_digest']
    path.write_text(path.read_text().replace('expensive()', 'changed()'), encoding='utf-8')
    changed = compiler.compile_index(source)
    assert changed['observed'] == scoped['observed']
    assert changed['source_input_digest'] != scoped['source_input_digest']
    path.write_text(path.read_text() + '\ndef malformed(:\n', encoding='utf-8')
    with pytest.raises(SyntaxError): compiler.compile_index(source)


@pytest.mark.parametrize('selection,reason', [
    (None, 'ARCHITECTURE_SYMBOL_SELECTION_INVALID'),
    ({'missing.py': ['missing.py::execute']}, 'ARCHITECTURE_SYMBOL_SELECTION_INVALID'),
    ({'scripts/example.py': []}, 'ARCHITECTURE_SYMBOL_SELECTION_INVALID'),
    ({'scripts/example.py': [123]}, 'ARCHITECTURE_SYMBOL_SELECTION_INVALID'),
    ({'scripts/example.py': ['elsewhere.py::execute']}, 'ARCHITECTURE_SYMBOL_SELECTION_INVALID'),
    ({'scripts/example.py': ['scripts/example.py::execute'] * 2}, 'ARCHITECTURE_SYMBOL_SELECTION_INVALID'),
    ({'scripts/example.py': ['scripts/example.py::missing']}, 'ARCHITECTURE_SYMBOL_SELECTION_MISSING'),
    ({'scripts/example.py': ['scripts/example.py::helper']}, 'ARCHITECTURE_ROOT_MISSING'),
])
def test_invalid_or_root_excluding_symbol_scope_fails_closed(source, selection, reason):
    path = source / compiler.SELECTION
    manifest = json.loads(path.read_text())
    manifest['source_symbols'] = selection
    path.write_text(json.dumps(manifest), encoding='utf-8')
    with pytest.raises(ValueError, match=reason): compiler.compile_index(source)


def test_scope_does_not_hide_ambiguous_symbol_ids_outside_the_selected_body(source):
    path = source / compiler.SELECTION
    manifest = json.loads(path.read_text())
    manifest['source_symbols'] = {'scripts/example.py': ['scripts/example.py::execute']}
    path.write_text(json.dumps(manifest), encoding='utf-8')
    with (source / 'scripts/example.py').open('a', encoding='utf-8') as handle:
        handle.write('\ndef helper():\n    pass\n')
    with pytest.raises(ValueError, match='ARCHITECTURE_SYMBOL_ID_AMBIGUOUS'):
        compiler.compile_index(source)


def test_inputs_are_relocatable_and_generated_output_never_hashes_itself(source, tmp_path):
    first = compiler.compile_index(source)
    generated = source / 'architecture/generated'
    generated.mkdir()
    (generated / 'critical-index.json').write_text('old output', encoding='utf-8')
    assert compiler.compile_index(source) == first
    relocated = tmp_path / 'elsewhere'
    shutil.copytree(source / 'scripts', relocated / 'scripts')
    (relocated / 'architecture').mkdir()
    shutil.copyfile(source / compiler.SELECTION, relocated / compiler.SELECTION)
    assert compiler.compile_index(relocated) == first


@pytest.mark.parametrize('mutation', ['call', 'sql', 'retry', 'syntax', 'root'])
def test_source_changes_and_invalid_sources_fail_or_change_the_graph(source, mutation):
    first = compiler.compile_index(source)
    path = source / 'scripts/example.py'
    text = path.read_text(encoding='utf-8')
    if mutation == 'syntax':
        path.write_text('def broken(:', encoding='utf-8')
        with pytest.raises(SyntaxError): compiler.compile_index(source)
        return
    if mutation == 'root':
        path.write_text(text.replace('def execute(', 'def renamed('), encoding='utf-8')
        with pytest.raises(ValueError, match='ARCHITECTURE_ROOT_MISSING'): compiler.compile_index(source)
        return
    text = text.replace('helper()', {'call': 'different()', 'sql': 'connection.execute("DELETE FROM facts")', 'retry': 'retry(maximum=2)'}[mutation], 1)
    path.write_text(text, encoding='utf-8')
    changed = compiler.compile_index(source)
    assert changed['source_input_digest'] != first['source_input_digest']
    assert compiler.render(changed) != compiler.render(first)


@pytest.mark.parametrize('fixture_name', ['source', 'typescript_source'])
@pytest.mark.parametrize('scoped', [False, True])
def test_build_then_check_and_tamper_are_real_cli_boundaries(request, fixture_name, monkeypatch, scoped):
    source = request.getfixturevalue(fixture_name)
    if scoped:
        manifest_path = source / compiler.SELECTION
        selection = json.loads(manifest_path.read_text())
        selection['source_symbols'] = {'scripts/example.py': ['scripts/example.py::execute']}
        manifest_path.write_text(json.dumps(selection), encoding='utf-8')
    if fixture_name == 'typescript_source':
        import architecture_typescript_tool as tool
        package, _ = tool.resolve_package(ROOT)
        # The copied real CLI owns its own fixed installation, not an environment
        # override pointing back to the original checkout. TypeScript's parser
        # entry is self-contained; do not clone unrelated Web dependencies.
        installed = source / 'web/node_modules/typescript'
        (installed / 'lib').mkdir(parents=True)
        for relative in ('package.json', 'lib/typescript.js'):
            shutil.copyfile(package / relative, installed / relative)
        monkeypatch.setenv('ARCHITECTURE_TYPESCRIPT_PACKAGE', str(source / 'untrusted-tool'))
    command = [sys.executable, str(source / 'scripts/compile_architecture.py')]
    options = dict(capture_output=True, timeout=15,
                   creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    subprocess.run([*command, 'build'], check=True, **options)
    subprocess.run([*command, 'check'], check=True, **options)
    denied = subprocess.run([*command, 'check', '--root', str(ROOT)], text=True, **options)
    assert denied.returncode != 0 and 'unrecognized arguments' in denied.stderr
    (source / 'architecture/generated/fixture.mmd').write_text('tampered', encoding='utf-8')
    result = subprocess.run([*command, 'check'], text=True, **options)
    assert result.returncode == 1
    assert 'ARCHITECTURE_GENERATED_DRIFT' in result.stderr


@pytest.mark.parametrize('kind', ['empty', 'missing_root', 'language', 'escape', 'view_escape'])
def test_invalid_selection_cannot_silently_drop_coverage(source, kind):
    path = source / compiler.SELECTION
    manifest = json.loads(path.read_text())
    view = manifest['views']['fixture']
    if kind == 'empty': manifest['views'] = {}
    if kind == 'missing_root': view['roots'] = []
    if kind == 'language': view['files'].append('scripts/not-parsed.rb')
    if kind == 'escape': view['files'].append('../outside.py')
    if kind == 'view_escape': manifest['views'] = {'../outside': view}
    path.write_text(json.dumps(manifest), encoding='utf-8')
    with pytest.raises(ValueError, match='ARCHITECTURE_'):
        compiler.compile_index(source)


def test_real_cli_rejects_orphaned_view_after_rename(source):
    command = [sys.executable, str(source / 'scripts/compile_architecture.py')]
    options = dict(capture_output=True, timeout=15,
                   creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    subprocess.run([*command, 'build'], check=True, **options)
    path = source / compiler.SELECTION
    manifest = json.loads(path.read_text())
    manifest['views']['renamed'] = manifest['views'].pop('fixture')
    path.write_text(json.dumps(manifest), encoding='utf-8')
    subprocess.run([*command, 'build'], check=True, **options)
    result = subprocess.run([*command, 'check'], text=True, **options)
    assert result.returncode == 1
    assert 'fixture.mmd' in result.stderr
    assert (source / 'architecture/generated/fixture.mmd').exists()


@pytest.mark.skipif(not (shutil.which('pwsh') or shutil.which('powershell')), reason='PowerShell composition tested by architecture CI')
@pytest.mark.parametrize('scoped', [False, True])
def test_real_powershell_parser_emits_dynamic_calls_and_rejects_invalid_source(source, scoped):
    manifest_path = source / compiler.SELECTION
    manifest = json.loads(manifest_path.read_text())
    manifest['views']['fixture']['files'].append('scripts/fixture.ps1')
    manifest['views']['fixture']['roots'].append('scripts/fixture.ps1::Invoke-Fixture')
    if scoped:
        manifest['source_symbols'] = {'scripts/fixture.ps1': ['scripts/fixture.ps1::Invoke-Fixture']}
    manifest_path.write_text(json.dumps(manifest), encoding='utf-8')
    script = source / 'scripts/fixture.ps1'
    script.write_text('function Invoke-Fixture { & $unknown; Write-Output "中文" }\nfunction Other { Write-Output "unselected" }', encoding='utf-8')
    index = compiler.compile_index(source)
    assert any(e['target'] == '<dynamic-command>' and e['resolution'] == 'UNKNOWN'
               for e in index['observed']['edges'])
    assert any(row['id'] == 'scripts/fixture.ps1::Other' for row in index['observed']['symbols']) is not scoped
    script.write_text('function Invoke-Fixture {', encoding='utf-8')
    with pytest.raises(RuntimeError, match='ARCHITECTURE_PARSE_FAILED'):
        compiler.compile_index(source)


@pytest.mark.parametrize('case,expected', [
    ('exact_assertion', 'KILLED'), ('pytest_junit', 'KILLED'), ('passed', 'SURVIVED'),
    ('other_assertion', 'ERROR'), ('setup', 'ERROR'),
    ('skip', 'ERROR'), ('wrong_count', 'ERROR'), ('body_only_marker', 'ERROR'),
])
def test_mutation_kill_requires_exact_executed_assertion(tmp_path, monkeypatch, case, expected):
    monkeypatch.setitem(sys.modules, 'architecture_compiler', compiler)
    spec = importlib.util.spec_from_file_location('mutation_runner', ROOT / 'scripts/run_architecture_mutations.py')
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    document = ET.Element('testsuite')
    test = ET.SubElement(document, 'testcase', name='test_behavior')
    if case == 'exact_assertion': ET.SubElement(test, 'failure', type='AssertionError', message='AssertionError: NAMED_INVARIANT')
    if case == 'pytest_junit': ET.SubElement(test, 'failure', message='AssertionError: NAMED_INVARIANT')
    if case == 'other_assertion': ET.SubElement(test, 'failure', type='AssertionError', message='something else')
    if case == 'setup': ET.SubElement(test, 'error', message='NAMED_INVARIANT')
    if case == 'skip': ET.SubElement(test, 'skipped')
    if case == 'body_only_marker':
        failure = ET.SubElement(test, 'failure', type='TypeError', message='bad fixture')
        failure.text = 'source code contains NAMED_INVARIANT'
    xml = tmp_path / 'result.xml'
    ET.ElementTree(document).write(xml)
    outcome, _ = runner.classify(SimpleNamespace(returncode=0 if case == 'passed' else 1),
                                  xml, 2 if case == 'wrong_count' else 1, 'NAMED_INVARIANT')
    assert outcome == expected


@pytest.mark.parametrize('change,expected', [
    ('none', 'KILLED'), ('old_source', 'STALE'), ('body_only', 'UNRESOLVED'),
    ('wrong_test', 'IDENTITY_MISMATCH'), ('wrong_binding', 'BINDING_MISMATCH'),
    ('duplicate', 'IDENTITY_MISMATCH'), ('missing_family', 'UNIVERSE_MISMATCH'),
])
def test_retained_evidence_is_source_bound_and_does_not_invent_runtime_traces(tmp_path, monkeypatch, change, expected):
    monkeypatch.syspath_prepend(str(ROOT / 'scripts'))
    import architecture_evidence as evidence
    sha = 'a' * 40
    registry = {'mutation': [dict(id='MUT-A', test='tests/test_contract.py::test_boundary',
                                 expected_cases=1, failure_marker='BOUNDARY_FAILED',
                                 runtime_events=['pretend-start', 'pretend-commit'])]}
    row = dict(id='MUT-A', test=registry['mutation'][0]['test'], source_sha=sha, outcome='KILLED')
    report = dict(schema='architecture-mutation-report-v1', source_sha=sha, mutations=[row])
    if change == 'wrong_binding': row['test'] = 'tests/test_other.py::test_boundary'
    if change == 'missing_family': report['mutations'] = []
    (tmp_path / 'mutation-report.json').write_text(json.dumps(report), encoding='utf-8')
    folder = tmp_path / 'MUT-A'
    folder.mkdir()
    for phase in ('baseline', 'mutant'):
        suite = ET.Element('testsuite')
        case = ET.SubElement(suite, 'testcase', classname='tests.test_contract',
                             name='test_other' if change == 'wrong_test' else 'test_boundary')
        if change == 'duplicate': ET.SubElement(suite, 'testcase', **case.attrib)
        if phase == 'mutant':
            failure = ET.SubElement(case, 'failure', message='AssertionError: BOUNDARY_FAILED')
            if change == 'body_only':
                failure.set('message', 'TypeError: broken setup')
                failure.text = 'BOUNDARY_FAILED'
        ET.ElementTree(suite).write(folder / f'{phase}.xml')
    if expected.endswith('MISMATCH'):
        with pytest.raises(ValueError, match=expected):
            evidence.project_mutations(tmp_path, registry, sha)
        return
    result = evidence.project_mutations(tmp_path, registry, 'b' * 40 if change == 'old_source' else sha)
    assert result['mutations'][0]['status'] == expected
    assert result['runtime'] == {'status': 'UNKNOWN', 'traces': []}
    assert result['mutations'][0]['runtime_observed'] is False
    assert len(result['mutations'][0]['artifacts']) == 2


@pytest.fixture
def typescript_source(source, monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / 'scripts'))
    (source / 'web').mkdir()
    for name in ('package.json', 'package-lock.json'):
        shutil.copyfile(ROOT / 'web' / name, source / 'web' / name)
    manifest_path = source / compiler.SELECTION
    manifest = json.loads(manifest_path.read_text())
    manifest['views']['fixture']['files'].extend(['web/owner.ts', 'web/View.tsx'])
    manifest['views']['fixture']['roots'].extend(['web/owner.ts::Owner.execute', 'web/View.tsx::View.refresh'])
    manifest_path.write_text(json.dumps(manifest), encoding='utf-8')
    (source / 'web/owner.ts').write_text('''import type { Input } from './types';
import { fetch as alias } from './transport';
// ignoredGhost() must never become an observed call.
export interface Shape { value: number }
export class Owner {
  execute(db: unknown, value: Input) {
    db.prepare(`SELECT 中文 FROM records`).bind(value);
    db.prepare(`SELECT ${value} FROM records`);
    alias('/api/news-index');
    this[value.method]();
    return import(value.module);
  }
}
export async function GET() { return alias('/api/news-content'); }
const notExecuted = () => { throw new Error('not run'); };
''', encoding='utf-8')
    (source / 'web/View.tsx').write_text('''import { useCallback } from 'react';
import { GET } from './owner';
export function GET() { return 2; }
export default function View() {
  const refresh = useCallback(async () => GET(), []);
  return <Panel title="中文"><button onClick={refresh}>Open</button></Panel>;
}
''', encoding='utf-8')
    return source


def test_real_typescript_parser_preserves_symbols_calls_jsx_and_unknown_dispatch(typescript_source):
    index = compiler.compile_index(typescript_source)
    symbols = {row['id']: row for row in index['observed']['symbols']}
    assert {'web/owner.ts::GET', 'web/View.tsx::GET', 'web/owner.ts::Owner.execute',
            'web/owner.ts::Shape', 'web/View.tsx::View.refresh'} <= symbols.keys()
    method = symbols['web/owner.ts::Owner.execute']
    assert (method['line'], method['end_line']) == (6, 12)
    assert method['syntax_kind'] == 'MethodDeclaration'
    edges = index['observed']['edges']
    assert not any('ignoredGhost' in row['target'] for row in edges)
    assert any(row['kind'] == 'requires' and row['type_only'] for row in edges if 'type_only' in row)
    assert any(row['kind'] == 'renders' and row['target'] == 'Panel' for row in edges)
    assert any(row['target'] == 'this[<dynamic>]' and row['resolution'] == 'UNKNOWN' for row in edges)
    assert any(row['kind'] == 'requires' and row['target'] == '<dynamic-module>' for row in edges)
    assert any(row.get('statement') == 'SELECT 中文 FROM records' and row['resolution'] == 'LITERAL' for row in edges)
    assert any(row.get('statement') == '<dynamic SQL>' and row['resolution'] == 'UNKNOWN' for row in edges)
    assert next(row for row in edges if row.get('first_argument_literal') == '/api/news-index')['resolution'] == 'UNKNOWN'
    assert index['runtime'] == {'status': 'UNKNOWN', 'observations': []}
    assert index['tools']['typescript']['version'] == json.loads((ROOT / 'web/package.json').read_text())['devDependencies']['typescript']


def test_symbol_scopes_use_the_same_real_typescript_qualified_ids(typescript_source):
    complete = compiler.compile_index(typescript_source)
    path = typescript_source / compiler.SELECTION
    selection = json.loads(path.read_text())
    selection['source_symbols'] = {
        'web/owner.ts': ['web/owner.ts::Owner.execute'],
        'web/View.tsx': ['web/View.tsx::View'],
    }
    path.write_text(json.dumps(selection), encoding='utf-8')
    scoped = compiler.compile_index(typescript_source)
    assert scoped['tools'] == complete['tools']
    assert {row['id'] for row in scoped['observed']['symbols'] if row['path'].startswith('web/')} == {
        'web/owner.ts::Owner.execute', 'web/View.tsx::View', 'web/View.tsx::View.refresh'}
    assert [row for row in scoped['observed']['symbols'] if row['path'].endswith('.py')] == [
        row for row in complete['observed']['symbols'] if row['path'].endswith('.py')]
    assert any(row['kind'] == 'renders' and row['target'] == 'Panel' for row in scoped['observed']['edges'])
    assert any(row['kind'] == 'requires' and row['target'] == '<dynamic-module>' for row in scoped['observed']['edges'])
    assert not any(row.get('candidate_symbol') == 'web/View.tsx::GET' for row in scoped['observed']['edges'])


def test_typescript_parse_failure_and_source_drift_are_not_partial_success(typescript_source):
    first = compiler.compile_index(typescript_source)
    file = typescript_source / 'web/View.tsx'
    file.write_text(file.read_text().replace('<Panel ', '<OtherPanel '), encoding='utf-8')
    with pytest.raises(RuntimeError, match='ARCHITECTURE_PARSE_FAILED'):
        compiler.compile_index(typescript_source)  # mismatched JSX end tag
    file.write_text('export default function View() { const refresh = () => changed(); return null; }', encoding='utf-8')
    changed = compiler.compile_index(typescript_source)
    assert first['source_input_digest'] != changed['source_input_digest']
    assert any(row['target'] == 'changed' for row in changed['observed']['edges'])


def test_typescript_nested_object_and_class_property_methods_have_distinct_owners(typescript_source):
    file = typescript_source / 'web/owner.ts'
    with file.open('a', encoding='utf-8') as stream:
        stream.write('''
const handlers = { a: { run() { first(); } }, b: { run() { second(); } } };
class Nested { handlers = { a: { run() { third(); } }, [dynamic]: { run() { fourth(); } } }; }
function options() { accept({ headers: { run() { fifth(); } } }); accept({ headers: { run() { sixth(); } } }); }
''')
    index = compiler.compile_index(typescript_source)
    by_call = {row['target']: row['source'] for row in index['observed']['edges'] if row['kind'] == 'calls'}
    assert by_call['first'] == 'web/owner.ts::handlers.a.run'
    assert by_call['second'] == 'web/owner.ts::handlers.b.run'
    assert by_call['third'] == 'web/owner.ts::Nested.handlers.a.run'
    assert by_call['fourth'].startswith('web/owner.ts::Nested.handlers.<computed@')
    assert by_call['fourth'].endswith('>.run')
    assert by_call['fifth'] != by_call['sixth']
    for call in ('fifth', 'sixth'):
        assert by_call[call].startswith('web/owner.ts::options.<object@')
        assert by_call[call].endswith('>.headers.run')


@pytest.mark.parametrize('layout,reason', [
    ('web', 'ARCHITECTURE_TOOL_INTEGRITY_FAILED:installed-package'),
    ('cache', 'ARCHITECTURE_TOOL_UNAVAILABLE:run architecture_typescript_tool.py first'),
])
def test_typescript_sources_and_tool_identity_are_relocatable_without_unrelated_lock_churn(typescript_source, tmp_path, tmp_path_factory, monkeypatch, layout, reason):
    import architecture_typescript_tool as tool
    real_package, identity = tool.resolve_package(ROOT)
    tool_root = tmp_path_factory.mktemp('owned-parser')
    project = (tool_root / 'web' if layout == 'web' else
               tool_root / '.local/tools/architecture-typescript' / tool.identity_key(identity))
    installed = project / 'node_modules/typescript'
    (installed / 'lib').mkdir(parents=True)
    (project / 'package-lock.json').write_text(json.dumps({'packages': {'node_modules/typescript': identity}}))
    for relative in ('package.json', 'lib/typescript.js'):
        shutil.copyfile(real_package / relative, installed / relative)
    monkeypatch.setattr(tool, 'TOOL_ROOT', tool_root)
    parser_calls = []
    original_run = compiler.subprocess.run
    def run(command, **options):
        if len(command) > 1 and str(command[1]).endswith('extract_architecture_typescript.mjs'):
            parser_calls.append(command)
        return original_run(command, **options)
    monkeypatch.setattr(compiler.subprocess, 'run', run)
    first = compiler.compile_index(typescript_source)
    assert len(parser_calls) == 1  # The real pinned parser, not a mocked result.
    copy = tmp_path / 'relocated'
    shutil.copytree(typescript_source, copy)
    file = copy / 'web/owner.ts'
    file.write_bytes(file.read_bytes().replace(b'\r\n', b'\n').replace(b'\n', b'\r\n'))
    assert compiler.compile_index(copy) == first
    lock_path = copy / 'web/package-lock.json'
    lock = json.loads(lock_path.read_text())
    lock['packages']['node_modules/unrelated'] = {'version': '999.0.0'}
    lock_path.write_text(json.dumps(lock), encoding='utf-8')
    assert compiler.compile_index(copy) == first
    assert len(parser_calls) == 3
    lock['packages']['node_modules/typescript']['integrity'] = 'sha512-' + 'A' * 86 + '=='
    lock_path.write_text(json.dumps(lock), encoding='utf-8')
    # Fixed Web finds a mismatched installed lock; cache-only seeks a new digest
    # with no installed package. Both fail before launching a parser.
    with pytest.raises(RuntimeError, match=reason):
        compiler.compile_index(copy)
    assert len(parser_calls) == 3


@pytest.mark.parametrize('change', ['wrong_version', 'wrong_lock', 'missing_entry', 'malformed_metadata'])
def test_typescript_package_mismatch_fails_before_any_parser_code(typescript_source, tmp_path, monkeypatch, change):
    import architecture_typescript_tool as tool
    identity = tool.tool_identity(typescript_source)
    monkeypatch.setattr(tool, 'TOOL_ROOT', typescript_source)
    project = typescript_source / '.local/tools/architecture-typescript' / tool.identity_key(identity)
    package = project / 'node_modules/typescript'
    (package / 'lib').mkdir(parents=True)
    metadata = {'name': 'typescript', 'version': identity['version']}
    locked = dict(identity)
    if change == 'wrong_version': metadata['version'] = '0.0.0'
    if change == 'wrong_lock': locked['integrity'] = 'incorrect'
    (project / 'package-lock.json').write_text(json.dumps({'packages': {'node_modules/typescript': locked}}))
    (package / 'package.json').write_text('{' if change == 'malformed_metadata' else json.dumps(metadata))
    sentinel = tmp_path / 'PARSER_EXECUTED'
    if change != 'missing_entry':
        (package / 'lib/typescript.js').write_text(f'require("node:fs").writeFileSync({json.dumps(str(sentinel))},"bad");')
    with pytest.raises(RuntimeError, match='ARCHITECTURE_TOOL_INTEGRITY_FAILED'):
        compiler.compile_index(typescript_source)
    assert not sentinel.exists()


@pytest.mark.parametrize('locator', ['environment', 'directory_link'])
def test_external_matching_package_cannot_authorize_its_own_installation(typescript_source, tmp_path, tmp_path_factory, monkeypatch, locator):
    import architecture_typescript_tool as tool
    identity = tool.tool_identity(typescript_source)
    project = tmp_path_factory.mktemp('external-parser')
    package = project / 'node_modules/typescript'
    (package / 'lib').mkdir(parents=True)
    (project / 'package-lock.json').write_text(json.dumps({'packages': {'node_modules/typescript': identity}}))
    (package / 'package.json').write_text(json.dumps({'name': 'typescript', 'version': identity['version']}))
    sentinel = tmp_path / 'UNTRUSTED_PARSER_EXECUTED'
    (package / 'lib/typescript.js').write_text(f'require("node:fs").writeFileSync({json.dumps(str(sentinel))},"bad");')
    monkeypatch.setattr(tool, 'TOOL_ROOT', typescript_source)
    if locator == 'environment':
        # This retired environment name is ignored, never a runtime authority or
        # a missing-dependency fallback. Exact copied metadata cannot change it.
        monkeypatch.setenv('ARCHITECTURE_TYPESCRIPT_PACKAGE', str(package))
        reason = 'ARCHITECTURE_TOOL_UNAVAILABLE'
    else:
        alias = typescript_source / 'web/node_modules'
        if os.name == 'nt':
            subprocess.run(['cmd.exe', '/c', 'mklink', '/J', str(alias), str(project / 'node_modules')],
                           check=True, capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
        else:
            alias.symlink_to(project / 'node_modules', target_is_directory=True)
        reason = 'ARCHITECTURE_TOOL_INTEGRITY_FAILED:package-owner'
    original_read = tool.read_json
    reads = []
    def read(path):
        reads.append(path.resolve())
        return original_read(path)
    monkeypatch.setattr(tool, 'read_json', read)
    with pytest.raises(RuntimeError, match=reason):
        compiler.compile_index(typescript_source)
    assert all(not path.is_relative_to(project) for path in reads)
    assert not sentinel.exists()


def test_parser_installer_projects_one_lock_with_sri_and_reuses_valid_hot_cache(typescript_source, tmp_path, monkeypatch):
    import architecture_typescript_tool as tool
    identity = tool.tool_identity(typescript_source)
    observed = []
    def npm(command, **options):
        observed.append(command)
        project = options['cwd']
        package = json.loads((project / 'package.json').read_text())
        lock = json.loads((project / 'package-lock.json').read_text())
        assert package['devDependencies'] == {'typescript': identity['version']}
        assert set(lock['packages']) == {'', 'node_modules/typescript'}
        assert {key: lock['packages']['node_modules/typescript'][key] for key in identity} == identity
        assert '--ignore-scripts' in command and '--fetch-retries=0' in command
        assert '--fetch-timeout=30000' in command and options['timeout'] == 60
        assert options['creationflags'] == tool.NO_WINDOW
        installed = project / 'node_modules/typescript'
        (installed / 'lib').mkdir(parents=True)
        (installed / 'package.json').write_text(json.dumps({'name': 'typescript', 'version': identity['version']}))
        (installed / 'lib/typescript.js').write_text('// acquisition-contract fixture; not used for parser execution')
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(tool.subprocess, 'run', npm)
    package, cold = tool.install(typescript_source, tmp_path / 'cache')
    again, hot = tool.install(typescript_source, tmp_path / 'cache')
    assert package == again and len(observed) == 1
    assert cold['cache'] == 'COLD' and hot['cache'] == 'HOT'
    changed = dict(identity, integrity='sha512-' + 'A' * 86 + '==')
    assert tool.identity_key(identity) != tool.identity_key(changed)


@pytest.mark.parametrize('failure,reason', [
    ('network', 'ARCHITECTURE_TOOL_UNAVAILABLE'),
    ('integrity', 'ARCHITECTURE_TOOL_INTEGRITY_FAILED'),
    ('timeout', 'ARCHITECTURE_TOOL_UNAVAILABLE'),
    ('empty_success', 'ARCHITECTURE_TOOL_INTEGRITY_FAILED'),
])
def test_parser_acquisition_failures_never_become_source_success(typescript_source, tmp_path, monkeypatch, failure, reason):
    import architecture_typescript_tool as tool
    def npm(command, **_options):
        if failure == 'timeout': raise subprocess.TimeoutExpired(command, 60)
        return SimpleNamespace(returncode=0 if failure == 'empty_success' else 1,
                               stderr=b'EINTEGRITY' if failure == 'integrity' else b'network unavailable')
    monkeypatch.setattr(tool.subprocess, 'run', npm)
    with pytest.raises(RuntimeError, match=reason):
        tool.install(typescript_source, tmp_path / 'cache')


def test_typescript_runtime_is_explicit_in_each_existing_required_owner():
    shards = json.loads((ROOT / '.github/python-test-shards.json').read_text())['shards']
    owners = [row['id'] for row in shards if 'tests/test_architecture_compiler.py' in row['tests']]
    assert owners == ['python-4']
    quality = (ROOT / '.github/workflows/quality-gates.yml').read_text()
    assert "if: matrix.id == 'python-4'" in quality
    assert 'run: python scripts/architecture_typescript_tool.py\n' in quality
    architecture = (ROOT / '.github/workflows/architecture.yml').read_text()
    assert architecture.count('run: python scripts/architecture_typescript_tool.py\n') == 2
    assert '--github-env' not in quality + architecture
    assert 'ARCHITECTURE_TYPESCRIPT_PACKAGE' not in quality + architecture
    assert 'npm ci' not in architecture  # only the bounded one-package owner acquires the tool


def test_generated_transport_composes_complete_facts_with_explicit_finite_budgets(typescript_source, tmp_path):
    index = compiler.compile_index(typescript_source)
    outputs = compiler.render(index)
    generated = tmp_path / 'bounded-generated'
    compiler.write_outputs(generated, outputs)
    assert compiler.read_generated_index(generated) == index
    manifest = json.loads(outputs['critical-index.json'])
    assert manifest['schema'] == compiler.TRANSPORT_VERSION
    assert sum(row['bytes'] for row in manifest['parts']) + len(outputs['critical-index.json'].encode()) <= compiler.MAXIMUM_TRANSPORT_BYTES
    assert len(manifest['parts']) <= compiler.MAXIMUM_PARTS
    assert sum(manifest['counts'].values()) <= compiler.MAXIMUM_RECORDS
    assert compiler.MAXIMUM_INDEX_BYTES == 2 * 1024 * 1024
    assert compiler.MAXIMUM_TRANSPORT_BYTES == 3 * 1024 * 1024
    assert compiler.MAXIMUM_PARTS == 32
    assert compiler.MAXIMUM_RECORDS == 10_240
    index['unexpected_large_fact'] = 'x' * compiler.MAXIMUM_INDEX_BYTES
    with pytest.raises(ValueError, match='ARCHITECTURE_INDEX_BUDGET_EXCEEDED'):
        compiler.render(index)


def _decode_with_real_node(generated):
    loader = (ROOT / 'web/build/architecture-current-source.mjs').as_uri()
    result = subprocess.run(['node', '--input-type=module', '-e',
        f'import {{readCurrentSourceIndex}} from {json.dumps(loader)}; '
        'process.stdout.write(JSON.stringify(readCurrentSourceIndex(process.argv[1])));',
        str(generated / 'critical-index.json')], capture_output=True, encoding='utf-8',
        errors='strict', timeout=15,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


@pytest.mark.parametrize('target_family', ['manifest', 'part'])
@pytest.mark.parametrize('fault', [
    'unchanged', 'replaced_before_validation', 'replaced_after_validation',
    'fstat_failure', 'lstat_failure', 'realpath_failure', 'directory_redirect',
])
def test_real_node_reader_validates_opened_identity_before_reading(source, tmp_path, target_family, fault):
    index = compiler.compile_index(source)
    generated = tmp_path / 'descriptor-wire'
    compiler.write_outputs(generated, compiler.render(index))
    manifest = json.loads((generated / 'critical-index.json').read_text())
    target = generated / ('critical-index.json' if target_family == 'manifest' else manifest['parts'][0]['file'])
    expected = tmp_path / 'expected.json'
    expected.write_text(compiler.canonical(index), encoding='utf-8')
    # Isolated real Node process: wrap its built-in fs boundary only to place
    # actual renames/junctions at a deterministic point. The production module
    # is imported unchanged; no test-only filesystem interface is exported.
    script = r'''
import assert from 'node:assert/strict';
import fs from 'node:fs';
import { syncBuiltinESMExports } from 'node:module';
import { dirname, join, resolve } from 'node:path';
const [loader, manifest, target, expectedPath, fault] = process.argv.slice(1);
const { readCurrentSourceIndex } = await import(loader);
const original = Object.fromEntries(['openSync', 'fstatSync', 'lstatSync', 'realpathSync',
  'readSync', 'closeSync', 'renameSync', 'writeFileSync', 'readFileSync', 'symlinkSync', 'unlinkSync']
  .map(name => [name, fs[name]]));
const expected = JSON.parse(original.readFileSync(expectedPath, 'utf8'));
const directory = dirname(target);
const parkedFile = join(dirname(directory), 'owned-parked.json');
const parkedDirectory = join(dirname(directory), 'owned-parked-directory');
let targetFd, targetClosed = false, swappedFile = false, redirected = false;
let reads = 0, closes = 0, allocations = 0;
const operations = [];
const watched = fd => targetFd !== undefined && fd === targetFd && !targetClosed;
const replaceFile = () => {
  original.renameSync(target, parkedFile); swappedFile = true;
  original.writeFileSync(target, 'replacement is deliberately not valid JSON');
};
fs.openSync = (path, ...args) => {
  if (resolve(path) === target && fault === 'directory_redirect') {
    // Redirect after the reader's initial root check but before acquisition:
    // Windows does not permit renaming a directory containing this open fd.
    original.renameSync(directory, parkedDirectory); redirected = true;
    original.symlinkSync(parkedDirectory, directory, process.platform === 'win32' ? 'junction' : 'dir');
  }
  const fd = original.openSync(path, ...args);
  if (resolve(path) === target) {
    targetFd = fd; operations.push('open');
    if (fault === 'replaced_before_validation') replaceFile();
  }
  return fd;
};
fs.fstatSync = (fd, ...args) => {
  if (watched(fd)) {
    operations.push('fstat');
    if (fault === 'fstat_failure') throw new Error('INJECTED_FSTAT_FAILURE');
  }
  return original.fstatSync(fd, ...args);
};
fs.lstatSync = (path, ...args) => {
  if (resolve(path) === target) {
    operations.push('lstat');
    if (fault === 'lstat_failure') throw new Error('INJECTED_LSTAT_FAILURE');
  }
  return original.lstatSync(path, ...args);
};
fs.realpathSync = (path, ...args) => {
  if (resolve(path) === target) {
    operations.push('realpath');
    if (fault === 'realpath_failure') throw new Error('INJECTED_REALPATH_FAILURE');
  }
  return original.realpathSync(path, ...args);
};
fs.readSync = (fd, ...args) => {
  if (watched(fd)) {
    reads += 1; operations.push('read');
    if (fault === 'replaced_after_validation' && !swappedFile) replaceFile();
  }
  return original.readSync(fd, ...args);
};
fs.closeSync = (fd, ...args) => {
  if (watched(fd)) { closes += 1; targetClosed = true; }
  return original.closeSync(fd, ...args);
};
const allocate = Buffer.alloc;
Buffer.alloc = (...args) => { if (watched(targetFd)) allocations += 1; return allocate(...args); };
syncBuiltinESMExports();
try {
  if (fault === 'unchanged' || fault === 'replaced_after_validation') {
    assert.deepEqual(readCurrentSourceIndex(manifest), expected);
    assert.ok(reads > 0); assert.equal(allocations, 1);
    assert.deepEqual(operations.slice(0, 4), ['open', 'fstat', 'lstat', 'realpath']);
  } else {
    const reason = fault.endsWith('_failure')
      ? `INJECTED_${fault.toUpperCase()}` : 'ARCHITECTURE_INDEX_TRANSPORT_INVALID';
    assert.throws(() => readCurrentSourceIndex(manifest), error => error.message === reason);
    assert.equal(reads, 0); assert.equal(allocations, 0);
  }
  assert.notEqual(targetFd, undefined, 'path validation never precedes descriptor acquisition');
  assert.equal(closes, 1, 'every acquired descriptor is closed, including validation failures');
  assert.throws(() => original.fstatSync(targetFd), error => error.code === 'EBADF');
  process.stdout.write(JSON.stringify({ fault, reads, allocations, closes, operations }));
} finally {
  Buffer.alloc = allocate;
  Object.assign(fs, original); syncBuiltinESMExports();
  if (redirected) { original.unlinkSync(directory); original.renameSync(parkedDirectory, directory); }
  if (swappedFile) { original.unlinkSync(target); original.renameSync(parkedFile, target); }
}
'''
    result = subprocess.run(['node', '--input-type=module', '-e', script,
        (ROOT / 'web/build/architecture-current-source.mjs').as_uri(),
        str(generated / 'critical-index.json'), str(target), str(expected), fault],
        capture_output=True, encoding='utf-8', errors='strict', timeout=15,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    assert result.returncode == 0, result.stderr
    measurement = json.loads(result.stdout)
    assert measurement['closes'] == 1
    assert compiler.read_generated_index(generated) == index, 'owned fixture restored after the race'


@pytest.mark.skipif(os.name == 'nt', reason='POSIX FIFO/NOFOLLOW semantics; Windows uses the real junction family')
@pytest.mark.parametrize('target_family', ['manifest', 'part'])
@pytest.mark.parametrize('replacement', ['fifo', 'symlink'])
def test_real_node_reader_rejects_nonregular_open_without_blocking(source, tmp_path, target_family, replacement):
    index = compiler.compile_index(source)
    generated = tmp_path / 'fifo-wire'
    compiler.write_outputs(generated, compiler.render(index))
    manifest = json.loads((generated / 'critical-index.json').read_text())
    target = generated / ('critical-index.json' if target_family == 'manifest' else manifest['parts'][0]['file'])
    fifo = tmp_path / 'owned-fifo'
    if replacement == 'fifo':
        os.mkfifo(fifo)
    else:
        fifo.symlink_to(str(fifo) + '.original')
    script = r'''
import assert from 'node:assert/strict';
import fs from 'node:fs';
import { syncBuiltinESMExports } from 'node:module';
const [loader, manifest, target, fifo, replacement] = process.argv.slice(1);
const {readCurrentSourceIndex} = await import(loader);
let reads = 0, closes = 0, targetFd;
const original = { open: fs.openSync, read: fs.readSync, close: fs.closeSync };
fs.openSync = (path, ...args) => {
  if (path === target) {
    fs.renameSync(target, `${fifo}.original`);
    fs.renameSync(fifo, target);
    targetFd = original.open(path, ...args);
    return targetFd;
  }
  return original.open(path, ...args);
};
fs.readSync = (fd, ...args) => { if (fd === targetFd) reads += 1; return original.read(fd, ...args); };
fs.closeSync = (fd, ...args) => { if (fd === targetFd) closes += 1; return original.close(fd, ...args); };
syncBuiltinESMExports();
if (replacement === 'fifo') {
  assert.throws(() => readCurrentSourceIndex(manifest), /ARCHITECTURE_INDEX_TRANSPORT_INVALID/);
  assert.notEqual(targetFd, undefined); assert.equal(closes, 1);
} else {
  assert.throws(() => readCurrentSourceIndex(manifest), error => error.code === 'ELOOP');
  assert.equal(targetFd, undefined); assert.equal(closes, 0);
}
assert.equal(reads, 0);
process.stdout.write(JSON.stringify({ rejected: true, reads, closes }));
'''
    result = subprocess.run(['node', '--input-type=module', '-e', script,
        (ROOT / 'web/build/architecture-current-source.mjs').as_uri(),
        str(generated / 'critical-index.json'), str(target), str(fifo), replacement],
        capture_output=True, encoding='utf-8', errors='strict', timeout=3)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)['rejected'] is True


def test_python_parts_real_node_preserves_exact_order_duplicates_and_unicode(source, tmp_path):
    index = compiler.compile_index(source)
    index['inputs']['scripts/other.py'] = 'b' * 64
    first = dict(index['observed']['edges'][0], **{'\U00010000': '中文\U0001f600', '\ue000': 2, '10': 10, '2': 2})
    other = dict(first, source='scripts/other.py::owner')
    # Deliberately interleave sources and preserve a duplicate with tied sort
    # keys. A regroup/sort, Set, UTF-16 key sort or JS object-key order is wrong.
    index['observed']['edges'] = [other, first, dict(first), other, first]
    original = json.loads(compiler.canonical(index))
    outputs = compiler.render(index)
    generated = tmp_path / 'unicode-wire'
    compiler.write_outputs(generated, outputs)
    assert compiler.read_generated_index(generated) == original
    assert _decode_with_real_node(generated) == original
    assert index == original, 'transport must not mutate logical source facts'
    assert outputs == compiler.render(index), 'parts and logical identity are deterministic'


def test_source_growth_splits_only_at_complete_records_and_retains_all_facts(source, tmp_path):
    index = compiler.compile_index(source)
    edge = index['observed']['edges'][0]
    index['observed']['edges'] = [dict(edge, statement='中' * 230_000, sequence=i) for i in range(4)]
    assert len(compiler.canonical(index).encode('utf-8')) > compiler.MAXIMUM_INDEX_BYTES
    outputs = compiler.render(index)
    manifest = json.loads(outputs['critical-index.json'])
    assert len(manifest['parts']) == 2
    assert len({part['source_path'] for part in manifest['parts']}) == 1
    assert all(len(value.encode('utf-8')) <= compiler.MAXIMUM_INDEX_BYTES
               for name, value in outputs.items() if name.endswith('.json'))
    generated = tmp_path / 'large-wire'
    compiler.write_outputs(generated, outputs)
    assert compiler.read_generated_index(generated) == index
    assert _decode_with_real_node(generated) == index
    # Shrinking a source retires only the exact old-manifest-owned part.
    reduced = dict(index, observed=dict(index['observed'], edges=index['observed']['edges'][:1]))
    compiler.write_outputs(generated, compiler.render(reduced))
    assert compiler.read_generated_index(generated) == reduced
    assert not (generated / 'critical-facts-00001.json').exists()


@pytest.mark.parametrize('kind,reason', [
    ('record', 'ARCHITECTURE_INDEX_BUDGET_EXCEEDED'),
    ('total', 'ARCHITECTURE_TRANSPORT_TOTAL_BUDGET_EXCEEDED'),
    ('parts', 'ARCHITECTURE_TRANSPORT_PART_BUDGET_EXCEEDED'),
    ('records', 'ARCHITECTURE_TRANSPORT_RECORD_BUDGET_EXCEEDED'),
    ('float', 'ARCHITECTURE_TRANSPORT_VALUE_INVALID'),
    ('unsafe_integer', 'ARCHITECTURE_TRANSPORT_VALUE_INVALID'),
])
def test_transport_growth_has_independent_hard_limits(source, kind, reason):
    index = compiler.compile_index(source)
    edge = index['observed']['edges'][0]
    if kind == 'record':
        index['observed']['edges'] = [dict(edge, statement='x' * compiler.MAXIMUM_INDEX_BYTES)]
    elif kind == 'total':
        index['observed']['edges'] = [dict(edge, statement='x' * 810_000) for _ in range(4)]
    elif kind == 'parts':
        paths = [f'scripts/owner_{i}.py' for i in range(33)]
        index['inputs'].update({path: 'a' * 64 for path in paths})
        index['observed']['edges'] = [dict(edge, source=path + '::owner') for path in paths]
    elif kind == 'records':
        index['observed']['edges'] = [edge] * (compiler.MAXIMUM_RECORDS + 1)
    else:
        index['numeric_fact'] = 0.5 if kind == 'float' else 9_007_199_254_740_992
    with pytest.raises(ValueError, match=reason):
        compiler.render(index)


def test_producer_stops_serializing_the_record_tail_when_aggregate_bytes_are_exhausted(source, monkeypatch):
    index = compiler.compile_index(source)
    edge = dict(index['observed']['edges'][0], statement='x' * 500_000)
    index['observed']['edges'] = [edge] * 9
    original, ordinals = compiler.canonical, []
    def spy(value, *args, **kwargs):
        if isinstance(value, list) and len(value) == 2 and isinstance(value[0], int):
            ordinals.append(value[0])
        return original(value, *args, **kwargs)
    monkeypatch.setattr(compiler, 'canonical', spy)
    with pytest.raises(ValueError, match='ARCHITECTURE_TRANSPORT_TOTAL_BUDGET_EXCEEDED'):
        compiler.render_transport(index)
    # Symbols are encoded first; inspect only the statement-bearing edge tail.
    edge_ordinals = ordinals[-7:]
    assert edge_ordinals == list(range(7))
    assert 7 not in ordinals and 8 not in ordinals


@pytest.mark.parametrize('character,count,accepted', [('中', 200_000, True),
    ('x', 2 * 1024 * 1024, False), ('中', 1_000_000, False), ('\x00', 1_000_000, False)],
    ids=['valid-unicode', 'oversized-ascii', 'oversized-utf8', 'escaped-controls'])
def test_large_logical_strings_never_require_one_unbounded_escaped_allocation(monkeypatch, character, count, accepted):
    text = character * count
    original = json.dumps
    expected = original({'text': text}, ensure_ascii=False, sort_keys=True, separators=(',', ':')) + '\n' if accepted else None
    def bounded_dump(value, *args, **kwargs):
        assert value is not text, 'do not hand the complete oversized logical string to the scalar encoder'
        return original(value, *args, **kwargs)
    monkeypatch.setattr(json, 'dumps', bounded_dump)
    if accepted:
        assert compiler.canonical({'text': text}, compiler.MAXIMUM_INDEX_BYTES) == expected
    else:
        with pytest.raises(ValueError, match='ARCHITECTURE_INDEX_BUDGET_EXCEEDED'):
            compiler.canonical({'text': text}, compiler.MAXIMUM_INDEX_BYTES)


@pytest.mark.parametrize('kind,reason', [
    ('parts', 'ARCHITECTURE_TRANSPORT_PART_BUDGET_EXCEEDED'),
    ('records', 'ARCHITECTURE_TRANSPORT_RECORD_BUDGET_EXCEEDED'),
    ('total', 'ARCHITECTURE_TRANSPORT_TOTAL_BUDGET_EXCEEDED'),
])
def test_consumer_rejects_aggregate_budget_before_any_part_read(source, tmp_path, monkeypatch, kind, reason):
    index = compiler.compile_index(source)
    manifest = json.loads(compiler.render(index)['critical-index.json'])
    if kind == 'parts':
        manifest['parts'] *= 33
    elif kind == 'records':
        manifest['counts']['edges'] = compiler.MAXIMUM_RECORDS + 1
    else:
        descriptor = manifest['parts'][0]
        descriptor['bytes'] = compiler.MAXIMUM_INDEX_BYTES
        manifest['parts'].append(dict(descriptor, file='critical-facts-00001.json'))
        manifest['counts'] = {family: count * 2 for family, count in descriptor['counts'].items()}
    generated = tmp_path / 'rejected-wire'
    generated.mkdir()
    (generated / 'critical-index.json').write_text(compiler.canonical(manifest), encoding='utf-8', newline='\n')
    actual_read, reads = compiler._read_generated, []
    def bounded_read(directory, name, *args):
        reads.append(name)
        return actual_read(directory, name, *args)
    monkeypatch.setattr(compiler, '_read_generated', bounded_read)
    with pytest.raises(ValueError, match=reason):
        compiler.read_generated_index(generated)
    assert reads == ['critical-index.json']


@pytest.mark.parametrize('kind', ['duplicate', 'gap', 'source', 'digest', 'unknown_output'])
def test_transport_identity_cannot_replace_order_or_discard_unknown_files(source, tmp_path, kind):
    index = compiler.compile_index(source)
    generated = tmp_path / 'corrupt-wire'
    compiler.write_outputs(generated, compiler.render(index))
    manifest_path = generated / 'critical-index.json'
    manifest = json.loads(manifest_path.read_text())
    descriptor = manifest['parts'][0]
    path = generated / descriptor['file']
    part = json.loads(path.read_text())
    if kind == 'unknown_output':
        unknown = generated / 'not-generator-owned.txt'
        unknown.write_text('preserve me')
        with pytest.raises(ValueError, match='ARCHITECTURE_GENERATED_DRIFT'):
            compiler.read_generated_index(generated)
        with pytest.raises(ValueError, match='ARCHITECTURE_GENERATED_UNOWNED_OUTPUT'):
            compiler.write_outputs(generated, compiler.render(index))
        assert unknown.read_text() == 'preserve me'
        return
    if kind == 'duplicate': part['observed']['edges'][1][0] = part['observed']['edges'][0][0]
    elif kind == 'gap': part['observed']['edges'][0][0] = manifest['counts']['edges']
    elif kind == 'source': part['source_path'] = 'other.py'
    elif kind == 'digest': manifest['logical_sha256'] = '0' * 64
    raw = compiler.canonical(part).encode('utf-8')
    path.write_bytes(raw)
    descriptor.update(bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())
    manifest_path.write_text(compiler.canonical(manifest), encoding='utf-8', newline='\n')
    with pytest.raises(ValueError, match='ARCHITECTURE_TRANSPORT_'):
        compiler.read_generated_index(generated)


def test_exact_generated_bytes_survive_real_git_autocrlf_checkout(source, tmp_path):
    checkout = tmp_path / 'checkout'
    checkout.mkdir()
    shutil.copyfile(ROOT / '.gitattributes', checkout / '.gitattributes')
    generated = checkout / 'architecture/generated'
    index = compiler.compile_index(source)
    compiler.write_outputs(generated, compiler.render(index))
    before = {path.name: path.read_bytes() for path in generated.glob('*.json')}
    options = dict(cwd=checkout, capture_output=True, check=True, timeout=5,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    subprocess.run(['git', 'init', '--quiet'], **options)
    subprocess.run(['git', 'config', 'core.autocrlf', 'true'], **options)
    subprocess.run(['git', 'add', '.gitattributes', 'architecture/generated'], **options)
    for name, raw in before.items():
        (generated / name).write_bytes(raw.replace(b'\n', b'\r\n'))
    subprocess.run(['git', 'checkout-index', '--all', '--force'], **options)
    assert {name: (generated / name).read_bytes() for name in before} == before
    assert _decode_with_real_node(generated) == index


@pytest.mark.parametrize('interruption', ['torn_part', 'torn_manifest', 'next_part_before_manifest'])
def test_fresh_producer_repairs_its_exact_outputs_without_trusting_partial_old_bytes(source, tmp_path, interruption):
    original = compiler.compile_index(source)
    generated = tmp_path / 'recoverable-wire'
    compiler.write_outputs(generated, compiler.render(original))
    target = json.loads(compiler.canonical(original))
    if interruption == 'next_part_before_manifest':
        target['inputs']['scripts/other.py'] = 'a' * 64
        target['observed']['edges'].append(dict(target['observed']['edges'][0], source='scripts/other.py::owner'))
    outputs = compiler.render(target)
    if interruption == 'torn_manifest':
        (generated / 'critical-index.json').write_text('{torn', encoding='utf-8')
    elif interruption == 'torn_part':
        (generated / 'critical-facts-00000.json').write_text('{torn', encoding='utf-8')
    else:
        (generated / 'critical-facts-00001.json').write_text(outputs['critical-facts-00001.json'], encoding='utf-8', newline='\n')
    with pytest.raises((ValueError, KeyError)):
        compiler.read_generated_index(generated)
    compiler.write_outputs(generated, outputs)
    assert compiler.read_generated_index(generated) == target
    assert _decode_with_real_node(generated) == target


def test_retired_part_requires_its_own_proven_identity_before_deletion(source, tmp_path):
    original = compiler.compile_index(source)
    extended = json.loads(compiler.canonical(original))
    extended['inputs']['scripts/other.py'] = 'a' * 64
    extended['observed']['edges'].append(dict(extended['observed']['edges'][0], source='scripts/other.py::owner'))
    generated = tmp_path / 'retirement-wire'
    compiler.write_outputs(generated, compiler.render(extended))
    retired = generated / 'critical-facts-00001.json'
    # Same-sized valid JSON is no longer the old manifest's proven artifact.
    content = retired.read_bytes().replace(b'owner', b'other')
    retired.write_bytes(content)
    manifest_before = (generated / 'critical-index.json').read_bytes()
    with pytest.raises(ValueError, match='ARCHITECTURE_TRANSPORT_PART_IDENTITY_INVALID'):
        compiler.write_outputs(generated, compiler.render(original))
    assert retired.read_bytes() == content
    assert (generated / 'critical-index.json').read_bytes() == manifest_before


def test_generated_directory_junction_cannot_redirect_producer_or_python_consumer(source, tmp_path):
    physical, alias = tmp_path / 'physical-generated', tmp_path / 'alias-generated'
    outputs = compiler.render(compiler.compile_index(source))
    compiler.write_outputs(physical, outputs)
    before = {path.name: path.read_bytes() for path in physical.iterdir()}
    if os.name == 'nt':
        subprocess.run(['cmd.exe', '/c', 'mklink', '/J', str(alias), str(physical)],
            check=True, capture_output=True, timeout=5, creationflags=subprocess.CREATE_NO_WINDOW)
    else:
        alias.symlink_to(physical, target_is_directory=True)
    try:
        with pytest.raises(ValueError, match='ARCHITECTURE_OUTPUT_PATH_ESCAPE'):
            compiler.read_generated_index(alias)
        with pytest.raises(ValueError, match='ARCHITECTURE_OUTPUT_PATH_ESCAPE'):
            compiler.write_outputs(alias, outputs)
        assert {path.name: path.read_bytes() for path in physical.iterdir()} == before
    finally:
        if os.name == 'nt': alias.rmdir()
        else: alias.unlink()


def test_current_news_worker_audit_view_keeps_independent_transports_and_dynamic_binding():
    index = compiler.read_generated_index(ROOT / 'architecture/generated')
    roots = set(index['allowed']['views']['news-worker-audit']['roots'])
    symbols = {row['id'] for row in index['observed']['symbols']}
    edges = index['observed']['edges']
    def calls(owner):
        return {row['target'] for row in edges if row['source'] == owner and row['kind'] == 'calls'}
    def url_fragments(owner):
        return {part for row in edges if row['source'] == owner
                for part in ([row['first_argument_literal']] if 'first_argument_literal' in row
                             else row.get('first_argument_template', []))}
    assert roots <= symbols
    for family in ('index', 'content', 'evidence'):
        path = f'web/app/api/news-{family}/route.ts'
        assert {f'{path}::GET', f'{path}::POST'} <= symbols
    assert {'prepareNewsProjection', 'stageNewsProjectionBatch', 'activateNewsProjection', 'verifyNewsProjection'} <= calls('web/app/api/news-index/route.ts::POST')
    assert 'stageNewsProjectionBatch' in calls('web/app/api/news-content/route.ts::POST')
    assert 'activateNewsEvidenceSnapshot' in calls('web/app/api/news-evidence/route.ts::POST')
    snapshots = {row['target'] for row in edges if row['source'] == 'web/worker/api-router.ts::SNAPSHOT_ROUTES'
                 and row['kind'] == 'declares_member'}
    assert {'/api/audit', '/api/audit-decisions', '/api/audit-briefs', '/api/audit-stories'} <= snapshots
    assert not snapshots & {'/api/news-index', '/api/news-content', '/api/news-evidence'}
    assert {'snapshotRead', 'snapshotWrite', 'genericRoute'} <= calls('web/worker/api-router.ts::routeApiRequest')
    generic = [row for row in edges if row['source'] == 'web/worker/api-router.ts::genericRoute' and row['target'] in {'loader', 'handler'}]
    assert len(generic) == 2 and all(row['resolution'] == 'UNKNOWN' for row in generic)
    assert '/api/news-index?' in url_fragments('web/app/_views/AuditView.tsx::AuditView.refreshNews')
    assert '/api/audit' in url_fragments('web/app/_views/AuditView.tsx::AuditView.refreshAudit')
    assert 'loadDashboardResource' in calls('web/app/_views/AuditView.tsx::AuditView.refreshEvidence')
    detail_validators = [row for row in edges if row['source'].startswith('web/app/_views/AuditView.tsx::AuditView.refreshAuditDetail.')
                         and row['source'].endswith('.validate') and row['target'] == 'validAuditDetailPayload']
    assert len(detail_validators) == 1
    assert 'authoritativeNewsTotals' in calls('web/app/_views/AuditView.tsx::AuditView.refreshNews')
    assert not any(row['source'].startswith('web/') and row['resolution'] not in {'UNKNOWN', 'LITERAL'} for row in edges)

    api = 'scripts/run_dashboard_api.py::'
    news = 'xauusd_forecaster/dashboard/news_resources.py::'
    assert {api + name for name in ('Handler.do_GET', '_optional_resource_payload', 'main')} <= roots
    assert {news + name for name in ('_build_news_projection_source',
        '_news_projection_source_for_request', '_finish_news_projection_source_build',
        '_build_news_evidence_resource')} <= roots
    assert {'_news_projection_source_for_request', '_news_projection_batch',
            '_build_news_evidence_resource', '_news_evidence_page',
            'read_dashboard_read_model'} <= calls(api + 'Handler.do_GET')
    assert '_news_archive_page' not in calls(api + 'Handler.do_GET')
    assert {'_news_reader_rows', 'build_news_projection_generation'} <= calls(news + '_build_news_projection_source')
    assert '_build_news_projection_source_from_database' in calls(news + '_finish_news_projection_source_build')
    assert '_build_news_projection_source' in calls(news + '_build_news_projection_source_from_database')
    assert 'threading.Thread' in calls(news + '_news_projection_source_for_request')
    assert '_finish_news_projection_source_build' not in calls(news + '_news_projection_source_for_request'), 'callback argument is not a direct call'
    assert {'event_evidence_rows_from_connection', '_materialize_news_evidence_generation',
            '_publish_news_evidence_snapshot'} <= calls(news + '_build_news_evidence_resource')
    assert {'temporary.write_text', 'temporary.replace'} <= calls(news + '_materialize_news_evidence_generation')
    assert {'_dashboard_payload', 'audit_snapshot', 'audit_briefs_snapshot',
            'audit_stories_snapshot', 'audit_decisions_snapshot'} <= calls(api + '_optional_resource_payload')
    assert {'DashboardReadModelOwner', 'read_model_owner.start', 'ThreadingHTTPServer'} <= calls(api + 'main')
    scoped_ids = {row['id'] for row in index['observed']['symbols'] if row['path'] == 'scripts/run_dashboard_api.py'}
    selected = set(index['allowed']['source_symbols']['scripts/run_dashboard_api.py'])
    assert selected <= scoped_ids
    assert all(any(symbol == owner or symbol.startswith(owner + '.') for owner in selected) for symbol in scoped_ids)
    for prefix, target in ((news, '_news_reader_rows'), (api, '_dashboard_payload'),
                           (news, 'build_news_projection_generation')):
        frontier = [row for row in edges if row['source'].startswith(prefix) and row['target'] == target]
        assert frontier and all(row['resolution'] == 'UNKNOWN' and 'candidate_symbol' not in row for row in frontier)
    assert api + '_news_projection_source' not in scoped_ids
