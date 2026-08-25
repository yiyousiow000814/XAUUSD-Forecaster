# Repository Architecture Rules Contract

## Purpose and scope

This is the repository-wide architecture contract. It tells a change author
which questions must be answered before runtime or data-flow code changes. It
does not replace the more specific contracts for [system boundaries](SYSTEM_BOUNDARIES.md),
[forward-only evidence](FORWARD_ONLY.md), [hosting](HOSTING_BOUNDARIES.md),
[release control](RELEASE_CONTROL.md), [Preview isolation](PREVIEW_ISOLATION.md),
or [operational health](OPERATIONAL_HEALTH.md).

The current topology is mapped in
[`SYSTEM_ARCHITECTURE.md`](../design/SYSTEM_ARCHITECTURE.md). A proposal or
open pull request is not current architecture.

## Rule 1 — Ownership

**Plain meaning:** One important fact has one authoritative owner.

**Question:** Which component alone may create or transition this fact, and
where is that authority persisted?

**MUST**

- Name one authoritative writer or transition owner for important state.
- Mark every projection with its source authority and replacement policy.
- Preserve immutable evidence separately from mutable operational state.

**MUST NOT**

- Let a consumer infer the same fact again from adjacent fields.
- Let a mirror become recovery authority unless a specific contract permits it.
- Give two services competing write authority for one transition.

**Good current example:** the Collector appends decisions and outcomes to the
local forward ledger. Dashboard read models and public D1 snapshots project
that evidence; they do not replace it.

**Known current risk:** `forward_ledger.py` is the shared persistence surface
for many owners. Table-level authority is real, but the flat module makes the
owner boundary harder to see.

**Required PR evidence:** owner, store, allowed writers, readers, mutation
class, and recovery source; tests must prove unauthorized sibling paths cannot
perform the transition.

**Authoritative detail:** [Forward-only evidence](FORWARD_ONLY.md),
[Evidence lanes](EVIDENCE_LANES.md), and [Assistant state](ASSISTANT_STATE.md).

**Map notation:** every important store row names `OWNER`, `AUTHORITY` or
`MIRROR`, and `WRITERS`.

## Rule 2 — Boundary

**Plain meaning:** Work with different SLA, growth, authority, or failure
behavior needs an explicit boundary.

**Question:** Is this a process, thread, request, store, transport, or merely a
function boundary?

**MUST**

- Name process, thread, request, Worker, Durable Object, store, and transport
  boundaries accurately.
- Isolate work when its authority, SLA, growth, or failure behavior differs.
- State what still shares a process or store after scheduling is separated.

**MUST NOT**

- Call a function split failure isolation.
- Call a thread a process.
- Put states owned by different authorities into a universal state machine for
  convenience.

**Good current example:** annotation is a separate Python process. Background
training has a durable owner and independent SQLite connection, but is honestly
described as a thread inside the Collector process.

**Known current risk:** decision and training no longer wait on one another,
but still share one OS process failure domain.

**Required PR evidence:** before-and-after execution topology, lifecycle owner,
startup/shutdown behavior, shared dependencies, and failure-domain tests.

**Authoritative detail:** [System boundaries](SYSTEM_BOUNDARIES.md) and
[Hosting boundaries](HOSTING_BOUNDARIES.md).

**Map notation:** nodes carry an explicit `PROCESS`, `THREAD`, `REQUEST
HANDLER`, `WORKER`, `DURABLE OBJECT`, `STATIC`, or `STORE` label.

## Rule 3 — Critical Path

**Plain meaning:** Time-critical work must not wait for optional or historical
work.

**Question:** Can this dependency delay a five-minute decision, current
authority, heartbeat, readiness response, or first paint?

**MUST**

- Make every dependency of a critical path visible on the architecture map.
- Consume the last published valid model or state while background work runs.
- Build growing summaries outside request and heartbeat paths.

**MUST NOT**

- Make the five-minute decision path wait for training, reconciliation,
  Dashboard, Cloudflare, an LLM, history building, or optional sync.
- Build growing history while answering heartbeat or current-authority reads.
- Replace last-good state merely because optional refresh failed.

**Good current example:** a valid active generation lets the Collector start
the decision clock while reconciliation and retraining run through
`BackgroundTrainingOwner`.

**Known current risk:** startup still performs synchronous reconciliation when
no compatible active generation exists. This is intentional fail-closed
safety, not ordinary healthy-start behavior.

**Required PR evidence:** critical-path call graph, maximum added latency,
dependency failure fixture, and proof that last-good state remains usable.

**Authoritative detail:** [Hosting boundaries](HOSTING_BOUNDARIES.md),
[Operational health](OPERATIONAL_HEALTH.md), and
[Release control](RELEASE_CONTROL.md).

**Map notation:** critical arrows are named and optional arrows are explicitly
marked `OPTIONAL`.

## Rule 4 — Bounded Work

**Plain meaning:** One operation must have a known maximum amount of work.

**Question:** What is the item, byte, page, time, or equivalent upper bound for
one cycle, request, migration, retry, and transport operation?

**MUST**

- Give long-running cycles, requests, syncs, migrations, retries, and pages an
  explicit bound.
- Enforce serialized byte bounds independently from UI display limits.
- Keep the bound valid when authoritative data grows by 100 times.

**MUST NOT**

- Scan total history in a critical or request path.
- Treat a display count as a transport guarantee.
- Raise a host limit as the default repair for an unbounded projection.

**Good current example:** dashboard sync admits one heavy resource per cycle,
uses cursor pages and byte-bounded batches, and publishes the heartbeat through
an independent lane.

