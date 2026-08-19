# Dynamic AI Scheduler Design

This design describes the account-aware scheduler used by the news AI chain.
It allocates available provider capacity without changing which model owns a
semantic decision.

Model-output retries are corrective, not blind repetition. When an otherwise
valid news annotation fails only display validation, the worker sends one
bounded repair request containing the rejected fields and the exact prior
validation reason. Semantic and already-valid display fields stay frozen in an
immutable checkpoint. A second failure is persisted with a stable failure code
and bounded rejected output evidence, then returns to a display-only retry with
the latest reason. Display-only correction uses Gemma first, then escalates to
the declared Gemini fallback routes if validation still fails. These jobs use bounded backoff
but do not become terminal while the source and checkpoint remain current. A
repair-version change may authorize one auditable recovery attempt for failures
that predate checkpoints.

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

Operational retry-loop health uses the unresolved streak of genuine job errors,
not lifetime claim count. Capacity and dispatch deferrals remain immutable audit
attempts but are neutral for this alert, as are embedding and deliberate
maintenance deferrals; a later `OK` or `NOT_CURRENT` outcome resets the effective
streak. Health evidence reports both lifetime claims and the effective failure
streak.

Daily Brief capacity protection is account-scoped. If its selected account
fills the shared Gemma window, only that account's lane is temporarily limited
to Gemini annotation. Other independent accounts must continue impact and
title work; one account can never suspend the global Gemma queue.

Multiple keys in one account share quota and therefore share one capacity
score. Extra keys add transport redundancy, not imaginary quota. Independent
accounts add real capacity automatically on the next cycle. Authentication
failure may try another key in the same account; shared quota or provider
pressure skips the remaining keys in that account.

Independent account quotas do not authorize simultaneous provider bursts. One
durable Google transport governor staggers outbound work across accounts and
task types, initially at 250 ms and never at a fixed minute-scale cadence.
Successful responses reduce the interval gradually toward 120 ms. HTTP 429
doubles it up to five seconds, honors a longer bounded `Retry-After`, and then
recovers through later successes. A pacing deferral reserves neither account
quota nor a job attempt; its next eligible time is persisted instead of using a
blocking sleep.

Dispatch priority is pressure-driven rather than a permanent task weight or
capacity percentage. Active task demand records backlog, oldest wait, overdue
retry age, dependency fan-out, and the gap between backlog and recent
completions. A task dominated on every current pressure signal yields its turn;
non-dominated tasks rotate by least-recent dispatch so aging prevents
starvation. Thus an embedding generation blocking many impact jobs can rise
above ordinary catch-up, while a fresh actionable annotation surge can take the
next turns once that dependency pressure falls. The pressure snapshot and last
dispatch time survive process restart.

Daily Brief is its own provider task class. When synthesis is eligible it
publishes changed distinct events as backlog, material-event count as dependency
pressure, Brief staleness as oldest age, and repeated provider deferrals as the
drain gap. It then uses the same atomic account admission and provider dispatch
reservation as every other generation request. It has no independent timer,
fixed weight, capacity share, or transport path.

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

Current-contract annotation work has two operational ownership lanes. `LIVE`
contains eligible evidence first received after the contract activation point;
`CONTRACT_BACKFILL` contains earlier evidence that still needs the same current
semantic contract. This distinction changes scheduling only and never creates
a second semantic authority.

LIVE annotation is `FAST`, is always claimed before contract backfill, and may
use the existing preemptible account and priority quota reserve. Contract
backfill is `BACKGROUND` and routine-only. A contract handover persists its
activation point and descending receipt cursor, scans at most 50 historical
rows per scheduler cycle, and resumes from that cursor after restart. Backfill
is enqueued only when frozen relevance and category freshness rules show that
the record can still affect current operational behavior; immutable older
evidence remains stored without entering the live queue. Unfinished Daily Brief
dates remain a bounded operational exception and use the background lane.

Within one ownership lane, fresh work receives at most a one-minute
semantic-priority head start. After that interval, ready jobs follow original
FIFO order. Continuous historical migration therefore cannot consume the
capacity reserved for newly arriving live evidence.

Queue ordering is not quota isolation. Before every contract-backfill provider
dispatch, the same atomic account admission transaction computes a one-request
grant from the authoritative provider/model/account quota surface. Spendable
capacity is the current quota-day remainder minus the conservative P95 of
remaining LIVE demand from 7–14 complete Pacific quota days, an operational
reserve, a retry/critical reserve, and a safety buffer. Cold start fails closed.
The grant is recomputed after every admitted request; it stops immediately for
claimable or overdue LIVE work, increasing LIVE backlog, recent provider
throttling, LIVE capacity deferral, or the bounded instantaneous backfill share.

