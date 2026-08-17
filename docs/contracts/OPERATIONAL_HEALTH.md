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
| `OPS_NEWS_ANNOTATION_CONTRACT_STATE_INVALID` | A superseded display-failure placeholder remains actionable on the latest canonical relevant revision and requires recovery before model use. Irrelevant evidence and noncanonical collection copies do not alert. |
| `OPS_COMPONENT_UNHEALTHY` | A published runtime component is warning, stale, or in error. |
| `OPS_NEWS_SOURCE_UNHEALTHY` | A monitored news source is degraded, stale, or failing. |
| `OPS_RUNTIME_UPDATE_FAILED` | A runtime update failed and was retained or rolled back. |
| `OPS_SYNC_RESOURCE_FAILED` | A named mirror resource failed while the target heartbeat remained available; evidence preserves its upstream error code. |
| `OPS_NEWS_MIRROR_STATE_DIVERGED` | The public news mirror is reachable but violates its state, detail, derived-column, cluster, or completed-contract invariants. |
| `OPS_DAILY_BRIEF_DEFERRED` | Daily Brief generation is waiting for a retry after a coded failure or capacity deferral. |
| `OPS_DAILY_BRIEF_STALLED` | Daily Brief generation remained pending beyond its 30-minute progress boundary. |
| `OPS_DAILY_BRIEF_DEGRADED` | Daily Brief finalized with terminally unreviewed inputs. |
| `OPS_ASSISTANT_JOB_RETRY_LOOP` | One active Cloudflare Assistant job reached its bounded retry ceiling. |
| `OPS_ASSISTANT_PIPELINE_STALLED` | A claimable Assistant queue exceeded its SLA without recent completion. |
| `OPS_ASSISTANT_BACKLOG_OVERDUE` | The oldest claimable Assistant job exceeded its queue SLA while progress continued. |
| `OPS_ASSISTANT_NEW_TERMINAL_FAILURE` | A Cloudflare Assistant queue recorded a new terminal failure. |
| `OPS_ASSISTANT_HEALTH_UNAVAILABLE` | The production page could not read aggregate Assistant D1 health. |
| `OPS_PUBLIC_ENDPOINT_UNAVAILABLE` | The external probe could not obtain a required public page or API. |
| `OPS_PUBLIC_RENDER_CONTRACT_FAILED` | A public page responded but omitted its server-rendered identity marker. |
| `OPS_PUBLIC_RESPONSE_INVALID` | A public operational API returned invalid JSON or the wrong schema. |
| `OPS_PUBLIC_ASSISTANT_HEALTH_UNAVAILABLE` | The external probe could not obtain current Assistant health. |

Provider work that is rejected before transport uses
`MODEL_CAPACITY_DEFERRED`. It is not a provider request failure and must not be
reported as one.

Task-level failure evidence uses these existing stable families:

| Code | Meaning |
| --- | --- |
| `MODEL_CAPACITY_DEFERRED` | Local quota admission rejected the request before transport. |
| `MODEL_ROUTE_DISABLED` | No enabled model route was available for the task. |
| `NEWS_EMBEDDING_BACKFILL_PENDING` | The append-only identity embedding generation is catching up; defer impact work without counting this maintenance state as a model-output failure. Provider capacity admission remains authoritative for every catch-up batch. |
| `MODEL_OUTPUT_CONTRACT_FAILED` | A response arrived but violated the semantic or evidence contract. |
| `MODEL_OUTPUT_INVALID` | A response arrived but could not be decoded as the required schema. |
| `PROVIDER_HTTP_ERROR` | The provider returned an HTTP failure. |
| `MODEL_REQUEST_FAILED` | Transport or request execution failed without a more specific provider code. |
| `SCHEDULER_EXECUTION_FAILED` | The scheduler caught an unexpected task execution exception. |
| `STATUS_SNAPSHOT_REFRESH_IN_PROGRESS` | The local status API is rebuilding its bounded snapshot; runtime observation is deferred without consuming its rollback budget. |
| `STATUS_ENDPOINT_HTTP_ERROR` | The local status API returned an HTTP failure other than the known refresh deferral. |
| `STATUS_ENDPOINT_UNAVAILABLE` | Runtime observation could not reach the local status API. |
| `STATUS_ENDPOINT_URL_INVALID` | A production-shape probe was pointed outside the permitted loopback status endpoint. |
| `STATUS_RESPONSE_INVALID` | The local status API responded but did not return the required JSON object. |

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

The local status snapshot covers the forward-prediction and news data plane.
After its fresh TTL, a still-bounded snapshot is returned immediately with a
`stale` response marker while exactly one background refresh runs. A snapshot
older than the declared maximum stale boundary remains unavailable; it is never
presented as current or silently extended.
The production health route separately reads aggregate Cloudflare D1 state for
Assistant turns, news questions, titles, compaction, and memory indexing. These
queues remain separate from local scheduler counters and expose their own
claimable, scheduled-retry, progress, failure, age, and attempt evidence.

A successful transport response is not proof that a materialized resource is
healthy. Write routes reject rows that violate their public state contract.
After each bounded news synchronization, the synchronizer checks the persisted
D1 state machine, required detail relationship, derived index columns, active
cluster uniqueness, and (after replay completes) the active mirror contract.
During a contract replay, derived-column checks apply only to rows already
written under the current contract because handover deliberately neutralizes
old candidate flags. The completed-contract gate then requires every active row
to use the current contract.
Any mismatch is retained as a resource-level error with bounded counts and is
promoted to `OPS_NEWS_MIRROR_STATE_DIVERGED`. Every other optional mirror
resource failure retains its own upstream code under
`OPS_SYNC_RESOURCE_FAILED`; it must not collapse into an uncoded component
warning.

Runtime rollout observation is also a state machine, not a binary HTTP probe.
A candidate preflight validates generation completeness and every production
contract that can be proven from the copied evidence database, but it does not
require a post-generation live decision that the candidate has not yet had an
opportunity to produce. After installation, the observation state remains the
authority for that temporal proof: the candidate must produce two subsequent
five-minute decision cycles before activation, and an eligible market closure
pauses rather than waives that requirement. A code-only update therefore does
not fail preflight merely because the market cannot currently create a new
decision, while a model-generation handover still cannot become active without
live evidence.
A bounded stale snapshot remains suitable for production-shape validation while
its single background refresh runs. When no bounded snapshot exists, the
explicit `STATUS_SNAPSHOT_REFRESH_IN_PROGRESS` response is a bounded deferral
and must not consume the candidate rollback budget. Other HTTP, transport,
schema, or production-shape failures remain failures. The update state records
the deferral code and time, and clears them only after a complete production
snapshot passes again.

Preview never presents production D1 alerts as branch-current evidence. A
separate scheduled GitHub Actions probe checks the public live, status, and
health pages plus the status and Assistant-health APIs from outside Cloudflare.
It detects endpoint loss, invalid API contracts, and missing server-rendered
page markers without creating a second deployment plane. Failures are visible
as failed `Production health` workflow runs with a stable code.

No HTTP probe can prove that every browser and device successfully hydrated or
painted the page. Client-only rendering faults still require browser telemetry
or a reproduced browser check. This is an explicit observability boundary, not
a claim that the client surface is healthy.
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

The outcome settler runs inside the supervised collector loop. Its health uses
that loop's successful heartbeat, not the timestamp of the most recently
appended outcome. A quiet interval with no decision past its 30-minute horizon
is valid idle work and must not become a stale-component alert.
