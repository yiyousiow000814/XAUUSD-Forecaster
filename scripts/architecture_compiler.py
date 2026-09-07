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
import stat
import subprocess

SELECTION = 'architecture/critical-paths.json'
TOOL_INPUTS = ('scripts/architecture_compiler.py', 'scripts/compile_architecture.py',
               'scripts/extract_architecture_powershell.ps1',
               'scripts/extract_architecture_typescript.mjs',
               'scripts/architecture_typescript_tool.py')
VERSION = 'critical-source-index-v1'
MAXIMUM_INDEX_BYTES = 2 * 1024 * 1024
MAXIMUM_TRANSPORT_BYTES = 3 * 1024 * 1024
MAXIMUM_PARTS = 32
MAXIMUM_RECORDS = 10_240
TRANSPORT_VERSION = 'critical-source-index-parts-v1'
PART_VERSION = 'critical-source-facts-v1'
FAMILIES = ('symbols', 'edges', 'tests')


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + '\n'


def canonical(value, maximum_bytes=MAXIMUM_TRANSPORT_BYTES):
    """The wire/hash domain is Unicode scalar JSON with safe integer numbers.

    Code-point key order (not JavaScript UTF-16 order), original array order and
    multiplicity are part of the logical identity. Floating point is not a
    current source fact and cannot silently gain a cross-runtime hash meaning.
    """
    pieces, size = [], 1  # Reserve the final LF before encoding any value.
    reason = ('ARCHITECTURE_INDEX_BUDGET_EXCEEDED' if maximum_bytes == MAXIMUM_INDEX_BYTES
              else 'ARCHITECTURE_TRANSPORT_TOTAL_BUDGET_EXCEEDED')
    def write(fragment):
        nonlocal size
        size += len(fragment.encode('utf-8', errors='strict'))
        if size > maximum_bytes:
            raise ValueError(reason)
        pieces.append(fragment)

    def emit(item, depth=0):
        if depth > 32:
            raise ValueError('ARCHITECTURE_TRANSPORT_VALUE_INVALID')
        if item is None or isinstance(item, bool):
            write('null' if item is None else 'true' if item else 'false')
        elif isinstance(item, str):
            # The logical input already exists. Do not allocate one enormous
            # escaped JSON string before discovering that its wire cannot fit.
            if len(item) + 2 > maximum_bytes - size:
                raise ValueError(reason)
            if len(item) <= 1024:
                write(json.dumps(item, ensure_ascii=False))
            else:
                write('"')
                for offset in range(0, len(item), 1024):
                    write(json.dumps(item[offset:offset + 1024], ensure_ascii=False)[1:-1])
                write('"')
        elif isinstance(item, int) and abs(item) <= 9_007_199_254_740_991:
            write(str(item))
        elif isinstance(item, list):
            if 2 * len(item) + 1 > maximum_bytes - size:
                raise ValueError(reason)
            write('[')
            for ordinal, child in enumerate(item):
                if ordinal: write(',')
                emit(child, depth + 1)
            write(']')
        elif isinstance(item, dict):
            # At least five bytes per key/value prevents sorting an unbounded
            # key set which could never satisfy this wire's byte admission.
            if 5 * len(item) + 1 > maximum_bytes - size:
                raise ValueError(reason)
            if not all(isinstance(key, str) for key in item):
                raise ValueError('ARCHITECTURE_TRANSPORT_VALUE_INVALID')
            write('{')
            for ordinal, key in enumerate(sorted(item)):
                if ordinal: write(',')
                emit(key, depth + 1)
                write(':')
                emit(item[key], depth + 1)
            write('}')
        else:
            raise ValueError('ARCHITECTURE_TRANSPORT_VALUE_INVALID')
    emit(value)
    return ''.join(pieces) + '\n'


def _part_name(ordinal):
    return f'critical-facts-{ordinal:05d}.json'


def _record_source(family, record):
    return record['source'].split('::', 1)[0] if family == 'edges' else record['path']


def _counts(value):
    if (not isinstance(value, dict) or set(value) != set(FAMILIES)
            or any(type(count) is not int or not 0 <= count <= MAXIMUM_RECORDS
                   for count in value.values()) or sum(value.values()) > MAXIMUM_RECORDS):
        raise ValueError('ARCHITECTURE_TRANSPORT_RECORD_BUDGET_EXCEEDED')
    return value


