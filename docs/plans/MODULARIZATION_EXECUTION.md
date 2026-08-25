# Repository Modularization Execution

## Architecture evidence compiler campaign refresh (PENDING)

This 2026-08-26 refresh supersedes older replacement SHA and merge-order rows
below. Latest integrated main is
`da5b47ab507b078b8b8959ffa03ebee0f681e763`. The repaired existing stack ends at
#304 `6b70398593d377cf5e9e64558145ee164e239e09`, followed linearly by:

| PR | Boundary | Exact base | Exact head |
|---:|---|---|---|
| #321 | Deterministic source compiler | `6b70398593d377cf5e9e64558145ee164e239e09` | `4d9c5ff466fc82eae39aa1b9ddb5d9c5cf14f733` |
| #324 | Contract evidence registry | `4d9c5ff466fc82eae39aa1b9ddb5d9c5cf14f733` | `d1ff1b37942e9638542d1b48b0b470f85b9d05cb` |
| #325 | Targeted mutation audit | `d1ff1b37942e9638542d1b48b0b470f85b9d05cb` | `06334347be86acecc5f81a1fde87729fb9d869de` |
| #328 | Private generated-evidence Explorer | `06334347be86acecc5f81a1fde87729fb9d869de` | `f5fbf9b20fcbaea9e20472c856524926ad8fa278` |
| #302 | Documentation-only closure | `f5fbf9b20fcbaea9e20472c856524926ad8fa278` | live PR #302 head OID |

All rows remain Draft and `PENDING`. Merge order is the repaired table in
`docs/audits/REPOSITORY_MODULARIZATION_CLOSURE_2026_08_24.md`, ending
#304 -> #321 -> #324 -> #325 -> #328 -> #302. No Git operation may move Stable.
Assistant remains `PAUSED`.

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
- Latest main: `0bc4c1f84e7b7f48e628f5111c56adb6ad824a2a`.
- Rebuild reason: the pending modularization stack predates the Control Plane,
  bounded repository retry, exact child identity/WPF lifecycle, and deterministic
  structured Control Center operation-result contracts merged by #284, #286,
  #293, #300, #303, and #305 through #311.
- Latest-main collection: 1,580 tests.
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

### Semantic integration evidence

- #287 tracker add/add: retained both this latest-main integration snapshot and
  the original Phase C execution tracker; no runtime boundary was involved.
- #295 Control Center: `0bc4c1f84e7b7f48e628f5111c56adb6ad824a2a`
  was the behavioral source, including #303 Candidate static-asset validation
  and #305 local release-observability credential loading, plus #306 through
  #310 release-lifecycle and exact CPU-evidence corrections. PowerShell AST
  comparison mapped all 210 functions exactly once: 73 runtime/Control Plane,
  94 release/validation,
  and 43 presentation/structured-result definitions.
  The stable entry retains the complete latest-main parameter block, constants,
  service inventory, `Action`/`ServiceKey` dispatch, and three dot-source paths.
- The runtime-control bundle adds all three split owners to the latest-main
  exact-revision/hash transaction; Control Plane tests build and restore the
  complete nine-file bundle. Business Runtime processes and Stable remain
  untouched by this source-only reconstruction.

### Final replacement heads

These are the repaired `PENDING` heads. The rows below supersede the historical
planning heads in the frozen original tracker section.

