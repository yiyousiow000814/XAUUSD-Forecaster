# Change Safety Protocol

Use this protocol before a material change to architecture, persistence, state,
storage, deployment, process lifecycle, ownership, a public or provider API, a
CLI or interface, authentication, concurrency, cross-version behavior,
background services, rollback, recovery, or irreversible mutation. Tiny local,
behavior-preserving edits do not require it.

## Change contract

Record before editing:

1. **Change:** the exact boundary changing.
2. **Invariants:** everything that must remain true.
3. **Ownership:** code, mutable state, persistent data, config, secrets, cache,
   receipts, and lifecycle authority.
4. **Boundary and impact graph:** callers, callees, and changed interfaces.
5. **Compatibility matrix:** applicable `new -> new`, `new controller -> old
   runtime`, `old controller -> new runtime`, `new code -> old state`, `old code
   -> new state`, rollback-to-old, and partial-migration restart combinations.
6. **State transitions:** before, during, after, retry, and crash/restart.
7. **Failure matrix:** failure at zero progress, partial progress, after an
   irreversible mutation, after the new component starts, and before commit.
8. **Recovery authority:** prove the old known-good system can recover with
   captured old authority, independently of the failing change.
9. **External truth:** verify applicable pagination, truncation, eventual
   consistency, omission, encoding, rate limits, real envelopes, and versions.
10. **Observability:** distinguish delayed, unavailable, corrupt, partial, and
    provider-omitted results.
11. **Rehearsal:** run the production-shaped real interaction unit tests cannot
    prove. For every OS, filesystem, process, provider, network, or other
    environment-sensitive assumption, name the required environment-equivalent
    rehearsal or real-resource non-mutating preflight. A synthetic or temporary
    resource test is supporting evidence only and cannot establish the real
    environment invariant by itself.
12. **Rollout and rollback:** stage the change and prove rollback before switch.
13. **Cleanup:** state when temporary bridges and adapters are removed.

## Runtime bundles, locators, and maintenance

A runtime bundle must be dependency-closed, not merely self-consistent with its
own manifest. Derive direct and transitive runtime dependencies, require each to
be declared, copied, hashed, and verified, and rehearse startup from an isolated
staged root that cannot resolve omitted files from a development checkout.

For every persistent filesystem locator, record:

- its authoritative owner and permitted roots;
- whether it is absolute, relative, or otherwise portable;
- the finite relocation behavior for known old roots;
- old-code/new-state and new-code/old-state compatibility;
- whether its bytes participate in artifact hashes, generation identities,
  receipts, or immutable evidence.

Unknown roots and traversal fail closed. Do not rewrite immutable locator-bearing
content without accounting for every derived identity and receipt.

Heavy maintenance begins only after critical startup viability is established.
It needs an explicit single owner, a bounded completion or failure receipt, and
idempotent restart behavior. A crash or restart must not multiply the same heavy
operation, and recovery may clean only temporary state proven to belong to that
owner.

The implementer's assumptions are not independent evidence. For large changes,
use non-overlapping architecture, recovery, or final-evidence review when
delegation is authorized and useful; do not duplicate broad scans.

## Verification latency and composition

Required verification has an explicit latency budget. Extending a timeout is
not a correction for state-space explosion or inefficient test architecture.
Formal models must prove invariants at the smallest correct abstraction and
compose bounded subsystem proofs instead of exhaustively multiplying independent
implementation state. Before changing a verification timeout, ask:

- Is verification modeling the invariant or simulating implementation detail?
- Are independent subsystems being multiplied unnecessarily?
- Can the proof be decomposed without weakening coverage?
- Does the required gate meet its latency budget?
- Is a timeout increase hiding a modeling defect?

Expiry of machine freshness evidence should renew the machine observation, not
repeat unchanged human acceptance. Human qualification is invalidated by a
behavior change; machine freshness is renewed only by a complete, continuous
reinspection of the qualified boundary.

