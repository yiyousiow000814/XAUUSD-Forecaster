"""Source-bound evidence projection for the existing architecture Explorer.

Reuses #324's declaration/binding/execution separation, but never generates a
runtime trace from a declared event sequence. This imports retained test files;
it neither runs a test nor changes the deterministic source index.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import tomllib
from types import SimpleNamespace
import xml.etree.ElementTree as ET

from run_architecture_mutations import classify

BOUND = 2 * 1024 * 1024


def bounded_file(root, relative):
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()) or not path.is_file():
        raise ValueError('ARCHITECTURE_EVIDENCE_FILE_UNAVAILABLE')
    if path.stat().st_size > BOUND:
        raise ValueError('ARCHITECTURE_EVIDENCE_FILE_OVERSIZE')
    return path, path.read_bytes()


def project_mutations(directory, registry, source_sha):
    """Verify exact tests and raw JUnit; a report's claimed KILLED is insufficient.

    source_sha is the caller's explicitly selected source authority, not a SHA
    inferred from an evidence file. Historical evidence stays visible as STALE.
    Digests here identify retained bytes, not signatures or execution attestation.
    """
    if not re.fullmatch('[0-9a-f]{40}', source_sha):
        raise ValueError('ARCHITECTURE_EVIDENCE_SOURCE_IDENTITY_INVALID')
    _, raw = bounded_file(directory, 'mutation-report.json')
    report = json.loads(raw)
    if report.get('schema') != 'architecture-mutation-report-v1':
        raise ValueError('ARCHITECTURE_EVIDENCE_SCHEMA_INVALID')
    expected = {item['id']: item for item in registry['mutation']}
    rows = report.get('mutations', [])
    if len(expected) != len(registry['mutation']) or len(rows) != len(expected) or {row['id'] for row in rows} != set(expected):
        raise ValueError('ARCHITECTURE_EVIDENCE_UNIVERSE_MISMATCH')
    current = report.get('source_sha') == source_sha
    projected = []
    for row in rows:
        spec = expected[row['id']]
        if row.get('source_sha') != report.get('source_sha') or row.get('test') != spec['test']:
            raise ValueError('ARCHITECTURE_EVIDENCE_BINDING_MISMATCH')
        file_name, function = spec['test'].split('::', 1)
        expected_class = file_name.removesuffix('.py').replace('/', '.')
        artifacts = []
        verified = []
        for phase in ('baseline', 'mutant'):
            relative = f"{row['id']}/{phase}.xml"
            path, content = bounded_file(directory, relative)
            cases = list(ET.fromstring(content).iter('testcase'))
            identities = [(case.get('classname'), case.get('name')) for case in cases]
            if len(set(identities)) != len(identities) or any(
                classname != expected_class or not (name == function or (name.startswith(function + '[') and name.endswith(']')))
                for classname, name in identities
            ):
                raise ValueError('ARCHITECTURE_EVIDENCE_TEST_IDENTITY_MISMATCH')
            outcome, _ = classify(SimpleNamespace(returncode=0 if phase == 'baseline' else 1),
                                  path, spec['expected_cases'], spec['failure_marker'], phase == 'baseline')
            verified.append(outcome)
            artifacts.append(dict(path=relative, sha256=hashlib.sha256(content).hexdigest()))
        killed = verified == ['BASELINE_PASSED', 'KILLED'] and row.get('outcome') == 'KILLED'
        projected.append(dict(id=row['id'], test_id=spec['test'], source_sha=row['source_sha'],
                              status=('KILLED' if killed else 'UNRESOLVED') if current else 'STALE',
                              artifacts=artifacts, runtime_observed=False))
    return dict(schema='architecture-retained-evidence-v1', source_sha=source_sha,
                evidence_source_sha=report.get('source_sha'),
                report_sha256=hashlib.sha256(raw).hexdigest(),
                provenance='LOCAL_RETAINED_TEST_FILES_NOT_ATTESTATION',
                mutations=projected, runtime=dict(status='UNKNOWN', traces=[]))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evidence', type=Path, required=True)
    parser.add_argument('--source-sha', required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    registry = tomllib.loads((root / 'architecture/contracts/mutations.toml').read_text(encoding='utf-8'))
    print(json.dumps(project_mutations(args.evidence, registry, args.source_sha), indent=2))
