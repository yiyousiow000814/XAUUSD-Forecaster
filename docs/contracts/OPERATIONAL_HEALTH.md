# Operational Health Contract

## Purpose

The system must make unexpected runtime behavior diagnosable without an
operator querying SQLite or reading process output. A live heartbeat proves
only that a process is running. It does not prove that work is progressing,
capacity is usable, or outputs remain within expected bounds.

Operator presentation has three independent axes. API read state is
`CURRENT`, `REFRESHING`, `STALE_SNAPSHOT`, or `UNAVAILABLE`; a failed refresh
with a prior snapshot retains the last factual state and identifies the stale
snapshot. Live-market state is `LIVE`, `MARKET_CLOSED`, or
`MARKET_DATA_UNAVAILABLE`. Operational state is `HEALTHY`, `WARNING`, or
`ERROR` and comes from authoritative component and scheduler detectors.
`system.online` is only live quote/decision readiness and is never global
operational health.

## Stable error codes

Every published operational alert has a stable uppercase `code`, severity,
scope, concise user-facing explanation, and bounded evidence fields. Messages
may improve without changing the code. A code changes only when the failure
meaning or required operator response changes.

`xauusd_forecaster/operational_codes.json` is the authoritative machine-readable
catalog. It classifies alert, task-failure, and structured health-reason codes
with a category, root-cause family, default root/symptom/state role, recovery
policy, Chinese title, and bounded description. Python and TypeScript consume
that same file; Markdown must not duplicate its complete mapping.

Every emitted event is normalized through the catalog and retains `code`,
`severity`, `scope`, `blocking`, `message_zh`, and bounded `evidence`. It also
publishes `category`, `root_cause_family`, `role`, and `recovery_policy`.
Unknown runtime codes remain visible with an explicit taxonomy error. Contract
tests fail when a published `OPS_*` code is absent from the catalog or when an
emitter uses a severity outside that alert's `allowed_severities`. A runtime
metadata mismatch remains a visible alert with an explicit taxonomy error; it
must not be dropped or interpreted as healthy.

Stable task/provider failure codes published through `failure_code`,
`latest_failure_code`, `dominant_failure_code`, or actionable failure counts
must also be cataloged. This includes the embedding capacity, throttle,
transport, and response-validation family. `UNCLASSIFIED` is the sole bounded
absence sentinel: it means no stable dominant failure family was available,
is intentionally outside incident correlation, and must conservatively produce
no reason-to-root match. Arbitrary exception text is never a failure code.

Provider work rejected before transport preserves the admission layer that
deferred it. Local account/model quota uses `MODEL_CAPACITY_DEFERRED`; adaptive
provider pacing uses `PROVIDER_DISPATCH_DEFERRED`. Neither code proves an HTTP
request was sent.

New code paths must reuse a catalog meaning or update the canonical catalog.
They must not persist a changing exception sentence as the only diagnostic key.

## Incident projection

Detector execution remains in its authoritative domain. The backend event set
remains authoritative for safety gates and blocking state. The Web incident
correlator is a deterministic presentation projection only; it creates no
mutable incident database and cannot weaken scheduler or health decisions.

Correlation uses controlled codes, scopes, root-cause families, and structured
evidence. It never parses human-readable messages. Local capacity, provider
pacing, and model-output failures remain separate families. Queue backlog,
stall, Daily Brief deferral, and each semantic-component pending, recovering,
terminal, or overdue reason join a
root incident only when coded evidence establishes the relationship. When a
stage has multiple candidate roots, actionable failure counts select a unique
matching failure family; an absent or ambiguous match remains standalone. An
unexplained component reason remains independently visible. The authoritative
raw component event is retained exactly once in technical evidence; derived
reason projections do not duplicate it or transfer unrelated blocking state.

Semantic lifecycle is incident-scoped. One component event may contain reasons
for multiple semantic stages, but each projected incident evaluates only its
own pending, recovering, overdue, or terminal projections. The incident that
retains a shared nonblocking raw event for audit does not inherit another
stage's lifecycle, severity, or blocking state. If a shared aggregate component
event is itself `ERROR` or blocking and cannot be attributed to one projected
incident, it remains visible as a separate component-level fault. In every
case, the authoritative raw component event is retained exactly once.

