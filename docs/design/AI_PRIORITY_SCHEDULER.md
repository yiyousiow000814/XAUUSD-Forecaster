# Dynamic AI Scheduler Design

This design describes the account-aware scheduler used by the news AI chain.
It allocates available provider capacity without changing which model owns a
semantic decision.

## Goals

- Discover configured accounts and keys again on every scheduler cycle.
- Route work to the independent account with the most usable live capacity.
- Try every compatible independent account before delaying a job.
- Keep the queue moving when one task or model route is temporarily full.
- Preserve semantic priority without allowing indefinite starvation.
- Persist leases, attempts, quota reservations, backoff, and recovery.

## Model routes

`ai_task_registry.py` is the runtime source of truth for task-to-model routes.
The scheduler may choose an account or a declared fallback model, but it must
not invent a new semantic owner:

- `ACTIVE_ANNOTATION`: Gemini 3.5 Flash-Lite, then Gemini 3.1 Flash-Lite.
- `ACTIVE_IMPACT`: Gemma 4 only; this route owns event identity and impact.
- `TITLE_TRANSLATION`: Gemma 4, then the declared Gemini display fallbacks.

Adding a future task requires one route entry and contract coverage proving
that every queued task has exactly one declared route.

## Dynamic capacity selection

`GEMINI_API_ACCOUNTS` is reloaded for every batch. Each independent account is
ranked using durable usage from the current Pacific quota day and the exact
trailing 60-second request window. The rank considers the daily request limit,
requests per minute, and input tokens per minute for the task's model route.

Serial maintenance batches attempt the account with the most headroom first. A
capacity deferral, provider throttle, or transient provider error advances to
the next compatible independent account. Production account lanes use only
their assigned account; they must not duplicate another lane's account probes.
After a lane proves one model route has no capacity, it skips that route for
the remainder of the batch and continues with other ready routes. It does not
claim and defer every remaining job behind the same exhausted capacity gate.

Daily Brief capacity protection is account-scoped. If its selected account
fills the shared Gemma window, only that account's lane is temporarily limited
to Gemini annotation. Other independent accounts must continue impact and
title work; one account can never suspend the global Gemma queue.

Multiple keys in one account share quota and therefore share one capacity
score. Extra keys add transport redundancy, not imaginary quota. Independent
accounts add real capacity automatically on the next cycle. Authentication
failure may try another key in the same account; shared quota or provider
pressure skips the remaining keys in that account.

The ordinary unbounded annotator batch runs two synchronous provider lanes per
independent account. This bounded fan-out hides provider latency while atomic
account quota reservation remains authoritative for both RPM and TPM. Lanes
from one account share route-capacity state, so an exhausted route is skipped
for the rest of that batch after at most one already in-flight probe per lane,
instead of repeatedly claiming the remaining queue. Every lane owns its SQLite
connection. Explicit bounded maintenance
batches remain serial for deterministic operation. Runtime status reports the
per-account RPM limit and the aggregate limit across configured independent
accounts; it must not describe independently metered accounts as one
project-shared minute budget.

## Fair queueing

Fresh work receives at most a one-minute semantic-priority head start. After a
job has waited one scheduler cycle, all ready jobs are claimed in original FIFO
order. This keeps immediate market-relevant work responsive while guaranteeing
that annotation, impact, and display stages cannot be starved by a continuous
stream of newer urgent work.

Preemptible accounts remain restricted to `IMMEDIATE` and `FAST` jobs. Routine
accounts may serve every priority and provide overflow capacity for urgent
jobs. If no routine account exists, routine work remains queued rather than
consuming the preemptible reserve.

## Account configuration

```json
[
  {"account_id":"routine-a","pool":"ROUTINE","api_keys":["key-1","key-2"]},
  {"account_id":"urgent-a","pool":"PREEMPTIBLE","api_keys":["key-3"]}
]
```

Account IDs are operational metadata. API keys are represented in persisted
attempts only by short SHA-256 fingerprints. Legacy `GEMINI_API_KEYS` and
`GEMINI_API_KEY` remain routine-only compatibility inputs; each distinct legacy
key is treated as an independent account because no account grouping exists.

Every provider request reserves durable quota before transport. Daily counters
use the provider's Pacific reset day. RPM and TPM admission use exact durable
request timestamps over the trailing 60 seconds. The aggregate minute buckets
remain a reporting ledger, but they are not an admission window because adding
two whole buckets can retain a request for almost 120 seconds and unnecessarily
halve sustained throughput. The migration preserves still-live legacy bucket
usage during the first rolling window so a deployment cannot create a burst.

## Operational recovery

- `QUEUED`: ready now or at `available_at`.
- `LEASED`: exclusively owned until the lease expires.
- `BACKING_OFF`: retryable after its declared `available_at` time.
- `COMPLETED`: immutable output exists or the job completed.
- `DEAD_LETTER`: terminal failure; not reclaimed without a versioned recovery
  authorization tied to a deployed contract fix.

`BACKING_OFF` applies to the AI task, not publisher-content retrieval. A local
model-output contract failure becomes available again after five minutes and
gets one recovery attempt; the same repeated failure moves to `DEAD_LETTER`.
Provider rate limits and transient server failures keep their separate bounded
progressive schedule.

A recovery version can authorize one additional attempt for the exact bounded
failure family it fixes. The authorization is persisted by failure ID. It does
not reset attempt history. Once the authorized job is claimed, later terminal
or no-longer-current state cannot consume the same authorization again, and a
later failure is not covered by the earlier authorization.

Impact identity validation gets one metered contract-repair request containing
the rejected JSON, failed invariant, and exact offered candidate universe. The
repair remains fail-closed. Its versioned recovery authorization requeues only
terminal identity-shape failures covered by that deployed repair and only once
per annotation/model/prompt combination.

Scheduler rows are mutable operational state. News revisions, annotations,
impact assessments, translations, failures, model metadata, and historical
predictions remain immutable evidence under the repository contracts.