Separate durable qualification from renewable freshness. Expiration of a
freshness observation must not invalidate an unchanged expensive or human
qualification. Revalidate only the smallest authority that can actually have
changed.

Release evidence should form a dependency graph keyed by behavior-affecting
identity. A failure or change invalidates only its dependent evidence, not the
entire release.

Any state-space reduction assumption must be explicit, documented, and owned by
another verified shard or interface contract. Required safety and liveness may
run independently when they do not require the same state dimensions.

A required test gate must be organized by independent contract ownership. When
an integration suite exceeds its latency budget, profile it and compose bounded
parallel contract shards instead of serially repeating every historical
regression.

## Control-Proportional Assurance

Required assurance is proportional to control. Classify each material evidence
source before assigning it to a gate:

- **Controlled exact:** the system owns production, identity, persistence, and
  completeness. Required safety facts are deterministic, unexplained absence or
  corruption fails closed, and receipts bind the exact transaction.
- **External authoritative/eventual:** returned values are authoritative, but
  delivery, propagation, ordering, pagination, sampling, or temporary
  availability belongs to a provider. Declare required observed coverage,
  uncertainty and tolerance budgets, maximum active wait, monotonic
  accumulation, narrow retry, corroboration, and the terminal non-success state.
- **External advisory:** useful diagnostic or corroborating evidence that cannot
  become the sole safety authority or silently invent missing facts.

Exact internal invariants stay exact. Tolerance for an external delivery channel
does not weaken product thresholds, reduce required observed samples, conceal a
hard failure, or pretend omitted evidence exists. Accepted external evidence is
accumulated monotonically; later partial, reordered, or duplicated queries do
not erase it, while conflicting identity or values fail closed.

External failures may be correlated across categories that appear independent.
Do not assume only one bucket, family, or request class can be affected unless
the provider contract guarantees that independence. Bound recovery by total
work and required confidence. Delivery tolerance never applies to CPU or other
acceptance limits, error limits, required observed quotas, Candidate identity,
or correctness receipts.

Human acceptance is invalidated by changes to the behavior it qualifies, not by
unrelated repository movement. Persist a versioned qualification key over the
behavior-affecting boundary, include external configuration owned outside the
repository, and reuse a valid human qualification only when that exact key and
its authority remain unchanged. Reuse evidence is a distinct machine receipt;
it never claims that a person repeated the acceptance steps.

A noisy externally measured metric must not become a deterministic terminal
failure from one successful observation when the provider contract distinguishes
occasional overage from actual resource termination. Preserve the observation
and use one bounded, same-shape confirmation to distinguish isolated variance
from reproducible pressure. Confirmation never weakens internally controlled
exact evidence, required quotas, product thresholds, identity, or actual
resource-failure handling.

Retry only the deficient stage or family, preserve independent accepted stages,
and persist budgets across restart. Reuse evidence only when a versioned key
contains every input capable of changing the measured behavior; unrelated Git
movement is not invalidation authority, while any behavior-affecting mismatch
requires fresh evidence. Distinguish provider delay or unavailability from a
system failure in state, UI, and audit history.

Human intervention is reserved for an actual human-authority gate, a
deterministic safety blocker, or ambiguity outside the declared external
uncertainty budget. Routine provider delay, omission, or retry must not ask a
person to certify machine evidence.

The reusable escaped-blocker lesson is: do not make a best-effort or eventually
consistent external telemetry channel the sole exact transaction ledger, and do
not demand completeness stronger than its contract. Preserve exact internally
controlled evidence and apply an explicit confidence model to external
measurements.

## Pre-mortem

- What failure appears only after all earlier gates pass?
- Which old/new combination has never executed?
- Which hidden state, process, provider, filesystem, encoding, or identity
  boundary is assumed?
- What happens at the worst possible failure point?

## Escaped-blocker learning

For each escaped deterministic blocker:

1. Fix the concrete blocker.
2. Identify the violated generic invariant.
3. Inspect siblings governed by that invariant.
4. Strengthen one family-level regression at the correct abstraction.
5. Determine why design, review, and testing did not expose it.
6. Improve durable guidance only when the lesson generalizes.
7. Continue the authorized workflow when the small-blocker conditions below
   remain satisfied.