| PR | Exact repaired base | Exact repaired head | Semantic conflict owner |
|---:|---|---|---|
| #282 | `main` / `0bc4c1f84e7b7f48e628f5111c56adb6ad824a2a` | `3ec3f8d4b30302a7ad0803cd3e0ab880bbda00f8` | Current-main architecture baseline |
| #283 | `3ec3f8d4b30302a7ad0803cd3e0ab880bbda00f8` | `bc151d061ec0b673ecb4834344743dc4d2598b5e` | None |
| #285 | `bc151d061ec0b673ecb4834344743dc4d2598b5e` | `123fd495657c2a9f524dff1bddc1138bd745a931` | None |
| #287 | `123fd495657c2a9f524dff1bddc1138bd745a931` | `24b07b947c4523884eabdcfc75f8aa3123f9ea38` | Tracker add/add retained both audit sections |
| #288 | `24b07b947c4523884eabdcfc75f8aa3123f9ea38` | `4f7d6aadba8cdf40a06ce24f37b9f112107f3f16` | None |
| #289 | `4f7d6aadba8cdf40a06ce24f37b9f112107f3f16` | `d062854327366e8a109d28ae8b13367a1c6c2dae` | None |
| #290 | `d062854327366e8a109d28ae8b13367a1c6c2dae` | `676ebf4497aebd0d4ba4012e08be2cbd7926ea90` | None |
| #291 | `676ebf4497aebd0d4ba4012e08be2cbd7926ea90` | `4475923781814a55520a2f9d24a51e22b44d2575` | Bounded retry for Windows loopback abort in rejection-only test transport |
| #292 | `4475923781814a55520a2f9d24a51e22b44d2575` | `290586f0ed06da76406fd485590a48826280b117` | None |
| #294 | `290586f0ed06da76406fd485590a48826280b117` | `c5aad43c85fcedecd200aea79e623202a7ae5262` | None |
| #295 | `c5aad43c85fcedecd200aea79e623202a7ae5262` | `2f809e3e3fbe6deaadfa214aae5de132953a8397` | Latest-main semantic reconstruction plus delayed-event validation-window repair |
| #296 | `2f809e3e3fbe6deaadfa214aae5de132953a8397` | `5c522cd34a69fa8c50dce72dad13014c728a9f99` | None |
| #297 | `5c522cd34a69fa8c50dce72dad13014c728a9f99` | `f0b876e50b2acf6953aad408389d2fb38362c764` | None |
| #298 | `f0b876e50b2acf6953aad408389d2fb38362c764` | `78ef970f1f13692cccf164d5b2bcfd60a7ca5b3b` | None; one transient loopback reset passed on clean rerun |
| #299 | `78ef970f1f13692cccf164d5b2bcfd60a7ca5b3b` | `7587435b821fe78d87a729fe871de2e8422b168b` | Documentation merge retained current control semantics |
| #301 | `7587435b821fe78d87a729fe871de2e8422b168b` | `386c38f3ef119dc9801fa104b2b8dd7bdf585b85` | Control Plane test-root correction and full inventory reconciliation |
| #304 | `386c38f3ef119dc9801fa104b2b8dd7bdf585b85` | `884f12232701c90da4f8d4780ada3fcbc97a393c` | Node-link graph plus semantic rank/track/convergence hints, beginner-first Explore/Reference navigation, single-owner camera, deterministic per-edge anchors, overlap-safe routing, explicit disclosure, and mobile interaction/viewport completion |
| #302 | `884f12232701c90da4f8d4780ada3fcbc97a393c` | live PR #302 head OID | Documentation-only closure reconstruction and immutable exact-viewport Preview screenshots |

