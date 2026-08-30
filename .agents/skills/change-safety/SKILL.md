---
name: change-safety
description: Review material architecture, persistence, migration, deployment, lifecycle, ownership, provider API, CLI, authentication, concurrency, cross-version, background-service, rollback, recovery, or irreversible changes before implementation and release. Skip tiny local behavior-preserving edits.
---

# Change Safety

Before implementing a material boundary change, read
[`docs/protocols/CHANGE_SAFETY.md`](../../../docs/protocols/CHANGE_SAFETY.md)
completely and produce its Change Contract, impact graph, compatibility matrix,
failure/recovery matrix, test plan, and production-shaped rehearsal plan.

Inspect real old/new interfaces. Do not let a new component be the sole recovery
authority for its own failed rollout. Capture the old known-good identity and
recovery contract before mutation. Unit tests do not replace a real interaction
rehearsal at lifecycle, provider, filesystem, or cross-version boundaries.
For every invariant that depends on OS, filesystem, process, provider, network,
or other environment semantics, identify whether it needs an environment-
equivalent rehearsal or a non-mutating preflight against the real resource.
Synthetic or temporary resources are supporting evidence, not sufficient proof
of an environment-sensitive invariant by themselves.

Treat runtime packaging as a dependency graph: a deployable bundle is complete
only when every direct and transitive runtime dependency is declared, copied,
hashed, and verified from an isolated staged root. For every persisted
filesystem locator, declare its authority, portability, relocation mapping,
old/new compatibility, and hash or receipt consequences. Schedule heavy
maintenance only after critical startup viability, behind an explicit owner,
and make its retry behavior idempotent and bounded across restart.

Classify every material evidence source as `controlled exact`, `external
authoritative/eventual`, or `external advisory`. For external evidence, record
the provider's actual completeness contract, an explicit uncertainty/tolerance
budget, bounded retry, corroboration, fallback, and whether human action is ever
permitted. Ask what the system can prove itself, what the provider guarantees,
what is only empirical, and what happens when evidence is omitted, delayed,
duplicated, reordered, or sampled. Do not mistake missing provider evidence for
product failure or require certainty stronger than the source contract.
External failures may be correlated across categories that look independent.
Do not assume only one bucket, family, or request class can be affected unless
the provider contract guarantees that independence. Bound recovery by total
work and required confidence. Tolerance applies only to evidence delivery; it
does not relax thresholds, error limits, required observed quotas, identity, or
correctness receipts.
Determine whether accepted evidence can be reused by an exact identity made
from every behavior-affecting artifact input rather than an unrelated repository
revision.
Human acceptance follows the behavior it qualifies, not the overall repository
identity. Include external configuration in that identity, reuse prior human
authority only when the key and receipt remain valid, and record reuse as
machine evidence rather than a new human action.

After an escaped deterministic blocker: fix it, identify the violated generic
invariant, inspect siblings, strengthen one family-level regression at the
correct abstraction, and determine why design, review, or testing missed it.
Improve durable guidance only when the lesson generalizes; do not add a rule for
a typo, literal filename, or transient provider incident. Continue the
authorized workflow without new user approval when the root cause is proven,
the correction remains narrow and reversible inside the authorized family, no
new mutation/security/identity authority is needed, no ambiguity is guessed,
and no acceptance gate is weakened.

When correctness depends on language or runtime composition, execute the real
boundary rather than inferring it from source text. For each material changed
entrypoint, record the runtime, caller, callee/import, parameter binding,
filesystem roots, working directory, success case, and fail-closed case. At
least one automated regression must execute every changed critical composition
boundary with the real runtime involved.

Treat required-verification latency as a change-safety contract. Do not extend a
timeout to conceal state-space explosion or inefficient verification design.
Prove each invariant at the smallest correct abstraction, compose independently
bounded subsystem proofs, keep safety and liveness state separate when possible,
and verify that required gates meet their declared latency budget.
