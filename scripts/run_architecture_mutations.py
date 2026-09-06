#!/usr/bin/env python
"""Run three source-validated mutations in a disposable tracked-source copy.

Adapted from #325's symbol extents, exact replacement, baseline-first execution
and distinct outcomes. Unlike that implementation, a nonzero process exit is
not itself KILLED: every selected case must fail its named behavior assertion.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import tomllib
import xml.etree.ElementTree as ET
import zipfile

from architecture_compiler import compile_index

NO_WINDOW = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0


def run(command, root, timeout=45, **options):
    environment = {key: value for key, value in os.environ.items() if key.upper() in {
        'PATH', 'SYSTEMROOT', 'WINDIR', 'TEMP', 'TMP', 'COMSPEC', 'USERPROFILE',
        'APPDATA', 'LOCALAPPDATA', 'HOME', 'PATHEXT', 'PROGRAMFILES', 'PROGRAMFILES(X86)'}}
    environment['PYTHONUTF8'] = '1'
    environment['PYTHONDONTWRITEBYTECODE'] = '1'
    return subprocess.run(command, cwd=root, env=environment, capture_output=True,
                          timeout=timeout, creationflags=NO_WINDOW, **options)


def classify(result, xml, expected, marker, baseline=False):
    if not xml.is_file():
        return 'ERROR', 'MISSING_TEST_RESULT'
    try:
        cases = list(ET.parse(xml).iter('testcase'))
    except ET.ParseError:
        return 'ERROR', 'MALFORMED_TEST_RESULT'
    if len(cases) != expected or any(case.find('skipped') is not None or case.find('error') is not None for case in cases):
        return 'ERROR', 'CASE_COUNT_SKIP_OR_SETUP_ERROR'
    failures = [case.find('failure') for case in cases]
    if result.returncode == 0 and all(failure is None for failure in failures):
        return ('BASELINE_PASSED' if baseline else 'SURVIVED'), 'ALL_CASES_PASSED'
    if baseline:
        return 'ERROR', 'BASELINE_FAILED'
    if result.returncode == 1 and all(
        failure is not None
        and failure.get('type', '').endswith('AssertionError')
        and marker in failure.get('message', '')
        for failure in failures
    ):
        return 'KILLED', 'EXACT_BEHAVIOR_ASSERTIONS_FAILED'
    return 'ERROR', 'UNEXPECTED_TEST_FAILURE'


def extent(root, mutation, text):
    if mutation['path'].endswith('.py'):
        nodes = [node for node in ast.walk(ast.parse(text)) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == mutation['symbol']]
        if len(nodes) != 1:
            raise ValueError('SELECTOR_AMBIGUOUS')
        start, end = nodes[0].lineno, nodes[0].end_lineno
    else:
        symbols = compile_index(root)['observed']['symbols']
        matches = [symbol for symbol in symbols if symbol['id'] == mutation['path'] + '::' + mutation['symbol']]
        if len(matches) != 1:
            raise ValueError('SELECTOR_AMBIGUOUS')
        start, end = matches[0]['line'], matches[0]['end_line']
    lines = text.splitlines(keepends=True)
    left, right = sum(map(len, lines[:start - 1])), sum(map(len, lines[:end]))
    fragment = text[left:right]
    if fragment.count(mutation['before']) != 1:
        raise ValueError('SELECTOR_CONTEXT_CHANGED')
    return text[:left] + fragment.replace(mutation['before'], mutation['after'], 1) + text[right:]


def measure(root, mutation, directory, baseline):
    xml = directory / ('baseline.xml' if baseline else 'mutant.xml')
    started = time.perf_counter()
    try:
        result = run([sys.executable, '-m', 'pytest', '-q', '-p', 'no:cacheprovider',
                      mutation['test'], '--junitxml', str(xml)], root, timeout=60)
        outcome, reason = classify(result, xml, mutation['expected_cases'], mutation['failure_marker'], baseline)
        (directory / ('baseline.log' if baseline else 'mutant.log')).write_bytes(result.stdout + result.stderr)
    except subprocess.TimeoutExpired:
        outcome, reason = 'TIMEOUT', 'TEST_BUDGET_EXHAUSTED'
    return dict(outcome=outcome, reason=reason, seconds=round(time.perf_counter() - started, 3))


def execute(root, output):
    status = run(['git', 'status', '--porcelain'], root).stdout
    if status.strip():
        raise ValueError('CLEAN_CHECKPOINT_REQUIRED')
    sha = run(['git', 'rev-parse', 'HEAD'], root).stdout.decode().strip()
    archive = run(['git', 'archive', '--format=zip', 'HEAD'], root, timeout=30)
    if archive.returncode:
        raise ValueError('TRACKED_SOURCE_EXPORT_FAILED')
    archive_hash = hashlib.sha256(archive.stdout).hexdigest()
    registry = tomllib.loads((root / 'architecture/contracts/mutations.toml').read_text())
    rows = []
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='architecture-mutation-') as temporary:
        copy = Path(temporary) / 'source'
        copy.mkdir()
        with zipfile.ZipFile(io.BytesIO(archive.stdout)) as zipped:
            for item in zipped.infolist():
                if not (copy / item.filename).resolve().is_relative_to(copy):
                    raise ValueError('ARCHIVE_PATH_ESCAPE')
            zipped.extractall(copy)
        for mutation in registry['mutation']:
            directory = output / mutation['id']
            directory.mkdir(exist_ok=True)
            target = (copy / mutation['path']).resolve()
            if not target.is_relative_to(copy):
                raise ValueError('MUTATION_PATH_ESCAPE')
            original = target.read_text(encoding='utf-8-sig')
            row = dict(id=mutation['id'], source_sha=sha, test=mutation['test'])
            row['baseline'] = measure(copy, mutation, directory, True)
            if row['baseline']['outcome'] != 'BASELINE_PASSED':
                row.update(outcome='ERROR', reason='BASELINE_NOT_PASSED')
                rows.append(row)
                continue
            try:
                changed = extent(copy, mutation, original)
                if mutation['path'].endswith('.py'):
                    ast.parse(changed)
                target.write_text(changed, encoding='utf-8', newline='\n')
                if mutation['path'].endswith('.ps1'):
                    compile_index(copy)  # Real parser rejects malformed mutants.
                row.update(measure(copy, mutation, directory, False))
                row['before_sha256'] = hashlib.sha256(original.encode()).hexdigest()
                row['after_sha256'] = hashlib.sha256(changed.encode()).hexdigest()
            except (ValueError, SyntaxError, RuntimeError) as error:
                row.update(outcome='INVALID', reason=str(error)[:500])
            finally:
                target.write_text(original, encoding='utf-8', newline='\n')
            rows.append(row)
    if run(['git', 'status', '--porcelain'], root).stdout != status:
        raise ValueError('SOURCE_WORKTREE_CHANGED')
    report = dict(schema='architecture-mutation-report-v1', source_sha=sha,
                  archive_sha256=archive_hash, scope='THREE_HISTORICAL_SURVIVORS_ONLY',
                  runtime_evidence='NOT_A_PRODUCTION_TRACE', mutations=rows)
    (output / 'mutation-report.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report, indent=2))
    return 0 if all(row['outcome'] == 'KILLED' for row in rows) else 1


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    raise SystemExit(execute(Path(__file__).resolve().parents[1], args.output.resolve()))