Hourly per-quota-day workload summaries retain only the latest 14 complete
days, independent of news-history size. Fine-grained request evidence remains
bounded separately. `BACKFILL_BUDGET_DEFERRED` is healthy pacing: it performs no
provider call, reserves no quota, and increments neither job attempt nor retry
count. Repeated identical budget deferrals use one durable record per
job/account/reason and refresh at most once per five minutes, so scheduler ticks
do not create an unbounded evidence stream. LIVE scheduler health excludes
migration queue age and pacing; a
separate non-blocking `contract_backfill` summary exposes its states, oldest
age, and recent budget deferrals.

Every annotation contract transition is declared as `REUSE_COMPATIBLE`,
`DETERMINISTIC_MIGRATION`, or `MODEL_REVIEW_REQUIRED`. Only the last class may
create model-backed migration jobs. Compatible reuse creates a validated
current-contract projection while retaining the source annotation and original
model provenance. Deterministic migration applies a declared, versioned local
transform and records source and projected hashes. Both paths are cursor-bounded,
replay-safe, and consume no provider dispatch, account quota, job attempt, retry,
or model-backed backfill. A contract failure is retained under
`SEMANTIC_TRANSITION_CONTRACT_FAILED` and never falls through to model review.
Historical demand is independently
classified as `CURRENT_OPERATIONAL`, `TRAINING_REQUIRED`, or `ARCHIVAL_ONLY`;
training work is schedulable only after an explicit generation demand, while
archival evidence is never scheduled. The V16-to-V17 transition is explicitly
model-review-required, remains cursor-bounded to 50 records per discovery page,
and still passes through the same LIVE-reserving quota gate.

Existing annotation jobs are assigned to LIVE or contract-backfill lanes by a
durable `(created_at, job_id)` keyset migration of at most 100 jobs per scheduler
cycle. Its cursor and page updates commit together. Unclassified annotation jobs
cannot be claimed, while newly created jobs are classified at insertion. A
restart resumes after the last committed cursor without changing attempts or
replaying provider work; completion is durable and prevents later full scans.

Preemptible accounts remain restricted to `IMMEDIATE` and `FAST` jobs. Routine
accounts may serve every priority and provide overflow capacity for urgent
jobs. If no routine account exists, routine work remains queued rather than
consuming the preemptible reserve.

## Account configuration

```json
[
  {"account_id":"routine-a","pool":"ROUTINE","api_keys":["key-1","key-2"],"credential_ids":["credential-a","credential-b"]},
  {"account_id":"urgent-a","pool":"PREEMPTIBLE","api_keys":["key-3"]}
]
```

Account and credential IDs are non-secret operational metadata. Explicit
`credential_ids` are preferred and must align one-for-one with `api_keys`.
They preserve historical attempt identity without deriving it from key material.
When omitted, a versioned 128-bit HMAC identity is derived with the high-entropy
API key as the HMAC key and a fixed application-domain message; raw keys are
never persisted or included in credential representations. Legacy
`GEMINI_API_KEYS` and `GEMINI_API_KEY` remain routine-only compatibility inputs;
each distinct legacy key is treated as an independent account because no
account grouping exists.

Before upgrading a legacy deployment that must retain existing scheduler and
quota identity, convert it to `GEMINI_API_ACCOUNTS` and pin each existing
non-secret account and credential ID in `account_id` and `credential_ids`.
The format is accepted by the previous runtime because unknown fields are
ignored, so the configuration can be staged before the new runtime. Without
that bounded migration, legacy inputs deliberately cut over to visibly
versioned `legacy-hmac-v1-*` accounts instead of silently claiming old identity.

Pre-scheduler JSON quota ledgers recognize the retired SHA-256 identifier only
as a bounded migration lookup. `snapshot` computes the maximum of the legacy
and canonical counts in memory and never changes the file. On the first
`reserve` or `seed`, the ledger writes that maximum under the canonical HMAC
identity, removes the legacy key, and atomically replaces the file. Counts are
never added, so migration cannot reset or double-count daily usage. Current
production request accounting writes the scheduler database; production status
and compatibility JSON reads do not write these files. The compatibility JSON
writer lock is process-local, so any caller that uses `reserve` or `seed` must
enforce a single-writer process for a given path.

Every provider request reserves durable quota before transport. These counters
represent conservative local admission, not provider-confirmed success. The
request lifecycle separately records provider transport, success, throttle or
other failure, and committed embedding vectors. Daily counters
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
Provider rate limits, transient server failures, and typed transport
interruptions keep their separate bounded progressive schedule.

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
