# XAUUSD Forecaster System Architecture

## How to use this map

This is a current design map, not a release receipt. Start at Level 0, select
an owner at Level 1, follow its flow at Level 2, then use the Level 3 table to
find code and tests. Normative boundaries are in
[Architecture rules](../contracts/ARCHITECTURE_RULES.md).

The system publishes `LONG`, `SHORT`, or `WAIT` research forecasts on a fixed
five-minute clock and later records a fixed 30-minute executable Bid/Ask
outcome. It is Shadow research only and has no order-submission authority.

## Level 0 — System context

```text
cTrader quotes + broker session       News sources + AI providers
                 \                     /
                  Windows Business Runtime
                             |
          daily quote files + local SQLite + model artifacts
                    /                    \
          local Dashboard API       Dashboard Sync
                                             |
                     Cloudflare static assets + Worker + D1
                                             |
                                      browser / operator

optional bounded live publisher -> isolated broadcast Worker / LiveHub

local SQLite = forecasting authority
D1 = bounded public mirror, not forecasting recovery authority
```

## Level 1 — Runtime owners

| Owner | Boundary | Critical path | Bounded work | Incremental state | Failure and recovery |
|---|---|---|---|---|---|
| Control Plane / Control Center | Hidden Windows control processes plus immutable Cloudflare Versions; one bounded read-only runtime read model | Explicit Candidate, Promote, Switch, Observe, Reverse | Time-bounded checks and single-flight provider observation; 15 receipt-keyed evidence nodes | Release state/history, behavior keys, renewable leases; provider caches and the read model add no authority | Stable remains owner until observation; artifact availability, placement, and Reverse eligibility remain separate |
| Quote Bridge | cTrader process | Broker-native Bid/Ask and session facts | One append per tick; one current session file | Daily UTC quote partitions | Collector fails closed to `WAIT` when quote/session authority is stale |
| Collector | Python process, main decision loop plus owned background threads | Five-minute decision and later outcome append | Quote lookback 61 minutes; restart grid detail at most the live window; bounded maintenance pages | Decision clock, append-only IDs, checkpoints, training requests | Optional News/training/display failures do not invent a decision |
| News collection | Collector-owned thread with separate source cadence | Immutable source revisions available to future decisions | Registered bounded source polls | Poll/revision identities | Failure degrades News availability without stopping market evidence |
| Annotator | Separate Python process with bounded account lanes | Durable semantic jobs and Daily Brief work | Bounded claims, leases, retries, and lane capacity | Scheduler jobs, attempts, quotas, Brief revisions | Last valid semantics remain; deterministic failures are visible |
| Background training | Collector-owned thread with separate SQLite connection | Publishes complete immutable model generations | 200-row materialization pages; real retrain only when due | Dirty revision, cursor, cache and generation activation | Last complete generation stays active on failure |
| Dashboard read-model owner / API | Python process, background producer plus request threads | Fixed critical status and local operator surface | 90-minute critical window; partitioned/paged detail resources | Per-resource source revision, hash and last-good generation | Failed rebuild retains last-good resource and cannot block status |
| Dashboard Sync | Separate Python process with serial authoritative writer | Critical heartbeat and authenticated D1 projection | Item/byte batches; one bounded heavy lane; deterministic hash delta | Per-target cursors, schedules, projection receipts | Failed optional resource is isolated; exact deferred request can resume |
| Cloudflare Worker | Request boundary plus static asset service | Bounded public APIs and authenticated ingest | Route-specific query/subrequest/byte limits | D1 projection identities and immutable Worker Version | No local evidence authority; last-good projection stays readable |
| Live broadcast | Optional Windows publisher plus isolated Worker/DO | Low-latency current status only | One bounded latest state | Monotonic sequence and acknowledgement | HTTP status remains fallback; no forecast/release authority |
| Assistant contracts | Private routes and retained provider-independent state | None while PAUSED | Admission fails closed | D1 retained state; derived index retained | Chat, Q&A, titles, compaction and indexing remain PAUSED |