An incident retains a deterministic key, category, maximum child severity,
conservative blocking flag, active/recovering state, action state, root event,
related events, affected scopes, bounded metrics, and technical-event count.
`AUTO_RECOVERING` is an internal presentation state for a bounded automatic
retry/recovery attempt that is active; it does not mean recovery succeeded or
is guaranteed. The operator surface calls this state "automatic retry". It may
be shown only when current-instance structured evidence contains either a
controlled `*_RECOVERING` reason whose backend contract guarantees a scheduled
retry, or scheduler evidence with `claimable=false` and a non-null
`next_retry_at`. A catalog `recovery_policy=AUTO`, historical progress, or
human-readable error text alone is not current retry evidence.

Blocking, `*_TERMINAL`, and `*_OVERDUE` evidence takes precedence over an
automatic retry and requires attention. A successful completion or current OK
state removes or resolves the active incident; an overdue retry becomes an
error requiring attention; a terminal/dead-letter outcome becomes an error
requiring attention. Claimable work without progress remains
`ACTION_REQUIRED`. Technical details lead with translated structured state,
component, reason, and human-formatted duration. Exact bounded raw codes and
fields remain available in a second nested disclosure.

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
Production pages show a global incident banner linking to the health page. The
Health page leads with correlated incident cards and keeps raw codes, scopes,
evidence, local scheduler progress, and Assistant D1 queue evidence in
accessible technical disclosures. Banner counts use blocking/error incident
count, not raw event count; a blocking child keeps its incident globally visible.
The operator-facing header counts every unique affected scope/component,
including each incident root, but not same-scope symptoms, retries, or technical
events more than once.
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
The news collector pulses every 30 seconds. A pulse no older than 60 seconds is
current; 60 to 300 seconds is a non-blocking late/grace state; and more than
300 seconds is stale, matching the existing supervisor failure boundary.
While its lifecycle is `STARTING`, a pulse within that 300-second heartbeat
boundary is a non-blocking startup warning rather than readiness; an older
pulse is stale. The supervisor's separate process-start timeout remains the
authority for a startup that stays alive but never becomes ready.
Source-poll completion timestamps never substitute for this process heartbeat.
Each source retains its own registered cadence, bounded retry, and freshness
contract.
Broker-native cTrader `Symbol.MarketHours` is authoritative for daily market
closure. Its `market-session.json` heartbeat is state telemetry and must remain
fresh on the Algo timer independently of quote ticks. Python and dashboard code
must not hard-code or infer a daily maintenance window. Missing or stale broker
evidence outside the bounded weekend fallback remains `DATA_UNAVAILABLE`; quote
or decision silence alone must never be normalized to closure.

Authoritative `CLOSED` and bounded weekend-fallback `WEEKLY_CLOSED` suspend only
freshness clocks whose outputs are not expected during closure. Quote, decision,
outcome, and decision-time semantic-snapshot components report `MARKET_CLOSED`
instead of aging into component faults. A clock-classified weekly closure records
the payload generation time as its observation boundary. Fresh broker `OPEN`
evidence immediately restores normal quote and decision freshness enforcement.

News collection, annotation, impact processing, sources, and scheduling continue
independently while XAUUSD is closed. An old decision-time semantic snapshot or
its old pending reason must not masquerade as a newly observed semantic failure
when no market decision is expected; current independent news and AI failures
remain visible through their own component and scheduler detectors.

Decision-time semantic health keeps incomplete current semantics and impact
evidence visible without acting as the model-input gate. The separate frozen
news-input coverage contract in `NEWS_EVIDENCE.md` decides whether a news-aware
identity can run. Operator presentation classifies a future bounded retry as
automatic retry and keeps the pending reason visible;
it never presents that retry as completed or guaranteed recovery. It escalates
terminal work or a retry overdue beyond the scheduler's task SLA to error.
Local admission (`MODEL_CAPACITY_DEFERRED` and `LOCAL_TPM_LIMIT`),
provider pacing (`PROVIDER_DISPATCH_DEFERRED`), and provider transport
(`PROVIDER_HTTP_ERROR`) remain distinct evidence. Presentation severity never
changes, deletes, or retroactively enriches decision snapshots, prediction
visibility, learning admission, training rows, or execution-learning evidence.

The outcome settler runs inside the supervised collector loop. Its health uses
that loop's successful heartbeat, not the timestamp of the most recently
appended outcome. A quiet interval with no decision past its 30-minute horizon
is valid idle work and must not become a stale-component alert.
