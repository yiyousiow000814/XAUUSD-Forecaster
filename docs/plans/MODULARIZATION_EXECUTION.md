# Repository Modularization Execution

## Full-stack latest-main integration (PENDING)

This section records a pending Draft PR stack reconstruction. It does not
describe merged `CURRENT` architecture.

### Architecture change gate

```text
Owner: Repository modularization campaign; each PR retains one named owner boundary.
Authoritative state/store: Git commits, Draft PR metadata, architecture contracts, and test inventories.
Execution boundary: Source/build/test only; no runtime, provider, database, deployment, or Stable mutation.
Critical or optional: Optional development and validation path.
Maximum work per operation: One branch rebase and its bounded focused/full validation before proceeding.
Incremental cursor/revision/checkpoint: Exact parent/head SHA, normalized pytest node inventory, and exact-head CI check set.
Failure domain: The lowest PR that owns a semantic conflict or missing contract.
Last-good/recovery behavior: Preserve remote pre-integration heads; rewrite only with force-with-lease after local proof.
Architecture documents affected: SYSTEM_ARCHITECTURE, RUNTIME_AND_RELEASE, CODEBASE_MAP, this tracker, and final closure audit.
```

### Pre-integration snapshot

- Verification date: 2026-08-24.
- Latest main: `17b71467d95dbde12ed59cda2537a4e4b5f32a1e`.
- Rebuild reason: the pending modularization stack predates the Control Plane,
  bounded repository retry, exact child identity/WPF lifecycle, and deterministic
  structured Control Center operation-result contracts merged by #284, #286,
  #293, and #300.
- Latest-main collection: 1,547 tests.
- Pre-rebase Closure collection: 1,512 tests.
- Assistant remains `PAUSED`.

| PR | Branch | Pre-integration head |
|---|---|---|
| #282 | `docs/architecture-baseline` | `5fb64cbb05f7487f59bcc8a8ec779c4bd9fd8f44` |
| #283 | `refactor/dashboard-status-cache` | `9dfdbf606211b8ac836451d84b6f3ede95418779` |
| #285 | `refactor/dashboard-health-projection` | `d4103fbe61e0c025b9d246d35804fecb2a3c3fdb` |
| #287 | `refactor/dashboard-resource-contracts` | `8bad9dca33099bb53b13bf1b4089c7e2d09dea72` |
| #288 | `refactor/dashboard-api-news-resources` | `ffc38d8786d73b0e1b963b35b3e497e0fcae7604` |
| #289 | `refactor/dashboard-api-market-resources` | `db8f10d8ad4334a7d14351ced1566223025bedcf` |
| #290 | `refactor/dashboard-api-optional-resources` | `ada781248cc18723a2b11ce563c135c67e9656d7` |
| #291 | `refactor/dashboard-operator-bridge` | `6358b897f88a3c8bdf8de2af63421fa65dc11312` |
| #292 | `refactor/dashboard-sync-runtime` | `305eb1113026f974aee12ce0ca8bbe475a3eebdf` |
| #294 | `refactor/news-annotator-runtime` | `caf0a8a0862cceb939159faa39c49dd9dea7ef97` |
| #295 | `refactor/control-center-boundaries` | `a3dd64fee0b58967475d3c36f9841986162bccb9` |
| #296 | `refactor/decision-evidence-packages` | `a73b594893dd68bf1e87d89a89ac60ad6161ad56` |
| #297 | `refactor/training-package` | `304e44cac64cf50be6ef319caffbc5dd90db24a5` |
| #298 | `refactor/news-ai-packages` | `388b6a870693b6132324fbfa4ca013975545f051` |
| #299 | `refactor/assistant-runtime-dashboard-packages` | `bd9aa3626da5bf3cb4a44883f64c4d6e14944dff` |
| #301 | `refactor/test-organization` | `2481d3170ba797cd0d5e5aafefd0b26d9490e8fe` |
| #302 | `chore/modularization-campaign-closure` | `c8b1ed04152064c979ef188a891ba2be7166d2a9` |

Replacement heads and semantic conflict ownership are recorded as each Draft
PR is repaired. A replacement head remains `PENDING` until its PR is merged.
## Original campaign execution tracker

## Status and baseline

This document is a `PENDING` execution tracker. It records the open stacked
campaign and MUST NOT be read as `CURRENT` architecture until its PRs merge in
order.

