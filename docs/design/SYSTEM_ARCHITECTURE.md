# XAUUSD Forecaster System Architecture

## How to read this map

XAUUSD Forecaster is a Windows-hosted research system that appends one
`LONG`, `SHORT`, or `WAIT` forecast every five minutes and settles the fixed
30-minute executable Bid/Ask outcome later. It publishes bounded, derived
views through Cloudflare. It is **SHADOW RESEARCH ONLY** and has **NO
ORDER-SUBMISSION AUTHORITY**.

Start with Level 0, then use the execution topology, data flows, subsystem
table, and detailed maps. The [Codebase Map](../reference/CODEBASE_MAP.md) is the
last step from an owner to code.

- `CURRENT`: present in the audited `main` code and authoritative documents.
- `PENDING`: present only in an open pull request; it is not current runtime.
- `TARGET`: a proposal or plan; it is not current runtime.

**Audited revision:** `55593c05b3f47697a21b46cfa59a0cb228b1b035`

**Last verified:** 2026-08-25

Shared live broadcast, production-shaped Preview behavior, and system-level
review discipline are present in the audited `main`. Repository presence does
not imply external activation: the broadcast publisher remains an optional
service and requires explicit configuration and coordinated bootstrap.

## Level 0 — System context

```text
[cTrader quote source]          [News sources]       [AI providers]
          |                           |                     |
          +-------------+-------------+---------------------+
                        v
          [Windows Forecaster Runtime: PROCESS group]
                        |
                        v
        [Local JSONL + SQLite evidence + model artifacts]
                        |
              +---------+----------+
              v                    v
     [Dashboard API]       [Dashboard Sync]
                                   |
                                   v
        [Cloudflare STATIC assets + API WORKER + D1 mirror]
                                   |
                                   v
                         [Browser / Operator]

[Optional Windows Broadcast Publisher]
          -> [isolated Broadcast WORKER + LiveHub DURABLE OBJECT]
          -> [Browser live transport; bounded HTTP fallback remains]

SHADOW RESEARCH ONLY — NO ORDER-SUBMISSION AUTHORITY
```

Static public pages are prerendered assets. They are not React SSR requests on
every page load. API calls enter a minimal Worker router that loads the selected
route module.

## Level 1 — Execution topology

```text
[Control Plane: PROCESS / scheduled CONTROL]
  owns the installed runtime-control bundle, watchdog handoff and supervision
  |
  +-- [Control Center: exact child PROCESS]
  |     owns local service supervision, Candidate validation, Promote/Reverse
  |     and structured operation-result presentation
  |
  +-- [cTrader CLI + Quote Bridge robot: PROCESS]
  |     writes daily quote JSONL and atomic market-session.json
  |
  +-- [Collector: PYTHON PROCESS]
  |     [main THREAD] decision append, outcome settlement,
  |                   exit checkpoint work, daily backup/archive
  |     [THREAD] NewsCollectionOwner
  |     [THREAD] BackgroundTrainingOwner (separate SQLite connection)
  |       [temporary THREAD] training lease keeper while a job runs
  |     [THREAD] runtime heartbeat pulse
  |
  +-- [Annotator: PYTHON PROCESS]
  |     [main THREAD] durable scheduler and Daily Brief cycle
  |     [THREAD pool lanes] bounded per-account AI work
  |     [THREAD] runtime heartbeat pulse
  |
  +-- [Dashboard API: PYTHON PROCESS]
  |     [REQUEST THREADS] ThreadingHTTPServer
  |     [THREAD] DashboardReadModelOwner
  |     [bounded refresh THREADS] critical-status cache
  |     local retry-override POST is an audited write path
  |
  +-- [Dashboard Sync: PYTHON PROCESS]
  |     [main THREAD] critical heartbeat publication
  |     [THREAD] control-resource lane
  |     [THREAD] heavy-resource lane (at most one heavy resource per cycle)
  |
  +-- [Live Broadcast Publisher: optional PYTHON PROCESS]
        publishes one bounded current-state projection when explicitly enabled

[Cloudflare Static Assets: STATIC]
[Cloudflare API Router: WORKER]
[Public snapshots/history: D1 MIRROR]
[Live Broadcast service: isolated WORKER]
[LiveHub latest-state transport: DURABLE OBJECT]
[Assistant canonical retained state: D1 AUTHORITY, PAUSED]
[Assistant memory index: Vectorize DERIVED, PAUSED]
```

