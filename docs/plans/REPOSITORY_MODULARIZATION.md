# Repository Modularization Plan

## Status

This document is `TARGET/PLAN`. It does not describe the current package tree
or grant authority to move runtime code.

**No production file move is performed by the architecture-baseline PR.**

The first Phase B extraction is implemented by the current stacked change but
remains `PENDING` until that change merges. The baseline architecture remains
the current package authority until then.

The first Phase C extraction is implemented by the next stacked change but
also remains `PENDING` until merge. It must not be read as selection or
authorization of a Phase 4 boundary.

The modularization completion campaign is tracked in
[`MODULARIZATION_EXECUTION.md`](MODULARIZATION_EXECUTION.md). Its C2 resource
contract extraction remains `PENDING`; later rows remain `TARGET` until their
own stacked PR exists.

The baseline was measured at
`b763a96f5b862b72a0fbb34419ff01909e450338` on 2026-08-23. Generated types,
lockfiles, generated migration manifests, fixtures, and vendor files were
excluded from refactor candidates.

## Current structural findings

A standard-library AST audit of the flat Python package found a large strongly
connected import group spanning ledger, scheduler, annotation, collection,
training-adjacent, and Assistant-support modules. `forward_ledger.py` had 25
package importers; `news_scheduler.py` had 13; `annotation.py` and
`news_semantics.py` each had 12. These numbers indicate shared change surfaces,
not automatic split points.

Recent Git history also shows repeated change coupling: since 2026-08-01,
`run_dashboard_api.py` appeared in 99 commits, `test_dashboard_api.py` and
`test_dashboard_sync.py` in 68 each, `run_dashboard_sync.py` in 56,
`AuditView.tsx` in 56, and `annotation.py` in 55. Counts are planning evidence,
not an architectural contract.

| File | Size | Current responsibilities | Owners mixed | Change risk | Suggested future action |
|---|---:|---|---|---|---|
| `scripts/xauusd_control_center.ps1` | 5,456 lines / 273,725 bytes | Service supervision, watchdog, Candidate discovery, CI/Cloudflare checks, DB preflight, Promote/Reverse, WPF/WinForms UI, diagnostics | Runtime, release, platform validation, operator UI | Very high | Extract one behavior-preserving control boundary at a time; keep the entry script as orchestration |
| `scripts/run_dashboard_api.py` | 3,004 / 144,559 | HTTP server, cache and health-projection wiring, resource adapters/builders, paging, payload orchestration, retry override | API, read models, market/news/audit, scheduler bridge | High | Status cache and runtime-health projection extracted in pending stacked changes; later work is outside this change |
| `scripts/run_dashboard_sync.py` | 2,080 / 90,707 | Remote transport, compaction, paging, cursor state, scheduling, retries, heartbeat and lanes | Sync control, resource owners, transport | High | Separate resource codecs/checkpoints from process/lane orchestration in later single-resource PRs |
| `scripts/run_news_annotator.py` | 698 / 27,384 | Process loop, scheduler lane execution, credential selection, Brief cycle, heartbeat | Runtime orchestration, scheduler, provider execution | Medium-high | Keep thread/process creation here; move transition execution behind existing domain owners |
| `xauusd_forecaster/news_scheduler.py` | 3,799 / 163,737 | Schema, jobs, attempts, overrides, account quota, provider governor, backfill admission, migrations | Scheduler, capacity, operator control, migrations | Very high | Split only by durable table/transition owner after contract tests define dependency direction |
| `xauusd_forecaster/annotation.py` | 3,144 / 146,260 | Annotation, impact, title prompts/transports, validation/repair payloads | Three semantic products and provider integration | High | Extract complete product families, never validators without repair schemas and regression family |
| `xauusd_forecaster/news.py` | 1,669 / 71,938 | Source clients, parsers, full-text fetch, macro conversion, intake failures | Multiple source owners and shared intake | High | Group sources behind the existing registry while preserving source-specific evidence contracts |
| `xauusd_forecaster/forward_ledger.py` | 1,434 / 67,710 | Schema install and persistence methods for forecast, news, Brief and model state | Many table owners | Very high | Avoid a generic repository layer; move table-specific persistence only with its domain owner |
| `xauusd_forecaster/daily_brief.py` | 1,255 / 65,763 | Population, ranking, refresh, synthesis, validation, finalization, recovery | Brief lifecycle stages | Medium-high | Split deterministic population/state from provider synthesis only after end-to-end lifecycle fixtures |
| `xauusd_forecaster/training_v2.py` | 1,211 / 58,465 | Materialization, weighting, cross-fit, fitting, artifacts, manifest, activation | Materialization and publication owners | High | Separate materialization from generation build in two behavior-preserving PRs, retaining atomic activation |
| `web/app/globals.css` | 2,056 / 211,860 | Shared tokens/layout plus many feature-specific responsive states | Nearly every Web feature | High | Move stable feature blocks beside owners without changing selector order or responsive contracts |
| `web/app/_views/AuditView.tsx` | 1,727 / 118,017 | Audit navigation, news/decision/story presentation, data/resource states | Several audit resources | High | Extract leaf presentation sections with existing resource contracts; preserve view ownership |
| `web/app/audit/LearningGraphModal.tsx` | 1,060 / 85,749 | Modal shell, chart transforms, controls, table/detail rendering | Learning resource and visualization | Medium | Extract pure chart/data transforms before UI shell, with rendered and data-shape tests |
| `tests/test_forward_only.py` | 5,354 / 246,524 | Collection, point-in-time, ledger, source and forward-only families | Multiple evidence owners | High test coupling | Split by contract family, not production filename |
| `tests/test_dashboard_api.py` | 3,290 / 152,423 | Critical cache, read models, API resources, market/news paging, overrides | API and several data owners | High test coupling | Extract stable owner-level contract modules as production boundaries are separated |
| `tests/test_evidence_integrity_v2.py` | 3,112 / 151,447 | Repair, materialization, generation, weighting and evidence integrity | Evidence and training owners | High test coupling | Preserve one explicit cross-runtime integrity suite; split independent setup-heavy families |
| `tests/test_news_scheduler.py` | 2,855 / 127,007 | Jobs, quota, governor, retry, overrides, migrations | Scheduler sub-owners | High test coupling | Organize around transition invariants and capacity contracts |
| `tests/test_dashboard_sync.py` | 2,404 / 107,643 | Payload bounds, resources, cursors, target isolation, continuous lanes | Sync plus multiple resource owners | High test coupling | Keep cross-resource isolation tests; split resource protocols after production ownership is explicit |
| `tests/test_runtime_launchers.py` | 2,352 / 132,873 | Hidden launch, service inventory, watchdog and release transitions | Runtime and release owners | High test coupling | Separate supervision from release transaction contracts without losing exact cross-boundary tests |

