# Codebase Map

## How to use this reference

Use this page after the [System Architecture](../design/SYSTEM_ARCHITECTURE.md)
has identified the owning subsystem. “Start here” means the first source of
runtime truth to inspect, not permission to edit before reading the linked
contract.

## I want to change…

| I want to change… | Start here | Main owner | Supporting modules | Tests | Docs |
|---|---|---|---|---|---|
| Five-minute decision | `scripts/run_forward_collector.py` | Collector process loop | `xauusd_forecaster/decision/collector_runtime.py`, `xauusd_forecaster/decision/engine.py`, `xauusd_forecaster/decision/live.py`, `xauusd_forecaster/decision/inference.py` | `tests/decision/test_selection.py`, `tests/integration/test_forward_only.py` | [Decision and Evidence](../design/DECISION_AND_EVIDENCE.md) |
| Quote or session handling | `ctrader/XauusdForwardQuoteBridge/XauusdForwardQuoteBridge.cs` | Quote Bridge | `xauusd_forecaster/market.py`, `xauusd_forecaster/market_session.py` | `tests/test_quotes_and_labeling.py`, `tests/test_market_session.py` | [Forward-only Evidence](../contracts/FORWARD_ONLY.md) |
| Outcome labeling | `xauusd_forecaster/decision/live.py` | Collector settlement loop | `xauusd_forecaster/evidence/executable_label.py`, `xauusd_forecaster/execution_costs.py` | `tests/test_quotes_and_labeling.py`, `tests/test_execution_costs.py` | [System Boundaries](../contracts/SYSTEM_BOUNDARIES.md) |
| Training materialization | `xauusd_forecaster/training/materialization.py` | BackgroundTrainingOwner | `xauusd_forecaster/training/runtime.py`, `xauusd_forecaster/evidence/ledger.py` | `tests/training/test_owner.py`, `tests/evidence/test_integrity_v2.py` | [Training and Models](../design/TRAINING_AND_MODELS.md) |
| Model training or publish | `xauusd_forecaster/training/generation.py` | BackgroundTrainingOwner | `xauusd_forecaster/training/ridge.py`, `xauusd_forecaster/news/semantics/model_contracts.py` | `tests/evidence/test_integrity_v2.py`, `tests/runtime/test_production_shape.py` | [Training and Models](../design/TRAINING_AND_MODELS.md) |
| News collection | `xauusd_forecaster/news/collection/runtime.py` | NewsCollectionOwner | `xauusd_forecaster/news/collection/intake.py`, `xauusd_forecaster/news/collection/source_polling.py` | `tests/test_news_collection_owner.py`, `tests/test_source_polling.py` | [News and AI](../design/NEWS_AND_AI.md) |
| Annotation | `xauusd_forecaster/news/annotation/product.py` | Annotator scheduler | `xauusd_forecaster/news/semantics/contracts.py`, `scripts/run_news_annotator.py` | `tests/test_news_semantic_contract_v15.py`, `tests/news/test_critical_annotation_state.py` | [News Evidence](../contracts/NEWS_EVIDENCE.md) |
| Impact and event identity | `xauusd_forecaster/news/annotation/impact.py` | Annotator scheduler | `xauusd_forecaster/news/retrieval/identity.py`, `xauusd_forecaster/news/retrieval/event_identity.py` | `tests/test_news_event_identity.py`, `tests/test_news_hybrid_retrieval.py` | [News Evidence](../contracts/NEWS_EVIDENCE.md) |
| Scheduler or retry | `xauusd_forecaster/news/scheduler/state.py` | Durable scheduler state-transition owner | `xauusd_forecaster/news/scheduler/runtime.py`, `xauusd_forecaster/news/semantics/transitions.py`; entry script owns process/thread wiring | `tests/news/test_scheduler.py`, `tests/news/test_scheduler_transition_execution.py` | [AI Scheduler](../design/AI_PRIORITY_SCHEDULER.md) |
| Embedding or retrieval | `xauusd_forecaster/news/retrieval/search.py` | News identity retrieval owner | `xauusd_forecaster/news/retrieval/gemini_embeddings.py`, `xauusd_forecaster/news/annotation/impact.py` | `tests/test_gemini_embeddings.py`, `tests/test_news_hybrid_retrieval.py` | [News Identity Retrieval](../design/NEWS_IDENTITY_RETRIEVAL.md) |
| Daily Brief | `xauusd_forecaster/news/brief/product.py` | Daily Brief result/transition owner | `xauusd_forecaster/news/brief/runtime.py`, `xauusd_forecaster/news/scheduler/state.py` | `tests/news/test_daily_brief.py` | [Daily Brief](../contracts/DAILY_BRIEF.md) |
| Operational health | `xauusd_forecaster/runtime/operational_health.py` | Component health owners; alert/taxonomy aggregation remains here | `xauusd_forecaster/dashboard/health_projection.py`, `xauusd_forecaster/news/scheduler/health.py`, `xauusd_forecaster/runtime/health.py` | `tests/test_dashboard_health_projection.py`, `tests/runtime/test_operational_health.py`, `tests/runtime/test_runtime_health.py` | [Operational Health](../contracts/OPERATIONAL_HEALTH.md) |
| Dashboard first paint | `xauusd_forecaster/dashboard/status_resources.py` | Dashboard status-resource composition owner | `xauusd_forecaster/dashboard/payloads.py`, `xauusd_forecaster/dashboard/health_projection.py`, `xauusd_forecaster/dashboard/status_cache.py`; `scripts/run_dashboard_api.py` owns HTTP translation | `tests/test_dashboard_status_resources.py`, `tests/test_dashboard_payloads.py`, `tests/test_dashboard_health_projection.py`, `tests/test_dashboard_status_cache.py`, `tests/dashboard/test_api.py` | [Dashboard and Sync](../design/DASHBOARD_AND_SYNC.md) |
| Decision-output status | `xauusd_forecaster/dashboard/health_projection.py` | Dashboard runtime-component health projection owner | `scripts/run_dashboard_api.py`; decision evidence and broker session remain authoritative inputs | `tests/test_dashboard_health_projection.py`, `tests/dashboard/test_api.py` | [Operational Health](../contracts/OPERATIONAL_HEALTH.md) |
| Collector heartbeat presentation | `xauusd_forecaster/dashboard/health_projection.py` | Dashboard runtime-component health projection owner | `xauusd_forecaster/runtime/health.py` writes heartbeat state; `scripts/run_dashboard_api.py` orchestrates the payload | `tests/test_dashboard_health_projection.py`, `tests/runtime/test_runtime_health.py`, `tests/dashboard/test_api.py` | [Operational Health](../contracts/OPERATIONAL_HEALTH.md) |
| Audit, learning, or market detail | `xauusd_forecaster/dashboard/read_models.py` | DashboardReadModelOwner | `xauusd_forecaster/dashboard/summaries.py`, `xauusd_forecaster/dashboard/learning_curves.py` | `tests/dashboard/test_api.py` | [Paged Dashboard History](../design/PAGED_DASHBOARD_HISTORY.md) |
| Local news archive or evidence detail | `xauusd_forecaster/dashboard/news_resources.py` | Dashboard news-resource owner | Local SQLite evidence authority; `scripts/run_dashboard_api.py` owns HTTP translation | `tests/test_dashboard_news_resources.py`, `tests/dashboard/test_api.py` | [Dashboard and Sync](../design/DASHBOARD_AND_SYNC.md) |
| Local market history or current chart | `xauusd_forecaster/dashboard/market_resources.py` | Dashboard market-resource owner | Quote JSONL and local SQLite authority; `scripts/run_dashboard_api.py` owns HTTP translation | `tests/test_dashboard_market_resources.py`, `tests/dashboard/test_api.py` | [Dashboard and Sync](../design/DASHBOARD_AND_SYNC.md) |
| Dashboard resource serialization | `xauusd_forecaster/dashboard/resource_contracts.py` | Dashboard resource-contract owner | `xauusd_forecaster/dashboard/payloads.py`; API and Sync are consumers | `tests/test_dashboard_resource_contracts.py`, `tests/dashboard/test_sync.py` | [Dashboard and Sync](../design/DASHBOARD_AND_SYNC.md) |
| Local scheduler operator bridge | `xauusd_forecaster/dashboard/operator_bridge.py` | Dashboard operator-bridge service owner | `xauusd_forecaster/news/scheduler/state.py` retains transition authority; `scripts/run_dashboard_api.py` owns HTTP translation | `tests/test_dashboard_operator_bridge.py`, `tests/dashboard/test_api.py`, `tests/news/test_scheduler.py` | [Dashboard and Sync](../design/DASHBOARD_AND_SYNC.md) |
| Dashboard sync | `scripts/run_dashboard_sync.py` | Dashboard Sync process orchestration | `xauusd_forecaster/dashboard/sync/progress.py`, `xauusd_forecaster/dashboard/sync/transport.py`, `xauusd_forecaster/dashboard/sync/resource_protocols.py`, `xauusd_forecaster/dashboard/resource_contracts.py` | `tests/dashboard/test_sync.py` | [Dashboard and Sync](../design/DASHBOARD_AND_SYNC.md) |
| Cloudflare API routing | `web/worker/api-router.ts` | Minimal API Worker router | `web/worker/index.ts`, `web/db/schema.ts` | `web/tests/d1-capabilities.test.mjs`, `web/tests/worker-cpu-headroom.test.mjs` | [Web and Cloudflare](../design/WEB_AND_CLOUDFLARE.md) |
| Static Web UI | `web/app/_components/DashboardApp.tsx` | Web feature/view owners | `web/app/_components/DashboardShell.tsx`, `web/app/globals.css` | `web/tests/rendered-html.test.mjs`, `web/tests/responsive-scroll.test.mjs` | [Dashboard Presentation](../specs/DASHBOARD_PRESENTATION.md) |
| Private architecture navigation | `architecture/manifest.json` | Architecture manifest and private Explorer view | `web/build/architecture-manifest.ts`, `web/app/_lib/architecture-explorer.ts`, `web/app/_views/ArchitectureExplorerView.tsx` | `tests/test_architecture_manifest.py`, `web/tests/architecture-explorer.test.mjs` | [Architecture manifest](../../architecture/README.md), [Web and Cloudflare](../design/WEB_AND_CLOUDFLARE.md) |
| Preview | `scripts/build_preview_bundle.py` | Preview build owner | `web/app/_lib/preview-manifest.ts`, `web/app/_lib/preview-resources.ts` | `tests/dashboard/test_sync.py`, `web/tests/rendered-html.test.mjs` | [Preview Behavior](../specs/PREVIEW_BEHAVIOR.md), [Preview Isolation](../contracts/PREVIEW_ISOLATION.md) |
| Admin authentication | `web/app/_lib/admin-auth-session.ts` | Admin auth boundary | `web/app/chatgpt-auth.ts`, `web/app/admin/api/session/route.ts` | `web/tests/admin-auth-session.test.mjs`, `web/tests/assistant-auth.test.mjs` | [Assistant Security](../contracts/ASSISTANT_SECURITY.md) |
| Release control | `scripts/xauusd_control_center_release.ps1` | Exact installed Control Center release owner | `scripts/xauusd_control_center.ps1`, `scripts/install_control_plane.ps1`, `scripts/build_release_validation_fixtures.py`, `xauusd_forecaster/runtime/production_shape.py` | `tests/runtime/test_control_center_contracts.py`, `tests/runtime/test_control_plane_install.py`, `tests/runtime/test_release_validation_fixtures.py` | [Release Control](../contracts/RELEASE_CONTROL.md) |
| Runtime supervision | `scripts/xauusd_control_center_runtime.ps1` | Control Plane and Control Center runtime owner | `scripts/xauusd_control_center.ps1`, `scripts/install_control_plane.ps1`, `scripts/xauusd_watchdog_guard.ps1`, `xauusd_forecaster/runtime/health.py` | `tests/runtime/test_control_center_contracts.py`, `tests/runtime/test_control_plane_install.py`, `tests/runtime/test_runtime_health.py` | [Runtime and Release](../design/RUNTIME_AND_RELEASE.md) |
| Assistant retained architecture | `docs/design/ASSISTANT_ARCHITECTURE.md` | Assistant contracts; execution PAUSED | `web/db/schema.ts`, `xauusd_forecaster/assistant/agent.py` | `tests/assistant/test_agent.py`, `web/tests/assistant-chat.test.mjs` | [Assistant Status](../design/ASSISTANT_IMPLEMENTATION_STATUS.md) |
| Live broadcast delivery | `xauusd_forecaster/live_broadcast.py` | Optional Windows Stable publisher and isolated LiveHub transport | `scripts/run_live_broadcast_publisher.py`, `broadcast/src/index.js`, `web/app/_lib/live-broadcast.ts` | `tests/test_live_broadcast.py`, `broadcast/test/index.test.mjs`, `web/tests/live-broadcast.test.mjs` | [Live Broadcast Design](../design/LIVE_BROADCAST.md), [Live Broadcast Contract](../contracts/LIVE_BROADCAST.md) |

