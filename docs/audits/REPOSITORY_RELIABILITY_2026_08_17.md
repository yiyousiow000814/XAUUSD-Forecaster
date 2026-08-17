# Repository Reliability Audit — 2026-08-17

## A. Executive result

The repository already has strong point-in-time, append-only, lease, generation,
and mirror boundaries. The audit did not find a reason to replace its runtime
architecture or merge its distinct state machines.

It did find three concrete correctness defects and two operational contract
gaps:

1. current news-cluster representative selection had divergent cross-source
   tie-breaks;
2. scheduled retries could be presented as currently blocking retry loops;
3. display repair could split ticker punctuation into a false English clause
   and reuse a stale invalid-field list across retries;
4. public news review classification had separate TypeScript and SQL maps; and
5. mirror health recorded the whole process outcome but not per-resource
   latency and progress time.

The stabilization change fixes those boundaries without changing forecasting,
news semantics, model policy, historical evidence, or production data.

## B. Runtime component and dependency inventory

| Component | Cadence | Produces | Consumes | Authority and recovery |
| --- | --- | --- | --- | --- |
| Control Center, watchdog, guard task | supervisory | process state and rollout receipts | process heartbeats, git/runtime state | Windows process control; restart-safe, no evidence authority |
| Quote bridge | live | timestamped XAUUSD Bid/Ask JSONL | cTrader read-only quotes | local append-only quote files; collector fails closed on stale/malformed quotes |
| Forward Collector | 10 seconds; news poll 60 seconds | source polls, news revisions, decisions, outcomes, training/generation receipts, backups | quotes, official/news transports, matured evidence | local SQLite and quote archive; startup reconciliation and daily backup |
| News Annotator | 60 seconds | annotation, impact, title, Daily Brief, scheduler attempt evidence | current revisions, model capacity ledgers | leased local scheduler with bounded retry, dead letter, recovery authorization, WAL contention retry |
| Dashboard API | request-driven with bounded snapshot cache | read-only status, archive pages, market/history pages | local SQLite, heartbeat/status files | no write authority; last good bounded snapshot may serve briefly while refresh runs |
| Dashboard Mirrors | 30 seconds | heartbeat and bounded Cloudflare/Sites resource mirrors | Dashboard API, D1/Worker endpoints | independent target state files, bounded resource cursors, transport retry, D1 invariant verification |
| Local Assistant worker | 1 second | Assistant turn events and validated completion | Cloudflare worker claims, local Ollama models | remote lease token plus bounded attempts; no provider session authority |
| Cloudflare Worker and D1 | request-driven | public snapshots, Assistant/News-Q&A queues, immutable messages/events | authenticated human and machine requests | D1 is a replaceable read mirror for Forecaster evidence; Assistant state is owner-scoped D1 authority |
| Vectorize | background indexing and query | versioned Assistant memory vectors | canonical D1 messages and index jobs | exact index-generation gate; failed generations remain auditable but inactive |

Critical dependency path:

```text
cTrader / source transports
        -> Collector -> local append-only SQLite
                         |-> Annotator scheduler -> annotations / impacts / briefs
                         |-> read-only Dashboard API
                                  -> Mirrors -> Worker/D1 -> public dashboard
Cloudflare Assistant queues -> local Assistant worker -> validated D1 completion
canonical D1 messages -> memory index jobs -> version-gated Vectorize entries
```

The quote/decision path does not depend on Cloudflare, Assistant, or LLM
availability. LLM work cannot authorize Long, Short, or Wait.

## C. State and failure-code inventory

These planes intentionally have different states and must not be collapsed into
one universal enum.

| Plane | States | Retry / terminal owner | Provider fallback | Model usable / public severity |
| --- | --- | --- | --- | --- |
| Local news scheduler | `QUEUED`, `LEASED`, `BACKING_OFF`, `COMPLETED`, `DEAD_LETTER` | Annotator; expired lease requeues, bounded failure reaches dead letter | account-aware Gemini/Gemma routing | only validated current evidence may complete model work; claimable stalls and new dead letters are operational alerts |
| Public news review | `COMPLETED`, `PROCESSING`, `ISOLATED` derived from payload states | local producer owns transitions; D1 verifies tuples | none in Worker | `READY`/`NOT_REQUIRED` complete; display repair/backoff process; dead letter/content unavailable isolate |
| Daily Brief | `WAITING`, `UPDATING`, `DEFERRED`, `FINAL`, `DEGRADED`, `EMPTY` | Annotator and finalization ledger | bounded Gemma route | only final/degraded revisions display as completed synthesis; stalled/deferred reasons remain explicit |
| Assistant turns | `PENDING`, `PROCESSING`, `ANSWERED`, `FAILED`, `REJECTED`, `EXPIRED`, `CANCELLED` | Worker route and lease owner | policy-selected model/capacity pool | only validated `ANSWERED` content is usable; terminal failures are owner-visible and monitored |
| News Q&A | `PENDING`, `PROCESSING`, `ANSWERED`, `FAILED`, `REJECTED`, `EXPIRED` | Worker route | bounded metered route | evidence receipt required for answer completion |
| Title jobs | `PENDING`, `PROCESSING`, `COMPLETED`, `FAILED`, `CANCELLED` | title worker | bounded route | display-only; cannot mutate user title or conversation activity incorrectly |
| Compaction / memory-index jobs | `PENDING`, `PROCESSING`, `COMPLETED`, `FAILED` | background worker | bounded route / local embedding | old valid summary/index remains active; generation mismatch fails closed |
| Assistant capacity | `IN_FLIGHT`, `SUCCEEDED`, `FAILED`, `THROTTLED`, `CAPACITY_REJECTED`, `ABANDONED` | capacity ledger | next healthy credential/model allowed by policy | provenance only; secrets never persist |