Decision does not synchronously wait for training. Decision and training still
share one OS process boundary. The Annotator's account lanes and the Dashboard
Sync lanes are threads, not independent services.

### Other production and operator entry types

- HTTP request owners exist in the local Dashboard API and Cloudflare route
  modules under `web/app/api/` and `web/app/admin/api/`.
- `scripts/xauusd_watchdog_guard.ps1` and the VBS launchers keep the watchdog
  hidden and supervised.
- One-shot maintenance commands include U5 initialization, evidence repair,
  embedding backfill, unused-news pruning, Preview bundle creation, production
  shape checks, public-health checks, and release-validation fixture creation.
- GitHub Actions validate code and public health. They are not a deployment or
  release authority. Repository/GitHub reads retry only bounded transient
  transport, rate-limit and server failures; deterministic provenance,
  authentication, permission and reachability failures remain fail-closed.

## Level 2 — Main data flows

### 1. Quote to decision

```text
cTrader Tick callback
  -> daily append-only Bid/Ask JSONL + atomic market-session heartbeat
  -> JsonlMarketProvider receipt-time cutoff
  -> ForwardEngine freezes market/news/model evidence
  -> Collector appends decision_events and complete prediction identities
```

The five-minute path reads the latest valid active generation. It does not wait
for training, annotation, Dashboard, sync, Cloudflare, or an LLM.

### 2. Decision to outcome

```text
decision_id + post-decision quotes
  -> first executable entry within contract
  -> first executable terminal quote after the fixed 30-minute hold
  -> append-only outcome and score records
  -> durable background-training request
```

Outcome append never mutates its decision.

### 3. Outcome to model publication

```text
new mature outcome
  -> training_materialization_dirty_v1
  -> bounded materialization page (200 source rows)
  -> durable materialized_training_rows_v1
  -> early NOT_DUE when retrain cadence is unmet
  -> full materialized dataset consumption when a real retrain is due
  -> versioned artifact set + manifest
  -> atomic complete-generation activation record
```

Incremental materialization is bounded. A real retrain is not claimed to be
constant-time.

### 4. News to decision-time evidence

```text
NewsCollectionOwner THREAD
  -> source polls + immutable news/macro revisions in local SQLite
  -> Annotator PROCESS durable scheduler
  -> annotation -> impact -> event identity / title / display
  -> point-in-time coverage and event snapshots frozen by Collector
  -> decision-time news features
```

Collection and annotation are different execution boundaries. Embedding
prerequisite/backfill, provider quota/governor work, and Daily Brief also belong
to the Annotator process.

### 5. Local authority to browser

```text
local SQLite AUTHORITY
  -> DashboardReadModelOwner builds audit/learning/market summaries off-request
  -> atomic per-resource derived read models with source revisions and hashes
  -> Dashboard API critical cache + lazy/paged resources
  -> Dashboard Sync heartbeat lane + bounded optional pages/batches
  -> D1 public MIRROR
  -> minimal Worker router or STATIC asset
  -> browser
```

One read-model resource failure retains its last-good model and cannot
invalidate critical status. Public D1 is not forecasting recovery authority.

### 6. Source revision to Stable

```text
Git main revision
  -> immutable Cloudflare Version upload
  -> local Candidate discovery
  -> exact identity, CI, platform, API, data and Windows preflight validation
  -> explicit local Control Center Promote
  -> coordinated Worker placement + Windows observation
  -> Stable commit in release-control state
```

Push, PR merge, and movement of `main` never change Stable. Reverse Stable is
the normal rollback path.

### 7. Assistant retained and paused path