## Runtime entry-point index

| Entry point | Process/service | Owner | Starts what | Reads | Writes |
|---|---|---|---|---|---|
| `scripts/xauusd_control_center.ps1` | Exact child Control Center process | Stable CLI, Action and ServiceKey composition entry | Dot-sourced runtime, release and presentation owners; five core services, optional broadcast publisher and operator actions | Git/CI, Cloudflare Versions, config, health and release state | Process lifecycle, release state/history, structured operation results and runtime control files |
| `scripts/install_control_plane.ps1` | One-shot Control Plane installer | Control Plane installation owner | Exact detached `origin/main` installer and watchdog handoff | Git revision, verified bundle and current process identities | Installed runtime-control bundle and installation evidence; Business Runtime preserved |
| `ctrader/XauusdForwardQuoteBridge/run_live_quote_bridge.ps1` | cTrader CLI Quote Bridge process | Operator/Control Center | cTrader robot | CLI path and external secrets | Launch/process logs |
| `ctrader/XauusdForwardQuoteBridge/XauusdForwardQuoteBridge.cs` | cTrader robot in Quote Bridge process | Quote Bridge | Tick and timer callbacks | Broker-native symbol, Bid/Ask, session | Daily quote JSONL, atomic session heartbeat |
| `scripts/run_forward_collector.py` | Collector process | Collector | Decision loop, collection/training/heartbeat threads | Quote files, SQLite, models, config | Forecast evidence, outcomes, owner state, status, backups |
| `scripts/run_news_annotator.py` | Annotator process | Annotator process orchestration | CLI/config, account thread-pool construction, heartbeat, startup/shutdown, top-level cycles | SQLite jobs/revisions, provider config | Semantic results, attempts, quota, Briefs, status |
| `scripts/run_dashboard_api.py` | Dashboard API process | Local API/read-model owner | ThreadingHTTPServer, read-model owner, cache refresh | SQLite, quote/status/release files | Derived read models and audited local retry overrides |
| `scripts/run_dashboard_sync.py` | Dashboard Sync process | Sync owner | Heartbeat loop and control/heavy lanes | Local APIs, target config, cursor/schedule state | D1 mirrors, cursor/schedule state, sync status |
| `scripts/run_live_broadcast_publisher.py` | Optional live publisher process | Windows Stable broadcast publisher | Bounded periodic publish cycle | Local public projection, local acknowledgement, pinned broadcast health | Authenticated latest-state delivery and atomic acknowledgement |
| `broadcast/src/index.js` | Isolated broadcast Worker / LiveHub Durable Object | Public live transport owner | Publish, health and hibernating WebSocket requests | Authenticated bounded live state | Latest state and minimal monotonic sequence metadata |
| `web/worker/index.ts` | Cloudflare Worker | Worker dispatch owner | Request dispatch and asset fallback | Request, deployment metadata, bindings | Response/observability only; selected route may write |
| `web/worker/api-router.ts` | Worker request router | API router | Selected API module | D1 and route bindings | Authenticated route-specific D1 writes |
| `scripts/xauusd_watchdog_guard.ps1` | Watchdog guard control process | Guard owner | Hidden watchdog replacement | Process/control state | Guard/watchdog lifecycle |
| `scripts/initialize_u5_warmup.py` | One-shot maintenance command | U5 initialization owner | No child service | Historical warm-up source | Versioned local U5 state only |
| `scripts/run_evidence_repair_v2.py` | One-shot maintenance command | Repair owner | No child service | Retained immutable evidence | Append-only repaired evidence and receipts |
| `scripts/backfill_news_identity_embeddings.py` | One-shot maintenance command | Embedding repair owner | No child service | Local revisions/embeddings and credentials | Missing embeddings, leases and accounting |
| `scripts/prune_unused_news.py` | One-shot maintenance command | News maintenance owner | No child service | Local news evidence | Explicit audited prune set |
| `scripts/build_preview_bundle.py` | One-shot build command | Preview bundle owner | No runtime service | Bounded source snapshots | Immutable Preview build bundle |
| `scripts/build_release_validation_fixtures.py` | One-shot release validation | Release validation owner | No runtime service | Candidate checkout/config | Preflight fixtures only |
| `scripts/check_production_shape.py` | One-shot check | Production shape validator | No runtime service | Candidate local state | Check output only |
| `scripts/check_public_health.py` | One-shot/scheduled check | Public health validator | No runtime service | Public API | Check output only |