## Level 2 — Principal flows

### Quote to immutable decision

```text
cTrader tick
  -> daily append-only quote partition + atomic session heartbeat
  -> JsonlMarketProvider point-in-time window
  -> ForwardEngine freezes source/model/news identities
  -> append-only decision and prediction evidence
```

The decision path does not wait for training, annotation, Dashboard, Sync,
Cloudflare, or an LLM.

### Outcome to model generation

```text
mature decision + exact post-decision quote window
  -> append outcome and score
  -> mark training materialization dirty
  -> bounded materialization page
  -> deterministic cache identity check
  -> train only missing/due members
  -> atomic complete-generation activation
```

### Local authority to public views

```text
local SQLite authority
  -> independent background read models
  -> bounded local API resources
  -> hash/cursor-driven Dashboard Sync
  -> D1 projection
  -> Worker route or static asset
  -> browser
```

Historical market pages read only cursor-relevant UTC quote partitions.
Learning Sync transmits changed records and retains hashes only for the current
source universe. A periodic full reconciliation is recovery work, not normal
unchanged mutation.

### Source revision to Stable

```text
exact Git source + immutable Worker Version
  -> behavior-keyed evidence DAG
  -> renew only near-expiry live leases
  -> Candidate at 0%
  -> cheap Promote precheck
  -> Switch Windows and Worker owner
  -> targeted deferred projection + immediate Sync
  -> Observe
  -> COMMIT_STABLE
```

`scripts/release-evidence-contract.json` declares the current evidence graph.
CPU, semantic, Access, migration, placement, rollback, Promote, and Observe are
separate nodes; an unrelated source change must not invalidate all nodes.

### Storage lifecycle

```text
authoritative SQLite
  -> one receipt-backed daily online backup
  -> bounded daily/weekly/monthly retention
  -> Collector-owned passive checkpoint
  -> truncate only after all valid WAL frames are backfilled
```

Only deterministic managed backup and stale-temp families may be reclaimed.
Unknown objects, active worktrees, and rollback artifacts are not deleted.

## Level 3 — Code and contract index

