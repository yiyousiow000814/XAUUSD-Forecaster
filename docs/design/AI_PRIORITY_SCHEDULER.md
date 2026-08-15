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
ranked using durable usage from the current Pacific quota day and the current
plus previous UTC minute bucket. The rank considers the daily request limit,
requests per minute, and input tokens per minute for the task's model route.

The scheduler attempts the account with the most headroom first. A capacity
deferral, provider throttle, or transient provider error advances immediately
to the next compatible independent account. Only after all compatible accounts
are unavailable is that job delayed until the next one-minute cycle. Other
ready jobs continue in the same batch, so one blocked stage does not freeze the
whole chain.

Multiple keys in one account share quota and therefore share one capacity
score. Extra keys add transport redundancy, not imaginary quota. Independent
accounts add real capacity automatically on the next cycle. Authentication
failure may try another key in the same account; shared quota or provider
pressure skips the remaining keys in that account.

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
use the provider's Pacific reset day. Minute counters use conservative current
and previous UTC minute buckets to prevent boundary bursts.

## Operational recovery

- `QUEUED`: ready now or at `available_at`.
- `LEASED`: exclusively owned until the lease expires.
- `BACKING_OFF`: retryable after a provider-specified time.
- `COMPLETED`: immutable output exists or the job completed.
- `DEAD_LETTER`: terminal failure; never reclaimed automatically.

Scheduler rows are mutable operational state. News revisions, annotations,
impact assessments, translations, failures, model metadata, and historical
predictions remain immutable evidence under the repository contracts.
