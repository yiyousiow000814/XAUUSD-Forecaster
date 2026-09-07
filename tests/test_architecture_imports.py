"""Declared package directions, compatibility and non-executing source checks."""
from copy import deepcopy
import importlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]


def put(root, path, text):
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding='utf-8')
    return target


def save_policy(root, policy):
    put(root, 'architecture/critical-paths.json', json.dumps({'python_import_policy': policy}))


@pytest.fixture
def checker(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / 'scripts'))
    return importlib.import_module('check_architecture_imports')


@pytest.fixture
def source(tmp_path):
    policy = deepcopy(json.loads((ROOT / 'architecture/critical-paths.json').read_text())['python_import_policy'])
    policy['script_imports'] = []
    policy['legacy_shims'] = []
    put(tmp_path, 'xauusd_forecaster/__init__.py', '"""Public facade."""\n')
    put(tmp_path, 'scripts/entry.py', '# Runtime entrypoint fixture; never executed.\n')
    save_policy(tmp_path, policy)
    return tmp_path, policy


@pytest.mark.parametrize('body', [
    'import scripts', 'import scripts.entry as owner', 'from scripts import entry as owner',
    'from scripts.entry import run', 'def later():\n    from scripts import entry',
])
def test_package_to_entrypoint_direction_is_enforced_for_the_import_family(checker, source, body):
    root, _ = source
    put(root, 'xauusd_forecaster/domain.py', body)
    result = checker.check_architecture_imports(root)
    assert any(row['reason'] == 'ARCHITECTURE_PACKAGE_IMPORTS_SCRIPT' for row in result['violations'])
    assert all(row['path'] == 'xauusd_forecaster/domain.py' for row in result['violations'])


@pytest.mark.parametrize('body', [
    'from ..dashboard import owner', 'from xauusd_forecaster.dashboard import owner',
    'import xauusd_forecaster.dashboard.owner as owner',
])
@pytest.mark.parametrize('area', ['news', 'training', 'evidence', 'ai', 'runtime', 'assistant', 'decision'])
def test_canonical_sibling_packages_cannot_reverse_the_dashboard_direction(checker, source, area, body):
    root, _ = source
    put(root, f'xauusd_forecaster/{area}/owner.py', body)
    result = checker.check_architecture_imports(root)
    assert any(row['reason'] == 'ARCHITECTURE_DOMAIN_IMPORTS_DASHBOARD' for row in result['violations'])


@pytest.mark.parametrize('body', ['from ..assistant import owner', 'import web.worker', 'from web import worker'])
def test_declared_decision_namespace_cannot_gain_optional_runtime_dependency(checker, source, body):
    root, _ = source
    put(root, 'xauusd_forecaster/decision/owner.py', body)
    assert any(row['reason'] == 'ARCHITECTURE_DECISION_OPTIONAL_DEPENDENCY'
               for row in checker.check_architecture_imports(root)['violations'])


def test_similar_names_flat_modules_and_dynamic_requests_do_not_gain_runtime_authority(checker, source):
    root, _ = source
    put(root, 'xauusd_forecaster/decision.py', 'from .dashboard import owner\n')
    put(root, 'xauusd_forecaster/news/owner.py',
        'import scripts_extra\nfrom xauusd_forecaster.dashboard_extra import value\n'
        'import importlib\nimportlib.import_module("scripts.entry")\n'
        'importlib.import_module(selected_by_environment)\n'
        'importlib.import_module(name="scripts.entry")\n'
        'importlib.util.spec_from_file_location(name="scripts.entry", location=selected_path)\n')
    result = checker.check_architecture_imports(root)
    assert result['violations'] == []
    assert [row['requested_module'] for row in result['dynamic_requests']] == ['scripts.entry', None, 'scripts.entry', 'scripts.entry']
    assert all(row['runtime_resolution'] == 'UNKNOWN' for row in result['dynamic_requests'])
    assert result['runtime_resolution'] == 'UNKNOWN'