| Concern | Authoritative implementation | Entry point / consumer | Contract tests |
|---|---|---|---|
| Quotes and causal market snapshot | `xauusd_forecaster/market.py`, `xauusd_forecaster/market_session.py` | `scripts/run_forward_collector.py` | `tests/test_market_session.py`, `tests/test_quotes_and_labeling.py` |
| Decision and evidence | `xauusd_forecaster/forward_engine.py`, `xauusd_forecaster/forward_ledger.py`, `xauusd_forecaster/live_v2.py` | Collector | `tests/test_forward_only.py`, `tests/test_evidence_integrity_v2.py` |
| Execution learning | `xauusd_forecaster/execution_learning.py` | Collector background work | `tests/test_evidence_integrity_v2.py` |
| Training | `xauusd_forecaster/training_owner.py`, `xauusd_forecaster/training_v2.py` | Collector background owner | `tests/test_training_owner.py`, `tests/test_forward_only.py` |
| News collection and scheduling | `xauusd_forecaster/news_collection_owner.py`, `xauusd_forecaster/news_scheduler.py`, `xauusd_forecaster/annotation.py` | Collector thread / `scripts/run_news_annotator.py` | `tests/test_news_collection_owner.py`, `tests/test_news_scheduler.py` |
| Dashboard bounded payloads, provenance, cache, component health, source health, incremental News archive selection and presentation, learning, market, runtime, and storage status resources | `xauusd_forecaster/dashboard_payloads.py`, `xauusd_forecaster/dashboard_read_models.py`, `xauusd_forecaster/dashboard/deployment_provenance.py`, `xauusd_forecaster/dashboard/status_cache.py`, `xauusd_forecaster/dashboard/health_projection.py`, `xauusd_forecaster/dashboard/news_source_health.py`, `xauusd_forecaster/dashboard/news_archive.py`, `xauusd_forecaster/dashboard/news_presentation.py`, `xauusd_forecaster/dashboard/learning_resources.py`, `xauusd_forecaster/dashboard/market_resources.py`, `xauusd_forecaster/dashboard/runtime_status.py`, `xauusd_forecaster/dashboard/storage_status.py` | `scripts/run_dashboard_api.py` | `tests/test_dashboard_api.py`, `tests/test_dashboard_payloads.py`, `tests/test_dashboard_status_cache.py`, `tests/test_dashboard_health_projection.py`, `tests/test_dashboard_market_resources.py`, `tests/test_dashboard_runtime_status.py` |
| Dashboard transport and progress policy | `xauusd_forecaster/dashboard/sync/transport.py`, `xauusd_forecaster/dashboard/sync/progress.py`; runtime-state I/O stays at the trusted entry-point boundary | `scripts/run_dashboard_sync.py` | `tests/test_dashboard_sync_transport.py`, `tests/test_dashboard_sync_progress.py`, `tests/test_dashboard_sync.py` |
| Dashboard operator retry bridge | `xauusd_forecaster/dashboard/operator_bridge.py` | HTTP adapter in `scripts/run_dashboard_api.py` | `tests/test_dashboard_api.py` |
| Local storage lifecycle | `xauusd_forecaster/maintenance.py`, `xauusd_forecaster/sqlite_wal.py` | Collector maintenance owners | `tests/test_backup_containment.py`, `tests/test_wal_checkpoint_ownership.py` |
| Release evidence DAG | `scripts/release_evidence_nodes.ps1`, `scripts/release-evidence-contract.json` | `scripts/xauusd_control_center.ps1` | `tests/test_release_evidence_nodes.py`, `tests/test_runtime_launchers.py` |
| Release runtime read model | `scripts/release_runtime_read_model.ps1` | Pure resolved Committed/Previous/Target identities; provider-only cached facts joined with once-per-refresh Windows, business-health, ownership, bundle, lock, and transaction facts; shared WPF/WinForms presentation with one max-stale envelope bound to the full authority fingerprint; asynchronous verified single-flight process-tree cleanup including nested native PID/start-token ownership; both JSON views; and fresh Reverse action-time authority in `scripts/xauusd_control_center.ps1` | `tests/test_release_runtime_read_model.py`, `tests/test_runtime_launchers.py`, `formal/release-control/ReleaseRuntimeReadModel.tla` |
| Runtime root and heartbeat | `xauusd_forecaster/runtime_paths.py`, `xauusd_forecaster/runtime_health.py` | All Windows launchers | `tests/test_runtime_launchers.py`, `tests/test_runtime_health.py` |
| Worker and D1 projection | `web/worker/index.ts`, `web/worker/api-router.ts`, `web/db/schema.ts` | Cloudflare Workers | `web/tests/d1-capabilities.test.mjs`, `web/tests/worker-cpu-headroom.test.mjs` |
| Live broadcast | `xauusd_forecaster/live_broadcast.py`, `broadcast/src/index.js` | `scripts/run_live_broadcast_publisher.py` | `tests/test_live_broadcast.py`, `broadcast/tests/broadcast.test.mjs` |

## Current structural gaps

- `scripts/xauusd_control_center.ps1` still combines 374 functions across
  release, supervision, provider adapters, recovery, and presentation.
- `scripts/run_dashboard_api.py` and `scripts/run_dashboard_sync.py` still own
  substantial domain logic as well as process composition.
- The flat Python package contains one 14-module strongly connected import
  component around ledger, scheduler, annotation, time, and retained Assistant
  support.
- Package extraction must therefore proceed one owner and one dependency
  direction at a time. The old stacked modularization Drafts are design input,
  not mergeable current implementation.

The point-in-time evidence is recorded in
[Current-main architecture audit](../audits/CURRENT_MAIN_ARCHITECTURE_2026_09_01.md).
