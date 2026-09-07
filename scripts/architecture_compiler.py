"""Current-source facts; AST/span approach adapted from PR #321.

This is a syntactic index, not an ownership or runtime correctness proof.
"""
from __future__ import annotations

import ast
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess

SELECTION = 'architecture/critical-paths.json'
TOOL_INPUTS = ('scripts/architecture_compiler.py', 'scripts/compile_architecture.py',
               'scripts/extract_architecture_powershell.ps1',
               'scripts/extract_architecture_typescript.mjs',
               'scripts/architecture_typescript_tool.py')
VERSION = 'critical-source-index-v1'
MAXIMUM_INDEX_BYTES = 2 * 1024 * 1024


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + '\n'


def source_path(root, relative):
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()) or not path.is_file():
        raise ValueError(f'ARCHITECTURE_INPUT_INVALID:{relative}')
    return path


def extract_python(path, relative):
    """Preserve nested/class qualification; do not guess object dispatch."""
    tree = ast.parse(path.read_text(encoding='utf-8-sig'), filename=relative)
    symbols, edges = [], []

    class Visitor(ast.NodeVisitor):
        def __init__(self):
            self.scope = []

        def definition(self, node):
            self.scope.append(node.name)
            symbols.append(dict(id=f'{relative}::{".".join(self.scope)}',
                                name='.'.join(self.scope), path=relative,
                                line=node.lineno, end_line=node.end_lineno,
                                language='python', syntactic_owner=relative))
            self.generic_visit(node)
            self.scope.pop()

        visit_FunctionDef = definition
        visit_AsyncFunctionDef = definition
        visit_ClassDef = definition

        def visit_Import(self, node):
            for alias in node.names:
                edges.append(dict(source=f'{relative}::<module>', target=alias.name,
                    kind='requires', line=node.lineno, resolution='UNKNOWN',
                    extractor='python-ast', binding='Import syntax; module loading not executed'))

        def visit_ImportFrom(self, node):
            edges.append(dict(source=f'{relative}::<module>',
                target='.' * node.level + (node.module or ''), kind='requires',
                line=node.lineno, resolution='UNKNOWN', extractor='python-ast',
                binding='Import syntax; module loading not executed'))

        def visit_Call(self, node):
            if self.scope:
                target = ast.unparse(node.func)
                edge = dict(source=f'{relative}::{".".join(self.scope)}',
                            target=target, kind='calls', line=node.lineno,
                            resolution='UNKNOWN', extractor='python-ast',
                            binding='Runtime binding not inferred from call syntax')
                edges.append(edge)
                method = node.func.attr if isinstance(node.func, ast.Attribute) else ''
                if method in {'commit', 'rollback', 'replace', 'write_text', 'write_bytes', 'read_text', 'read_bytes', 'start', 'terminate', 'kill'}:
                    edges.append(dict(edge, kind={'commit': 'commits', 'rollback': 'rolls_back',
                        'replace': 'publishes', 'write_text': 'writes', 'write_bytes': 'writes',
                        'read_text': 'reads', 'read_bytes': 'reads', 'start': 'spawns',
                        'terminate': 'terminates', 'kill': 'terminates'}[method]))
                if method in {'execute', 'executemany', 'executescript'}:
                    sql = node.args[0] if node.args else None
                    literal = isinstance(sql, ast.Constant) and isinstance(sql.value, str)
                    edges.append(dict(edge, kind='sql', statement=sql.value if literal else '<dynamic SQL>',
                                      resolution='LITERAL' if literal else 'UNKNOWN'))
            self.generic_visit(node)

    Visitor().visit(tree)
    return symbols, edges