@pytest.mark.parametrize('body, valid', [
    ('"""Facade."""\nfrom .owner import value\n__all__ = ["value"]\n', True),
    ('__all__ = ("value",)\n', True),
    ('start_thread()\n', False), ('client = Client()\n', False),
    ('__all__ = build_names()\n', False), ('def business_logic():\n    return 1\n', False),
    ('if configured:\n    install_schema()\n', False),
    ('from .owner import *\n', False),
])
@pytest.mark.parametrize('path', ['xauusd_forecaster/__init__.py', 'xauusd_forecaster/news/__init__.py', 'xauusd_forecaster/news/nested/__init__.py'])
def test_initializer_declaration_only_contract_covers_nested_siblings(checker, source, body, valid, path):
    root, _ = source
    put(root, path, body)
    result = checker.check_architecture_imports(root)
    assert (result['violations'] == []) is valid
    if not valid:
        assert result['violations'][0]['reason'] == 'ARCHITECTURE_IMPORT_DECLARATIONS_ONLY'


def test_explicit_whole_file_shim_has_one_owner_and_canonical_callers_cannot_use_it(checker, source):
    root, policy = source
    put(root, 'xauusd_forecaster/news/owner.py', 'def value():\n    return 1\n')
    put(root, 'xauusd_forecaster/old.py', 'from .news.owner import value\n')
    policy['legacy_shims'] = [{'path': 'xauusd_forecaster/old.py',
        'owner': 'xauusd_forecaster/news/owner.py', 'remove_when': 'All old callers are migrated.'}]
    save_policy(root, policy)
    assert checker.check_architecture_imports(root)['violations'] == []
    put(root, 'xauusd_forecaster/training/consumer.py', 'from ..old import value\n')
    assert any(row['reason'] == 'ARCHITECTURE_CANONICAL_IMPORTS_SHIM'
               for row in checker.check_architecture_imports(root)['violations'])
    put(root, 'xauusd_forecaster/old.py', 'client = Client()\n')
    assert any(row['reason'] == 'ARCHITECTURE_IMPORT_DECLARATIONS_ONLY'
               for row in checker.check_architecture_imports(root)['violations'])


@pytest.mark.parametrize('body', ['import scripts.entry as old', 'from scripts import entry', 'from entry import value'])
def test_script_exceptions_are_exact_reviewable_pairs_not_wildcard_runtime_identity(checker, source, body):
    root, policy = source
    put(root, 'scripts/caller.py', body)
    assert checker.check_architecture_imports(root)['violations'][0]['reason'] == 'ARCHITECTURE_UNDECLARED_SCRIPT_IMPORT'
    policy['script_imports'] = [{'source': 'scripts/caller.py', 'target': 'scripts/entry.py',
        'reason': 'Read parity from the explicit producer root.', 'remove_when': 'Owner interface replaces this pair.',
        'binding': 'PARAMETERIZED_PRODUCER_ROOT'}]
    save_policy(root, policy)
    result = checker.check_architecture_imports(root)
    assert result['violations'] == []
    assert result['runtime_resolution'] == 'UNKNOWN'
    put(root, 'scripts/unrelated.py', body)
    assert checker.check_architecture_imports(root)['violations'][0]['path'] == 'scripts/unrelated.py'


@pytest.mark.parametrize('body', ['import scripts.missing', 'from scripts import missing', 'from scripts.missing import value'])
def test_a_missing_script_target_does_not_erase_the_declared_dependency_request(checker, source, body):
    root, _ = source
    put(root, 'scripts/caller.py', body)
    errors = checker.check_architecture_imports(root)['violations']
    assert errors == [{'path': 'scripts/caller.py', 'line': 1,
                       'reason': 'ARCHITECTURE_UNDECLARED_SCRIPT_IMPORT', 'target': 'scripts/missing.py'}]


