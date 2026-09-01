# Current-Main Architecture Audit — 2026-09-01

## Scope and identity

- Audited revision: `0ae538b0724d43b9ea122b92deea844082bc6d5c`
- Production mutation: none
- Assistant state: PAUSED
- Old Draft use: design and evidence input only; no stale commit was merged or
  rebased.

This audit follows the completed P0/P1 containment program. It records current
source structure before any owner extraction.

## Measured structure

| Surface | Current measurement | Finding |
|---|---:|---|
| `scripts/xauusd_control_center.ps1` | 16,152 nonblank/read lines; 374 functions | Release, runtime, provider, recovery, and presentation ownership remain mixed |
| `scripts/run_dashboard_api.py` | 69 top-level functions/classes; over 4,100 physical lines | Status, News, market, read models, backup/WAL status, and HTTP routing remain mixed |
| `scripts/run_dashboard_sync.py` | 99 top-level functions/classes; about 2,700 physical lines | Payload shaping, transport, scheduling, cursors, deferred projection, and process loop remain mixed |
| `xauusd_forecaster/news_scheduler.py` | 3,799 measured lines | Durable job, quota, retry, and transition ownership has high fan-out |
| `xauusd_forecaster/annotation.py` | 3,144 measured lines | Annotation execution is both a high fan-in and high fan-out owner |
| Python import graph | one 14-module strongly connected component | Ledger, scheduler, annotation, time, retrieval, market, and retained Assistant support are cyclic |
| Package to scripts imports | zero | The required direction is currently preserved |
| Changed since old Draft audit base `55593c05…` | 163 files | Old implementation patches cannot be treated as current |

The largest strongly connected component is:

```text
ai_task_registry, annotation, assistant_capacity, assistant_routing,
critical_annotation_state, daily_brief, forward_ledger, gemini_embeddings,
market, market_session, news_retrieval, news_scheduler, news_time,
semantic_transition
```

`forward_ledger` has the highest measured package fan-in (27). The cycle is
not evidence that all 14 modules share one owner; it is evidence that broad
package moves would preserve hidden coupling unless interfaces are extracted
first.

## P0/P1 foundations now present

- Managed SQLite backups have receipt-backed bounded retention.
- Proven stale backup temporaries have exact reclaim plans; unknown storage is
  not deleted.
- WAL checkpointing has one Collector-supervised owner and a size diagnostic.
- Release Control has 15 first-class behavior-keyed evidence nodes.
- Semantic retry, live lease renewal, deferred projection, and immediate Sync
  have narrow owners.
- Execution-learning quote recovery is time-windowed.
- Crossfit checks immutable cache identity before prefix materialization.
- Learning Sync hash state is bounded to the current source universe.
- Market-history requests read cursor-relevant day partitions.
- Collector restart catch-up settles the unobservable historical prefix in
  constant work and inspects only the live quote window.

These foundations remove the main reason to preserve the old modularization
stack as one unit.

## Old Draft classification

`STILL_VALID` means the owner boundary remains a good next extraction, not that
the old patch is mergeable. `PARTIALLY_VALID` means only a smaller subset or
the design intent should be reused. `ALREADY_SUPERSEDED` means current main or
an already merged PR owns the result. `OBSOLETE` means the proposed artifact no
longer represents the selected architecture.

| PR | Classification | Current-main decision |
|---:|---|---|
| #282 architecture maps/rules | PARTIALLY_VALID | Reuse the hierarchy and ownership questions; replace stale SHA, gaps, paths, and campaign plan with this current map |
| #283 status snapshot cache | STILL_VALID | Cache is still implemented inside Dashboard API and has a coherent, testable owner boundary |
| #285 runtime health projection | STILL_VALID | Health projection remains a cohesive Dashboard API domain slice |
| #287 dashboard resource contracts | ALREADY_SUPERSEDED | Current `dashboard_payloads.py` plus hosting contracts already own bounded shared payload rules; do not introduce a competing contract module |
| #288 dashboard News resources | STILL_VALID | News projection/build/page logic remains concentrated in Dashboard API, but must be extracted from current code |
| #289 dashboard market resources | STILL_VALID | Boundary remains valid; old code is stale because current main now has partition-bounded market history |
| #290 dashboard status resources | STILL_VALID | Fixed critical-status shaping remains a coherent owner, using current read-model and freshness contracts |
| #291 dashboard operator bridge | STILL_VALID | Local authenticated retry bridge remains independently testable; use current UTF-8 and loopback contracts |
| #292 dashboard Sync owners | PARTIALLY_VALID | Transport/progress extraction is valid, but old implementation predates deferred projection, runtime-root, learning-hash, and targeted Sync corrections |
| #293 Control Center stabilization | ALREADY_SUPERSEDED | PR is already merged and later Release Control changes supersede its exact implementation evidence |
| #294 annotator runtime owners | STILL_VALID | Script composition can move behind current scheduler/Daily Brief owner APIs |
| #295 collector/control owners | PARTIALLY_VALID | Both directions remain valuable, but Collector and Control Center must be split into separate PRs from current source |
| #296 decision/evidence packages | PARTIALLY_VALID | Canonical ownership is valid; the 81-file move is too broad and would carry the current SCC rather than resolve it |
| #297 training package | PARTIALLY_VALID | Training boundary remains valid; old patch predates materialization, crossfit-cache, and generation corrections |
| #298 News/AI packages | PARTIALLY_VALID | Owner separation is needed, but the 161-file stacked change and active/paused mixing are not acceptable current scope |
| #299 Assistant/runtime/dashboard packages | PARTIALLY_VALID | Runtime and Dashboard ideas remain; Assistant movement is low priority while execution stays PAUSED |
| #300 deterministic Control Center outcomes | ALREADY_SUPERSEDED | PR is already merged and current result/evidence behavior has evolved further |
| #301 test organization | PARTIALLY_VALID | Owner-oriented discovery is useful, but mass file moves do not improve runtime ownership and current CI selection has changed |
| #302 campaign closure | OBSOLETE | It closes a stack that was never adopted into current main and its screenshots/evidence are historical only |
| #304 private architecture explorer | OBSOLETE | A new production/admin UI is not required to establish source authority; current work starts with versioned repository docs and checks |
| #321 architecture compiler | PARTIALLY_VALID | Deterministic source extraction may be useful later; the 39-file compiler stack is not required for the first trustworthy map |
| #324 executable architecture evidence | PARTIALLY_VALID | Binding claims to tests is valid; add it incrementally as owner boundaries are extracted |
| #325 mutation audit | PARTIALLY_VALID | Bounded mutations may later measure critical contracts, but are not a prerequisite for current owner extraction |
| #328 evidence UI drill-down | OBSOLETE | Depends on the unselected Explorer/compiler stack; source and test evidence remain repository-owned |

## Recommended current-main sequence

1. Extract `StatusSnapshotCache` from Dashboard API without changing cache
   behavior. It is small, cohesive, and already covered by focused contracts.
2. Extract Dashboard health projection as a separate owner.
3. Extract current market resource reads, preserving partition-bounded history.
4. Extract Dashboard Sync transport and progress primitives, leaving scheduling
   and deferred-projection orchestration in the entry point until their own PR.
5. Split Control Center only by current provider/runtime/release interfaces;
   never revive the old 9,000-line aggregate patch.
6. Address the 14-module SCC with interface inversion, starting from durable
   scheduler/ledger protocols. Do not begin with package-directory moves.

The first extraction should reduce one mixed entrypoint while producing no
runtime, schema, payload, or production change.
