# Dashboard and Sync

## 1. Purpose

This subsystem converts local forecasting authority into bounded operator
views, serves a fast local first paint, and mirrors independent resources to
Cloudflare without letting optional history delay heartbeat publication.

## 2. Execution boundary

Dashboard API and Dashboard Sync are separate Python processes. The API uses
`ThreadingHTTPServer`, a background `DashboardReadModelOwner`, and bounded
critical-cache refresh threads owned by
`xauusd_forecaster.dashboard.status_cache`. Sync has a main heartbeat thread
plus one single-worker control lane and one single-worker heavy lane. The
read-only health-projection module is called in the existing API process and
introduces no process, thread, or store boundary.

| Dimension | Current state |
|---|---|
| Ownership | Read-model owner builds local projections; API owns reads/local audited override; Sync owns remote mirroring. |
| Boundary | Dashboard API `PROCESS`; Dashboard Sync `PROCESS`; request/lane `THREADS`. |
| Critical Path | Bounded local critical status and remote heartbeat. |
| Bounded Work | Fixed first paint, page/item/byte caps, one heavy resource per cycle. |
| Incremental | Source revisions, atomic model generations, cursors, per-target schedule/checkpoint files. |
| Failure Isolation | Optional resource failure retains last-good and cannot invalidate critical status. |

## 3. Owner

`DashboardReadModelOwner` uniquely builds audit, learning, and market-chart
summary models. `run_dashboard_api.py` owns the local HTTP boundary.
`StatusSnapshotCache` owns each process-local serialized snapshot body, age,
single-flight refresh state, and last error. This disposable cache is not
forecasting evidence authority and does not own any historical builder.
`xauusd_forecaster.dashboard.health_projection` owns only the derived
interpretation of current runtime heartbeats, decision cadence, broker session,
and materialized semantic health. The source files and SQLite rows remain
authoritative under their existing runtime and evidence owners; operational
alert aggregation remains separate.
`run_dashboard_sync.py` owns D1 mirroring and per-target progress. The
scheduler remains the state-transition owner for audited retry overrides.

## 4. Inputs and outputs

Inputs are local SQLite, quote JSONL, runtime heartbeat files, release-control
state, and target configuration. Outputs are local JSON API resources,
critical-status cache entries, derived read models, D1 snapshots/history rows,
sync status, per-resource degradation, and retry-command acknowledgements.

## 5. Durable state

- Local SQLite forecasting authority and durable dashboard read models.
- Atomic per-service status JSON and sync-status JSON.
- Per-target learning, market, news, news-evidence, and resource-schedule state
  files with cursors and next-run times.
- D1 public snapshot/history tables are replaceable mirrors or bounded ledgers.
- D1 operator request/event tables retain the authenticated remote command
  lifecycle; local SQLite owns the eventual scheduler transition.

## 6. Current data flow

```text
SQLite/quote files -> DashboardReadModelOwner (off request)
  -> atomic audit / learning / market_chart model per source revision
  -> Dashboard API
       -> /api/status == /api/critical-status bounded first paint
       -> lazy summary and paged history resources
       -> audited local retry override POST
  -> Dashboard Sync
       -> critical heartbeat lane
       -> control lane + one heavy resource per cycle
  -> authenticated Worker ingest -> D1 -> browser APIs
```

Audit, learning, and market-chart GETs read durable models rather than running
their growing builders in request threads. Critical payload orchestration calls
the fixed-work health projections before aggregation and serialization.

## 7. Critical path

Local `/api/status` and `/api/critical-status` expose the same fixed recent
90-minute decision window. A bounded cache refresh may serve a recent
last-good value while one daemon refresh thread per cache instance runs. One
builder invocation may be in flight per instance; the cache does not add a new
bound to builder work. Sync publishes the critical heartbeat before it
schedules control or heavy optional work.

## 8. Bounded-work mechanisms

- Market history page: at most 500 local items.
- News archive page: at most 20 items.
- News evidence page: at most 50 items and 350,000 serialized bytes.
- Sync envelope: 750,000 bytes; resource-specific item and byte bounds are
  smaller, including 20 news writes, 25 market-history items, and 60,000-byte
  learning-history batches.
