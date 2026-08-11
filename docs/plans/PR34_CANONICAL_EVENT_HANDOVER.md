# PR 34: Canonical Events And Model Handover

Status: placeholder; implementation has not started.

## Scope

- Give one real-world event one canonical identity.
- Treat multiple reports as evidence instead of duplicate training events.
- Bound the total weight of one event and one source.
- Retrain all five models under the complete v15 contract.
- Verify and switch the generation as one complete set.
- Remove superseded v14 runtime and transition code after verification.

## Acceptance Boundary

Immutable historical records remain available for audit. Active runtime code
must not mix v14 and v15 model members.
