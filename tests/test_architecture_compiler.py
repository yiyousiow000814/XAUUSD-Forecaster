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


def test_build_then_check_and_tamper_are_real_cli_boundaries(source):
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
    if kind == 'language': view['files'].append('scripts/not-parsed.ts')
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
