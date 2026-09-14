---
name: change-safety
description: Review material system-boundary changes and their rollout or recovery. Skip tiny local behavior-preserving edits.
---

# Change Safety

For material architecture, state, persistence, migration, lifecycle, ownership,
API/CLI, authentication, concurrency, cross-version, background-service,
deployment, recovery, or irreversible changes, follow the
[Change Safety Protocol](../../../docs/protocols/CHANGE_SAFETY.md).

Before implementation, read the protocol core and the references selected by
its boundary table. Record the changed boundary,
invariants, impact graph, compatibility/failure/recovery matrices, and test and
rehearsal plan. Apply its environment-sensitive evidence and real-runtime
composition requirements to the affected boundary. After implementation,
complete its final adversarial review and required verification.

The protocol is the authority for these procedures; do not maintain a second
checklist here. Routine text edits and local behavior-preserving changes do not
trigger the material-change workflow. Pure-function repairs without a changed
external contract or lifecycle follow focused regression coverage instead.
