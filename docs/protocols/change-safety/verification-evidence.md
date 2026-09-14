# Verification Evidence

Read when selected by the [Change Safety Protocol](../CHANGE_SAFETY.md).

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