Final implementation collection is 1,633: all 1,580 current-main contracts,
32 campaign net additions, and 21 Explorer manifest cases, with zero unexplained
removals. The exact replacement proof is in the test-organization and Closure
audits. The complete merge order now contains 18 Draft PRs; the campaign from
#287 through #302 contains 15, including #304.

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
| 1 | C2 `refactor/dashboard-resource-contracts` | `refactor/dashboard-health-projection` | Deterministic Dashboard resource serialization and byte bounds | Dashboard resource owner, API/Sync imports, focused tests, architecture policy/docs | Dashboard API/Sync/resource contracts; full Python | Revert one extraction commit; no state migration | DRAFT PR OPEN | #287 / `8bad9dca33099bb53b13bf1b4089c7e2d09dea72` |
| 2 | C3a `refactor/dashboard-api-news-resources` | C2 | News archive, evidence, and content read resources | Dashboard news resource owners, API wrapper, focused tests/maps | Dashboard/news resource and route tests; full Python | Revert mechanical owner move | DRAFT PR OPEN | #288 / `ffc38d8786d73b0e1b963b35b3e497e0fcae7604` |
| 3 | C3b `refactor/dashboard-api-market-resources` | C3a | Market history and chart read resources | Dashboard market owner, API wrapper, focused tests/maps | Market/API production-shape tests; full Python | Revert mechanical owner move | DRAFT PR OPEN | #289 / `db8f10d8ad4334a7d14351ced1566223025bedcf` |
| 4 | C3c `refactor/dashboard-api-optional-resources` | C3b | Current status and optional read-resource composition | Status resource owner, API compatibility imports, focused tests/maps | API, audit and release-read tests; full Python | Revert mechanical owner move | DRAFT PR OPEN | #290 / `ada781248cc18723a2b11ce563c135c67e9656d7` |
| 5 | C3d `refactor/dashboard-operator-bridge` | C3c | Audited local scheduler operator bridge | Auth/list/apply service owner and HTTP adapter | Retry authorization/transition tests; full Python | Revert bridge extraction | DRAFT PR OPEN | #291 / `6358b897f88a3c8bdf8de2af63421fa65dc11312` |
| 6 | C4 `refactor/dashboard-sync-runtime` | C3d | Sync progress, cadence, transport, and lane runtime | `dashboard/sync/` owners, thin script, focused tests/maps | Sync isolation/protocol tests; full Python | Revert package extraction | DRAFT PR OPEN | #292 / `305eb1113026f974aee12ce0ca8bbe475a3eebdf` |
| 7 | C5 `refactor/news-annotator-runtime` | C4 | Durable annotator batch execution and Brief-cycle orchestration | News scheduler/Brief runtime owners, thin script, focused tests/maps | Scheduler, annotation, retrieval, Brief; full Python | Revert package extraction | DRAFT PR OPEN | #294 / `caf0a8a0862cceb939159faa39c49dd9dea7ef97` |
| 8 | C6 `refactor/control-center-boundaries` | C5 | Collector domain runtime and Control Center release/supervision/presentation owners behind stable entry paths | Python runtime owner, PowerShell owner files, bundle manifest, launcher tests/maps | Collector contracts plus Windows runtime and release fixtures | Revert owner extraction and manifest change | DRAFT PR OPEN | #295 / `a3dd64fee0b58967475d3c36f9841986162bccb9`; PR #279 conflict anticipated and must be resolved by rebase, not cherry-pick |
| 9 | D1 `refactor/decision-evidence-packages` | C6 | Canonical Decision and Evidence packages | Canonical modules, narrow shims, migration map/tests | Decision/evidence/forward-only/production-shape; full Python | Restore canonical files to legacy paths | DRAFT PR OPEN | #296 / `a73b594893dd68bf1e87d89a89ac60ad6161ad56` |
| 10 | D2 `refactor/training-package` | D1 | Canonical Training package | Training modules, narrow shims, migration map/tests | Training/evidence integrity; full Python | Restore canonical files to legacy paths | DRAFT PR OPEN | #297 / `304e44cac64cf50be6ef319caffbc5dd90db24a5` |
| 11 | D3 `refactor/news-ai-packages` | D2 | Canonical News and AI packages and SCC reduction | News/AI modules, narrow shims, migration map/tests | Scheduler/annotation/retrieval/Brief/AI; full Python | Restore canonical files to legacy paths | DRAFT PR OPEN | #298 / `388b6a870693b6132324fbfa4ca013975545f051` |
| 12 | D4 `refactor/assistant-runtime-dashboard-packages` | D3 | Assistant, Runtime, and Dashboard package closure | Canonical modules, public facades/shims, migration map/tests | Assistant/runtime/Dashboard; full Python | Restore canonical files to legacy paths | DRAFT PR OPEN | #299 / `bd9aa3626da5bf3cb4a44883f64c4d6e14944dff` |
| 13 | E `refactor/test-organization` | D4 | Python, Web, and Windows test ownership | Safe test moves/support modules and audit mapping | Collection reconciliation; full Python/Web/Windows | Reverse test-only moves | DRAFT PR OPEN | #301 / `2481d3170ba797cd0d5e5aafefd0b26d9490e8fe` |
| 14 | Closure `chore/modularization-campaign-closure` | E | Campaign evidence and merge/rollback order | Closure audit and final architecture map status | Full stack exact-head checks | Revert docs-only commit | DRAFT PR OPEN | #302 / live PR head OID (a commit cannot embed its own hash) |

The exact boundary may be narrowed after audit, but unrelated owners will not be
combined to save PR count. The hard limit is 14 Draft PRs.

## Baseline dependency evidence

The initial SCC contains `ai_task_registry`, `annotation`,
`assistant_capacity`, `assistant_routing`, `critical_annotation_state`,
`daily_brief`, `forward_ledger`, `gemini_embeddings`, `market`,
`market_session`, `news_retrieval`, `news_scheduler`, `news_time`, and
`semantic_transition`. Phase D must measure this exact component again rather
than assuming file moves remove it.

C4 removes the two transitional build imports of `run_dashboard_sync.py`;
script-to-script shared-library imports are rejected from that row onward.