def render_transport(index):
    """Bounded source-owned parts; no extraction, filtering or sorting of facts."""
    observed = index['observed']
    counts = _counts({family: len(observed[family]) for family in FAMILIES})
    if set(observed) != set(FAMILIES):
        raise ValueError('ARCHITECTURE_TRANSPORT_FAMILY_INVALID')
    groups = {}
    record_bytes = 0
    # Serialize each complete record once for linear packing, retaining its
    # global ordinal instead of relying on a later sort to recover stable ties.
    for family in FAMILIES:
        for ordinal, record in enumerate(observed[family]):
            source = _record_source(family, record)
            if source not in index['inputs']:
                raise ValueError('ARCHITECTURE_TRANSPORT_SOURCE_INVALID')
            pair = [ordinal, record]
            size = len(canonical(pair, MAXIMUM_INDEX_BYTES).encode('utf-8')) - 1
            if size >= MAXIMUM_INDEX_BYTES:
                raise ValueError('ARCHITECTURE_INDEX_BUDGET_EXCEEDED')
            record_bytes += size
            if record_bytes > MAXIMUM_TRANSPORT_BYTES:
                raise ValueError('ARCHITECTURE_TRANSPORT_TOTAL_BUDGET_EXCEEDED')
            groups.setdefault(source, []).append((family, pair, size))
    outputs, descriptors = {}, []
    total = 0

    def emit(source, part):
        nonlocal total
        if len(descriptors) >= MAXIMUM_PARTS:
            raise ValueError('ARCHITECTURE_TRANSPORT_PART_BUDGET_EXCEEDED')
        name = _part_name(len(descriptors))
        content = canonical(part, MAXIMUM_INDEX_BYTES)
        raw = content.encode('utf-8')
        if len(raw) > MAXIMUM_INDEX_BYTES:
            raise ValueError('ARCHITECTURE_INDEX_BUDGET_EXCEEDED')
        total += len(raw)
        if total > MAXIMUM_TRANSPORT_BYTES:
            raise ValueError('ARCHITECTURE_TRANSPORT_TOTAL_BUDGET_EXCEEDED')
        outputs[name] = content
        descriptors.append(dict(file=name, source_path=source, bytes=len(raw),
            sha256=hashlib.sha256(raw).hexdigest(),
            counts={family: len(part['observed'][family]) for family in FAMILIES}))

    for source in sorted(groups):
        def empty_part():
            return dict(schema=PART_VERSION, source_input_digest=index['source_input_digest'],
                        source_path=source, observed={family: [] for family in FAMILIES})
        part = empty_part()
        size = len(canonical(part).encode('utf-8'))
        for family, pair, pair_size in groups[source]:
            extra = pair_size + bool(part['observed'][family])
            if size + extra > MAXIMUM_INDEX_BYTES:
                if not any(part['observed'].values()):
                    raise ValueError('ARCHITECTURE_INDEX_BUDGET_EXCEEDED')
                emit(source, part)
                part = empty_part()
                size = len(canonical(part).encode('utf-8'))
                extra = pair_size
            if size + extra > MAXIMUM_INDEX_BYTES:
                raise ValueError('ARCHITECTURE_INDEX_BUDGET_EXCEEDED')
            part['observed'][family].append(pair)
            size += extra
        emit(source, part)
    manifest = dict(schema=TRANSPORT_VERSION,
        index={key: value for key, value in index.items() if key != 'observed'},
        counts=counts, logical_sha256=hashlib.sha256(canonical(index).encode('utf-8')).hexdigest(),
        parts=descriptors)
    content = canonical(manifest, MAXIMUM_INDEX_BYTES)
    size = len(content.encode('utf-8'))
    if size > MAXIMUM_INDEX_BYTES:
        raise ValueError('ARCHITECTURE_INDEX_BUDGET_EXCEEDED')
    if total + size > MAXIMUM_TRANSPORT_BYTES:
        raise ValueError('ARCHITECTURE_TRANSPORT_TOTAL_BUDGET_EXCEEDED')
    outputs['critical-index.json'] = content
    return outputs