- Audited base: `d4103fbe61e0c025b9d246d35804fecb2a3c3fdb`
- Base branch: `refactor/dashboard-health-projection` (Draft PR #285)
- Execution date: 2026-08-23
- Python collection baseline: 1,500 cases
- Python production/script modules: 104
- Import edges: 316
- Non-trivial import SCCs: one 14-module component
- Package imports from `scripts`: zero
- Script-to-script shared-library imports: three call sites across two build
  scripts and the Dashboard API; C2 removes the Dashboard API dependency.

`CURRENT` remains the architecture recorded for the audited main revision.
Every campaign row below is `PENDING` until merged. The package and test trees
described by the campaign are `TARGET` until the corresponding row is merged.

## Planned linear stack

| Order | Phase / branch | Immediate base | Owner boundary | Expected files | Validation family | Rollback | Status | PR / final SHA |
|---:|---|---|---|---|---|---|---|---|
| 1 | C2 `refactor/dashboard-resource-contracts` | `refactor/dashboard-health-projection` | Deterministic Dashboard resource serialization and byte bounds | Dashboard resource owner, API/Sync imports, focused tests, architecture policy/docs | Dashboard API/Sync/resource contracts; full Python | Revert one extraction commit; no state migration | DRAFT PR OPEN | #287 / `587dd554d4184e2f265ba1cf0e50ff21f666b2ab` |
| 2 | C3a `refactor/dashboard-api-news-resources` | C2 | News archive, evidence, and content read resources | Dashboard news resource owners, API wrapper, focused tests/maps | Dashboard/news resource and route tests; full Python | Revert mechanical owner move | LOCALLY VALIDATED | Pending PR creation |
| 3 | C3b `refactor/dashboard-api-market-resources` | C3a | Market history and chart read resources | Dashboard market owner, API wrapper, focused tests/maps | Market/API production-shape tests; full Python | Revert mechanical owner move | PLANNED | TBD |
| 4 | C3c `refactor/dashboard-api-optional-resources` | C3b | Remaining optional/audit/deployment reads and operator bridge | Explicit resource owners, API wrapper, focused tests/maps | API, retry, audit and release-read tests; full Python | Revert mechanical owner move | PLANNED | TBD |
| 5 | C4 `refactor/dashboard-sync-runtime` | C3c | Sync progress, cadence, transport, and lane runtime | `dashboard/sync/` owners, thin script, focused tests/maps | Sync isolation/protocol tests; full Python | Revert package extraction | PLANNED | TBD |
| 6 | C5 `refactor/news-annotator-runtime` | C4 | Durable annotator batch execution and Brief-cycle orchestration | News scheduler/Brief runtime owners, thin script, focused tests/maps | Scheduler, annotation, retrieval, Brief; full Python | Revert package extraction | PLANNED | TBD |
| 7 | C6 `refactor/control-center-boundaries` | C5 | Control Center release/supervision modules behind stable entry path | PowerShell owner files, bundle manifest, launcher tests/maps | Windows runtime and release fixtures | Revert dot-source extraction and manifest change | PLANNED | TBD |
| 8 | D1 `refactor/decision-evidence-packages` | C6 | Canonical Decision and Evidence packages | Canonical modules, narrow shims, migration map/tests | Decision/evidence/forward-only/production-shape; full Python | Restore canonical files to legacy paths | PLANNED | TBD |
| 9 | D2 `refactor/training-package` | D1 | Canonical Training package | Training modules, narrow shims, migration map/tests | Training/evidence integrity; full Python | Restore canonical files to legacy paths | PLANNED | TBD |
| 10 | D3 `refactor/news-ai-packages` | D2 | Canonical News and AI packages and SCC reduction | News/AI modules, narrow shims, migration map/tests | Scheduler/annotation/retrieval/Brief/AI; full Python | Restore canonical files to legacy paths | PLANNED | TBD |
| 11 | D4 `refactor/assistant-runtime-dashboard-packages` | D3 | Assistant, Runtime, and Dashboard package closure | Canonical modules, public facades/shims, migration map/tests | Assistant/runtime/Dashboard; full Python | Restore canonical files to legacy paths | PLANNED | TBD |
| 12 | E1 `refactor/python-test-organization` | D4 | Python contract and integration test ownership | Test moves/support modules and audit mapping | Collection reconciliation; full Python | Reverse test-only moves | PLANNED | TBD |
| 13 | E2 `refactor/web-windows-test-organization` | E1 | Web and Windows test ownership | Safe test moves/names and runner references only where discovered | Full Web and Windows runtime suites | Reverse test-only moves | PLANNED | TBD |
| 14 | Closure `chore/modularization-campaign-closure` | E2 | Campaign evidence and merge/rollback order | Closure audit and final architecture map status | Full stack exact-head checks | Revert docs-only commit | PLANNED | TBD |

The exact boundary may be narrowed after audit, but unrelated owners will not be
combined to save PR count. The hard limit is 14 Draft PRs.

## Baseline dependency evidence

The initial SCC contains `ai_task_registry`, `annotation`,
`assistant_capacity`, `assistant_routing`, `critical_annotation_state`,
`daily_brief`, `forward_ledger`, `gemini_embeddings`, `market`,
`market_session`, `news_retrieval`, `news_scheduler`, `news_time`, and
`semantic_transition`. Phase D must measure this exact component again rather
than assuming file moves remove it.

The two permitted transitional build imports of `run_dashboard_sync.py` are
owned by C4 and have no runtime authority. They are explicitly rejected at
campaign completion.
