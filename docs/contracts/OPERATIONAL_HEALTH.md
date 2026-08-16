# Operational Health Contract

## Purpose

The system must make unexpected runtime behavior diagnosable without an
operator querying SQLite or reading process output. A live heartbeat proves
only that a process is running. It does not prove that work is progressing,
capacity is usable, or outputs remain within expected bounds.

## Stable error codes

Every published operational alert has a stable uppercase `code`, severity,
scope, concise user-facing explanation, and bounded evidence fields. Messages
may improve without changing the code. A code changes only when the failure
meaning or required operator response changes.

The initial cross-component catalog is:

| Code | Meaning |
| --- | --- |
| `OPS_AI_JOB_RETRY_LOOP` | One active AI job has been claimed unusually often. |
| `OPS_AI_ROUTE_CAPACITY_SATURATED` | Capacity deferrals exceed useful completions for a model route. |
| `OPS_AI_PIPELINE_STALLED` | Work exceeded its route-specific SLA and the route made no progress during the monitoring window. |
| `OPS_AI_BACKLOG_OVERDUE` | The oldest currently claimable work exceeded its task-specific queue SLA. Future scheduled retries do not count as overdue backlog. |
| `OPS_AI_FAILURE_RATE_HIGH` | Recent model, transport, or validation failures exceed the expected rate. |
| `OPS_AI_NEW_DEAD_LETTER` | New terminally isolated work appeared during the monitoring window. |
| `OPS_COMPONENT_UNHEALTHY` | A published runtime component is warning, stale, or in error. |
| `OPS_NEWS_SOURCE_UNHEALTHY` | A monitored news source is degraded, stale, or failing. |
| `OPS_RUNTIME_UPDATE_FAILED` | A runtime update failed and was retained or rolled back. |
| `OPS_DAILY_BRIEF_DEFERRED` | Daily Brief generation is waiting for a retry after a coded failure or capacity deferral. |
| `OPS_DAILY_BRIEF_STALLED` | Daily Brief generation remained pending beyond its 30-minute progress boundary. |
| `OPS_DAILY_BRIEF_DEGRADED` | Daily Brief finalized with terminally unreviewed inputs. |

Provider work that is rejected before transport uses
`MODEL_CAPACITY_DEFERRED`. It is not a provider request failure and must not be
reported as one.

Task-level failure evidence uses these existing stable families:

| Code | Meaning |
| --- | --- |
| `MODEL_CAPACITY_DEFERRED` | Local quota admission rejected the request before transport. |
| `MODEL_ROUTE_DISABLED` | No enabled model route was available for the task. |
| `MODEL_OUTPUT_CONTRACT_FAILED` | A response arrived but violated the semantic or evidence contract. |
| `MODEL_OUTPUT_INVALID` | A response arrived but could not be decoded as the required schema. |
| `PROVIDER_HTTP_ERROR` | The provider returned an HTTP failure. |
| `MODEL_REQUEST_FAILED` | Transport or request execution failed without a more specific provider code. |
| `SCHEDULER_EXECUTION_FAILED` | The scheduler caught an unexpected task execution exception. |

New code paths must reuse one of these meanings or add a documented code. They
must not persist a changing exception sentence as the only diagnostic key.

## Coverage

The status payload must cover every published runtime component and news
source, every active scheduler task route, and the separately persisted Daily
Brief state machine. Scheduler evidence includes:

- queued, leased, backing-off, claimable, scheduled-retry, and dead-letter counts;
- successful, deliberately retired, deferred, and failed attempts over the
  current 15-minute window;
- oldest claimable work age and the earliest future retry time;
- highest active claim count and a bounded non-secret job reference.

Counts from articles, event identities, prediction exposures, and training
rows remain distinct. One must never substitute for another in health gates.

This contract covers the local forward-prediction and news data plane published
by the status snapshot. Cloudflare Assistant turn, title, compaction, and memory
index jobs have their own D1 execution contracts and are not represented by the
local scheduler counters. Browser rendering failures and loss of the public
status endpoint itself require an external observer; the application cannot
reliably declare its own endpoint unreachable.
## Visibility

Warnings and errors must be visible without expanding a diagnostic control.
Production pages show a global alert banner linking to the health page. The
health page shows the error code, scope, evidence, and per-route progress.
Preview snapshots must not present frozen operational alerts as current live
state.

## Evidence and safety

Operational summaries are derived from existing durable job attempts, quota
reservations, source polls, component heartbeats, and update state. They expose
no API key, prompt body, article body, account secret, or full internal job ID.
An absent or unreadable evidence source must never be interpreted as healthy.
Supervised workers must refresh their runtime heartbeat independently of batch
completion while a bounded provider or I/O operation is in progress. Progress
counters supplement this pulse; they are not the only proof of liveness.
Expected weekly market closure is the only exception to market-component
freshness alarms: quote, decision, and outcome components report
`MARKET_CLOSED` during that clock window. A clock-classified weekly closure
records the payload generation time as its observation boundary. Missing broker
evidence outside the weekly closure remains `DATA_UNAVAILABLE` and must not be
normalized to healthy.
