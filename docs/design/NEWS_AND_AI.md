# News and AI

## 1. Purpose

This subsystem collects time-qualified news and macro evidence, performs
structured annotation and impact/event-identity work, and freezes what was
usable at each decision time. It also produces the display-only Daily Brief.

## 2. Execution boundary

Collection runs in `NewsCollectionOwner`, a background thread inside the
Collector process. Annotation, impact, title/display, embedding prerequisite,
quota/governor, and Daily Brief work run in the separate Annotator process.
Annotator account lanes are a thread pool, not separate services.

| Dimension | Current state |
|---|---|
| Ownership | Collection owner writes revisions/polls; Annotator scheduler owns semantic jobs and transitions. |
| Boundary | Collector `THREAD` for collection; Annotator `PROCESS` with bounded lane `THREADS`. |
| Critical Path | Decision consumes only already-persisted point-in-time evidence. |
| Bounded Work | Source poll sets, job claims, account lanes, payload/token limits, Brief backlog. |
| Incremental | Source revisions, first-seen clocks, durable jobs, leases, attempts, retry times. |
| Failure Isolation | Provider/semantic failure degrades news; quote and market-only decision work continues. |

## 3. Owner

`NewsCollectionOwner` uniquely schedules collection. The Annotator process owns
the durable AI job scheduler and calls domain validators; individual annotation,
impact, translation, retrieval, and Daily Brief modules own their result
contracts.

## 4. Inputs and outputs

Inputs are registered public sources, publication/receipt timestamps, complete
publisher bodies, provider credentials, model quotas, prior immutable events,
and durable jobs. Outputs are source polls, immutable revisions, structured
annotations, impacts, event identities, title translations, embeddings,
decision-time coverage snapshots, failures, retries, and Daily Brief revisions.

## 5. Durable state

Local SQLite stores revisions, macro observations, source polls, scheduler
jobs, attempts/deferrals, quota accounting, annotations, impacts, identities,
retrieval receipts, failures, Daily Brief revisions/finalizations, and mutable
refresh/retry state. Historical results are append-only; lease and scheduler
state is mutable and restart-recoverable.

## 6. Current data flow

```text
registered source -> collection thread -> revision/source-poll append
  -> sync_pending_jobs -> durable scheduler claim
  -> annotation -> impact and identity retrieval -> title/display
  -> point-in-time semantic/coverage snapshot -> decision evidence
  + bounded Daily Brief synthesis -> display projection
```

News identity retrieval uses Gemini Embedding 2. The removed local Assistant
runtime and paused Assistant memory indexing are separate concerns.

## 7. Critical path

Collection and semantic work never execute synchronously inside a five-minute
decision. Decision reads only records and health/coverage that existed by its
cutoff. Current provider health cannot rewrite a past decision-time snapshot.
Only a frozen `UNAVAILABLE` news-input state forces news-dependent identities
to `WAIT`; `MARKET_ONLY` remains independent.

## 8. Bounded-work mechanisms

- Scheduler execution uses claimed jobs and a maximum derived from configured
  accounts or an explicit batch size.
- Each annotation/impact/title operation processes one claimed record.
- Provider requests have token/byte limits and durable RPM/TPM/RPD accounting.
- Annotator concurrency is bounded by account lanes.
- Daily Brief processes the current date plus a bounded newest-first unfinished
  backlog and emits bounded structured content.
- Embedding backfill uses an explicit batch size and finite lease.

## 9. Incremental mechanisms

Source item/revision identities and collector first-seen time prevent unchanged
recollection from becoming new evidence. Durable scheduler jobs have
idempotency keys, claim leases, attempts, deferrals, and next retry times.
Daily Brief stores candidate/source hashes, refresh state, revisions, and
finalization markers. Embedding retrieval records profile/generation progress.

## 10. Failure behavior

Transport, capacity, model output, validation, prerequisite, and provider
governor failures retain distinct reason codes. Retryable work is backed off;
terminal work remains audit evidence. One failed Brief or account lane does not
terminate the Annotator. Missing, quiet, degraded, stale, and unavailable are
not interchangeable.

## 11. Restart/recovery behavior

The Annotator reconstructs pending work from SQLite, reclaims only eligible
leases, preserves completed stages, and resumes at durable retry times. Lock
contention rolls back the current connection and retries after five seconds.
Daily Brief reuses a committed revision if finalization was interrupted rather
than spending a second model request.

## 12. Entry points

- Collection lifecycle: `scripts/run_forward_collector.py`
- Semantic worker: `scripts/run_news_annotator.py`
- One-shot embedding repair: `scripts/backfill_news_identity_embeddings.py`
- One-shot pruning/audits: `scripts/prune_unused_news.py`,
  `scripts/audit_news_candidate_retrieval.py`, and
  `scripts/audit_named_reference_reviewer.py`

## 13. Core modules

- `xauusd_forecaster/news_collection_owner.py`: collection thread lifecycle.
- `xauusd_forecaster/news.py`: source adapters and collection rules.
- `xauusd_forecaster/news_scheduler.py`: durable jobs, quota/governor, retry.
- `xauusd_forecaster/annotation.py`: structured annotation, impact and title
  execution.
- `xauusd_forecaster/news_semantics.py`: annotation validation.
- `xauusd_forecaster/news_impact.py`: impact and event-candidate context.
- `xauusd_forecaster/news_retrieval.py`: hybrid retrieval and embedding progress.
- `xauusd_forecaster/gemini_embeddings.py`: Gemini embedding transport/accounting.
- `xauusd_forecaster/news_input_coverage.py`: frozen decision-time availability.
- `xauusd_forecaster/news_pipeline_health.py`: operational semantic health.
- `xauusd_forecaster/daily_brief.py`: date-scoped Brief lifecycle.

## 14. Relevant tests

`tests/test_news_collection_owner.py`, `tests/test_forward_only.py`,
`tests/test_news_scheduler.py`, `tests/test_scheduler_transition_execution.py`,
`tests/test_critical_annotation_state.py`, `tests/test_news_event_identity.py`,
`tests/test_news_hybrid_retrieval.py`, `tests/test_gemini_embeddings.py`,
`tests/test_news_pipeline_health.py`, and `tests/test_daily_brief.py`.

## 15. Authoritative contracts/specs

- [News Evidence](../contracts/NEWS_EVIDENCE.md)
- [Daily Brief](../contracts/DAILY_BRIEF.md)
- [Forward-only Evidence](../contracts/FORWARD_ONLY.md)
- [AI Scheduler Design](AI_PRIORITY_SCHEDULER.md)
- [News Identity Retrieval](NEWS_IDENTITY_RETRIEVAL.md)
- [AI Provider Quotas](../AI_PROVIDER_QUOTAS.md)

## 16. Known current gaps

`news_scheduler.py`, `annotation.py`, and `news.py` are large multi-responsibility
modules with high fan-in/fan-out. Collection and Decision also share the
Collector process and database, while annotation has process isolation but
still shares the SQLite authority. The current import graph contains a large
cross-domain cycle group.

## 17. Links back to System Architecture

Return to [System Architecture](SYSTEM_ARCHITECTURE.md) or continue to the
[Codebase Map](../reference/CODEBASE_MAP.md).
