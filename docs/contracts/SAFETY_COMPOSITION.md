# Safety Composition Contract

Safety-sensitive behavior is correct only when its rules compose with every
actor that can observe or mutate the same state. A local fail-closed check is
not sufficient if it can deadlock valid work, race another owner, make retry
unreachable, or make recovery depend on the failed precondition.

## Design record

Before implementation, the owning design must identify:

- actors and external dependencies;
- durable states and internal checkpoints;
- legal transitions and the authority that performs each mutation;
- safety invariants and liveness requirements;
- failure classifications and exact retry paths;
- crash, process-restart, and machine-restart recovery;
- rollback or return-to-previous-version behavior;
- timeout, expiry, lease, cleanup, and stale-actor behavior; and
- the runtime owner that maintains every long-term invariant.

The review must answer who can mutate between check and use, whether a
supervisor can undo an intentional freeze, whether accepted evidence survives a
retry, whether cleanup can remove active or in-flight state, whether recovery
works when normal-state assumptions are already false, and whether healthy
dependencies still permit progress.

## Simplification rule

Simplify before adding coordination. A new durable state, lock, hold, watchdog
exception, retry mode, recovery mode, or compatibility branch is permitted only
when the design explains why an existing authoritative transaction or parent
phase cannot enforce the invariant.

Operator lifecycle states describe goals, not function calls. Internal
checkpoints may be persisted for idempotence and crash recovery, but must not
become first-level operator concepts unless the operator has a distinct choice
or obligation there. Each persisted checkpoint still requires entry, exit,
timeout, retry, restart, rollback, observability, and test semantics.

## Required properties

Safety states what must never occur. Liveness states what must eventually be
possible under explicit healthy and fairness assumptions. A design is
incomplete when it prevents unsafe action but strands a retryable or recoverable
state.

Interaction defects require a family-level composition review. Tests must
cover the relevant actor family and transition boundary; a scenario suite is
not evidence of completeness when an actor or transition was omitted. Use
model checking for concurrent lifecycle and ownership protocols,
property-based or cross-runtime contracts for transforms and serialization,
capacity tests for bounded work, adversarial tests for authority boundaries,
and outside-in acceptance for deployed user-visible behavior.

## Production recovery order

Safe recovery and permanent correction are distinct. When the running system
is degraded, preserve forensic evidence and restore the last-known-safe Stable
service through the safest supported path before redesigning the failed
mechanism. Permanent correction then repairs the owning invariant and proves
the full workflow.

Any mechanism that can stop, isolate, or fence a production owner is incomplete
without a direct legal transition back to normal Stable operation after an
abandoned, expired, paused, invalidated, or crashed release attempt. Recovery
must be reachable from the degraded state and must not require the failed
normal-state precondition. If safe restoration is impossible, the mechanism
fails closed with the exact missing recovery fact and retains all evidence.