## State-store index

| Store | Authority or mirror | Writers | Readers | Growth model |
|---|---|---|---|---|
| `.local/forward/forward-evidence.sqlite3` | Complete local forecasting evidence authority plus mutable owner state | Collector, collection owner, Annotator, training owner, read-model owner, audited scheduler transition | All local runtime/read owners | Append-only evidence plus bounded mutable queues/projections; WAL |
| Daily quote JSONL under `.local/forward/` | Broker quote authority | Quote Bridge | Collector, market builders | Append-only by UTC day; completed days archived |
| `.local/forward/market-session.json` | Current broker session authority | Quote Bridge | Collector, Dashboard | Atomic replace; constant-size current state |
| Local model directories under `.local/forward/` | Immutable artifact authority paired with activation row | BackgroundTrainingOwner | Decision/inference, Dashboard metadata | Versioned generations; no in-place model rewrite |
| Per-service status JSON under `.local/forward/` | Runtime heartbeat authority for that service | Owning process | Watchdog, Dashboard API | Atomic replace; constant-size current state |
| Dashboard read-model tables in local SQLite | Replaceable derived read models | DashboardReadModelOwner | Dashboard API | One current generation per resource plus bounded metadata |
| Per-target sync state files under `.local/forward/` | Sync progress/checkpoint authority | Dashboard Sync | Dashboard Sync, operator diagnostics | Constant number of resources/targets; cursors advance |
| Cloudflare D1 dashboard tables | Public mirror/projection | Authenticated sync/ingest routes | Public Worker routes/browser | Replaceable snapshots plus indexed bounded/paged histories |
| LiveHub Durable Object storage | Public live delivery state, not forecast authority | Isolated broadcast Worker after publisher authentication | Browser subscribers and health checks | One bounded latest state plus minimal monotonic sequence metadata |
| Cloudflare D1 operator request/event tables | Remote command lifecycle authority until local transition/ack | Authenticated admin routes and worker | Sync, admin UI | Append/indexed requests and events with bounded claims |
| Cloudflare D1 Assistant tables | Retained canonical Assistant authority | Authenticated Assistant routes/workers; new execution PAUSED | Admin Assistant routes/UI | Immutable messages/events plus bounded mutable jobs |
| Vectorize Assistant index | Derived retained Assistant memory index | Assistant index worker, PAUSED | Assistant retrieval, PAUSED | Versioned vectors; never canonical conversation authority |
| Cloudflare Version metadata | Immutable Worker artifact identity | Cloudflare build/upload | Control Center release validation and runtime | Append-only Versions; placement is separate current state |
| Local release-control state/history | Stable/Candidate transition authority | Control Center/Watchdog | Operator UI, Dashboard, recovery | Constant-size current identities plus append-only bounded history/projections |