```text
Admin/Assistant UI and route code: RETAINED
  -> owner-scoped D1 conversations/messages/jobs: RETAINED AUTHORITY
  -> Assistant chat admission: PAUSED and fail closed
  -> local Assistant worker: REMOVED
  -> title, compaction and memory indexing workers: PAUSED
  -> Vectorize memory generation: RETAINED DERIVED state, not active indexing
  -> future API-model activation: TARGET
```

News identity retrieval remains active and uses Gemini Embedding 2. It is not
the paused Assistant memory-indexing path.

| Status | Assistant-related scope |
|---|---|
| `ACTIVE` | News event-identity retrieval through Gemini Embedding 2; this is a forecasting news subsystem, not Assistant memory. |
| `PAUSED` | Assistant chat, Q&A execution, title generation, compaction, and memory indexing; new admission fails closed. |
| `RETAINED` | Private UI/routes, provider-independent contracts, D1 conversations/messages/jobs and audit history, and the Vectorize binding/index contract. The local Assistant worker is removed. |
| `TARGET` | One explicit API-model activation that validates chat, derived jobs, capacity, migrations, and one complete memory-index generation together. |

### 8. Live broadcast

```text
bounded local public status projection
  -> optional Windows publisher (about 30-second cadence)
  -> authenticated PUBLIC_LIVE_V1 publish
  -> isolated aurum-live-broadcast WORKER
  -> singleton LiveHub DURABLE OBJECT latest-state authority
  -> browser LIVE_PUSH
  -> bounded /api/status HTTP fallback on failure or staleness
```

The transport is current repository architecture but optional operationally.
It has no forecast, evidence, or release authority; failed or disabled
broadcast leaves collection and the bounded HTTP path intact. Lifecycle
bootstrap and publisher activation remain explicit operator actions.

## Source-of-truth table

| State | Authoritative owner | Authoritative store | Other copies | Allowed writers |
|---|---|---|---|---|
| Broker quotes | Quote Bridge robot | Daily local JSONL; current session fact in `market-session.json` | Aggregated candles and dashboard projections | Quote Bridge only |
| Decisions | Collector decision loop | Local `forward-evidence.sqlite3`, append-only decision tables | Read models, D1 snapshots, UI | Collector only |
| Outcomes and scores | Collector settlement loop | Local SQLite append-only outcome/score tables | Training materialization, dashboard projections | Collector only |
| News and macro revisions | NewsCollectionOwner | Local SQLite immutable revision/poll tables | Semantic jobs, bounded public news mirrors | News collection owner only |
| Annotation, impact, title and scheduler jobs | Annotator scheduler | Local SQLite durable queues, attempts, results and governor state | Dashboard health and public bounded detail | Annotator; audited retry transition through its scheduler contract |
| Daily Brief revisions | Annotator Daily Brief owner | Local SQLite append-only revisions/finalizations plus mutable refresh state | D1 display snapshots | Daily Brief owner only |
| Model artifacts | BackgroundTrainingOwner | Immutable versioned files under local model roots | Manifests and dashboard metadata | Background training owner only |
| Active model generation | BackgroundTrainingOwner publication step | Local SQLite complete-generation activation record plus matching artifacts | Decision receipts and dashboard metadata | Background training owner only; promotion remains manual where required |
| Training materialization | BackgroundTrainingOwner | Local SQLite materialized rows, dirty revisions and cursor/state row | In-memory full dataset during due retrain | Background training owner; source triggers only mark dirty |
| Runtime heartbeat | Each supervised service | Per-service atomic local status JSON | Dashboard critical projection and watchdog state | Owning service only |
| Dashboard read models | DashboardReadModelOwner | Local SQLite derived model tables | In-memory/API response | Dashboard read-model owner only; replaceable derived state |
| Public D1 snapshots/history | Dashboard Sync and authenticated Worker ingest routes | Cloudflare D1 mirror/projection tables | Browser caches | Authenticated sync/ingest owners only |
| Public live delivery state | Windows Stable broadcast publisher; transport owned by isolated broadcast Worker/LiveHub | Singleton Durable Object latest state and minimal sequence metadata | Browser tab cache; `/api/status` remains fallback | Explicitly enabled Windows Stable publisher only |
| Operator retry commands | Authenticated operator route, then scheduler transition owner | D1 request/event records for remote command delivery; local SQLite scheduler rows for execution | UI status and sync acknowledgements | Authenticated admin routes and the audited local scheduler override path |
| Stable/Candidate release identity | Local Control Center release owner | Local release-control state/history coordinated with immutable Cloudflare Version metadata | Dashboard release status | Explicit Control Center validation, Promote, Reverse, and recovery actions only |
| Assistant conversations/messages/jobs | Forecaster Assistant route contract | Owner-scoped D1 canonical state | UI caches; Vectorize is a derived index | Authenticated Assistant routes; new chat admission currently PAUSED |
| Assistant memory vectors | Assistant indexing contract | Vectorize derived generation | D1 index receipts | Indexing worker, currently PAUSED |