- Audit first page is capped at 16,000 bytes; detail snapshots at 120,000 bytes.
- Sync admits at most one heavy resource per cycle.
- Resource backoff is capped at 3,600 seconds.

Display limits and serialized transport limits remain distinct.
The local market-history response is capped at 500 items, but its request path
still enumerates retained quote files and merges the complete cached candle
history before slicing the page. Archive parsing is cached and the active file
is consumed from a byte offset, but the merge work grows with retained history.
This is a current gap, not a claimed bounded request implementation.

## 9. Incremental mechanisms

Each read model records its own source revision, contract version, payload hash,
generation, success time, and failure state. The owner rebuilds only changed
resources and replaces only after rechecking the source revision. Sync resources
use cursor overlap, per-target acknowledgements, page progression, and durable
next-run/backoff state.

## 10. Failure behavior

A read-model build failure records the resource error and preserves its prior
known-good payload. Corruption, hash mismatch, or contract mismatch fails that
resource closed. API critical status remains independent. Sync records failure
on the failed target/resource; another healthy target and the next heartbeat
continue. A status-cache refresh failure records the error and retains the
prior body, but serves it only through 90 seconds of age; without a bounded-age
body the request fails closed and a later request may retry. One oversized item
fails explicitly instead of silently truncating authority. Health projection
owns no cache, fallback, or retry state; `StatusSnapshotCache` continues to own
last-good and refresh behavior.

## 11. Restart/recovery behavior

Read models persist in SQLite and are verified before use. On restart, the
owner schedules only stale/changed resources. Sync restores each target cursor,
source revision, next-run time, failure count, and acknowledgement; it does not
collapse all optional resources into one restart burst.

## 12. Entry points

- `scripts/run_dashboard_api.py`
- `scripts/run_dashboard_sync.py`
- Preview snapshot producer: `scripts/build_preview_bundle.py`
- Public probe: `scripts/check_public_health.py`
- Production-shape probe: `scripts/check_production_shape.py`

## 13. Core modules

- `xauusd_forecaster/dashboard_read_models.py`: per-resource model owner,
  atomic replacement, hashes and last-good behavior.
- `xauusd_forecaster/dashboard/status_cache.py`: process-local serialized
  snapshot cache, single-flight refresh, bounded last-good, and health state.
- `xauusd_forecaster/dashboard/health_projection.py`: read-only Collector,
  decision-output, and semantic-pipeline component projections from bounded
  current inputs.
- `xauusd_forecaster/dashboard_payloads.py`: critical and audit contracts.
- `xauusd_forecaster/dashboard_summaries.py`: indexed summary queries.
- `xauusd_forecaster/learning_curves.py`: learning resource.
- `xauusd_forecaster/operational_health.py`: component health projection.
- `xauusd_forecaster/news_scheduler.py`: retry-command transition owner.

## 14. Relevant tests

`tests/test_dashboard_health_projection.py`,
`tests/test_dashboard_status_cache.py`, `tests/test_dashboard_api.py`,
`tests/test_dashboard_payloads.py`,
`tests/test_dashboard_sync.py`, `tests/test_operational_health.py`,
`tests/test_runtime_health.py`, and `tests/test_news_scheduler.py` cover first
paint, runtime-component thresholds and broker boundaries, cache single-flight
and bounded last-good, resource isolation, byte/page bounds, cursor retry, and
audited retry semantics.

## 15. Authoritative contracts/specs

- [Hosting Boundaries](../contracts/HOSTING_BOUNDARIES.md)
- [Operational Health](../contracts/OPERATIONAL_HEALTH.md)
- [Paged Dashboard History](PAGED_DASHBOARD_HISTORY.md)
- [Dashboard Presentation](../specs/DASHBOARD_PRESENTATION.md)

## 16. Known current gaps

Both entry scripts contain extensive serialization, query, transport,
scheduling, and domain logic. `run_dashboard_api.py` imports selected sync
builders, so the entry-point dependency direction is not yet clean. The large
API/sync tests group several resource families in single files. The lazy local
market-history route has bounded output but growing source enumeration/merge
work in its request path.

## 17. Links back to System Architecture

Return to [System Architecture](SYSTEM_ARCHITECTURE.md) or continue to the
[Codebase Map](../reference/CODEBASE_MAP.md).