`xauusd_forecaster/shared_store_schema.py` is the explicit composition boundary
that invokes each co-resident SQLite owner's schema installer in the preserved
historical order. It owns no schema or data itself.

## Package/module index by subsystem

### Decision and evidence

- `xauusd_forecaster/decision/engine.py` — coordinates one frozen decision and
  later outcome settlement.
- `xauusd_forecaster/evidence/ledger.py` — installs and exposes the shared local
  append-only persistence surface.
- `xauusd_forecaster/market.py` — reads receipt-time quote files and builds
  point-in-time market snapshots.
- `xauusd_forecaster/market_session.py` — applies broker and weekly closure
  eligibility.
- `xauusd_forecaster/decision/live.py` — appends the complete V2 decision and outcome
  evidence sets.
- `xauusd_forecaster/decision/inference.py` — validates and consumes one complete
  active generation.
- `xauusd_forecaster/evidence_v2.py` — materializes versioned derived evidence
  and eligibility.
- `xauusd_forecaster/execution_learning.py` — owns exit checkpoint predictions
  and execution-learning artifacts.

### News and AI

- `xauusd_forecaster/news/collection/runtime.py` — owns collection-thread cadence
  and snapshot reporting.
- `xauusd_forecaster/news/collection/intake.py` — implements registered source adapters and
  collection/intake behavior.