The complete forecasting evidence authority is local SQLite. D1 contains public
read projections and separate retained Assistant authority; those roles must
not be collapsed.

## Subsystem summary

| Subsystem | Ownership | Boundary | Critical Path | Bounded Work | Incremental | Failure Isolation |
|---|---|---|---|---|---|---|
| Decision and evidence | Collector | Collector process/main thread; local SQLite authority | Five-minute decision and later outcome append | One appended decision/outcome is fixed; restart catch-up iteration lacks an item cap | Append-only clock, decision IDs and checkpoints | Provider/UI/training failure does not stop a valid market decision |
| News and AI | Collection owner plus Annotator scheduler | Collection thread inside Collector; separate Annotator process | Only already-frozen valid evidence enters decision | Bounded source polls, scheduler jobs, account lanes and Brief backlog | Revision IDs, durable jobs, leases, retry times | Annotation/provider failure degrades news; market-only decision work continues |
| Training and models | BackgroundTrainingOwner | Thread and separate SQLite connection inside Collector process | Last valid generation is read by Decision | 200-row materialization page; retrain cadence gate | Dirty revisions, cursor, materialized rows, generations | Training error retries in background; shared Collector process remains a gap |
| Dashboard and sync | API/read-model owner and Sync owner | Two Python processes; request threads and sync lanes | Critical status/heartbeat only | 90-minute decision view; page/item/byte bounds; one heavy resource/cycle | Source revisions, cursors, per-target schedule state | Optional resource failure retains last-good and does not stop heartbeat |
| Web and Cloudflare | Static build, minimal API router, and isolated live transport | Static asset, request Workers, D1, private routes and LiveHub Durable Object | Bounded `/api/status` first paint; optional live push | Route-specific bytes/pages/D1 operations; 16,384-byte live state | D1 indexed ledgers, lazy pages and monotonic live sequence | Cloudflare or broadcast failure does not stop local evidence collection; HTTP fallback remains |
| Runtime and release | Control Center/Watchdog | Local control processes plus Cloudflare Version control plane | Service liveness and explicit Stable identity | Bounded checks, observation windows and diagnostics | Candidate watermark, transaction state and history | Failed Candidate retains Stable; Reverse is explicit |
| Assistant | Forecaster Assistant contracts | D1/private routes retained; local worker removed | None while PAUSED | Bounded turns, tools, pages, leases in retained contract | Message/event sequence, summaries and index generations | Paused admission fails closed without affecting forecasts |

### Operating characteristics

