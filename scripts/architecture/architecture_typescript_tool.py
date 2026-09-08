"""Acquire only the parser already locked by Web; never substitute another tool.

The temporary npm project is a projection of the repository lock, not another
dependency authority. npm ci verifies its SRI and cannot run package scripts.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time

TOOL_ROOT = Path(__file__).resolve().parents[2]
NO_WINDOW = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0


def read_json(path):
    try:
        with path.open('rb') as stream:
            content = stream.read(2 * 1024 * 1024 + 1)
        if len(content) > 2 * 1024 * 1024:
            raise ValueError('oversized metadata')
        return json.loads(content.decode('utf-8-sig'))
    except (OSError, ValueError, UnicodeError) as error:
        raise RuntimeError('ARCHITECTURE_TOOL_INTEGRITY_FAILED:metadata') from error


def tool_identity(root):
    package = read_json(root / 'web/package.json')
    lock = read_json(root / 'web/package-lock.json')
    try:
        version = package['devDependencies']['typescript']
        record = lock['packages']['node_modules/typescript']
        identity = {key: record[key] for key in ('version', 'resolved', 'integrity')}
        if (lock.get('lockfileVersion') != 3 or not re.fullmatch(r'\d+\.\d+\.\d+', version)
                or identity['version'] != version
                or lock['packages']['']['devDependencies']['typescript'] != version):
            raise ValueError('version')
        if identity['resolved'] != f'https://registry.npmjs.org/typescript/-/typescript-{version}.tgz':
            raise ValueError('source')
        if not identity['integrity'].startswith('sha512-'):
            raise ValueError('algorithm')
        if len(base64.b64decode(identity['integrity'][7:], validate=True)) != 64:
            raise ValueError('digest')
    except (KeyError, TypeError, ValueError) as error:
        raise RuntimeError('ARCHITECTURE_TOOL_INTEGRITY_FAILED:source-lock') from error
    return identity


def identity_key(identity):
    return hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()


def validate_package(package, identity, owner_root):
    """Enforce independently selected installation authority before metadata/JS."""
    package = package.resolve()
    if (not package.is_relative_to(owner_root.resolve())
            or package.name != 'typescript' or package.parent.name != 'node_modules'):
        raise RuntimeError('ARCHITECTURE_TOOL_INTEGRITY_FAILED:package-owner')
    lock = read_json(package.parent.parent / 'package-lock.json')
    metadata = read_json(package / 'package.json')
    try:
        record = lock['packages']['node_modules/typescript']
        actual = {key: record[key] for key in identity}
    except (KeyError, TypeError) as error:
        raise RuntimeError('ARCHITECTURE_TOOL_INTEGRITY_FAILED:installed-lock') from error
    entry = (package / 'lib/typescript.js').resolve()
    if (actual != identity or metadata.get('name') != 'typescript'
            or metadata.get('version') != identity['version']
            or not entry.is_relative_to(package) or not entry.is_file()):
        raise RuntimeError('ARCHITECTURE_TOOL_INTEGRITY_FAILED:installed-package')
    return package


def resolve_package(root):
    identity = tool_identity(root)
    local = TOOL_ROOT / 'web/node_modules/typescript'
    cached = TOOL_ROOT / '.local/tools/architecture-typescript' / identity_key(identity) / 'node_modules/typescript'
    for path in (local, cached):
        if path.exists():
            return validate_package(path, identity, TOOL_ROOT), identity
    raise RuntimeError('ARCHITECTURE_TOOL_UNAVAILABLE:run architecture_typescript_tool.py first')


def install(root, cache):
    identity = tool_identity(root)
    project = cache.resolve() / identity_key(identity)
    package = project / 'node_modules/typescript'
    started = time.monotonic()
    if package.exists():
        return validate_package(package, identity, cache), dict(state='AVAILABLE', cache='HOT', seconds=0, **identity)
    node, npm = shutil.which('node'), shutil.which('npm')
    if not node or not npm:
        raise RuntimeError('ARCHITECTURE_TOOL_UNAVAILABLE:node/npm')
    npm_path = Path(npm).resolve()
    npm_cli = npm_path.parent / 'node_modules/npm/bin/npm-cli.js' if os.name == 'nt' else npm_path
    if not npm_cli.is_file():
        raise RuntimeError('ARCHITECTURE_TOOL_UNAVAILABLE:npm-cli')
    project.mkdir(parents=True, exist_ok=True)
    projected = {'name': 'architecture-typescript-parser', 'version': '1.0.0',
                 'private': True, 'devDependencies': {'typescript': identity['version']}}
    lock = {'name': projected['name'], 'version': '1.0.0', 'lockfileVersion': 3,
            'requires': True, 'packages': {'': projected, 'node_modules/typescript': dict(identity, dev=True)}}
    for name, value in [('package.json', projected), ('package-lock.json', lock)]:
        (project / name).write_text(json.dumps(value, indent=2) + '\n', encoding='utf-8')
    # Exact HTTPS package, no dependencies and no lifecycle scripts: npm performs
    # the acquisition itself, without a package-owned shell or native build tree.
    command = [node, str(npm_cli), 'ci', '--ignore-scripts', '--no-audit', '--no-fund',
               '--loglevel=error', '--fetch-retries=0', '--fetch-timeout=30000']
    try:
        result = subprocess.run(command, cwd=project, capture_output=True,
                                timeout=60, creationflags=NO_WINDOW)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise RuntimeError('ARCHITECTURE_TOOL_UNAVAILABLE:bounded-npm-ci') from error
    if result.returncode:
        reason = 'INTEGRITY_FAILED' if b'EINTEGRITY' in result.stderr else 'UNAVAILABLE'
        raise RuntimeError(f'ARCHITECTURE_TOOL_{reason}:npm-ci')
    validated = validate_package(package, identity, cache)
    return validated, dict(state='AVAILABLE', cache='COLD', seconds=round(time.monotonic() - started, 3), **identity)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cache', type=Path, default=TOOL_ROOT / '.local/tools/architecture-typescript')
    args = parser.parse_args()
    package, report = install(TOOL_ROOT, args.cache)
    print(json.dumps(dict(report, package=str(package)), sort_keys=True))


if __name__ == '__main__':
    try:
        main()
    except RuntimeError as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1)