**Known current risk:** some local builders can deliberately consume full
history outside the request path. Their execution owners and rebuild modes must
remain explicit so they cannot drift into first paint.

**Required PR evidence:** configured and serialized bounds, an over-limit
fixture, scale behavior, and the failure returned for one oversized item.

**Authoritative detail:** [Hosting boundaries](HOSTING_BOUNDARIES.md) and the
[paged dashboard design](../design/PAGED_DASHBOARD_HISTORY.md).

**Map notation:** growing arrows show `PAGE`, `BATCH`, `BYTES`, or the equivalent
bound beside their cursor or checkpoint.

## Rule 5 — Incremental First

**Plain meaning:** Do not repeat unchanged historical work.

**Question:** Which cursor, dirty queue, source revision, materialized state,
checkpoint, append-only delta, or idempotent page proves what changed?

**MUST**

- Prefer durable incremental progress over repeated history reconstruction.
- Make full rebuilds explicit, versioned, restart-safe, audited, and outside
  critical paths.
- Record a completion marker before a rebuild becomes readable authority.

**MUST NOT**

- Restart from zero merely because a process restarts.
- Advance a cursor before its target acknowledges the corresponding write.
- Mix partial generations into an active model or read model.

**Good current example:** training materialization uses dirty revisions, a
200-row page, a durable cursor, and atomic replacement when contract drift
requires a full rebuild.

**Known current risk:** an actual retrain still consumes the complete
materialized training dataset. Incremental materialization does not make all
training computation constant-time.

**Required PR evidence:** progress state schema, idempotent retry fixture,
restart behavior, completion marker, and deliberate rebuild command or mode.

**Authoritative detail:** [Forward-only evidence](FORWARD_ONLY.md),
[Assistant state](ASSISTANT_STATE.md), and
[Paged dashboard history](../design/PAGED_DASHBOARD_HISTORY.md).

**Map notation:** an incremental path names its `CURSOR`, `DIRTY REVISION`,
`SOURCE REVISION`, `CHECKPOINT`, or `GENERATION`.

## Rule 6 — Failure Isolation

**Plain meaning:** One broken subsystem must not unnecessarily stop unrelated
healthy work.

**Question:** Which state becomes degraded, what last-good state remains, and
which unrelated work continues?

**MUST**

- Attribute failure to the resource that failed.
- Preserve unrelated last-good projections and immutable evidence.
- Make retries bounded, backed off, durable, and restart-safe.
- Expose degraded state without turning it into a false global outage.

**MUST NOT**

- Clear unrelated state after one optional resource fails.
- Retry every optional resource in the same cycle until success.
- Stop valid decision append because a provider, Dashboard, Cloudflare, or
  training operation failed.

**Good current example:** Dashboard read models replace each resource
atomically and retain its prior valid model on rebuild failure; critical status
has a separate bounded cache.

**Known current risk:** multiple owners share the local SQLite database and the
Collector process. WAL, separate connections, leases, and lock retry reduce
coupling but do not create complete process or store isolation.

**Required PR evidence:** injected failure at the owned resource, bounded retry
schedule, last-good assertion, unaffected-work assertion, and operator-visible
degraded evidence.

**Authoritative detail:** [Operational health](OPERATIONAL_HEALTH.md),
[Hosting boundaries](HOSTING_BOUNDARIES.md), and
[Preview isolation](PREVIEW_ISOLATION.md).

**Map notation:** each subsystem states its failure domain and the work that
continues outside it.

## Generated architecture evidence

Repository source, semantic declarations, and executable contracts are the
authoritative compiler inputs. Files under `architecture/generated/` are
derived and MUST NOT be edited directly. Architecture-affecting changes MUST
regenerate artifacts, pass the byte-for-byte drift check, and review the
generated architecture diff.

Static evidence proves only what its extractor observes. Imports do not prove
owner, authority, criticality, runtime execution, or failure isolation.
Observed imports and allowed dependency policy MUST remain separate. Semantic
claims MUST bind to current source facts with explicit cardinality, and a
declaration alone MUST NOT be presented as verified. Exact, fallback,
unresolved, stale, contradicted, test, runtime, and mutation evidence remain
distinct categories. Generated artifacts MUST contain repository-relative
spans and normalized metadata only, never secrets or production values.

**Authoritative detail:** [Compiler design](../design/ARCHITECTURE_EVIDENCE_COMPILER.md),
[artifact protocol](../protocols/ARCHITECTURE_ARTIFACTS.md), and
[evidence runbook](../runbooks/ARCHITECTURE_EVIDENCE.md).

## Code organization consequences

- Packages and folders should correspond to subsystem owners where practical.
- Runtime entry points should contain thin process and thread orchestration;
  domain rules should not remain permanently in `scripts/`.
- An abstraction must hide a real dependency or boundary, not merely wrap one
  function.
- Do not create `Manager`, `Factory`, `Base`, or `Adapter` layers to make a
  layout appear sophisticated.
- Do not create a generic framework before a second implementation or a clear
  boundary requires it.
- A temporary compatibility import needs an owner, a removal condition, and a
  bounded handover.
- `CURRENT`, `PENDING`, and `TARGET` architecture must never be combined into
  one factual diagram.

## Change evidence template

Every runtime, state, API, storage, scheduler, background-owner, or deployment
change answers this before implementation:

```text
Owner:
Authoritative state/store:
Execution boundary:
Critical or optional:
Maximum work per operation:
Incremental cursor/revision/checkpoint:
Failure domain:
Last-good/recovery behavior:
Architecture documents affected:
```