| Subsystem | Cadence / SLA | Inputs | Outputs | Durable state | Execution type | Current known gap |
|---|---|---|---|---|---|---|
| Decision and evidence | 5-minute decision; 30-minute outcome | Quotes, session, frozen news, active model | Decisions, predictions, outcomes, checkpoints | Local JSONL, SQLite, U5/checkpoint files | PROCESS main THREAD | Canonical Decision and Evidence packages; stable legacy imports are thin facades |
| News and AI | Collection poll and scheduler retry times; provider-governed | Registered sources, revisions, credentials | Revisions, annotations, impacts, titles, Briefs | Local SQLite | Collector THREAD + Annotator PROCESS/thread pool | Shared SQLite and large scheduler/annotation modules |
| Training and models | Requested on outcomes/reconciliation; early `NOT_DUE` | Mature eligible rows | Materialized rows and complete generation | SQLite plus immutable files | Collector THREAD | Scheduling isolated, process failure domain shared |
| Dashboard and sync | Critical cache seconds; sync heartbeat; resource-specific cadence | Local authority/read models | Local API and D1 mirrors | SQLite plus target cursor/schedule files | Two PROCESSES with THREADS | Large entry scripts own both orchestration and domain logic |
| Web and Cloudflare | Request-driven and static build | Static bundle, D1, auth | HTML/assets/API JSON/admin state | D1, Vectorize, build artifacts, metadata | STATIC + WORKER + REQUEST HANDLERS | Large shared CSS/views and many route-adjacent owners |
| Runtime and release | Watchdog cycles and explicit operator actions | Git revision, CI, Cloudflare Versions, health | Stable/Candidate state and service lifecycle | Local control state/history, Cloudflare metadata | CONTROL PROCESSES / one-shot actions | Stable entry path loads separate same-process supervision, release and presentation owners |
| Assistant | PAUSED | Authenticated owner request | Fail-closed paused status; retained reads | D1 and retained Vectorize | REQUEST HANDLERS; no local worker | Implementation remains retained while activation is intentionally absent |

## Current gaps

These findings are evidenced by the audited code. They are not repair work in
this baseline.

1. Training is thread-isolated and uses a separate connection and durable
   lease, but it is not process-isolated from Decision.
2. The flat `xauusd_forecaster` package hides subsystem ownership. A static AST
   audit found one large strongly connected import group across ledger,
   scheduler, annotation, news, training-adjacent, and Assistant support code.
3. The remaining large entry points retain process orchestration while their
   extracted package or dot-sourced owners hold resource, scheduler, release,
   supervision and presentation behavior.
4. Large production files span multiple responsibilities. Size alone is not
   the reason to split them; owner mixing, fan-in/fan-out, critical-path risk,
   change frequency, and rollback difficulty are the reasons to investigate.
5. Web feature ownership is partly organized into views, libraries and routes,
   but `globals.css`, `AuditView.tsx`, and `LearningGraphModal.tsx` remain large
   shared change surfaces.
6. Several test modules group thousands of lines of cross-domain contracts.
   Future organization must preserve family-level invariants rather than mirror
   production files mechanically.
7. Current architecture facts were previously spread across contracts, code,
   and historical PR descriptions. PR descriptions and historical audits remain
   evidence or clues, not current architecture authority.
8. Collector restart scans five-minute candidate boundaries from the last
   persisted decision to the current boundary without an explicit item cap.
   Missed non-live grids are skipped and never fabricated, but one restart
   operation still grows with downtime.
9. The lazy local market-history request returns a 500-item page, but constructs
   its ordered source from all quote-history files. Archive parsing is cached
   and the live file advances by byte offset, yet per-request file enumeration
   and history merging still grow with total retained quote history.

## Drill-down maps

- [Decision and Evidence](DECISION_AND_EVIDENCE.md)
- [News and AI](NEWS_AND_AI.md)
- [Training and Models](TRAINING_AND_MODELS.md)
- [Dashboard and Sync](DASHBOARD_AND_SYNC.md)
- [Web and Cloudflare](WEB_AND_CLOUDFLARE.md)
- [Runtime and Release](RUNTIME_AND_RELEASE.md)
- [Assistant Architecture](ASSISTANT_ARCHITECTURE.md) and
  [Assistant Implementation Status](ASSISTANT_IMPLEMENTATION_STATUS.md)
- [Codebase Map](../reference/CODEBASE_MAP.md)
- [Architecture Rules](../contracts/ARCHITECTURE_RULES.md)
- [Repository Modularization Plan](../plans/REPOSITORY_MODULARIZATION.md)
