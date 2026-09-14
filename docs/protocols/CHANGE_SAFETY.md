# Change Safety Protocol

Use this protocol before a material change to architecture, persistence, state,
storage, deployment, process lifecycle, ownership, a public or provider API, a
CLI or interface, authentication, concurrency, cross-version behavior,
background services, rollback, recovery, or irreversible mutation. Tiny local,
behavior-preserving edits do not require it. A small pure-function repair with
no changed external contract, persistence, ownership, concurrency, or lifecycle
uses focused regression coverage instead; classify the actual boundary, not
keywords such as state or dictionary.

Read the common change contract, pre-mortem, boundary acceptance, and final
review below. Read additional procedures only when the changed boundary needs
them; record applicability in the same change record rather than creating a
second checklist:

| Boundary | Additional procedure |
| --- | --- |
| Runtime packaging, persisted paths, startup/maintenance, scripts or process composition | [Runtime boundaries](change-safety/runtime-boundaries.md) |
| External-provider evidence, reusable qualification/freshness, formal models or verification-gate cost | [Verification and evidence](change-safety/verification-evidence.md) |
| Stateful/concurrent lifecycle | [Safety Composition](../contracts/SAFETY_COMPOSITION.md) |
| Serialized transport, growing data, recurring owners | [Hosting Boundaries](../contracts/HOSTING_BOUNDARIES.md) |
| Production activation or recovery | [Release Control](../contracts/RELEASE_CONTROL.md) and [deployment runbook](../runbooks/CLOUDFLARE_DEPLOYMENT.md) |

These procedures do not change mutation authorization. Deployment follows
[Release Control](../contracts/RELEASE_CONTROL.md): protected `main` feeds
Cloudflare Workers Builds, which deploys one version at 100 percent traffic.
The local service owner updates its single runtime from `main` independently;
it is not a second deployment slot. Recovery corrects forward while preserving
authoritative data. Scope compatibility checks to interfaces and state that can
actually meet during that update order; no blue-green controller/runtime matrix
or retained old-version recovery path is required.

## Change contract

Record before editing:

1. **Change:** the exact boundary changing.
2. **Invariants:** everything that must remain true.
3. **Ownership:** code, mutable state, persistent data, config, secrets, cache,
   receipts, and lifecycle authority.
4. **Boundary and impact graph:** callers, callees, and changed interfaces.
5. **Compatibility:** check changed code against existing production data,
   schema, configuration, and actual producers/consumers. For independently
   updated components (for example, the local sender and Cloudflare Worker),
   check the interface combinations reachable in the actual update order.
   When state or schema changes, cover interrupted migration and restart;
   include existing code reading changed state only if it can still run during
   that transition. A small matrix is useful when multiple combinations are
   reachable; otherwise record the relevant check or why none is needed.
6. **State transitions:** before, during, after, retry, and crash/restart.
7. **Failure matrix:** failure at zero progress, partial progress, after an
   irreversible mutation, after the new component starts, and before commit.
8. **Recovery authority:** capture the known-good identity and prove the
   supported recovery owner can restore service independently of the failing
   component. Use the current release contract for forward repair.
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
12. **Rollout and recovery:** prove the supported recovery path before mutation.
    Follow the current release contract; do not invent retained code slots or
    rollback machinery for a forward-only deployment.
13. **Cleanup:** state when temporary bridges and adapters are removed.

## Pre-mortem

- What failure appears only after all earlier gates pass?
- Which reachable interface or code/data combination has never executed?
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
irreversible action, human identity/Access, uncertain root cause, loss of
safe service recovery, or a proposed safety-contract relaxation.

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
- compatibility with existing code/state, activation ordering, and the supported
  recovery path under the current release contract;
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
ineligible for successful acceptance; test rejection and recovery on that
same identity. Terminal failure requires a reason the immutable input cannot
legitimately succeed.

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