Allowed transition families are:

- scheduler: `QUEUED -> LEASED -> COMPLETED`, with `LEASED -> BACKING_OFF
  -> LEASED`, bounded `LEASED -> DEAD_LETTER`, and expired lease recovery back
  to claimable work;
- public news: local payloads derive exactly one of `COMPLETED`, `PROCESSING`,
  or `ISOLATED`; D1 rejects contradictory payload tuples rather than inventing
  a transition;
- Daily Brief: `WAITING/DEFERRED -> UPDATING -> FINAL`, with bounded
  `DEGRADED` or `EMPTY` terminal display states and startup reconciliation of
  incomplete updates;
- Assistant and News Q&A: `PENDING -> PROCESSING -> ANSWERED`, or an explicit
  terminal `FAILED/REJECTED/EXPIRED/CANCELLED` state owned by the lease route;
- title, compaction, and index jobs: `PENDING -> PROCESSING -> COMPLETED`, or
  explicit `FAILED` (and title-only `CANCELLED`). A generation mismatch cannot
  be retried by an incompatible worker;
- capacity attempts: one `IN_FLIGHT` reservation reaches exactly one outcome.
  A provider/account fallback creates another audited attempt, never a rewrite.

Stable operator codes remain defined by
`docs/contracts/OPERATIONAL_HEALTH.md`. Internal failure families are:

- capacity and transport: `MODEL_CAPACITY_DEFERRED`,
  `PROVIDER_HTTP_ERROR`, `PROVIDER_TRANSPORT_UNAVAILABLE`,
  `TRANSPORT_UNAVAILABLE`;
- output and evidence: `MODEL_OUTPUT_CONTRACT_FAILED`,
  `MODEL_OUTPUT_INVALID`, `NEWS_RETRIEVAL_PROVENANCE_MISMATCH`;
- lifecycle: `LEASE_EXPIRED`, `LEASE_RENEWAL_REJECTED`,
  `CURRENT_EVIDENCE_NO_LONGER_ELIGIBLE`;
- generation and mirror: `GENERATION_ARTIFACT_VALIDATION_FAILED`,
  `NEWS_MIRROR_STATE_INVARIANT_VIOLATION`,
  `REMOTE_STATE_INVARIANT_VIOLATION`;
- access and payload: `AUTH_REJECTED`, `PAYLOAD_BUDGET_EXCEEDED`,
  `PAYLOAD_CONTRACT_REJECTED`.

Internal codes diagnose the owning component. Only the stable `OPS_*` catalog
is the cross-component operator interface.

## D. Contract drift findings

### Fixed: cluster representative drift

The intended current representative is the current revision with the longest
complete body, then the smallest stable `(source, source_item_id)` identity.
`source_item_id` is source-local and cannot be a cross-source tie-break alone.

Before this audit, pending annotation and Daily Brief used the complete key,
while completed annotation, title translation, pipeline health, operational
health, model factor aggregation, and Dashboard archive readers omitted
`source`. This could make one cluster simultaneously appear pending, completed,
or absent depending on the reader.

The ordering now lives in `xauusd_forecaster.news_identity` and all sibling
readers use it. A family regression deliberately orders `source_item_id`
opposite to `source` and proves pending, title, and completed readers agree.

### Fixed: public review-state half-upgrade risk

The route-local SQL map was removed. TypeScript classification, SQL filtering,
and invariant SQL now derive from the state collections in
`web/app/_lib/news-review-state.ts`.

### Fixed: non-convergent display repair

The Chinese-primary validator treated a semicolon inside a parenthesized ticker
pair such as `TSX: AYA; NASDAQ: AYA` as a sentence boundary. It therefore
rejected an otherwise Chinese-primary clause. A retry could then preserve the
latest narrow invalid-field list even when another display field still failed
number provenance, causing repeated repairs of the wrong field.

Clause splitting now respects paired brackets. A durable display checkpoint is
locally revalidated before any new model request, and a still-invalid checkpoint
recomputes its complete invalid-field list from the frozen semantic result. No
semantic re-analysis is performed.

### Retained by design