Do not require new user authorization when the root cause is proven, the defect
remains inside the authorized family, the correction is narrow and reversible,
no new production mutation or security/access authority is required, no
external ambiguity must be guessed, and no acceptance gate is weakened. Stop
for a new failure family, changed mutation authority, destructive or
irreversible action, human identity/Access, uncertain root cause, loss of safe
Stable recovery, or a proposed safety-contract relaxation.

## Real composition execution

Static source inspection is not sufficient when correctness depends on language
or runtime composition semantics such as dot-sourcing and scope, environment
inheritance, CLI parsing, working directory, import resolution, subprocess
quoting, environment precedence, or serializer/consumer wiring.

Before completing a material script or orchestration change, record a compact
execution matrix containing the actual runtime, entrypoint, caller, callee or
import, parameter binding, filesystem roots, working directory, success case,
and fail-closed case. At least one automated test must execute every changed
critical composition boundary with the real runtime involved.

Expected lifecycle:
`request -> change contract -> impact graph -> compatibility/failure/recovery
matrix -> test and rehearsal plan -> implementation -> independent verification
-> rollout -> post-release cleanup`.

## Pre-Completion Adversarial Review

A non-trivial change is not complete merely because focused tests, the full
suite, lint, builds, or CI pass. After implementation is substantially finished,
independently review the final exact head as if another engineer wrote it.

For every changed cross-boundary workflow, trace the real production path:

`producer -> state generation/storage -> transport -> routing -> production entry point -> consumer -> externally observable behavior`

Use actual production call sites, configuration and route names, coordination
keys, schemas, ownership, and deployed entry points. A helper-level test does
not prove integration when production wiring can supply different values or
take another path. The review must establish:

- what invokes the workflow in production and who consumes every changed field;
- behavior at first start with no state, genuine empty/zero and partial state,
  stale state, process restart, machine restart, reconnect, dependency failure,
  and later recovery;
- compatibility with old Stable, activation ordering, Promote, Reverse Stable,
  and rollback where those boundaries apply;
- what grows, what bounds each operation and transport, whether optional
  failure is isolated, and whether every recurring responsibility has one owner;
- whether compact state can erase richer authority, mutable external state can
  become accidentally terminal, and the production entry point matches the
  tested assumptions; and
- whether the result works from observable contracts without relying on the
  implementation author's intended design.

If this review finds a defect, fix it, rerun affected focused tests, repeat the
review on the new exact head, and then run final exact-head validation. Before
reporting completion, inspect the final diff, production callers, consumers,
state transitions, lifecycle and restart paths, and tests independently. The
implementation plan, earlier reasoning, and green tests are not evidence of the
final implementation by themselves; completion reports must cite evidence from
the final exact head.

When human or code review finds a defect after implementation was considered
complete, ask which reusable review rule or authoritative contract failed to
catch that class. Update the appropriate rule or contract when the invariant
generalizes beyond the incident; do not create permanent rules for one-off
typos.

## Boundary acceptance

Classify each non-success state as transient/pending, externally retryable,
operator-reviewable, or terminal deterministic failure. Externally mutable
readiness for the same immutable identity remains retryable, fail closed, and
non-promotable; test rejection and recovery on that same identity. Terminal
failure requires a reason the immutable input cannot legitimately succeed.

For changed serialized boundaries, review names, shapes, units, optionality,
ordering, and visible semantics across the producer and every meaningful
consumer. Changed semantics need consumer-level behavioral coverage. Follow
[Hosting Boundaries](../contracts/HOSTING_BOUNDARIES.md) for baseline/delta,
ownership, growth, and recovery invariants.

Wiring-sensitive tests exercise actual production routes, entrypoints,
configuration names, coordination keys, serializers, consumers, and service
registries. Helper tests must also prove their production caller supplies all
semantics-controlling values. Differences in test identifiers must be
intentional and independently covered.
