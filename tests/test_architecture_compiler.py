"""Source truth, incomplete-analysis visibility and generated drift contracts."""
import importlib.util
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
def test_build_then_check_and_tamper_are_real_cli_boundaries(request, fixture_name, monkeypatch):
    source = request.getfixturevalue(fixture_name)
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
def test_real_powershell_parser_emits_dynamic_calls_and_rejects_invalid_source(source):
    manifest_path = source / compiler.SELECTION
    manifest = json.loads(manifest_path.read_text())
    manifest['views']['fixture']['files'].append('scripts/fixture.ps1')
    manifest['views']['fixture']['roots'].append('scripts/fixture.ps1::Invoke-Fixture')
    manifest_path.write_text(json.dumps(manifest), encoding='utf-8')
    script = source / 'scripts/fixture.ps1'
    script.write_text('function Invoke-Fixture { & $unknown; Write-Output "中文" }', encoding='utf-8')
    index = compiler.compile_index(source)
    assert any(e['target'] == '<dynamic-command>' and e['resolution'] == 'UNKNOWN'
               for e in index['observed']['edges'])
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


def test_typescript_sources_and_tool_identity_are_relocatable_without_unrelated_lock_churn(typescript_source, tmp_path):
    first = compiler.compile_index(typescript_source)
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
    lock['packages']['node_modules/typescript']['integrity'] = 'sha512-' + 'A' * 86 + '=='
    lock_path.write_text(json.dumps(lock), encoding='utf-8')
    with pytest.raises(RuntimeError, match='ARCHITECTURE_TOOL_INTEGRITY_FAILED'):
        compiler.compile_index(copy)


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


def test_generated_transport_compacts_without_discarding_facts_or_raising_bound(typescript_source):
    index = compiler.compile_index(typescript_source)
    content = compiler.render(index)['critical-index.json']
    assert json.loads(content) == index
    assert len(content.encode()) < len(compiler.encoded(index).encode())
    assert compiler.MAXIMUM_INDEX_BYTES == 2 * 1024 * 1024
    index['unexpected_large_fact'] = 'x' * compiler.MAXIMUM_INDEX_BYTES
    with pytest.raises(ValueError, match='ARCHITECTURE_INDEX_BUDGET_EXCEEDED'):
        compiler.render(index)


def test_current_news_worker_audit_view_keeps_independent_transports_and_dynamic_binding():
    index = json.loads((ROOT / 'architecture/generated/critical-index.json').read_text(encoding='utf-8'))
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