def _read_generated(directory, name, maximum_bytes=MAXIMUM_INDEX_BYTES):
    """Fixed generated names only; never accept a manifest-provided locator."""
    if name != 'critical-index.json' and not re.fullmatch(r'critical-facts-[0-9]{5}\.json', name):
        raise ValueError('ARCHITECTURE_OUTPUT_PATH_ESCAPE')
    if directory.resolve() != directory.absolute():
        raise ValueError('ARCHITECTURE_OUTPUT_PATH_ESCAPE')
    path = directory / name
    if path.is_symlink() or path.resolve().parent != directory.resolve():
        raise ValueError('ARCHITECTURE_OUTPUT_PATH_ESCAPE')
    before = path.stat(follow_symlinks=False)
    if not stat.S_ISREG(before.st_mode):
        raise ValueError('ARCHITECTURE_OUTPUT_PATH_ESCAPE')
    with path.open('rb') as stream:
        opened = os.fstat(stream.fileno())
        if not stat.S_ISREG(opened.st_mode) or (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino):
            raise ValueError('ARCHITECTURE_OUTPUT_PATH_ESCAPE')
        raw = stream.read(maximum_bytes + 1)
    if len(raw) > maximum_bytes:
        raise ValueError('ARCHITECTURE_INDEX_BUDGET_EXCEEDED')
    return raw, json.loads(raw.decode('utf-8', errors='strict'))


def _admit_manifest(raw, manifest):
    """Shared bounded admission, without opening or trusting any fact part."""
    if canonical(manifest).encode('utf-8') != raw:
        raise ValueError('ARCHITECTURE_TRANSPORT_ENCODING_INVALID')
    if (set(manifest) != {'schema', 'index', 'counts', 'logical_sha256', 'parts'}
            or manifest['schema'] != TRANSPORT_VERSION
            or manifest['index'].get('schema') != VERSION
            or 'observed' in manifest['index']):
        raise ValueError('ARCHITECTURE_TRANSPORT_MANIFEST_INVALID')
    counts = _counts(manifest['counts'])
    parts = manifest['parts']
    if not isinstance(parts, list) or len(parts) > MAXIMUM_PARTS:
        raise ValueError('ARCHITECTURE_TRANSPORT_PART_BUDGET_EXCEEDED')
    total = len(raw)
    totals = dict.fromkeys(FAMILIES, 0)
    for ordinal, part in enumerate(parts):
        if (set(part) != {'file', 'source_path', 'bytes', 'sha256', 'counts'}
                or part['file'] != _part_name(ordinal)
                or part['source_path'] not in manifest['index']['inputs']
                or type(part['bytes']) is not int or not 0 < part['bytes'] <= MAXIMUM_INDEX_BYTES
                or not re.fullmatch('[0-9a-f]{64}', part['sha256'])):
            raise ValueError('ARCHITECTURE_TRANSPORT_DESCRIPTOR_INVALID')
        for family, count in _counts(part['counts']).items():
            totals[family] += count
        total += part['bytes']
    if total > MAXIMUM_TRANSPORT_BYTES:
        raise ValueError('ARCHITECTURE_TRANSPORT_TOTAL_BUDGET_EXCEEDED')
    if totals != counts:
        raise ValueError('ARCHITECTURE_TRANSPORT_COUNTS_INVALID')
    return counts, parts


def read_generated_index(directory):
    """Independent persisted-artifact validation for CLI/test consumers."""
    raw, manifest = _read_generated(directory, 'critical-index.json')
    counts, parts = _admit_manifest(raw, manifest)
    # All aggregate admission checks precede part reads and restored-array allocation.
    observed = {family: [None] * count for family, count in counts.items()}
    for descriptor in parts:
        part_raw, part = _read_generated(directory, descriptor['file'], descriptor['bytes'])
        if len(part_raw) != descriptor['bytes'] or hashlib.sha256(part_raw).hexdigest() != descriptor['sha256']:
            raise ValueError('ARCHITECTURE_TRANSPORT_PART_IDENTITY_INVALID')
        if canonical(part).encode('utf-8') != part_raw:
            raise ValueError('ARCHITECTURE_TRANSPORT_ENCODING_INVALID')
        if (set(part) != {'schema', 'source_input_digest', 'source_path', 'observed'}
                or part['schema'] != PART_VERSION
                or part['source_input_digest'] != manifest['index']['source_input_digest']
                or part['source_path'] != descriptor['source_path']
                or set(part['observed']) != set(FAMILIES)):
            raise ValueError('ARCHITECTURE_TRANSPORT_PART_IDENTITY_INVALID')
        for family in FAMILIES:
            pairs = part['observed'][family]
            if not isinstance(pairs, list) or len(pairs) != descriptor['counts'][family]:
                raise ValueError('ARCHITECTURE_TRANSPORT_COUNTS_INVALID')
            for pair in pairs:
                if (not isinstance(pair, list) or len(pair) != 2 or type(pair[0]) is not int
                        or not 0 <= pair[0] < counts[family]
                        or observed[family][pair[0]] is not None
                        or _record_source(family, pair[1]) != part['source_path']):
                    raise ValueError('ARCHITECTURE_TRANSPORT_ORDINAL_INVALID')
                observed[family][pair[0]] = pair[1]
    if any(record is None for rows in observed.values() for record in rows):
        raise ValueError('ARCHITECTURE_TRANSPORT_ORDINAL_INVALID')
    expected = {part['file'] for part in parts} | {'critical-index.json'}
    for name in manifest['index']['allowed']['views']:
        if not re.fullmatch(r'[a-z][a-z0-9-]{0,63}', name):
            raise ValueError('ARCHITECTURE_VIEW_NAME_INVALID')
        expected.add(name + '.mmd')
    for path in directory.iterdir():
        if path.name not in expected:
            raise ValueError('ARCHITECTURE_GENERATED_DRIFT')
    index = dict(manifest['index'], observed=observed)
    if hashlib.sha256(canonical(index).encode('utf-8')).hexdigest() != manifest['logical_sha256']:
        raise ValueError('ARCHITECTURE_TRANSPORT_LOGICAL_IDENTITY_INVALID')
    return index


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
    outputs = render_transport(index)
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


