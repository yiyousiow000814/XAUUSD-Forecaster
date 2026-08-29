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

After an escaped blocker, identify the generic protocol question that should
have exposed it and improve that question rather than recording only an incident
literal. Keep authorization and task scope unchanged.