- `xauusd_forecaster/news/scheduler/state.py` — owns durable AI jobs, account capacity,
  provider governor, retries, and operator overrides.
- `xauusd_forecaster/news/scheduler/runtime.py` — owns scheduled job dispatch,
  account/model routing, durable transition orchestration, and lock retry.
- `xauusd_forecaster/news/brief/runtime.py` — owns the bounded Daily Brief
  backlog cycle over scheduler-owned routine capacity.
- `xauusd_forecaster/news/annotation/product.py` — executes structured annotation, impact and
  title model operations.
- `xauusd_forecaster/news/semantics/contracts.py` — validates current semantic structure.
- `xauusd_forecaster/news/annotation/impact.py` — validates impact and prepares prior-event
  candidate context.
- `xauusd_forecaster/news/retrieval/search.py` — owns hybrid retrieval, embedding
  prerequisite and progress receipts.
- `xauusd_forecaster/news/semantics/input_coverage.py` — freezes decision-time news
  availability without consulting later health.
- `xauusd_forecaster/news/brief/product.py` — owns date-scoped synthesis, refresh,
  finalization, retry and fallback.

### Training and models

- `xauusd_forecaster/training/runtime.py` — owns background claim/lease/recovery.
- `xauusd_forecaster/training/generation.py` — owns dirty materialization, due checks,
  fits, manifests and generation publication.