@pytest.mark.parametrize('mutation', ['missing', 'escape', 'duplicate', 'no_reason', 'wrong_area', 'no_policy'])
def test_policy_missing_or_invalid_authority_is_not_a_pass(checker, source, mutation):
    root, policy = source
    record = {'source': 'scripts/entry.py', 'target': 'scripts/missing.py',
              'reason': 'A declared bridge.', 'remove_when': 'A replacement is accepted.', 'binding': 'MODULE_REQUEST_ONLY'}
    policy['script_imports'] = [record]
    if mutation == 'escape': record['target'] = 'scripts/../outside.py'
    if mutation == 'duplicate':
        put(root, 'scripts/missing.py', '# Present\n')
        policy['script_imports'].append(deepcopy(record))
    if mutation == 'no_reason': record['reason'] = ''
    if mutation == 'wrong_area': policy['canonical_packages']['news'] = 'xauusd_forecaster/news.py'
    save_policy(root, policy)
    if mutation == 'no_policy': put(root, 'architecture/critical-paths.json', '{}')
    with pytest.raises(ValueError, match='ARCHITECTURE_(IMPORT_POLICY|INPUT_INVALID)'):
        checker.check_architecture_imports(root)


def test_actual_repository_policy_and_required_workflow(checker):
    result = checker.check_architecture_imports(ROOT)
    assert result['violations'] == []
    assert result['files'] == len(list((ROOT / 'xauusd_forecaster').rglob('*.py'))) + len(list((ROOT / 'scripts').glob('*.py')))
    preview = next(row for row in result['dynamic_requests']
                   if row['path'] == 'scripts/build_preview_bundle.py'
                   and row['requested_module'] == 'scripts.run_dashboard_sync')
    assert preview['requested_module'] == 'scripts.run_dashboard_sync'
    assert preview['runtime_resolution'] == 'UNKNOWN'
    workflow = (ROOT / '.github/workflows/architecture.yml').read_text()
    assert 'python scripts/check_architecture_imports.py' in workflow
    assert 'python -m pytest tests/test_architecture_imports.py -q' in workflow


def test_real_copied_cli_is_source_bound_read_only_and_never_imports_the_checked_code(source, tmp_path):
    root, policy = source
    for name in ['architecture_compiler.py', 'check_architecture_imports.py']:
        shutil.copyfile(ROOT / 'scripts' / name, root / 'scripts' / name)
    policy['script_imports'] = [{'source': 'scripts/check_architecture_imports.py',
        'target': 'scripts/architecture_compiler.py', 'reason': 'Use the shared parser.',
        'remove_when': 'Tool interface moves together.', 'binding': 'MODULE_REQUEST_ONLY'}]
    # The copied compiler's optional TypeScript helper import is a source request,
    # but the helper need not be installed or executed for this Python-only CLI.
    put(root, 'xauusd_forecaster/never_execute.py', 'raise RuntimeError("APPLICATION_WAS_EXECUTED")\n')
    save_policy(root, policy)
    other = tmp_path / 'unrelated-cwd'
    other.mkdir()
    command = [sys.executable, str(root / 'scripts/check_architecture_imports.py'), '--json']
    def run():
        return subprocess.run(command, cwd=other, capture_output=True, text=True, timeout=20,
                              creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    before = {path: path.read_bytes() for path in root.rglob('*.py')}
    result = run()
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)['violations'] == []
    assert {path: path.read_bytes() for path in before} == before
    put(root, 'xauusd_forecaster/never_execute.py', 'import scripts.entry\n')
    result = run()
    assert result.returncode == 1
    assert 'ARCHITECTURE_PACKAGE_IMPORTS_SCRIPT' in result.stdout
    put(root, 'xauusd_forecaster/never_execute.py', 'def invalid(:\n')
    result = run()
    assert result.returncode == 1 and 'SyntaxError' in result.stderr