## Proposed target layout

This is a navigation proposal, not a directory creation request:

```text
xauusd_forecaster/
  decision/       # frozen decision, outcome and execution evidence
  evidence/       # shared point-in-time records and integrity contracts
  training/       # materialization, fitting and publication
  news/
    collection/   # source polling and intake
    semantics/    # structure and time validation
    annotation/   # annotation, impact and display products
    scheduler/    # durable jobs, capacity, retry and governor
    retrieval/    # event identity and Gemini embeddings
  ai/             # provider-neutral request/accounting boundaries
  dashboard/      # critical status, read models and resource protocols
  runtime/        # heartbeat and local process contracts
  assistant/      # retained provider-independent Assistant implementation
```

```text
scripts/
  runtime/        # thin process/service launchers
  operations/     # deliberate maintenance commands
  release/        # Candidate, Stable and validation entry points
  build/          # immutable build/bundle commands
  audit/          # read-only evidence audits
```

Web should keep framework-required `web/app/` route structure. Feature-owned
components and libraries may be grouped without moving route files merely for
visual symmetry.

## Phased migration

### Phase A — Architecture baseline

This PR adds current maps, the Codebase Map, repository architecture rules, the
AGENTS change gate, documentation validation, and this plan. It changes no
runtime behavior, schema, route, payload, import, or production file location.

### Phase B — First low-risk extraction

Implementation status: `PENDING` until the current stacked change merges. This
change implements only the first refactor boundary described below:

- **Source file:** `scripts/run_dashboard_api.py`.
- **Exact responsibility:** `StatusSnapshotUnavailable` and
  `StatusSnapshotCache`, including bounded background refresh, wait, stale,
  last-good, and health behavior.
- **Target module:** `xauusd_forecaster/dashboard/status_cache.py`, with
  `xauusd_forecaster/dashboard/__init__.py` containing no eager re-exports.
- **Compatibility/import strategy:** the entry script imports the two public
  names and three constants directly so existing `run_dashboard_api` callers
  observe the same names during the handover. Remove that compatibility only
  after all callers import the owner module and the entry script no longer
  exposes those names as a supported surface.
- **Focused tests:** move/parameterize the existing cache behavior cases from
  `tests/test_dashboard_api.py` into
  `tests/test_dashboard_status_cache.py`; retain API-level first-paint and
  last-good integration assertions in the original suite.
- **Rollback:** revert the one extraction commit; no state or data migration is
  involved.
- **Expected files:** the source entry script, two new package files, the
  existing API tests, one focused test module, the Dashboard/Codebase maps.