def write_outputs(directory, outputs):
    """Publish a checked flat set; only retire parts proven by the prior manifest.

    Build is offline, not an atomic multi-file release. The manifest is written
    last; interrupted/mixed sets fail both consumers. Unknown files are never
    deleted or silently overwritten as cleanup, including abandoned temp files.
    """
    if directory.resolve() != directory.absolute():
        raise ValueError('ARCHITECTURE_OUTPUT_PATH_ESCAPE')
    # Fresh producer names authorize correction of known files after an
    # interrupted build; their existing bytes are not acceptance evidence.
    known = set(outputs)
    old_parts = {}
    if directory.exists() and (directory / 'critical-index.json').exists():
        try:
            raw, previous = _read_generated(directory, 'critical-index.json')
        except (json.JSONDecodeError, UnicodeDecodeError):
            previous = {}
        previous_index = {}
        if isinstance(previous, dict) and previous.get('schema') == TRANSPORT_VERSION:
            try:
                _, parts = _admit_manifest(raw, previous)
            except (ValueError, TypeError, KeyError, AttributeError):
                # A malformed old manifest cannot authorize removal. The
                # exact fresh outputs may still repair their own fixed names.
                parts = []
            else:
                previous_index = previous['index']
            old_parts = {part['file']: part for part in parts}
        elif isinstance(previous, dict) and previous.get('schema') == VERSION:
            # One-way build migration only. Runtime/build readers do not accept
            # the retired single-file transport as a successful fallback.
            previous_index = previous
        views = previous_index.get('allowed', {}).get('views', {})
        if any(not re.fullmatch(r'[a-z][a-z0-9-]{0,63}', name) for name in views):
            raise ValueError('ARCHITECTURE_VIEW_NAME_INVALID')
        known.update({'critical-index.json', *old_parts, *(name + '.mmd' for name in views)})
    if directory.exists():
        for path in directory.iterdir():
            if path.name not in known or path.is_symlink() or not path.is_file():
                raise ValueError('ARCHITECTURE_GENERATED_UNOWNED_OUTPUT:' + path.name)
    retired = set(old_parts) - set(outputs)
    for name in sorted(retired):
        if not (directory / name).exists():
            continue  # An interrupted prior attempt already retired this part.
        descriptor = old_parts[name]
        raw, _ = _read_generated(directory, name, descriptor['bytes'])
        if len(raw) != descriptor['bytes'] or hashlib.sha256(raw).hexdigest() != descriptor['sha256']:
            raise ValueError('ARCHITECTURE_TRANSPORT_PART_IDENTITY_INVALID')
    directory.mkdir(parents=True, exist_ok=True)
    # Every output name is derived by this producer, not accepted from a source path.
    for name in sorted(set(outputs) - {'critical-index.json'}):
        (directory / name).write_text(outputs[name], encoding='utf-8', newline='\n')
    for name in sorted(retired):
        # A fixed, individually verified old part; never recursive/glob cleanup.
        (directory / name).unlink(missing_ok=True)
    # Retire before publishing so interruption preserves deletion authority in
    # the old manifest; restart can finish rather than orphaning extra files.
    (directory / 'critical-index.json').write_text(outputs['critical-index.json'], encoding='utf-8', newline='\n')