- model generation identity is a complete feature/eligibility/policy triple,
  not one global version string;
- market, news, and learning cursors have different commit boundaries;
- Assistant, Q&A, title, compaction, and memory jobs have different terminal
  semantics;
- D1 mirrors local evidence but does not become local recovery authority.

### Residual risk

Python and TypeScript necessarily duplicate the serialized public news payload
contract across a runtime boundary. Cross-runtime fixture tests are the correct
control; replacing that boundary with source-string tests would be weaker.

## E. Incident-family review

Recent incidents cluster into four sibling families:

1. Daily Brief finalization, capacity starvation, timestamp comparison, and
   startup reconciliation ordering;
2. news display-repair convergence, recovery authorization consumption, and
   mirror `REPAIRING_DISPLAY` alignment;
3. Assistant memory generation gates, incomplete embedding catch-up, and
   context/routing provenance;
4. runtime rollout preflight, status refresh latency, and mirror replay health.

The common failure pattern was a correct local transition that was not covered
through restart, mirror, or sibling-reader boundaries. The response is
family-level lifecycle coverage and shared contract helpers, not another
case-specific status label.

## F. Deterministic lifecycle and soak coverage

| Required scenario | Durable coverage |
| --- | --- |
| continuous annotation and index completeness | representative-family regression plus existing bounded archive repeated-cycle tests |
| provider 429 / temporary unavailable | model capacity pool failover, scheduler retry, Daily Brief deferral, and News Q&A/Assistant capacity tests |
| SQLite WAL contention | Annotator lock retry test preserves database errors and retries the writer cycle |
| repeated processing cycles | existing three-cycle mirror test plus new scheduler restart lifecycle |
| generation mismatch | active model generation, Assistant memory cutover, and old-worker claim gates |
| migration / cutover | forward-only generation activation and D1 migration compatibility tests |
| mirror cursor updates | independent per-target state, overlap cursor, materialization-contract reset, and repeated incremental page tests |
| local -> API -> D1 repair state | `REPAIRING_DISPLAY` producer, mirror, D1 invariant, and public review-state tests |
| partial restart | Daily Brief restart debounce, scheduler restart lifecycle, expired lease recovery, and persisted mirror cursor tests |
| no duplicate terminal or requeue | new restart lifecycle proves deterministic job identity, one terminal row, and no implicit reopen |
| no invisible stuck item | operational queue depth, claimable/scheduled split, oldest age, failure code, dead-letter, and component heartbeat checks |

The new scheduler lifecycle performs claim, temporary failure, backoff, process
restart, early-claim rejection, one retry, completion, duplicate enqueue, and a
second restart without wall-clock sleeps or provider calls.

## G. Observability findings

Existing health already exposes queue depth, claimable work, scheduled retry,
oldest claimable age, 15-minute completions/deferrals/errors, dead letters,
component heartbeat, source health, mirror invariant failures, rollout failure,
and Daily Brief lifecycle.

This audit adds:

- retry-loop evidence with current state, claimability, and next retry time;
- per-target/per-resource mirror status, duration, and completion time; and
- failure duration on degraded mirror resources.

Remaining intentionally bounded gaps:

- state files expose cursors and `has_more`, but the UI does not yet calculate
  a universal cursor-lag number because different resources use incomparable
  cursor domains;
- no percentile latency history is persisted. The status file reports the
  latest cycle only; durable time-series telemetry would be a separate product
  and retention decision;
- provider-reported TPM is not treated as trusted progress. Local reservations
  and successful completions remain authoritative.

## H. Implemented minimal change set

1. Shared the current cluster-representative predicate and Python ordering key.
2. Updated all Python and Dashboard sibling readers to use the shared order.
3. Distinguished a scheduled high-attempt retry from a currently claimable
   blocking retry loop without hiding either condition.
4. Shared TypeScript public news review-state collections with route SQL.
5. Added deterministic cross-source and restart lifecycle coverage.
6. Added per-resource mirror observations to the existing synchronizer status
   artifact.
7. Made display checkpoint recovery revalidate locally and recompute stale
   invalid-field lists before bounded repair.
8. Updated the operational-health contract and this audit record.

No schema migration, production-data mutation, model-policy change, or news
semantic change is included.

## I. Verification and deployment order

Required order:

1. Python family tests and full repository test suite;
2. Worker type generation check, build, and Node contract tests;
3. repository policy check and diff review;
4. branch push and CI;
5. merge only after checks pass;
6. automatic local runtime update and restart;
7. verify exact main SHA, two decision cycles, fresh component heartbeats,
   `SYNC OK`, D1 news invariant, and operational alerts.

This audit does not authorize a direct production database write or manual D1
repair.

Local verification of the final change set completed with 901 Python tests,
168 Worker/web tests (164 passed and four expected Preview-only skips), ESLint,
Cloudflare type generation verification, and the Cloudflare-only repository
policy check. No paid-provider request or production-data write was used.
