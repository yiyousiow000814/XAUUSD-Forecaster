# AI Priority Scheduler Design

This design records the scheduler architecture implemented by PR 33. Historical
activation status belongs to the pull request and is not a current rule.

## Scope

- Separate routine and preemptible API-key pools.
- Let AI semantic review assign urgency; do not infer urgency from headline keywords.
- Allow every available key to help when the urgent queue is backlogged.
- Process impact review before title translation and background work.
- Track quotas per independent account.
- Persist queued work with leases, idempotent writes, backoff, and recovery.

## Runtime design

`news_ai_jobs_v1` is mutable operational state. Its deterministic identity is
the task type plus immutable news revision, annotation, and prompt version.
Workers claim one row under an atomic lease. An expired lease becomes available
again, while reconciliation closes a job whose immutable output was committed
before a worker stopped.

The scheduler discovers five task types: active annotation, active impact,
target annotation, target impact, and display-title translation. Impact work is
ordered before annotation at the same priority, and title translation is always
background work. Target annotation is not queued until the active annotation
for the same revision exists.

Only the v15 annotation's `review_priority` assigns semantic urgency. Active
v14 work defaults to `NORMAL`; title translation defaults to `BACKGROUND`.
There is no source-name or headline-keyword urgency rule in the scheduler.

Preemptible accounts claim only `IMMEDIATE` or `FAST` jobs. Routine accounts can
claim every priority, so they absorb urgent overflow after preemptible accounts
have taken their available work.

## Account Configuration

Set `GEMINI_API_ACCOUNTS` to a JSON list. Multiple keys may share one independent
account and therefore one quota counter:

```json
[
  {"account_id":"routine-a","pool":"ROUTINE","api_keys":["key-1","key-2"]},
  {"account_id":"urgent-a","pool":"PREEMPTIBLE","api_keys":["key-3"]}
]
```

The account ID is operational metadata; API keys are represented internally by
short SHA-256 fingerprints and are never written to the scheduler tables.
`GEMINI_API_KEYS` and `GEMINI_API_KEY` remain supported. In that compatibility
mode each distinct key is treated as a separate routine account.

Request accounting is reserved atomically before every provider call, including
repair and fallback calls. Daily counters use the provider's Pacific-day reset;
minute counters use UTC minute buckets. The existing Gemini 3.1 annotation
fallback remains available when Gemini 3.5 routine capacity cannot be reserved.

## Operational Recovery

- `QUEUED`: available for a compatible pool.
- `LEASED`: owned by one worker until its lease expires.
- `BACKING_OFF`: retryable only after `available_at`.
- `COMPLETED`: immutable output exists or the worker completed the job.
- `DEAD_LETTER`: terminal failure; never reclaimed automatically.

The scheduler tables are intentionally excluded from append-only evidence
guards. News revisions, annotations, impacts, translations, failures, model
metadata, and historical predictions keep their existing immutable contracts.

## Included UI Repair

The compact status payload now carries `counts.live_oos_model_groups`. The audit
navigation can display the Live OOS group count before the user opens the
learning view, while the larger curve history remains lazy-loaded.

## Original migration boundary

This PR changes scheduling only. It must not activate the v15 news contract or
switch a model generation.