def compile_index(root: Path):
    root = root.resolve()
    selection = json.loads(source_path(root, SELECTION).read_text(encoding='utf-8'))
    if selection.get('schema') != 'critical-path-selection-v1' or not selection.get('views'):
        raise ValueError('ARCHITECTURE_SELECTION_INVALID')
    for name, view in selection['views'].items():
        if not re.fullmatch(r'[a-z][a-z0-9-]{0,63}', name):
            raise ValueError('ARCHITECTURE_VIEW_NAME_INVALID')
        if not view.get('roots') or not view.get('files'):
            raise ValueError('ARCHITECTURE_SELECTION_INVALID')
        if any(not name.endswith(('.py', '.ps1', '.ts', '.tsx', '.mts', '.cts')) for name in view['files']):
            raise ValueError('ARCHITECTURE_LANGUAGE_UNSUPPORTED')
    files = sorted({file for view in selection['views'].values() for file in view['files']})
    source_symbols = selection.get('source_symbols', {})
    if not isinstance(source_symbols, dict):
        raise ValueError('ARCHITECTURE_SYMBOL_SELECTION_INVALID')
    for path, selected in source_symbols.items():
        if (path not in files or not isinstance(selected, list) or not selected
                or any(not isinstance(symbol, str) or not symbol.startswith(path + '::')
                       for symbol in selected)
                or len(set(selected)) != len(selected)):
            raise ValueError('ARCHITECTURE_SYMBOL_SELECTION_INVALID')
    inputs = sorted(set(files + [SELECTION, *TOOL_INPUTS] +
                        [file for view in selection['views'].values() for file in view['tests']]))
    hashes = {name: hashlib.sha256(source_path(root, name).read_text(encoding='utf-8-sig').replace('\r\n', '\n').encode()).hexdigest() for name in inputs}
    symbols, edges = [], []
    tools = {}
    for name in files:
        if name.endswith('.py'):
            found, calls = extract_python(source_path(root, name), name)
            symbols.extend(found); edges.extend(calls)
    if any(name.endswith('.ps1') for name in files):
        shell = shutil.which('pwsh') or shutil.which('powershell')
        if not shell:
            raise RuntimeError('ARCHITECTURE_POWERSHELL_UNAVAILABLE')
        result = subprocess.run([shell, '-NoProfile', '-NonInteractive', '-File',
            str(root / TOOL_INPUTS[2]), '-Root', str(root), '-Selection', str(root / SELECTION)],
            capture_output=True, encoding='utf-8', errors='strict', timeout=45,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        if result.returncode:
            raise RuntimeError('ARCHITECTURE_PARSE_FAILED:' + result.stderr[:1000])
        parsed = json.loads(result.stdout)
        symbols.extend(parsed['symbols']); edges.extend(parsed['edges'])
    typescript_files = [name for name in files if name.endswith(('.ts', '.tsx', '.mts', '.cts'))]
    if typescript_files:
        # This is an explicit tool dependency, never a dependency-detection skip.
        from architecture_typescript_tool import resolve_package
        package, identity = resolve_package(root)
        node = shutil.which('node')
        if not node:
            raise RuntimeError('ARCHITECTURE_TOOL_UNAVAILABLE:node')
        request = dict(package=str(package), version=identity['version'], files=[
            dict(path=name, content=source_path(root, name).read_text(encoding='utf-8-sig'))
            for name in typescript_files])
        result = subprocess.run([node, str(root / 'scripts/extract_architecture_typescript.mjs')],
            input=json.dumps(request), capture_output=True, encoding='utf-8', errors='strict',
            timeout=30, creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        if result.returncode:
            reason = ('ARCHITECTURE_TOOL_INTEGRITY_FAILED' if 'ARCHITECTURE_TOOL_INTEGRITY_FAILED' in result.stderr
                      else 'ARCHITECTURE_PARSE_FAILED')
            raise RuntimeError(reason + ':' + result.stderr[:1000])
        parsed = json.loads(result.stdout)
        if parsed.get('tool') != dict(name='typescript', version=identity['version']):
            raise RuntimeError('ARCHITECTURE_TOOL_INTEGRITY_FAILED:parser-identity')
        symbols.extend(parsed['symbols']); edges.extend(parsed['edges'])
        tools['typescript'] = identity
        # Only this package's locked identity affects parser behavior. Unrelated
        # Web dependency edits cannot churn the source index's tool identity.
        hashes['tool:typescript:web/package-lock.json'] = hashlib.sha256(encoded(identity).encode()).hexdigest()
    ids = {symbol['id'] for symbol in symbols}
    if len(ids) != len(symbols):
        raise ValueError('ARCHITECTURE_SYMBOL_ID_AMBIGUOUS')
    # Scope only after complete parsing and ID validation. This is a declared
    # source slice, not inferred call closure or permission to omit parse errors.
    for selected in source_symbols.values():
        for symbol in selected:
            if symbol not in ids:
                raise ValueError(f'ARCHITECTURE_SYMBOL_SELECTION_MISSING:{symbol}')
    retained = {symbol['id'] for symbol in symbols
                if symbol['path'] not in source_symbols or any(
                    symbol['id'] == selected or symbol['id'].startswith(selected + '.')
                    for selected in source_symbols[symbol['path']])}
    symbols = [symbol for symbol in symbols if symbol['id'] in retained]
    edges = [edge for edge in edges if edge['source'].split('::')[0] not in source_symbols
             or edge['source'] in retained or edge['source'].endswith('::<module>')]
    ids = retained
    for name, view in selection['views'].items():
        for symbol in view['roots']:
            if symbol not in ids:
                raise ValueError(f'ARCHITECTURE_ROOT_MISSING:{name}:{symbol}')
    # Resolution is a lexical candidate only. Never claim dynamic dispatch exact.
    for edge in edges:
        path = edge['source'].split('::')[0]
        local = f'{path}::{edge["target"]}'
        if local in ids and edge['kind'] == 'calls':
            edge['candidate_symbol'] = local
            edge['binding'] = 'Same-file lexical candidate; rebinding remains UNKNOWN'
    tests = []
    for name in sorted({file for view in selection['views'].values() for file in view['tests']}):
        if name.endswith('.py'):
            found, _ = extract_python(source_path(root, name), name)
            tests.extend(dict(symbol, execution='NOT_OBSERVED') for symbol in found
                         if symbol['name'].split('.')[-1].startswith('test_'))
    return dict(schema=VERSION, source_input_digest=hashlib.sha256(encoded(hashes).encode()).hexdigest(),
        inputs=hashes, observed=dict(symbols=sorted(symbols, key=lambda s:s['id']),
        edges=sorted(edges, key=lambda e:(e['source'], e['line'], e['kind'], e['target'])),
        tests=sorted(tests, key=lambda s:s['id'])),
        allowed=dict(status='NOT_EVALUATED', views=selection['views'], source_symbols=source_symbols),
        runtime=dict(status='UNKNOWN', observations=[]), tools=tools,
        coverage=dict(scope='DECLARED_CRITICAL_SLICES_ONLY', ownership='UNKNOWN',
                      transaction_atomicity='NOT_PROVEN_BY_STATIC_INDEX'))


def render(index):
    # This shared static transport retains every fact. Whitespace is not source
    # coverage; explain and Mermaid remain readable without raising the budget.
    outputs = {'critical-index.json': json.dumps(index, ensure_ascii=False, sort_keys=True, separators=(',', ':')) + '\n'}
    if len(outputs['critical-index.json'].encode('utf-8')) > MAXIMUM_INDEX_BYTES:
        raise ValueError('ARCHITECTURE_INDEX_BUDGET_EXCEEDED')
    for name, view in index['allowed']['views'].items():
        roots = set(view['roots'])
        edges = [e for e in index['observed']['edges'] if e['source'] in roots]
        nodes = sorted(roots | {e.get('candidate_symbol', e['target']) for e in edges})
        labels = {node: 'n' + hashlib.sha256(node.encode()).hexdigest()[:16] for node in nodes}
        lines = ['%% Generated source syntax, not runtime/transaction proof.',
                 '%% input ' + index['source_input_digest'], 'flowchart LR']
        for node in nodes:
            label = node.replace('"', "'").replace('\n', ' ').replace('<', '&lt;').replace('>', '&gt;')
            lines.append(f'  {labels[node]}["{label}"]')
        for edge in edges:
            target = edge.get('candidate_symbol', edge['target'])
            lines.append(f'  {labels[edge["source"]]} -->|"{edge["kind"]} L{edge["line"]} {edge["resolution"]}"| {labels[target]}')
        outputs[name + '.mmd'] = '\n'.join(lines) + '\n'
    return outputs


def check_outputs(directory, outputs):
    stale = [name for name, value in outputs.items()
             if not (directory / name).is_file() or (directory / name).read_text(encoding='utf-8') != value]
    if directory.is_dir():
        stale.extend(path.name for path in directory.iterdir() if path.name not in outputs)
    return sorted(stale)
