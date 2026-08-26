# Safety Mechanism Inventory

This inventory records authoritative owners and composition obligations. A
mechanism's contract and tests are evidence, not substitutes for its runtime
owner or recovery path.

| Domain | Owner and state | Safety and liveness | Interactions / recovery | Verification and current gap |
|---|---|---|---|---|
| Stable, target, Previous Stable | Control Center; release-control state/history | Exact Git/Worker/Windows identity; Stable changes only after Observe; a valid target can progress | Cloudflare placement, Windows runtime, checks, reverse | TLA+, release contracts/runtime tests; operator lifecycle was too technical and is simplified by the Release Control design |
| Control Plane install | Exact-main installer; install transaction and bundle manifest | One verified bundle and supervisor; install never changes business runtime; failed install restores baseline | watchdog, guard, Stable Sync owner, release lock | TLA+ plus `test_control_plane_install.py`; recovery independently re-proves the service baseline |
| Watchdog/supervision | watchdog epoch and scheduled guard | One current supervisor; stale epochs cannot mutate; healthy stopped services recover | intentional holds, install, Switch/Observe | TLA+ and Windows contracts; epoch/fence must be implemented end to end |
| Sync ownership | Control Center and exact operation token | Stable keeps one owner during Prepare/Verify; zero owners only during exact bounded Switch | migration, watchdog, runtime reload, News projection | TLA+ and runtime contracts; Switch ownership is transaction-bound |
| Coordinated migration | immutable generation-watermark receipt | Stable Sync may advance CURRENT; recorded generation cannot mutate and later CURRENT must independently pass | Candidate identity, Sync, D1 | TLA+ plus migration receipt contracts; no long-lived production hold |
| Worker placement | Cloudflare Versions observed by Control Center | Stable version has production placement; target remains non-Stable before Switch | Git build, Windows target, rollback availability | outside-in placement probes plus TLA+ identity abstraction |
| Windows production runtime | detached runtime checkout and service process identity | one production writer, exact applied revision, no branch-driven activation | watchdog, switch, rollback | Windows process/launcher contracts and outside-in health |
| News CURRENT/STAGING generations | D1 generation rows; Windows sync/bootstrap proposes and Worker activates | CURRENT is unique; fresh STAGING is not cleaned; activation preserves Reverse-Stable projection | cleanup, receipts, release compatibility | TLA+ lifecycle abstraction plus Python/Worker cross-runtime and D1 tests; long-term compatibility must be activation-owned |
| Legacy Reverse-Stable News projection | generation activation owner and migration receipt | rollback-compatible identity remains available after every CURRENT activation | migration 0025/0026, cleanup, Reverse | property/cross-runtime contracts plus TLA+ compatibility bit |
| News cleanup/replacement | Worker D1 maintenance transaction | never remove CURRENT or legitimate fresh STAGING; bounded obsolete work eventually clears | generation leases and activation | D1 property and bounded-work tests; validate rates with production-shaped growth |
| News receipt chain/serialization | Python producer and Worker verifier | identical logical batches produce identical canonical bytes; immutable chain is append-only | Unicode, null, float, ordering, D1 | cross-runtime property tests and real forensic fixtures, not TLA+ |
| D1 bounded work | Worker route/maintenance owners | per-request CPU, rows, bytes, and pagination are bounded while backlog can drain | cleanup rates, queue growth, Candidate CPU | capacity/load contracts and production telemetry |
| Scheduler leases/retry/backoff | `news_scheduler` and durable job ledger | one valid lease publishes; stale worker cannot publish; retryable work eventually becomes eligible | provider quota, process restart, dead-letter/failure evidence | TLA+ only for lease protocol if changed; otherwise transition/property tests |
| Provider quota/capacity | model gateway quota ledger | reserve before request; no invented capacity; capacity deferral spends nothing | scheduler priority, backoff, external outage | capacity/property tests and provider-shaped rehearsals |
| News collection/reconciliation | collectors, forward ledger, annotation/sync owners | source-time evidence is append-only; reconciliation never rewrites accepted facts | source retries, semantic generations, D1 | invariant/property tests and real-provider rehearsal |
| Training background ownership | `training_owner` and immutable generation inputs | one training owner; point-in-time inputs; no incomplete activation | scheduler, Champion/Challenger, process restart | lease/ownership contracts and training-owner tests |
| Champion/Challenger promotion | model metadata and manual promotion owner | Challenger cannot silently become Champion; historical predictions remain immutable | training completion, dashboard, inference | policy/state-transition tests and outside-in model metadata |
| Immutable ledgers/evidence | forward and evidence ledgers | append-only, causal, reproducible identity; retries do not rewrite accepted evidence | collection, validation, release evidence | property/hash-chain tests and audit replay |
| Fail-closed WAIT | inference/critical annotation contracts | missing required evidence yields WAIT, never fabricated direction; healthy inputs can recover | provider outage, stale data, model state | property/scenario tests; liveness measured separately from safety |
| Preview/Production isolation | Worker routing and Preview build snapshot | Preview cannot mutate or consume production secrets/capacity | D1 bindings, Assistant, browser | policy/adversarial tests plus deployed Preview acceptance |
| Auth/Access | Worker Access JWT and owner policy | authentication and authorization precede parsing, storage, or expensive work | Admin, Assistant, operator retry | adversarial security tests and deployed Access checks |
| Optional services | broadcast/Assistant feature authority | optional failure cannot block critical forecast path or gain production authority | watchdog, status, quotas | failure-isolation contracts and runtime tests; Assistant remains paused |
| Restart/recovery | each durable owner; release/install/job/generation records | restart does not create a second transaction or writer; every non-terminal record has a legal continuation | all lifecycle owners | TLA+ for Release Control and lease protocols, family-level restart tests elsewhere |

## Technique routing

- Use TLA+/TLC for concurrent ownership, leases, holds, supervision, activation,
  retry/recovery, and release switching.
- Use property-based and cross-runtime contracts for canonical bytes, receipts,
  projections, and merge/delete semantics.
- Use capacity/load tests for D1 growth, cleanup throughput, payload size, CPU,
  and queues.
- Use policy/security/adversarial tests for Access, credentials, Preview, and
  authority restrictions.
- Use outside-in acceptance for deployed routes, hydration, responsive browser
  behavior, and operator-visible truthfulness.
