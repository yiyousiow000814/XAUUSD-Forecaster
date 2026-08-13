# Test Suite Audit

## Scope and baseline

This audit covers the complete Python and Web suites on `main`, with focused
review of `test_forward_only.py`, `test_evidence_integrity_v2.py`, dashboard
API/sync tests, runtime launchers, and the release gates proposed in PR60-62.

Baseline on 2026-08-13:

- Python: 340 tests passing.
- Web: 45 tests passing.
- Python modules: 16 test modules plus `conftest.py`.
- Web modules: one rendered/API contract module.

## Coverage map

| Area | Current coverage | Classification | Follow-up |
| --- | --- | --- | --- |
| Point-in-time market/news and immutable evidence | `test_forward_only.py`, `test_evidence_integrity_v2.py` | invariant, integration | Preserve; split by domain in a dedicated PR. |
| Credentialed macro collectors | formerly separate FRED/EIA/BEA cases | regression, family contract | Shared secrecy/error contracts now cover all siblings. |
| Model generation handover | `test_evidence_integrity_v2.py` | invariant, handover | Preserve until superseded runtime paths are removed. |
| Dashboard API and sync | `test_dashboard_api.py`, `test_dashboard_sync.py` | public contract, integration | Keep payload, cursor, durability, and boundedness tests explicit. |
| Runtime/watchdog | `test_runtime_launchers.py` | integration plus implementation-coupled checks | Replace source-string assertions with executable PowerShell contracts. |
| Web dashboard | `web/tests/rendered-html.test.mjs` | public contract plus source coupling | Keep route/output tests; migrate copy/source checks to rendered behavior. |
| PR60 quality gates | workflow execution | release contract | Merge before PR61/62; stable job names are an intentional external contract. |
| PR61 production shape | proposed `test_production_shape.py` | cross-component invariant | Keep positive and independently isolated negative contract cases. |
| PR62 safe runtime switch | proposed launcher/API/Web tests | safety contract plus source coupling | Rewrite function-name/order assertions as executable state transitions. |

## Findings and changes

The registered macro collectors had the A/B/C problem. FRED and EIA shared an
error-redaction test, BEA had only a success-path persistence assertion, and no
single test declared that credential secrecy applies to the whole registered
collector family. Parameterized family contracts now cover FRED, EIA, and BEA
for both success and failure observability. Existing collector-specific tests
remain because they also protect distinct cadence, data-shape, and evidence-role
semantics.

The old FRED/EIA-only error-redaction regression was removed because the new
family contract fully subsumes it and adds the previously missing BEA sibling.
Collector-specific success tests remain because they also protect distinct
cadence, data-shape, and evidence-role semantics.

## PR60-62 review

PR60 fixes a real fixture race by returning independent mock API objects. That
is test infrastructure, not a product contract, and should remain next to the
parallel capacity tests. The proposed workflow adds the missing complete-suite
release gate and should merge first.

PR61 introduces an appropriate cross-component release invariant, but its
single combined negative test should be separated by violation family. A
failure should identify whether generation completeness, live decision shape,
scheduler accounting, source recovery, or bounded transport broke. This is a
clarity improvement, not a request for more production behavior.

PR62 contains meaningful end-to-end safety behavior, but the proposed additions
to `test_runtime_launchers.py` pin private PowerShell function names, literal
constant declarations, and source ordering. Those assertions can pass while the
functions are dead and can fail after a safe rename. Before PR62 merges, replace
them with executable tests for these observable transitions:

1. a candidate that fails preflight never replaces the current revision;
2. two new decision cycles activate a staged revision;
3. repeated observation failures restore the previous revision;
4. a successful update remains silent while a failed update reaches the API;
5. the staged process is terminated on every exit path.

## Natural module boundaries

`test_forward_only.py` currently mixes ledger concurrency, market snapshots,
content hydration, discovery and macro collectors, annotation/quota behavior,
training, simulation, and maintenance. `test_evidence_integrity_v2.py` mixes
execution semantics, news time/evidence, learning curves, generation handover,
inference policy, and semantic identity. Safe split boundaries are:

- `test_forward_ledger_contracts.py`
- `test_news_collection_contracts.py`
- `test_news_annotation_contracts.py`
- `test_forward_training_contracts.py`
- `test_evidence_time_contracts.py`
- `test_model_generation_contracts.py`
- `test_execution_learning_contracts.py`

The physical split is intentionally deferred. Moving more than 170 tests while
PR60-62 are open would create conflict-heavy, review-hostile diffs without
improving behavior coverage. It should be a mechanical follow-up after those
release-safety PRs merge, with test node IDs mapped before and after.

## Remaining debt

- Move shared ledger, news, quote, decision, and model builders into focused
  fixtures; avoid a universal harness.
- Replace remaining source-text assertions in runtime and Web tests when the
  corresponding behavior can be executed cheaply.
- Split the two oversized modules using the boundaries above while preserving
  all node IDs or publishing an explicit old-to-new mapping.
- Separate PR61 negative production-shape scenarios for actionable failures.
- Keep Preview/production isolation, bounded payload, append-only evidence,
  causality, execution-cost, and fail-closed tests unchanged during that split.

## Refactor result and verification

- Python collected tests: 340 before, 345 after. The net increase is six
  FRED/EIA/BEA family cases minus the superseded FRED/EIA-only regression.
- Python test modules: 16 before, 17 after.
- Web collected tests: 45 before and after.
- Removed test: `test_registered_macro_errors_redact_keys`, because both of its
  siblings are covered by the broader registered-collector failure contract and
  BEA is now covered by the same rule.
- Consolidated contract: credential absence from persisted evidence and returned
  status, on both success and failure, across every registered macro collector.
- Newly covered sibling: BEA failure redaction.
- Shared fixtures/builders introduced: none. The new three-case table is local
  to one contract and does not justify a cross-suite harness.
- Production behavior, schemas, research semantics, model rules, and public APIs
  were not changed.

Verification commands:

```text
C:\Python314\python.exe -m pytest -q
345 passed in 41.08s

cd web
npm.cmd test
45 passed; production build passed

git diff --check
passed
```
