#!/usr/bin/env python
"""Check declared Python import directions, without importing application code.

Adapted from original #287's policy owner, using the current compiler's AST.
This checks source syntax, not runtime loading or transitive side effects.
"""
from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path, PurePosixPath

from architecture_compiler import SELECTION, parse_python, source_path


def _relative(value, prefix, *, suffix=None):
    if (not isinstance(value, str) or '\\' in value or not value.startswith(prefix + '/')
            or any(part in {'', '.', '..'} for part in value.split('/'))
            or (suffix and not value.endswith(suffix))):
        raise ValueError('ARCHITECTURE_IMPORT_POLICY_PATH_INVALID')
    return value


def _policy(root):
    value = json.loads(source_path(root, SELECTION).read_text(encoding='utf-8'))
    policy = value.get('python_import_policy')
    if (not isinstance(policy, dict) or policy.get('schema') != 'python-import-policy-v1'
            or set(policy) != {'schema', 'canonical_packages', 'script_imports', 'legacy_shims'}):
        raise ValueError('ARCHITECTURE_IMPORT_POLICY_INVALID')
    packages = policy['canonical_packages']
    if (not isinstance(packages, dict)
            or set(packages) != {'ai', 'assistant', 'dashboard', 'decision', 'evidence', 'news', 'runtime', 'training'}):
        raise ValueError('ARCHITECTURE_IMPORT_POLICY_PACKAGES_INVALID')
    for area, path in packages.items():
        if path != 'xauusd_forecaster/' + area:
            raise ValueError('ARCHITECTURE_IMPORT_POLICY_PACKAGES_INVALID')
    exceptions, shims = {}, {}
    for field, output in [('script_imports', exceptions), ('legacy_shims', shims)]:
        rows = policy[field]
        if not isinstance(rows, list):
            raise ValueError('ARCHITECTURE_IMPORT_POLICY_INVALID')
        for row in rows:
            keys = {'source', 'target', 'reason', 'remove_when', 'binding'} if field == 'script_imports' else {'path', 'owner', 'remove_when'}
            if (not isinstance(row, dict) or set(row) != keys
                    or any(not isinstance(row[key], str) or not row[key].strip() for key in keys)):
                raise ValueError('ARCHITECTURE_IMPORT_POLICY_DECLARATION_INVALID')
            if field == 'script_imports':
                pair = tuple(_relative(row[key], 'scripts', suffix='.py') for key in ('source', 'target'))
                if row['binding'] not in {'MODULE_REQUEST_ONLY', 'PARAMETERIZED_PRODUCER_ROOT'}:
                    raise ValueError('ARCHITECTURE_IMPORT_POLICY_DECLARATION_INVALID')
                key = pair
                paths = pair
            else:
                paths = tuple(_relative(row[key], 'xauusd_forecaster', suffix='.py') for key in ('path', 'owner'))
                key = paths[0]
            if key in output or paths[0] == paths[1]:
                raise ValueError('ARCHITECTURE_IMPORT_POLICY_DECLARATION_INVALID')
            for path in paths:
                source_path(root, path)
            output[key] = row
    if any(row['owner'] in shims for row in shims.values()):
        raise ValueError('ARCHITECTURE_IMPORT_POLICY_SHIM_OWNER_INVALID')
    return packages, exceptions, shims


def _module(path):
    parts = list(PurePosixPath(path).with_suffix('').parts)
    if parts[-1] == '__init__':
        parts.pop()
    return '.'.join(parts)


def _within(module, namespace):
    return module == namespace or module.startswith(namespace + '.')


def _declarations_only(tree):
    for node in tree.body:
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            continue
        if isinstance(node, ast.Import):
            continue
        if isinstance(node, ast.ImportFrom) and all(alias.name != '*' for alias in node.names):
            continue
        if (isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name) and node.targets[0].id == '__all__'):
            try:
                names = ast.literal_eval(node.value)
            except (ValueError, TypeError, SyntaxError):
                names = None
            if isinstance(names, (list, tuple)) and all(isinstance(name, str) for name in names):
                continue
        return False
    return True