- `xauusd_forecaster/training/materialization.py` — provides shared market/news fit operations.
- `xauusd_forecaster/training/ridge.py` — defines the regularized linear artifact.
- `xauusd_forecaster/news/semantics/model_contracts.py` — defines the required generation
  membership and active news contract.

### Dashboard and sync

- `xauusd_forecaster/dashboard/resource_contracts.py` — owns deterministic
  learning/news/market/audit projections and exact serialized-byte bounds.
- `xauusd_forecaster/dashboard/news_resources.py` — owns local news archive
  reads, event-evidence resource generation/manifest, page cache, and metrics.
- `xauusd_forecaster/dashboard/market_resources.py` — owns quote-file parsing
  cache, market history/paging, decision reads, and chart projection.
- `xauusd_forecaster/dashboard/status_resources.py` — owns current status and
  optional-resource composition over existing evidence and runtime inputs.
- `xauusd_forecaster/dashboard/operator_bridge.py` — owns local authorization,
  retry-job response, and bounded delegation to scheduler transitions.
- `xauusd_forecaster/dashboard/sync/progress.py` — owns Sync checkpoint/status
  persistence and per-resource cadence/backoff.
- `xauusd_forecaster/dashboard/sync/transport.py` — owns authenticated local and
  remote transport plus target isolation.