- **Measured result:** `run_dashboard_api.py` is 3,248 lines / 154,122 bytes
  after extraction, using the same measurement method as the baseline table.
- **Non-goals:** no HTTP route, TTL, timeout, thread, payload, database, sync,
  cache policy, package-wide dashboard migration, or test-semantic change.

Why first: the cache has one owner, no database schema, clear existing tests,
and an explicit boundary between request threads and bounded background
refresh. It is lower risk than moving shared persistence, scheduler transitions,
or release-control code.

Do not perform this extraction in Phase A.

### Phase C — Split orchestration from domain logic

Keep process and thread creation, command-line parsing, startup, shutdown, and
top-level health in entry points. Move transition rules, queries, serialization,
and recovery policy behind the domain owner already named by a contract. Each
PR must demonstrate identical externally observable behavior.

#### Phase C-1 — Dashboard runtime-health projection

Implementation status: `PENDING` until the Phase 3 stacked change merges.

- **Exact responsibility:** semantic-pipeline projection, current materialized
  semantic-health lookup, Collector heartbeat projection, Decision-output
  cadence/broker projection, and their seven fixed thresholds.
- **Target module:**
  `xauusd_forecaster/dashboard/health_projection.py`, a standard-library-only
  read owner with no cache or durable state.
- **Compatibility/import strategy:** `run_dashboard_api.py` directly imports
  all four private functions and seven constants during the handover. Remove
  those entry-point names only after every caller imports the owner module and
  the entry point no longer exposes them as a supported test/caller surface.
- **Focused tests:** direct projection boundaries move to
  `tests/test_dashboard_health_projection.py`; payload placement, snapshot
  timing, alert aggregation, route, database, and API integration remain in
  `tests/test_dashboard_api.py` and the operational/runtime suites.
- **Measured result:** `run_dashboard_api.py` is 3,004 lines / 144,559 bytes
  after extraction, using the same measurement method as the inventory table.
- **Rollback:** revert the single extraction commit; no state or data migration
  is involved.
- **Non-goals:** no health semantics, threshold, status/reason/message, SQL,
  route, payload, process, cache, database, sync, Preview, or production change.

### Phase D — Domain package migration

Move one subsystem owner and one boundary per PR. Avoid a repository-wide rename.
Each PR must be behavior-preserving, independently reversible, and explicit
about temporary import compatibility and its removal condition. Production
state migration is excluded unless separately approved and contracted.

### Phase E — Test organization

Organize tests by subsystem contract and invariant, not one-to-one with source
files. Preserve family-level coverage for point-in-time correctness, append-only
history, failure isolation, restart recovery, scale/boundedness, and
cross-runtime schemas. Keep a specific regression only when a broader family
contract does not subsume its failure mode.

### Phase C-2 — Shared Dashboard resource contracts

Implementation status: `PENDING` until the C2 stacked change merges.

- **Exact responsibility:** deterministic learning-history records and
  summaries, news mirror projections/batches, bounded market-chart projection,
  and critical/audit resource JSON serialization.
- **Target module:**
  `xauusd_forecaster/dashboard/resource_contracts.py`.
- **Compatibility:** `run_dashboard_sync.py` imports and exposes the canonical
  objects during handover; `run_dashboard_api.py` imports the package owner
  directly. Preview/release build imports are removed by C4.
- **Focused tests:** direct byte/item/history projection cases live in
  `tests/test_dashboard_resource_contracts.py`; transport, ordering, target
  isolation, and route integration remain in the API/Sync suites.
- **Rollback:** revert the extraction commit; no state, schema, cursor, or
  transport migration is involved.
- **Non-goals:** no payload byte, key order, omission, limit, cadence, route,
  cursor, remote transport, or production-state change.

## Priority method

File size is only an inventory signal. Rank a proposed split using:

1. number of distinct responsibilities;
2. number of state or transition owners mixed;
3. critical-path and failure-domain risk;
4. import fan-in and fan-out, including cycles;
5. recent change frequency and merge conflicts;
6. test coupling and availability of behavior-level contracts;
7. ease of isolated rollback;
8. likelihood that the new abstraction corresponds to a real boundary.

Prefer a reversible leaf with one owner over a large central module with many
writers. Do not create generic managers, repositories, factories, adapters, or
base classes unless a real second implementation or transport boundary requires
them.

## Completion criteria for each future refactor

- Current maps and Codebase Map remain accurate.
- No state, API, schema, timing, threshold, or failure behavior changes unless
  that behavior change has its own approved contract.
- Exact owner and dependency direction are clearer after the move.
- Existing family-level tests pass and focused boundary tests protect the
  extraction.
- Temporary compatibility has a named removal condition.
- Diff review proves one architecture boundary and an easy rollback.