def _requests(tree, relative):
    """Module spellings are syntax facts; physical module resolution is UNKNOWN."""
    package = _module(relative) if relative.endswith('/__init__.py') else _module(relative).rpartition('.')[0]
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield node.lineno, alias.name, 'IMPORT', None
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ''
            if node.level:
                parts = package.split('.')
                if node.level > len(parts):
                    raise ValueError(f'ARCHITECTURE_IMPORT_RELATIVE_INVALID:{relative}:{node.lineno}')
                base = '.'.join(parts[:len(parts) - node.level + 1] + ([base] if base else []))
            yield node.lineno, base, 'IMPORT', None
            for alias in node.names:
                if alias.name != '*':
                    yield node.lineno, base + '.' + alias.name, 'FROM_MEMBER', base
        elif isinstance(node, ast.Call):
            # Recognize requests for reporting, not a proof that importlib or
            # __import__ was not rebound. Dynamic calls never acquire file identity.
            spelling = ast.unparse(node.func)
            if spelling == '__import__' or spelling.endswith(('.import_module', '.spec_from_file_location')):
                argument = node.args[0] if node.args else next(
                    (keyword.value for keyword in node.keywords if keyword.arg == 'name'), None)
                literal = argument.value if isinstance(argument, ast.Constant) and isinstance(argument.value, str) else None
                yield node.lineno, literal, 'DYNAMIC_REQUEST', spelling


def check_architecture_imports(root):
    root = root.resolve()
    packages, exceptions, shims = _policy(root)
    if any(not (root / name).is_dir() for name in ('xauusd_forecaster', 'scripts')):
        raise ValueError('ARCHITECTURE_IMPORT_SOURCE_MISSING')
    paths = sorted((root / 'xauusd_forecaster').rglob('*.py')) + sorted((root / 'scripts').glob('*.py'))
    if not paths:
        raise ValueError('ARCHITECTURE_IMPORT_SOURCE_MISSING')
    violations, dynamic = [], []
    seen = set()
    script_modules = {_module(path.relative_to(root).as_posix()): path.relative_to(root).as_posix()
                      for path in paths if path.parent == root / 'scripts'}
    shim_modules = {_module(path): path for path in shims}
    for path in paths:
        relative = path.relative_to(root).as_posix()
        tree = parse_python(source_path(root, relative), relative)
        area = next((area for area, prefix in packages.items() if relative.startswith(prefix + '/')), None)
        def reject(line, reason, target=''):
            key = relative, line, reason, target
            if key not in seen:
                seen.add(key)
                violations.append(dict(path=relative, line=line, reason=reason, target=target))
        if (path.name == '__init__.py' or relative in shims) and not _declarations_only(tree):
            reject(1, 'ARCHITECTURE_IMPORT_DECLARATIONS_ONLY')
        for line, target, kind, spelling in _requests(tree, relative):
            if kind == 'DYNAMIC_REQUEST':
                dynamic.append(dict(path=relative, line=line, requested_module=target,
                                    syntax=spelling, runtime_resolution='UNKNOWN'))
                continue
            if relative.startswith('xauusd_forecaster/') and _within(target, 'scripts'):
                reject(line, 'ARCHITECTURE_PACKAGE_IMPORTS_SCRIPT', target)
            requested = target
            if relative.startswith('scripts/') and 'scripts.' + target in script_modules:
                requested = 'scripts.' + target
            if (relative.startswith('scripts/') and requested.startswith('scripts.')
                    and (kind != 'FROM_MEMBER' or spelling == 'scripts' or requested in script_modules)):
                # A qualified request is still a prohibited undeclared script
                # dependency if the requested module is missing from this checkout.
                target_path = script_modules.get(requested, requested.replace('.', '/') + '.py')
                if (relative, target_path) not in exceptions:
                    reject(line, 'ARCHITECTURE_UNDECLARED_SCRIPT_IMPORT', target_path)
            if area:
                target_area = next((label for label, prefix in packages.items()
                                    if _within(target, prefix.replace('/', '.'))), None)
                if area != 'dashboard' and target_area == 'dashboard':
                    reject(line, 'ARCHITECTURE_DOMAIN_IMPORTS_DASHBOARD', target)
                if area == 'decision' and (target_area == 'assistant' or _within(target, 'web')):
                    reject(line, 'ARCHITECTURE_DECISION_OPTIONAL_DEPENDENCY', target)
                if target in shim_modules:
                    reject(line, 'ARCHITECTURE_CANONICAL_IMPORTS_SHIM', target)
    return dict(scope='PACKAGE_AND_SCRIPT_PYTHON_SYNTAX', files=len(paths),
                violations=violations, dynamic_requests=dynamic, runtime_resolution='UNKNOWN')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--json', action='store_true')
    args = parser.parse_args()
    try:
        result = check_architecture_imports(Path(__file__).resolve().parents[1])
    except (ValueError, OSError, SyntaxError) as error:
        parser.exit(1, f'{type(error).__name__}: {error}\n')
    if args.json:
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    else:
        for error in result['violations']:
            print(f"{error['path']}:{error['line']}: {error['reason']}: {error['target']}")
        print(f"Python import policy: {len(result['violations'])} static violations in {result['files']} files; "
              f"{len(result['dynamic_requests'])} dynamic requests remain UNKNOWN.")
    return bool(result['violations'])


if __name__ == '__main__':
    raise SystemExit(main())