- `xauusd_forecaster/dashboard/sync/resource_protocols.py` — owns per-resource
  mirror protocols, bounded page advancement, and acknowledgements.
- `xauusd_forecaster/dashboard/health_projection.py` — owns read-only
  component projections for semantic health, Collector heartbeat, and Decision
  cadence; it owns no source authority, cache, or operational alert taxonomy.
- `xauusd_forecaster/dashboard/status_cache.py` — owns the disposable
  process-local serialized first-paint/readiness snapshot, bounded last-good,
  single-flight refresh, and cache health; it is not forecast authority.
- `xauusd_forecaster/dashboard/payloads.py` — defines bounded critical and audit
  payload contracts.
- `xauusd_forecaster/dashboard/read_models.py` — owns per-resource background
  builds, source revisions, hashes and last-good replacement.
- `xauusd_forecaster/dashboard/summaries.py` — provides indexed aggregate reads.
- `xauusd_forecaster/dashboard/learning_curves.py` — builds learning summaries and pages.
- `xauusd_forecaster/runtime/operational_health.py` — projects component incidents and
  bounded status.
- `scripts/run_dashboard_api.py` — owns local HTTP/process orchestration.
- `scripts/run_dashboard_sync.py` — owns CLI, heartbeat-first cycle, lane
  lifecycle, retry orchestration, and structured top-level logging.

### Web and Cloudflare

- `web/worker/index.ts` — dispatches assets, API, selected SSR fallback and
  observability.
- `web/worker/api-router.ts` — maps a request to one API module and fast-paths
  status/ingest snapshots.
- `web/db/schema.ts` — declares D1 state ownership and indexes.
- `web/app/_components/DashboardApp.tsx` — composes the client dashboard shell.
- `web/app/_views/AuditView.tsx` — owns the main audit experience but remains a
  large shared feature surface.
- `web/app/audit/LearningGraphModal.tsx` — owns detailed learning exploration.
- `web/app/globals.css` — holds the shared visual system and many feature rules.

### Runtime, release and Assistant

- `xauusd_forecaster/runtime/health.py` — atomically refreshes service heartbeat
  files from a dedicated pulse thread.
- `xauusd_forecaster/runtime/production_shape.py` — validates Candidate runtime/model
  shape without activating it.
- `scripts/xauusd_control_center.ps1` — owns supervision, Candidate validation,
  release transactions, deterministic structured operation results, recovery,
  WPF/WinForms lifecycle, and operator UI.
- `scripts/install_control_plane.ps1` — owns exact-revision Control Plane
  installation, complete bundle verification, watchdog handoff, and rollback.
- `xauusd_forecaster/assistant/agent.py` — retained bounded agent implementation;
  no local worker currently invokes it.
- `xauusd_forecaster/assistant/routing.py` — retained provider-independent task
  routing contract.
- `web/app/api/_shared/assistant-chat.ts` — retained D1 Assistant chat state and
  request contract; new execution admission is PAUSED.

## Status vocabulary

- `CURRENT`: in audited `main`.
- `PENDING`: open PR only; identify its exact head separately from this audited
  current map.
- `TARGET`: proposed by a plan or target architecture.

Never use a `PENDING` or `TARGET` file tree to navigate a `CURRENT` incident.
